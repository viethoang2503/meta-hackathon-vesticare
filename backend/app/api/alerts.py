"""
alerts.py

API cho viec truy van cac canh bao da duoc tao boi backend.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.schemas import AlertEvent


router = APIRouter(prefix="/api/alerts", tags=["Alerts"])


class AlertResponse(BaseModel):
    id: int
    patient_id: int
    timestamp: datetime
    alert_type: str
    confidence: float
    peak_svm: float
    is_acknowledged: bool
    acknowledged_at: datetime | None
    notes: str | None
    latency_ms: float | None
    feedback: str | None
    notification_status: str | None
    notification_sent_at: datetime | None
    notification_error: str | None


class RecentAlertsResponse(BaseModel):
    total: int
    limit: int
    offset: int
    alerts: list[AlertResponse]


class AlertStatsResponse(BaseModel):
    total_alerts: int
    unacknowledged: int
    alerts_today: int
    avg_latency_ms: float | None
    false_alert_count: int


class AcknowledgeAlertPayload(BaseModel):
    notes: str | None = None


class FeedbackPayload(BaseModel):
    label: Literal["valid", "false"]


def _to_naive_utc(dt: datetime) -> datetime:
    """Strip timezone so comparisons work against SQLite's naive datetime strings."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@router.get("/stats", response_model=AlertStatsResponse)
def get_alert_stats(
    patient_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
):
    query = db.query(AlertEvent)
    if patient_id is not None:
        query = query.filter(AlertEvent.patient_id == patient_id)

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )

    avg_latency = db.query(func.avg(AlertEvent.latency_ms)).filter(
        AlertEvent.latency_ms.isnot(None)
    )
    if patient_id is not None:
        avg_latency = avg_latency.filter(AlertEvent.patient_id == patient_id)
    avg_latency_ms = avg_latency.scalar()

    false_count = db.query(AlertEvent).filter(AlertEvent.feedback == "false")
    if patient_id is not None:
        false_count = false_count.filter(AlertEvent.patient_id == patient_id)

    return AlertStatsResponse(
        total_alerts=query.count(),
        unacknowledged=query.filter(AlertEvent.is_acknowledged.is_(False)).count(),
        alerts_today=query.filter(AlertEvent.timestamp >= today_start).count(),
        avg_latency_ms=round(avg_latency_ms, 1) if avg_latency_ms is not None else None,
        false_alert_count=false_count.count(),
    )


@router.get("/recent", response_model=RecentAlertsResponse)
def get_recent_alerts(
    limit: int = Query(default=10, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    patient_id: int | None = Query(default=None, gt=0),
    include_acknowledged: bool = Query(default=True),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(AlertEvent)

    if patient_id is not None:
        query = query.filter(AlertEvent.patient_id == patient_id)

    if not include_acknowledged:
        query = query.filter(AlertEvent.is_acknowledged.is_(False))

    if since is not None:
        query = query.filter(AlertEvent.timestamp >= _to_naive_utc(since))

    if until is not None:
        query = query.filter(AlertEvent.timestamp <= _to_naive_utc(until))

    total_count = query.count()  # full match count before paging

    alerts = (
        query.order_by(desc(AlertEvent.timestamp), desc(AlertEvent.id))
        .offset(offset)
        .limit(limit)
        .all()
    )

    return RecentAlertsResponse(
        total=total_count,
        limit=limit,
        offset=offset,
        alerts=[_to_alert_response(alert) for alert in alerts],
    )


@router.post("/acknowledge/{alert_id}", response_model=AlertResponse)
def acknowledge_alert(
    payload: AcknowledgeAlertPayload,
    alert_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
):
    alert = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    if not alert.is_acknowledged:
        alert.is_acknowledged = True
        alert.acknowledged_at = datetime.now(timezone.utc)

    if payload.notes is not None:
        alert.notes = payload.notes.strip() or None

    db.add(alert)
    db.commit()
    db.refresh(alert)

    return _to_alert_response(alert)


@router.post("/feedback/{alert_id}", response_model=AlertResponse)
def submit_feedback(
    payload: FeedbackPayload,
    alert_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
):
    alert = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert.feedback = payload.label
    db.add(alert)
    db.commit()
    db.refresh(alert)

    return _to_alert_response(alert)


def _to_alert_response(alert: AlertEvent) -> AlertResponse:
    return AlertResponse(
        id=alert.id,
        patient_id=alert.patient_id,
        timestamp=alert.timestamp,
        alert_type=alert.alert_type,
        confidence=alert.confidence or 0.0,
        peak_svm=alert.peak_svm or 0.0,
        is_acknowledged=bool(alert.is_acknowledged),
        acknowledged_at=alert.acknowledged_at,
        notes=alert.notes,
        latency_ms=alert.latency_ms,
        feedback=alert.feedback,
        notification_status=alert.notification_status,
        notification_sent_at=alert.notification_sent_at,
        notification_error=alert.notification_error,
    )
