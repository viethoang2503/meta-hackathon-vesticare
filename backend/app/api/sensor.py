"""
sensor.py

API ingest du lieu cam bien:
- POST /api/sensor/data
- POST /api/sensor/batch
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.config import DEVICE_TOKEN, ML_CONFIDENCE_THRESHOLD
from app.database import get_db
from app.models.schemas import AlertEvent, ForecastResult, SensorData
from app.services.broadcaster import broadcaster
from app.services.fall_detection import PredictionResult, fall_detection_service
from app.services.live_stream import DeviceSample
from app.services.monitoring import IncidentEvent, StreamMonitor
from app.services.spatial_runs import LiveRunEnded, spatial_runs
from app.services.telegram_bot import send_fall_alert


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sensor", tags=["Sensor"])


def verify_device_token(authorization: str | None = Header(default=None)) -> None:
    """Kiem tra device token neu DEVICE_TOKEN duoc cau hinh trong .env.
    Neu DEVICE_TOKEN chua set thi bo qua (simulator van hoat dong binh thuong).
    """
    if not DEVICE_TOKEN:
        return
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if authorization.removeprefix("Bearer ") != DEVICE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid device token")


class SensorReadingPayload(BaseModel):
    patient_id: int = Field(gt=0)
    timestamp: datetime | None = None
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    svm: float | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value):
        if value in (None, ""):
            return None
        return value


class BatchSensorReadingPayload(BaseModel):
    timestamp: datetime | None = None
    # Required while the patient has a live spatial session (docs/spatial/MVP2_ARCHITECTURE.md).
    sample_seq: int | None = Field(default=None, ge=0)
    device_ms: int | None = Field(default=None, ge=0)
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    svm: float | None = None


class SensorBatchPayload(BaseModel):
    patient_id: int = Field(gt=0)
    samples: list[BatchSensorReadingPayload]
    device_id: str | None = None
    firmware_version: str | None = None
    ntp_synced: bool | None = None
    transport: Literal["wifi", "usb"] | None = None
    boot_id: str | None = Field(default=None, max_length=64)
    batch_id: int | None = Field(default=None, ge=0)
    dropped_at_source: int | None = Field(default=None, ge=0)  # cumulative since boot

    @field_validator("samples")
    @classmethod
    def validate_samples(cls, value):
        if not value:
            raise ValueError("Batch phai co it nhat 1 sample")
        return value


def _ensure_timestamp(timestamp: datetime | None) -> datetime:
    if timestamp is None:
        return datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _ensure_svm(ax: float, ay: float, az: float, svm: float | None) -> float:
    if svm is not None:
        return float(svm)
    return float(sqrt(ax * ax + ay * ay + az * az))


def _build_sample_vector(
    *,
    ax: float,
    ay: float,
    az: float,
    gx: float,
    gy: float,
    gz: float,
    svm: float,
) -> np.ndarray:
    return np.array([ax, ay, az, gx, gy, gz, svm], dtype=np.float32)


def _sample_event(
    *,
    ax: float,
    ay: float,
    az: float,
    gx: float,
    gy: float,
    gz: float,
    svm: float,
) -> dict:
    return {
        "ax": float(ax),
        "ay": float(ay),
        "az": float(az),
        "gx": float(gx),
        "gy": float(gy),
        "gz": float(gz),
        "svm": round(svm, 4),
    }


def _store_sensor_sample(
    db: Session,
    *,
    patient_id: int,
    timestamp: datetime,
    ax: float,
    ay: float,
    az: float,
    gx: float,
    gy: float,
    gz: float,
    svm: float,
    device_id: str | None = None,
    firmware_version: str | None = None,
    ntp_synced: bool | None = None,
    source_type: str | None = None,
) -> SensorData:
    record = SensorData(
        patient_id=patient_id,
        timestamp=timestamp,
        ax=ax,
        ay=ay,
        az=az,
        gx=gx,
        gy=gy,
        gz=gz,
        svm=svm,
        device_id=device_id,
        firmware_version=firmware_version,
        ntp_synced=ntp_synced,
        source_type=source_type or ("hardware" if device_id else "simulator"),
    )
    db.add(record)
    return record


# One detector context per patient for the sensor routes. Alerts follow the same
# rules as spatial replay (services/monitoring.py): only a confirmed FALL followed
# by stillness opens one, INSTABILITY never does, and nearby falls merge into it.
_streams: dict[int, StreamMonitor] = {}
_alert_ids: dict[tuple[int, int], int] = {}  # (patient, incident index) -> alert row


def _stream(patient_id: int) -> StreamMonitor:
    if patient_id not in _streams:
        _streams[patient_id] = StreamMonitor(stream_key=patient_id, detector=fall_detection_service)
    return _streams[patient_id]


def reset_stream(patient_id: int) -> None:
    """Forget a patient's detector context and any pending fall candidate."""
    for key in [k for k in _alert_ids if k[0] == patient_id]:
        del _alert_ids[key]
    monitor = _streams.pop(patient_id, None)
    if monitor is not None:
        monitor.close()
    else:
        fall_detection_service.reset_patient(patient_id)


