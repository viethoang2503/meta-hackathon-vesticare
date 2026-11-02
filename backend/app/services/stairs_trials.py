"""
stairs_trials.py

M3 table from a camera-trial session (docs/spatial/MVP2.md, M3). The person marks a trial as
stairs in good light, stairs in dim light, or no stairs, walks the scene, then ends it. The
SAM readings the session stored while the trial was open decide its result. Conditions are
never merged: a good one cannot hide a bad one.

A trial is found when any reading saw stairs, not found when readings ran and none did, and
no result when no reading completed. No result is never counted as not found.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
from sqlalchemy.exc import IntegrityError

from app import database
from app.models.schemas import SpatialAction, SpatialAnalysis, SpatialSession

CONDITIONS = ("lit", "dim", "none")
MIN_TRIALS = 10            # valid trials per condition before a condition can be decided
LIT_TARGET = 0.8           # stairs found in good light
FALSE_POSITIVE_MAX = 0.1   # stairs reported in a scene without any
START, END = "m3_trial_start", "m3_trial_end"
SOURCE = "Camera trial (no device, no fall detected)"


class TrialRefused(Exception):
    """not_a_camera_trial, session_ended, busy or no_open_trial."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _result(trial: dict) -> str:
    calls = [c for c in trial["calls"] if c["status"] in ("Complete", "NoMatch")]
    if any(c["status"] == "Complete" for c in calls):
        return "found"
    return "not_found" if calls else "no_result"


def _latency(calls: list[dict]) -> dict:
    ms = [c["duration_ms"] for c in calls if c["status"] in ("Complete", "NoMatch") and c["duration_ms"] is not None]
    if not ms:
        return {"n": 0, "p50_ms": None, "p95_ms": None}
    return {"n": len(ms), "p50_ms": round(float(np.percentile(ms, 50))), "p95_ms": round(float(np.percentile(ms, 95)))}


def summarize(trials: list[dict]) -> dict:
    """`trials`: condition, ended, calls [{status, duration_ms}]. A trial that has not ended, or has a
    reading still running, is pending and is not scored."""
    conditions = {}
    for name in CONDITIONS:
        mine = [t for t in trials if t["condition"] == name]
        pending = [t for t in mine if not t["ended"] or any(c["status"] == "Running" for c in t["calls"])]
        settled = [t for t in mine if t not in pending]
        results = [_result(t) for t in settled]
        found, not_found = results.count("found"), results.count("not_found")
        valid = found + not_found
        conditions[name] = {
            "trials": len(mine), "pending": len(pending), "found": found, "not_found": not_found,
            "no_result": results.count("no_result"), "valid": valid,
            "rate": round(found / valid, 3) if valid else None,
            "latency": _latency([c for t in settled for c in t["calls"]]),
        }
        enough = valid >= MIN_TRIALS and not pending
        rate = found / valid if valid else 0.0
        conditions[name]["status"] = (
            "Incomplete" if not enough else
            "Reported" if name == "dim" else
            ("Pass" if rate >= LIT_TARGET else "Fail") if name == "lit" else
            ("Pass" if rate <= FALSE_POSITIVE_MAX else "Fail"))
    statuses = [c["status"] for c in conditions.values()]
    status = "Incomplete" if "Incomplete" in statuses else "Fail" if "Fail" in statuses else "Pass"
    return {"source": SOURCE, "status": status, "conditions": conditions}


def _open_trial(db, session_id: str):
    start = (db.query(SpatialAction).filter(SpatialAction.session_id == session_id, SpatialAction.kind == START)
             .order_by(SpatialAction.id.desc()).first())
    if start is None or db.query(SpatialAction).filter(SpatialAction.action_key == f"{END}:{start.id}").first():
        return None
    return start


def _camera_session(db, session_id: str, must_run: bool):
    session = db.get(SpatialSession, session_id)
    if session is None or session.kind != "live":
        raise TrialRefused("no_such_session")
    if json.loads(session.config_snapshot or "{}").get("source") != "camera_trial":
        raise TrialRefused("not_a_camera_trial")
    if must_run and session.status != "Running":
        raise TrialRefused("session_ended")
    return session


def start_trial(session_id: str, condition: str, session_factory=None) -> dict:
    with (session_factory or database.SessionLocal)() as db:
        _camera_session(db, session_id, must_run=True)
        if _open_trial(db, session_id) is not None:
            raise TrialRefused("busy")
        action = SpatialAction(session_id=session_id, kind=START, target=condition, created_at=_now())
        db.add(action)
        db.commit()
        return {"trial": action.id, "condition": condition}


def end_trial(session_id: str, session_factory=None) -> dict:
    with (session_factory or database.SessionLocal)() as db:
        _camera_session(db, session_id, must_run=False)
        start = _open_trial(db, session_id)
        if start is None:
            raise TrialRefused("no_open_trial")
        db.add(SpatialAction(session_id=session_id, kind=END, target=start.target, created_at=_now(),
                             action_key=f"{END}:{start.id}", detail=json.dumps({"trial": start.id})))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise TrialRefused("no_open_trial")
        return {"trial": start.id, "condition": start.target}


def collect(session_id: str, session_factory=None) -> list[dict]:
    with (session_factory or database.SessionLocal)() as db:
        marks = (db.query(SpatialAction).filter(SpatialAction.session_id == session_id, SpatialAction.kind.in_((START, END)))
                 .order_by(SpatialAction.id).all())
        ends = {json.loads(a.detail)["trial"]: a.created_at for a in marks if a.kind == END}
        readings = (db.query(SpatialAnalysis).filter(SpatialAnalysis.session_id == session_id,
                                                     SpatialAnalysis.kind == "sam_advisory")
                    .order_by(SpatialAnalysis.id).all())
        trials = []
        for start in (a for a in marks if a.kind == START):
            end = ends.get(start.id)
            calls = [{"status": r.status, "duration_ms": r.duration_ms, "frame_id": r.frame_id}
                     for r in readings if r.created_at >= start.created_at and (end is None or r.created_at <= end)]
            trials.append({"trial": start.id, "condition": start.target, "ended": end is not None, "calls": calls})
        return trials


def stairs_report(session_id: str, session_factory=None) -> dict:
    trials = collect(session_id, session_factory)
    return {**summarize(trials), "trials": [{**t, "result": _result(t)} for t in trials]}
