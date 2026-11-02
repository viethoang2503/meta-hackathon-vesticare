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
from app.services.spatial_runs import ECHO_KINDS
from app.services.wearer_channel import GUARD_MS
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
        if trial.get("echo"):
            continue  # echo trials measure the phone's own voice, not a person's answers
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
            if drill["expected"] in ECHO_KINDS:
                trials.append(_echo_trial(row, drill, actions, reports, _settled(row, grace, now)))
                continue
            trials.append({"incident_id": row.id, "person": drill["person"], "expected": drill["expected"],
                           "state": row.spatial_state, "alert_reason": row.alert_reason,
                           "settled": _settled(row, grace, now),
                           "cancelled": any(r.get("cancelled_alert") for r in reports),
                           "help_alert": row.alert_reason == "wearer_help",
                           "heard": [r["speech"]["transcript"] for r in reports if r.get("speech", {}).get("transcript")]})
        return trials


ECHO_PROMPTS = ("ask_ok", "ask_ok_10", "ask_ok_20", "cancel_confirmed")
ECHO_MIN_PLAYS = 3  # per volume and prompt (MVP2.md, M7)
VOLUMES = ("low", "mid", "high")


def _echo_trial(row, drill: dict, actions, reports: list[dict], settled: bool) -> dict:
    """One trial where nobody answers. The phone's own voice must not cancel the alert or raise HELP."""
    spoken = [(a.created_at, json.loads(a.detail)["template_id"]) for a in actions if a.kind == "spoken"]
    cancel_trial = drill["expected"] == "echo_cancel"
    # Each kind measures its own prompts. An echo_cancel trial is cut short by the laptop's OK, so the
    # question that played before it is not a clean measurement of that question.
    measured = ("cancel_confirmed",) if cancel_trial else ("ask_ok", "ask_ok_10", "ask_ok_20")
    plays = [t for _, t in spoken if t in measured]
    cancelled = any(r.get("cancelled_alert") for r in reports)
    help_alert = row.alert_reason == "wearer_help"
    if help_alert:
        result = "false_help"
    elif cancelled and not cancel_trial:
        result = "false_cancel"
    elif cancel_trial:
        result = "clean" if cancelled else "invalid"       # the laptop's OK has to have cancelled it first
    else:
        result = "clean" if row.alert_reason == "timeout" else "invalid"
    culprit = None
    if result in ("false_cancel", "false_help"):
        # The turn that was misread began after the prompt that had just finished playing.
        event = next((r for r in reversed(reports) if r.get("cancelled_alert") or r.get("raised_alert")), None)
        when = row.timestamp
        if event is not None:
            speech = event.get("speech", {})
            iso = speech.get("capture_start") or event.get("capture_end")
            when = datetime.fromisoformat(iso).replace(tzinfo=None) if iso else None
        before = [t for at, t in spoken if t in measured and (when is None or at <= when)]
        culprit = before[-1] if before else None
    return {"echo": True, "incident_id": row.id, "person": drill["person"], "expected": drill["expected"],
            "volume": drill["volume"], "room": drill["room"], "guard_ms": drill.get("guard_ms"), "settled": settled,
            "result": result, "plays": plays, "prompt_blamed": culprit,
            "heard": [r["speech"]["transcript"] for r in reports if r.get("speech", {}).get("transcript")]}


def echo_summary(trials: list[dict]) -> dict:
    """M7 echo table: per volume and prompt, how often the phone played it and what it set off by itself.
    A cell passes with at least 3 plays and no false cancel or false HELP. Groups are never merged."""
    cells = {v: {p: {"plays": 0, "false_cancel": 0, "false_help": 0} for p in ECHO_PROMPTS} for v in VOLUMES}
    rooms: dict[str, set] = {v: set() for v in VOLUMES}
    heard, unattributed, invalid, pending = [], 0, 0, 0
    for t in trials:
        if not t.get("echo"):
            continue
        if not t["settled"]:
            pending += 1
            continue
        invalid += t["result"] == "invalid"
        rooms[t["volume"]].add(t["room"])
        heard += [{"volume": t["volume"], "text": h} for h in t["heard"]]
        for prompt in t["plays"]:
            cells[t["volume"]][prompt]["plays"] += 1
        if t["result"] in ("false_cancel", "false_help"):
            if t["prompt_blamed"] is None:
                unattributed += 1
            else:
                cells[t["volume"]][t["prompt_blamed"]][t["result"]] += 1
    for volume in cells.values():
        for cell in volume.values():
            cell["status"] = ("Fail" if cell["false_cancel"] or cell["false_help"]
                              else "Pass" if cell["plays"] >= ECHO_MIN_PLAYS else "Incomplete")
    statuses = [c["status"] for v in cells.values() for c in v.values()]
    status = ("Fail" if "Fail" in statuses or unattributed else
              "Incomplete" if "Incomplete" in statuses or pending else "Pass")
    return {"status": status, "cells": cells, "rooms": {v: sorted(r) for v, r in rooms.items()},
            "guard_ms": GUARD_MS, "unattributed_false_events": unattributed, "invalid_trials": invalid,
            "pending_trials": pending, "stored": "transcripts only, no audio", "heard": heard,
            "source": "Voice drill (no device, no fall detected)"}


def drill_report(session_id: str, session_factory=None) -> dict:
    trials = collect(session_id, session_factory)
    return {**summarize(trials), "echo": echo_summary(trials), "trials": trials}