def _source_ms(timestamp: datetime) -> int:
    return int(timestamp.timestamp() * 1000)


def _create_alert_if_needed(
    db: Session,
    *,
    patient_id: int,
    event_time: datetime,
    event: IncidentEvent | None,
    latency_ms: float | None = None,
) -> AlertEvent | None:
    if event is None:
        return None
    if event.kind == "extended":
        row_id = _alert_ids.get((patient_id, event.incident.index))
        row = db.get(AlertEvent, row_id) if row_id is not None else None
        if row is not None:
            row.confidence = event.incident.max_fall_probability
            row.peak_svm = event.incident.peak_svm
        return None
    alert = AlertEvent(
        patient_id=patient_id,
        timestamp=event_time,
        alert_type="FALL",
        confidence=event.incident.max_fall_probability,
        peak_svm=event.incident.peak_svm,
        is_acknowledged=False,
        latency_ms=latency_ms,
        notification_status="pending",
        decision_source=event.incident.decision_source,
    )
    db.add(alert)
    db.flush()
    _alert_ids[(patient_id, event.incident.index)] = alert.id
    return alert


def _record_notification_result(
    db: Session,
    alert: AlertEvent,
    sent: bool,
    error: str | None,
) -> None:
    """Persist Telegram delivery outcome on the alert row (second commit)."""
    if sent:
        alert.notification_status = "sent"
        alert.notification_sent_at = datetime.now(timezone.utc)
    elif error == "not_configured":
        alert.notification_status = "skipped"
    else:
        alert.notification_status = "failed"
        alert.notification_error = (error or "unknown")[:500]
    db.add(alert)
    db.commit()


_ADAPTIVE_THRESHOLDS = {"HIGH": 0.35, "MEDIUM": 0.45, "LOW": ML_CONFIDENCE_THRESHOLD}


def _get_adaptive_threshold(db: Session, patient_id: int) -> float:
    """V.6: Đọc risk level mới nhất → trả về threshold tương ứng.

    HIGH  (≥60) → T=0.35  nhạy nhất, bắt sớm khi bệnh nhân đang nguy hiểm
    MEDIUM(≥30) → T=0.45  nhạy vừa
    LOW         → T=0.55  bình thường, tránh false positive
    Nếu chưa có forecast nào → dùng threshold mặc định.
    """
    latest = (
        db.query(ForecastResult)
        .filter(ForecastResult.patient_id == patient_id)
        .order_by(ForecastResult.generated_at.desc())
        .first()
    )
    if latest is None:
        return ML_CONFIDENCE_THRESHOLD
    return _ADAPTIVE_THRESHOLDS.get(latest.risk_level, ML_CONFIDENCE_THRESHOLD)


def _prediction_probabilities(prediction: PredictionResult) -> dict:
    """The three state probabilities for one SSE event.

    The model emits fall and instability; normal is the remainder. Derived here
    rather than in the browser because three connected clients recomputing it
    independently could disagree, and because the flag below is the honest way to
    say it is not a third model output.

    Renormalised so the three sum to 1.0. Rounding each to four places can leave
    a total slightly off, which would show as progress bars that do not fill the
    row.
    """
    fall = round(prediction.fall_probability or 0.0, 4)
    instability = round(prediction.instability_probability or 0.0, 4)
    normal = round(max(0.0, 1.0 - fall - instability), 4)
    total = normal + instability + fall
    if total > 0:
        normal, instability, fall = (
            round(value / total, 4) for value in (normal, instability, fall)
        )
    return {
        "normal_probability": normal,
        "instability_probability": instability,
        "fall_probability": fall,
        "normal_probability_derived": True,
    }


