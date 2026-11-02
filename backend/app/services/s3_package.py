"""
s3_package.py

The S3 package of one live session (docs/spatial/MVP2_ARCHITECTURE.md, "S3"): the IMU record, the
camera frames with their clock quality, every response event with its times, what the policy did,
and what is needed to run it again. A package never holds raw audio and is refused when it would
contain a token or key. Export reads the session's own record; load checks the hashes.

    manifest.json   schema, device, model/scaler hashes, config, clock quality, consent
    imu.csv         the session journal, source time, sequence and device time kept
    frames/, frames.json
    responses.json  per incident: events with capture, receive and policy times
    expected.json   incident boundaries and the policy's actions to compare a replay with
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app import database
from app.config import (
    ANALYSIS_TIMEOUT_SECONDS,
    FALL_CONFIRM_SECONDS,
    FALL_STILLNESS_GYRO_MEAN_MAX,
    FALL_STILLNESS_SECONDS,
    FALL_STILLNESS_SVM_STD_MAX,
    FALL_STILLNESS_WINDOW_SECONDS,
    ML_INSTABILITY_CONFIDENCE_THRESHOLD,
    SPATIAL_FALL_MERGE_SECONDS,
    SVM_NORMAL_MAX,
    SVM_SWAY_THRESHOLD,
    KERAS_MODEL_PATH,
    MODEL_API_KEY,
    ML_CONFIDENCE_THRESHOLD,
    SAMPLING_RATE,
    SCALER_PARAMS_PATH,
    SVM_FALL_THRESHOLD,
    TELEGRAM_BOT_TOKEN,
    WINDOW_SIZE,
)
from app.models.schemas import AlertEvent, SpatialAction, SpatialSession
from app.services.live_stream import MAX_SAMPLE_GAP_MS

SCHEMA_VERSION = 1
FRAME_LABEL = "Phone camera at eye height (glasses proxy)"
CONSENT_FIELDS = ("participant", "publication", "recorded_on")
JOURNAL_COLUMNS = ["source_ms", "seq", "device_ms", "received_epoch_ms", "session_ms", "ax", "ay", "az", "gx", "gy", "gz", "svm"]
VISIBLE = ("notify", "escalate", "late_ok", "ack", "self_report")
_TOKEN_SHAPES = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")  # a Telegram bot token


class PackageRefused(Exception):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ms(delta: timedelta) -> int:
    return round(delta.total_seconds() * 1000)


def _utc_ms(t0: datetime, iso: str | None) -> int | None:
    return None if iso is None else _ms(datetime.fromisoformat(iso).replace(tzinfo=None) - t0)


def incident_record(db, row, config: dict) -> dict:
    """What happened to one incident, in the shape the policy replay takes and returns."""
    t0 = row.wearer_deadline_at - timedelta(seconds=config["wearer_cancel_seconds"])
    actions = db.query(SpatialAction).filter(SpatialAction.incident_id == row.id).order_by(SpatialAction.id).all()
    events, sends, recorded, responses = [], {}, [], []
    for a in actions:
        if a.kind not in VISIBLE:
            continue
        detail, at = json.loads(a.detail or "{}"), _ms(a.created_at - t0)
        if a.kind == "self_report":
            speech = detail.get("speech", {})
            capture_end = _utc_ms(t0, detail.get("capture_end"))
            events.append({"kind": "reply", "reply": detail["response"], "capture_end_ms": capture_end, "policy_ms": at,
                           "spoken": bool(speech)})
            recorded.append({"t_ms": at, "kind": "self_report", "target": "wearer", "response": detail["response"],
                             "applied": detail["applied"], "reason": detail["reason"],
                             "cancelled_alert": detail["cancelled_alert"], "raised_alert": detail["raised_alert"],
                             "late_ok": detail["late_ok"]})
            responses.append({"kind": "reply", "reply": detail["response"], "policy_ms": at, "capture_end_ms": capture_end,
                              "spoken": bool(speech),
                              "capture_start_ms": _utc_ms(t0, speech.get("capture_start")),
                              "received_ms": _utc_ms(t0, speech.get("received_at")),
                              "transcript": speech.get("transcript"), "applied": detail["applied"], "reason": detail["reason"],
                              "capture_problem": speech.get("capture_problem"),
                              "not_counted_as_ok": speech.get("not_counted_as_ok")})
        elif a.kind == "ack":
            events.append({"kind": "ack", "actor": a.target, "policy_ms": at})
            recorded.append({"t_ms": at, "kind": "ack", "target": a.target})
            responses.append({"kind": "ack", "actor": a.target, "policy_ms": at})
        elif a.kind == "late_ok":
            recorded.append({"t_ms": at, "kind": "late_ok", "target": a.target,
                             "status": "included_in_alert" if detail.get("status") == "included_in_alert" else "scheduled"})
        else:
            status = detail.get("status")
            done = _utc_ms(t0, detail.get("done_at"))
            sends[a.target] = {"status": "unknown" if status == "scheduled" else status, "done_ms": done if done is not None else at}
            extra = ({"alert_reason": detail.get("alert_reason")} if a.kind == "notify"
                     else {"escalation_reason": detail.get("escalation_reason")})
            recorded.append({"t_ms": at, "kind": a.kind, "target": a.target, **extra})
    return {"events": events, "sends": sends, "actions": recorded, "responses": responses,
            "final": {"state": row.spatial_state, "alert_reason": row.alert_reason, "acknowledged": bool(row.is_acknowledged)}}


def detector_config() -> dict:
    """Everything outside the model files that decides which incidents the detector opens. Recorded in the
    package and checked again on replay: another value gives another detector."""
    return {"sampling_rate_hz": SAMPLING_RATE, "window_size": WINDOW_SIZE, "max_sample_gap_ms": MAX_SAMPLE_GAP_MS,
            "svm_fall_threshold_g": SVM_FALL_THRESHOLD, "svm_sway_threshold_g": SVM_SWAY_THRESHOLD,
            "svm_normal_max_g": SVM_NORMAL_MAX, "ml_confidence_threshold": ML_CONFIDENCE_THRESHOLD,
            "ml_instability_confidence_threshold": ML_INSTABILITY_CONFIDENCE_THRESHOLD,
            "fall_confirm_seconds": FALL_CONFIRM_SECONDS, "fall_merge_seconds": SPATIAL_FALL_MERGE_SECONDS,
            "stillness": {"seconds": FALL_STILLNESS_SECONDS, "window_seconds": FALL_STILLNESS_WINDOW_SECONDS,
                          "svm_std_max": FALL_STILLNESS_SVM_STD_MAX, "gyro_mean_max": FALL_STILLNESS_GYRO_MEAN_MAX}}


def config_differences(recorded: dict | None, current: dict, prefix: str = "") -> list[str]:
    """What differs between the recorded detector config and the one running now. A key the package does
    not record counts as unverified, not as equal."""
    if recorded is None:
        return ["the package does not record the detector config"]
    out = []
    for key, value in current.items():
        name = f"{prefix}{key}"
        if key not in recorded:
            out.append(f"{name} is not recorded")
        elif isinstance(value, dict):
            out += config_differences(recorded[key], value, f"{name}.") if isinstance(recorded[key], dict) else [f"{name} is not recorded"]
        elif recorded[key] != value:
            out.append(f"{name}: recorded {recorded[key]}, now {value}")
    return out


def unfinished(db, session, row, config: dict, now: datetime) -> str | None:
    """Why an incident's record is not complete yet, or None. A package of a half-played incident would
    carry a final state the policy has not reached."""
    grace = timedelta(seconds=config["late_help_grace_seconds"])
    if row.spatial_state == "awaiting_wearer":
        return "it is still waiting for the wearer"
    if now < row.wearer_deadline_at + grace:
        return f"its response window and grace run until {(row.wearer_deadline_at + grace).isoformat(timespec='seconds')} UTC"
    sends = db.query(SpatialAction).filter(SpatialAction.incident_id == row.id, SpatialAction.kind.in_(
        ("notify", "escalate", "late_ok", "agent_update"))).all()
    if any(json.loads(a.detail or "{}").get("status") == "scheduled" for a in sends):
        return "a message is still being sent"
    escalated = any(a.kind == "escalate" for a in sends)
    if row.spatial_state == "alerted" and not row.is_acknowledged and not escalated:
        if row.caregiver_deadline_at is None or now < row.caregiver_deadline_at:
            return "the caregiver acknowledgement window is still open"
        return "the escalation after the acknowledgement window has not been recorded"
    return None


def _secrets(extra: list[str]) -> list[str]:
    import os
    return [s for s in [MODEL_API_KEY, TELEGRAM_BOT_TOKEN, os.environ.get("DEVICE_TOKEN", ""), *extra] if s and len(s) >= 8]


def scan_for_secrets(folder: Path, secrets: list[str]) -> list[str]:
    """Names of files that contain a token, key or bot-token shape. Frames are scanned as bytes too."""
    found = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        data = path.read_bytes()
        text = data.decode("utf-8", errors="ignore")
        if any(s.encode() in data for s in secrets) or _TOKEN_SHAPES.search(text):
            found.append(str(path.relative_to(folder)))
    return found


def build(session_id: str, out_dir: Path, consent: dict, live_data_dir: Path, *, session_factory=None,
          extra_secrets: list[str] = (), force: bool = False, allow_incomplete: bool = False) -> dict:
    """Writes the package for a finished live session. Raises PackageRefused when it cannot be made."""
    missing = [k for k in CONSENT_FIELDS if not consent.get(k)]
    if missing or consent["publication"] not in ("granted", "not_granted"):
        raise PackageRefused(f"consent needs participant, publication (granted or not_granted) and recorded_on; missing {missing}")
    folder = live_data_dir / session_id
    if not (folder / "imu.csv").is_file():
        raise PackageRefused("this session has no IMU record")
    with (session_factory or database.SessionLocal)() as db:
        session = db.get(SpatialSession, session_id)
        if session is None or session.kind != "live":
            raise PackageRefused("no such live session")
        if session.status == "Running":
            raise PackageRefused("stop the session first")
        config = json.loads(session.config_snapshot or "{}")
        stats = json.loads(session.live_stats or "{}")
        rows = db.query(AlertEvent).filter(AlertEvent.session_id == session_id).order_by(AlertEvent.source_start_ms).all()
        incidents = [(i, r, incident_record(db, r, config)) for i, r in enumerate(rows)]
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        open_items = [f"incident {i}: {why}" for i, r, _ in incidents if (why := unfinished(db, session, r, config, now))]
        if session.status == "Interrupted":
            open_items.append("the backend restarted during the session, so its incidents never finished")
        if open_items and not allow_incomplete:
            raise PackageRefused("the session is not finished: " + "; ".join(open_items))
        started_at, device_id = session.started_at, session.device_id

    if out_dir.exists():
        if not force:
            raise PackageRefused(f"{out_dir} exists; pass force to replace it")
        shutil.rmtree(out_dir)
    (out_dir / "frames").mkdir(parents=True)
    shutil.copy(folder / "imu.csv", out_dir / "imu.csv")

    frames = []
    index = folder / "frames.jsonl"
    for line in (index.read_text().splitlines() if index.is_file() else []):
        frame = json.loads(line)
        shutil.copy(folder / frame["file"], out_dir / frame["file"])
        frames.append(frame)
    (out_dir / "frames.json").write_text(json.dumps({"label": FRAME_LABEL, "interval_s": 1, "frames": frames}, indent=1))

    responses = [{"index": i, "events": rec["responses"], "sends": rec["sends"]} for i, _, rec in incidents]
    (out_dir / "responses.json").write_text(json.dumps({"incidents": responses}, indent=1))
    expected = {
        "incidents": [{"index": i, "source_start_ms": r.source_start_ms, "source_end_ms": r.source_end_ms,
                       "decision_source": r.decision_source} for i, r, _ in incidents],
        "policy": [{"index": i, "actions": rec["actions"], "final": rec["final"]} for i, _, rec in incidents],
    }
    (out_dir / "expected.json").write_text(json.dumps(expected, indent=1))

    uncertainty = [f["uncertainty_ms"] for f in frames if f.get("uncertainty_ms") is not None]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "recorded_on": consent["recorded_on"], "session_started_at_utc": started_at.isoformat(),
        "source": config.get("source"), "source_label": config.get("source_label"), "device_id": device_id,
        "model": {"model_source": config.get("model_source"), "keras_sha256": _sha(KERAS_MODEL_PATH),
                  "scaler_sha256": _sha(SCALER_PARAMS_PATH)},
        "config": {"wearer_cancel_seconds": config["wearer_cancel_seconds"], "caregiver_ack_seconds": config["caregiver_ack_seconds"],
                   "late_help_grace_seconds": config["late_help_grace_seconds"], "sampling_rate_hz": SAMPLING_RATE,
                   "window_size": WINDOW_SIZE, "max_sample_gap_ms": config["max_sample_gap_ms"],
                   "svm_fall_threshold_g": SVM_FALL_THRESHOLD, "ml_confidence_threshold": ML_CONFIDENCE_THRESHOLD,
                   "stillness": {"seconds": FALL_STILLNESS_SECONDS, "svm_std_max": FALL_STILLNESS_SVM_STD_MAX,
                                 "gyro_mean_max": FALL_STILLNESS_GYRO_MEAN_MAX},
                   "analysis_timeout_seconds": ANALYSIS_TIMEOUT_SECONDS, "detector": detector_config()},
        "clock": {"device": stats.get("clock"), "frame_uncertainty_ms": {
            "max": max(uncertainty) if uncertainty else None, "frames_with_clock": len(uncertainty), "frames": len(frames)}},
        "imu": {"columns": JOURNAL_COLUMNS, "coverage": stats.get("coverage"), "resets": stats.get("resets", []),
                "dropped_at_source": stats.get("dropped_at_source")},
        "contents": {"incidents": len(incidents), "frames": len(frames), "raw_audio": "not stored"},
        "complete": not open_items, "incomplete_reasons": open_items,
        "consent": {k: consent[k] for k in CONSENT_FIELDS},
        "hashes": {"imu.csv": _sha(out_dir / "imu.csv")},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    leaks = scan_for_secrets(out_dir, _secrets(list(extra_secrets)))
    if leaks:
        shutil.rmtree(out_dir)
        raise PackageRefused(f"a secret was found in {leaks}; nothing was written")
    return manifest


def load(path: Path) -> dict:
    """The package with its IMU table, after checking the schema, the IMU hash and every frame hash."""
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise PackageRefused(f"schema {manifest['schema_version']} is not {SCHEMA_VERSION}")
    if _sha(path / "imu.csv") != manifest["hashes"]["imu.csv"]:
        raise PackageRefused("imu.csv does not match its hash")
    frames = json.loads((path / "frames.json").read_text())
    for frame in frames["frames"]:
        if hashlib.sha256((path / frame["file"]).read_bytes()).hexdigest() != frame["sha256"]:
            raise PackageRefused(f"{frame['file']} does not match its hash")
    table = np.loadtxt(path / "imu.csv", delimiter=",", skiprows=1, ndmin=2)
    return {"manifest": manifest, "table": table, "frames": frames,
            "responses": json.loads((path / "responses.json").read_text()),
            "expected": json.loads((path / "expected.json").read_text())}
