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

from app import database
from app.models.schemas import AlertEvent, SpatialAction

POSITIVE_TARGET = 9      # of 10 per person
POSITIVE_TRIALS = 10
NEGATIVE_TRIALS = {"ambiguous": 10, "silence_or_loudspeaker": 10}


def _group(expected: str) -> str:
    return "silence_or_loudspeaker" if expected in ("silence", "loudspeaker") else expected


def outcome(trial: dict) -> dict:
    """What happened to one drill incident: cancelled, raised by HELP, or left to the timeout."""
    return {"cancelled": trial["state"] == "cancelled_by_wearer",
            "help_alert": trial["alert_reason"] == "wearer_help",
            "timed_out": trial["alert_reason"] == "timeout"}


def summarize(trials: list[dict]) -> dict:
    """`trials`: dicts with person, expected, state, alert_reason and heard (transcripts)."""
    people: dict[str, dict] = {}
    for trial in trials:
        person = people.setdefault(trial["person"], {"groups": {}, "false_cancels": [], "trials": 0})
        person["trials"] += 1
        group = person["groups"].setdefault(_group(trial["expected"]), {"n": 0, "correct": 0, "false_help": 0, "cancelled": 0})
        result = outcome(trial)
        group["n"] += 1
        group["cancelled"] += result["cancelled"]
        if trial["expected"] == "ok":
            group["correct"] += result["cancelled"]
        elif trial["expected"] == "help":
            group["correct"] += result["help_alert"]
        else:
            group["correct"] += not result["cancelled"]
            group["false_help"] += result["help_alert"]
            if result["cancelled"]:
                person["false_cancels"].append({"expected": trial["expected"], "heard": trial["heard"]})
    for person in people.values():
        g = person["groups"]
        negatives = sum(g.get(k, {}).get("n", 0) for k in ("ambiguous", "silence_or_loudspeaker"))
        enough = (g.get("ok", {}).get("n", 0) >= POSITIVE_TRIALS and g.get("help", {}).get("n", 0) >= POSITIVE_TRIALS
                  and all(g.get(k, {}).get("n", 0) >= n for k, n in NEGATIVE_TRIALS.items()))
        ok_good = g.get("ok", {}).get("correct", 0) >= POSITIVE_TARGET
        help_good = g.get("help", {}).get("correct", 0) >= POSITIVE_TARGET
        person["negatives"] = negatives
        person["false_cancel_count"] = len(person["false_cancels"])
        person["status"] = ("Incomplete" if not enough else
                            "Pass" if ok_good and help_good and not person["false_cancels"] else "Fail")
    return {"people": people, "source": "Voice drill (no device, no fall detected)"}


def collect(session_id: str, session_factory=None) -> list[dict]:
    with (session_factory or database.SessionLocal)() as db:
        rows = db.query(AlertEvent).filter(AlertEvent.session_id == session_id,
                                           AlertEvent.decision_source == "voice_drill").order_by(AlertEvent.id).all()
        trials = []
        for row in rows:
            actions = db.query(SpatialAction).filter(SpatialAction.incident_id == row.id).order_by(SpatialAction.id).all()
            drill = next((json.loads(a.detail) for a in actions if a.kind == "drill"), None)
            if drill is None:
                continue
            heard = [json.loads(a.detail).get("speech", {}).get("transcript") for a in actions if a.kind == "self_report"]
            trials.append({"incident_id": row.id, "person": drill["person"], "expected": drill["expected"],
                           "state": row.spatial_state, "alert_reason": row.alert_reason, "heard": [h for h in heard if h]})
        return trials


def drill_report(session_id: str, session_factory=None) -> dict:
    trials = collect(session_id, session_factory)
    return {**summarize(trials), "trials": trials}