def _prediction_response(prediction: PredictionResult, event: IncidentEvent | None) -> dict:
    return {
        "status": "ok",
        "samples_buffered": prediction.samples_buffered,
        "window_ready": prediction.window_ready,
        "state": prediction.state,
        "fall_probability": prediction.fall_probability,
        "instability_probability": prediction.instability_probability,
        "peak_svm": prediction.peak_svm,
        "alert_created": event is not None and event.kind == "opened",
        "alert_suppressed": event is not None and event.kind == "extended",
        "model_source": prediction.model_source,
        "active_threshold": prediction.active_threshold,   # V.6
    }


@router.post("/data")
def ingest_sensor_data(
    payload: SensorReadingPayload,
    db: Session = Depends(get_db),
    _: None = Depends(verify_device_token),
):
    t_received = datetime.now(timezone.utc)
    timestamp = _ensure_timestamp(payload.timestamp)
    svm = _ensure_svm(payload.ax, payload.ay, payload.az, payload.svm)

    _store_sensor_sample(
        db,
        patient_id=payload.patient_id,
        timestamp=timestamp,
        ax=payload.ax,
        ay=payload.ay,
        az=payload.az,
        gx=payload.gx,
        gy=payload.gy,
        gz=payload.gz,
        svm=svm,
    )

    sample_vector = _build_sample_vector(
        ax=payload.ax,
        ay=payload.ay,
        az=payload.az,
        gx=payload.gx,
        gy=payload.gy,
        gz=payload.gz,
        svm=svm,
    )

    # V.6: đọc adaptive threshold dựa theo forecast risk của bệnh nhân
    adaptive_threshold = _get_adaptive_threshold(db, payload.patient_id)

    monitor = _stream(payload.patient_id)
    t_before_predict_ns = time.perf_counter_ns()
    event = monitor.add_sample(_source_ms(timestamp), sample_vector, fall_threshold=adaptive_threshold)
    prediction = monitor.last_prediction
    alert_opened = event is not None and event.kind == "opened"
    t_after_predict_ns = time.perf_counter_ns()

    logger.debug(
        "sample patient_id=%d samples=%d state=%s fall_prob=%.3f instab_prob=%.3f peak_svm=%.3f",
        payload.patient_id,
        prediction.samples_buffered,
        prediction.state,
        prediction.fall_probability or 0.0,
        prediction.instability_probability or 0.0,
        prediction.peak_svm or 0.0,
    )
    if alert_opened:
        logger.info(
            "ALERT patient_id=%d ts=%s state=%s fall_prob=%.3f instab_prob=%.3f peak_svm=%.3f",
            payload.patient_id,
            timestamp.isoformat(),
            prediction.state,
            prediction.fall_probability or 0.0,
            prediction.instability_probability or 0.0,
            prediction.peak_svm or 0.0,
        )

    latency_ms = (datetime.now(timezone.utc) - timestamp).total_seconds() * 1000 if alert_opened else None

    t_before_alert_ns = time.perf_counter_ns()
    alert = _create_alert_if_needed(
        db,
        patient_id=payload.patient_id,
        event_time=timestamp,
        event=event,
        latency_ms=latency_ms,
    )
    t_after_alert_ns = time.perf_counter_ns()

    try:
        db.commit()
    except Exception:
        # Best effort: drop the patient's detector context rather than keep it ahead of the database.
        reset_stream(payload.patient_id)
        db.rollback()
        raise

    if alert is not None:
        t_after_commit_ns = time.perf_counter_ns()
        db.refresh(alert)
        sent, err = send_fall_alert(
            alert_id=alert.id,
            patient_id=payload.patient_id,
            alert_type=alert.alert_type,
            confidence=alert.confidence or 0.0,
            peak_svm=prediction.peak_svm or 0.0,
            timestamp=timestamp,
        )
        t_after_telegram_ns = time.perf_counter_ns()
        _record_notification_result(db, alert, sent, err)

    response = _prediction_response(prediction, event)
    response["alert_id"] = alert.id if alert is not None else None

    if prediction.window_ready:
        _ns_to_ms = lambda a, b: round((b - a) / 1_000_000, 3)
        timing: dict = {
            "backend_processing_latency_ms": _ns_to_ms(t_before_predict_ns, t_after_predict_ns),
            "inference_latency_ms": round(prediction.inference_latency_ms or 0.0, 3),
        }
        if alert is not None:
            timing["prediction_to_alert_ms"] = _ns_to_ms(t_before_alert_ns, t_after_alert_ns)
            timing["alert_to_telegram_ms"] = _ns_to_ms(t_after_commit_ns, t_after_telegram_ns)
            timing["total_from_request_ms"] = round(
                (datetime.now(timezone.utc) - t_received).total_seconds() * 1000, 3
            )
        response["timing"] = timing

    broadcaster.publish({
        "event_type": "imu_prediction",
        "patient_id": payload.patient_id,
        "transport": None,
        "samples": [_sample_event(
            ax=payload.ax,
            ay=payload.ay,
            az=payload.az,
            gx=payload.gx,
            gy=payload.gy,
            gz=payload.gz,
            svm=svm,
        )],
        "state": prediction.state,
        "svm": round(svm, 4),
        "peak_svm": round(prediction.peak_svm or 0.0, 4),
        **_prediction_probabilities(prediction),
        "samples_buffered": prediction.samples_buffered,
        "window_ready": prediction.window_ready,
        "alert_created": alert_opened,
        "alert_id": alert.id if alert is not None else None,
        "timestamp": timestamp.isoformat(),
    })

    return response


