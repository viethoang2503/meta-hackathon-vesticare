"""
drill_report.py

M7 per-person table from a voice-drill session (docs/spatial/MVP2.md, M7). Each drill
incident records what the person was told to do (ok, help, ambiguous, silence,
loudspeaker); the outcome is what the policy did. Negatives (ambiguous, silence,
loudspeaker) must never cancel. Groups are never merged, so a good group cannot hide a
bad one.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app import database
from app.config import LATE_HELP_GRACE_SECONDS
from app.models.schemas import AlertEvent, SpatialAction, SpatialSession

POSITIVE_RATE = 0.9      # of the settled trials of a group
MIN_TRIALS = {"ok": 10, "help": 10, "ambiguous": 10, "silence_or_loudspeaker": 10}


def _group(expected: str) -> str:
    return "silence_or_loudspeaker" if expected in ("silence", "loudspeaker") else expected


def summarize(trials: list[dict]) -> dict:
    """`trials`: person, expected, settled (the incident has reached its final outcome), cancelled (the
    wearer cancelled it at any point, taken from the decision history), help_alert, heard.
    Only settled trials are scored; a trial still running keeps the person Incomplete."""
    people: dict[str, dict] = {}
    for trial in trials:
        person = people.setdefault(trial["person"], {"groups": {}, "false_cancels": [], "trials": 0, "pending": 0})
        person["trials"] += 1
        group = person["groups"].setdefault(_group(trial["expected"]),
                                            {"n": 0, "correct": 0, "false_help": 0, "cancelled": 0, "pending": 0})
        if not trial["settled"]:
            group["pending"] += 1
            person["pending"] += 1
            continue
        group["n"] += 1
        group["cancelled"] += trial["cancelled"]
        if trial["expected"] == "ok":
            group["correct"] += trial["cancelled"]
        elif trial["expected"] == "help":
            group["correct"] += trial["help_alert"]
        else:
            group["correct"] += not trial["cancelled"]
            group["false_help"] += trial["help_alert"]
            if trial["cancelled"]:
                person["false_cancels"].append({"expected": trial["expected"], "heard": trial["heard"]})
    for person in people.values():
        g = person["groups"]
        n = lambda k: g.get(k, {}).get("n", 0)
        rate = lambda k: g[k]["correct"] / g[k]["n"] if k in g and g[k]["n"] else 0.0
        enough = all(n(k) >= minimum for k, minimum in MIN_TRIALS.items()) and not person["pending"]
        person["negatives"] = n("ambiguous") + n("silence_or_loudspeaker")
        person["false_cancel_count"] = len(person["false_cancels"])
        person["status"] = ("Incomplete" if not enough else
                            "Pass" if rate("ok") >= POSITIVE_RATE and rate("help") >= POSITIVE_RATE
                            and not person["false_cancels"] else "Fail")
    return {"people": people, "source": "Voice drill (no device, no fall detected)"}


def _settled(row, grace_s: float, now) -> bool:
    """Alerted is final. A cancelled incident is final once its response window plus grace has closed,
    because until then a HELP can still raise it."""
    if row.spatial_state == "alerted":
        return True
    return row.spatial_state == "cancelled_by_wearer" and now >= row.wearer_deadline_at + timedelta(seconds=grace_s)


def collect(session_id: str, session_factory=None) -> list[dict]:
    with (session_factory or database.SessionLocal)() as db:
        grace = json.loads(db.get(SpatialSession, session_id).config_snapshot or "{}").get(
            "late_help_grace_seconds", LATE_HELP_GRACE_SECONDS)
        rows = db.query(AlertEvent).filter(AlertEvent.session_id == session_id,
                                           AlertEvent.decision_source == "voice_drill").order_by(AlertEvent.id).all()
        trials, now = [], datetime.now(timezone.utc).replace(tzinfo=None)
        for row in rows:
            actions = db.query(SpatialAction).filter(SpatialAction.incident_id == row.id).order_by(SpatialAction.id).all()
            drill = next((json.loads(a.detail) for a in actions if a.kind == "drill"), None)
            if drill is None:
                continue
            reports = [json.loads(a.detail) for a in actions if a.kind == "self_report"]
            trials.append({"incident_id": row.id, "person": drill["person"], "expected": drill["expected"],
                           "state": row.spatial_state, "alert_reason": row.alert_reason,
                           "settled": _settled(row, grace, now),
                           "cancelled": any(r.get("cancelled_alert") for r in reports),
                           "help_alert": row.alert_reason == "wearer_help",
                           "heard": [r["speech"]["transcript"] for r in reports if r.get("speech", {}).get("transcript")]})
        return trials


def drill_report(session_id: str, session_factory=None) -> dict:
    trials = collect(session_id, session_factory)
    return {**summarize(trials), "trials": trials}