def _ingest_live_batch(session_id: str, payload: SensorBatchPayload) -> dict:
    """A live session owns this patient's samples: one detector and one policy, in spatial_runs.
    The legacy alert path does not see them."""
    if not DEVICE_TOKEN:
        # verify_device_token lets anything through when no token is set; MVP2_ARCHITECTURE.md
        # requires one for live samples because the tunnel makes this route public.
        raise HTTPException(status_code=503, detail="Set DEVICE_TOKEN before streaming to a live session")
    if payload.device_id != spatial_runs.live_device(session_id):
        raise HTTPException(status_code=409, detail="This device is not the one paired with the live session")
    if not payload.boot_id or any(s.sample_seq is None or s.device_ms is None for s in payload.samples):
        raise HTTPException(status_code=422, detail="A live session needs boot_id and sample_seq/device_ms on every sample")
    samples = [
        DeviceSample(s.sample_seq, s.device_ms, _build_sample_vector(
            ax=s.ax, ay=s.ay, az=s.az, gx=s.gx, gy=s.gy, gz=s.gz, svm=_ensure_svm(s.ax, s.ay, s.az, s.svm)))
        for s in payload.samples
    ]
    try:
        return spatial_runs.ingest_live(session_id, payload.boot_id, samples, payload.dropped_at_source or 0)
    except LiveRunEnded:
        raise HTTPException(status_code=409, detail="The live session has ended")


@router.post("/batch")
def ingest_sensor_batch(
    payload: SensorBatchPayload,
    db: Session = Depends(get_db),
    _: None = Depends(verify_device_token),
):
    live_session = spatial_runs.live_session_for(payload.patient_id)
    if live_session is not None:
        return _ingest_live_batch(live_session, payload)

    prediction = PredictionResult(
        state="BUFFERING",
        window_ready=False,
        samples_buffered=0,
        model_source=fall_detection_service.model_source,
    )
    created_alert_ids: list[int] = []
    last_event: IncidentEvent | None = None
    stored_samples = 0
    event_samples: list[dict] = []

    # V.6: đọc adaptive threshold 1 lần cho cả batch
    adaptive_threshold = _get_adaptive_threshold(db, payload.patient_id)

    # Collect (alert_id, confidence, peak_svm, event_time) for Telegram after commit.
    _notify_queue: list[tuple] = []

    for sample in payload.samples:
        timestamp = _ensure_timestamp(sample.timestamp)
        svm = _ensure_svm(sample.ax, sample.ay, sample.az, sample.svm)

        _store_sensor_sample(
            db,
            patient_id=payload.patient_id,
            timestamp=timestamp,
            ax=sample.ax,
            ay=sample.ay,
            az=sample.az,
            gx=sample.gx,
            gy=sample.gy,
            gz=sample.gz,
            svm=svm,
            device_id=payload.device_id,
            firmware_version=payload.firmware_version,
            ntp_synced=payload.ntp_synced,
            source_type=(
                f"hardware_{payload.transport}"
                if payload.transport is not None
                else None
            ),
        )
        stored_samples += 1
        event_samples.append(_sample_event(
            ax=sample.ax,
            ay=sample.ay,
            az=sample.az,
            gx=sample.gx,
            gy=sample.gy,
            gz=sample.gz,
            svm=svm,
        ))

        sample_vector = _build_sample_vector(
            ax=sample.ax,
            ay=sample.ay,
            az=sample.az,
            gx=sample.gx,
            gy=sample.gy,
            gz=sample.gz,
            svm=svm,
        )
        monitor = _stream(payload.patient_id)
        event = monitor.add_sample(_source_ms(timestamp), sample_vector, fall_threshold=adaptive_threshold)
        prediction = monitor.last_prediction
        last_event = event
        alert = _create_alert_if_needed(
            db,
            patient_id=payload.patient_id,
            event_time=timestamp,
            event=event,
            latency_ms=(
                (datetime.now(timezone.utc) - timestamp).total_seconds() * 1000
                if event is not None and event.kind == "opened"
                else None
            ),
        )
        if alert is not None:
            db.flush()
            created_alert_ids.append(alert.id)
            _notify_queue.append((
                alert.id,
                alert.alert_type or "FALL",
                alert.confidence or 0.0,
                alert.peak_svm or 0.0,
                timestamp,
            ))

    logger.info(
        "batch patient_id=%d stored=%d alerts=%s final_state=%s fall_prob=%.3f instab_prob=%.3f peak_svm=%.3f",
        payload.patient_id,
        stored_samples,
        created_alert_ids,
        prediction.state,
        prediction.fall_probability or 0.0,
        prediction.instability_probability or 0.0,
        prediction.peak_svm or 0.0,
    )

    try:
        db.commit()
    except Exception:
        # Best effort: drop the patient's detector context rather than keep it ahead of the database.
        reset_stream(payload.patient_id)
        db.rollback()
        raise

    for aid, atype, conf, psvm, ts in _notify_queue:
        sent, err = send_fall_alert(
            alert_id=aid,
            patient_id=payload.patient_id,
            alert_type=atype,
            confidence=conf,
            peak_svm=psvm,
            timestamp=ts,
        )
        alert_obj = db.query(AlertEvent).filter(AlertEvent.id == aid).first()
        if alert_obj is not None:
            _record_notification_result(db, alert_obj, sent, err)

    broadcaster.publish({
        "event_type": "imu_prediction",
        "patient_id": payload.patient_id,
        "transport": payload.transport,
        "samples": event_samples,
        "state": prediction.state,
        "svm": round(svm, 4),
        "peak_svm": round(prediction.peak_svm or 0.0, 4),
        **_prediction_probabilities(prediction),
        "samples_buffered": prediction.samples_buffered,
        "window_ready": prediction.window_ready,
        "alert_created": len(created_alert_ids) > 0,
        "alert_id": created_alert_ids[-1] if created_alert_ids else None,
        "timestamp": timestamp.isoformat(),
    })

    response = _prediction_response(prediction, last_event)
    response["alert_created"] = len(created_alert_ids) > 0
    response["stored_samples"] = stored_samples
    response["created_alert_ids"] = created_alert_ids
    return response


# ─── Device health ────────────────────────────────────────────────────────────

class DeviceHealthResponse(BaseModel):
    online: bool
    last_seen_s: float | None
    last_seen_at: datetime | None
    source_type: str | None
    effective_sample_rate: float | None


@router.get("/health", response_model=DeviceHealthResponse)
def get_device_health(
    patient_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
):
    """Last-seen status and effective sample rate for the active device/simulator."""
    query = db.query(SensorData)
    if patient_id is not None:
        query = query.filter(SensorData.patient_id == patient_id)

    latest = query.order_by(desc(SensorData.timestamp)).first()
    if latest is None:
        return DeviceHealthResponse(
            online=False,
            last_seen_s=None,
            last_seen_at=None,
            source_type=None,
            effective_sample_rate=None,
        )

    now_utc = datetime.now(timezone.utc)
    last_ts = latest.timestamp
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)

    elapsed_s = (now_utc - last_ts).total_seconds()

    cutoff_naive = (now_utc - timedelta(seconds=60)).replace(tzinfo=None)
    rate_q = db.query(func.count(SensorData.id)).filter(SensorData.timestamp >= cutoff_naive)
    if patient_id is not None:
        rate_q = rate_q.filter(SensorData.patient_id == patient_id)

    return DeviceHealthResponse(
        online=elapsed_s < 10.0,
        last_seen_s=round(elapsed_s, 1),
        last_seen_at=last_ts,
        source_type=latest.source_type,
        effective_sample_rate=round((rate_q.scalar() or 0) / 60.0, 1),
    )
