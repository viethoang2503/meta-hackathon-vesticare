"""
m2_report.py

M2 table (docs/spatial/MVP2.md, M2) from the sessions you ran. Each incident is tagged with the branch it
exercised, the timing of the HELP path is broken into its steps, and the branches the spec lists are
counted as seen with real Telegram, seen with a stand-in, or not seen. Nothing here decides anything and
nothing is invented: a branch that was not run says so.

Timing steps for a spoken answer, in order: audio captured, received, waited in the queue, transcribed,
classified (the policy decision), the send scheduled, the send finished. The spec's 2 s target is from
classification (or the deadline, for silence) to the send being scheduled.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from app import database
from app.models.schemas import AlertEvent, SpatialAction, SpatialSession

SCHEDULE_TARGET_MS = 2000

# name -> what it covers. The order is the order of the table.
BRANCHES = {
    "ok_cancel": "OK before the deadline cancels",
    "help_alert": "HELP raises the alert at once",
    "timeout_alert": "Silence alerts at the deadline",
    "ack": "A caregiver acknowledges",
    "escalation_no_ack": "No acknowledgement escalates to contact 2",
    "send_failed": "The first alert fails to send and contact 2 is alerted at once",
    "send_unknown": "A send with no answer keeps the acknowledgement timer",
    "stop_during_window": "Stop pressed while the wearer is still being asked",
    "stop_during_help_only": "Stop pressed after a cancel, while only HELP is heard",
    "ok_then_help_before_deadline": "OK, then HELP before the deadline",
    "cancel_then_silence": "Cancelled, then silence to the end: no alert",
    "help_in_grace": "HELP captured before D, processed after D within the grace",
    "help_rejected_late": "HELP captured after D, too late, or with an unconfirmed clock is only logged",
    "ok_late": "OK after the alert is a late update, not a cancel",
    "ok_repeated": "A repeated OK is logged and sends nothing",
    "stt_dropped": "A turn dropped from a full speech queue",
    "stt_failed": "Speech recognition failed on a turn",
    "interrupted": "The backend restarted during the session",
}
REJECTED = ("captured_after_deadline", "processed_after_grace", "capture_time_unknown")


def _ms(delta: timedelta) -> int:
    return round(delta.total_seconds() * 1000)


def _at(t0: datetime, iso: str | None) -> int | None:
    return None if iso is None else _ms(datetime.fromisoformat(iso).replace(tzinfo=None) - t0)


def incident(db, session, row) -> dict:
    """One incident's tags and timings, read from its own record."""
    config = json.loads(session.config_snapshot or "{}")
    cancel_s = config.get("wearer_cancel_seconds", 30)
    t0 = row.wearer_deadline_at - timedelta(seconds=cancel_s)
    deadline_ms = round(cancel_s * 1000)
    actions = db.query(SpatialAction).filter(SpatialAction.incident_id == row.id).order_by(SpatialAction.id).all()
    detail = lambda a: json.loads(a.detail or "{}")
    reports = [(a, detail(a)) for a in actions if a.kind == "self_report"]
    sends = [(a, detail(a)) for a in actions if a.kind in ("notify", "escalate")]
    tags: set[str] = set()

    cancelled_at = next((_ms(a.created_at - t0) for a, d in reports if d.get("cancelled_alert")), None)
    if cancelled_at is not None:
        tags.add("ok_cancel")
    if row.alert_reason == "wearer_help":
        tags.add("help_alert")
    if row.alert_reason == "timeout":
        tags.add("timeout_alert")
    if any(a.kind == "ack" for a in actions):
        tags.add("ack")
    for a, d in sends:
        if a.kind == "escalate" and d.get("escalation_reason") == "no_ack":
            tags.add("escalation_no_ack")
        if a.kind == "notify" and d.get("status") == "failed":
            tags.add("send_failed")
        if d.get("status") == "unknown":
            tags.add("send_unknown")
    for a, d in reports:
        at = _ms(a.created_at - t0)
        speech = d.get("speech", {})
        if cancelled_at is not None and d.get("raised_alert") == "wearer_help":
            tags.add("ok_then_help_before_deadline" if at <= deadline_ms else "help_in_grace")
        if d.get("response") == "help" and not d.get("applied") and d.get("reason") in REJECTED:
            tags.add("help_rejected_late")
        if d.get("late_ok"):
            tags.add("ok_late")
        if d.get("reason") == "already_recorded":
            tags.add("ok_repeated")
        if speech.get("status") == "dropped":
            tags.add("stt_dropped")
        if speech.get("status") == "failed":
            tags.add("stt_failed")
    if cancelled_at is not None and row.spatial_state == "cancelled_by_wearer" and session.status != "Interrupted":
        tags.add("cancel_then_silence")
    ended_ms = None if session.ended_at is None else _ms(session.ended_at - t0)
    if session.end_reason == "user_stop" and ended_ms is not None and ended_ms < deadline_ms:
        tags.add("stop_during_help_only" if cancelled_at is not None and cancelled_at < ended_ms else "stop_during_window")
    if session.status == "Interrupted":
        tags.add("interrupted")

    notify = next(((a, d) for a, d in sends if a.kind == "notify"), None)
    answers = []
    for a, d in reports:
        speech = d.get("speech", {})
        classified = _ms(a.created_at - t0)
        entry = {"response": d.get("response"), "applied": d.get("applied"), "reason": d.get("reason"),
                 "transcript": speech.get("transcript"), "capture_end_ms": _at(t0, d.get("capture_end")),
                 "received_ms": _at(t0, speech.get("received_at")), "queue_ms": speech.get("queue_ms"),
                 "stt_ms": speech.get("stt_ms"), "classified_ms": classified}
        if d.get("raised_alert") and notify is not None:
            entry["scheduled_ms"] = _ms(notify[0].created_at - t0)
            entry["classified_to_scheduled_ms"] = entry["scheduled_ms"] - classified
        answers.append(entry)
    timeout_to_scheduled = None
    if row.alert_reason == "timeout" and notify is not None:
        timeout_to_scheduled = _ms(notify[0].created_at - t0) - deadline_ms
    send_results = [{"contact": a.target, "kind": a.kind, "status": d.get("status"), "channel": d.get("channel"),
                     "scheduled_ms": _ms(a.created_at - t0), "done_ms": _at(t0, d.get("done_at")),
                     "error": d.get("error")} for a, d in sends]
    return {"incident_id": row.id, "session_id": session.id, "state": row.spatial_state, "alert_reason": row.alert_reason,
            "tags": sorted(tags), "answers": answers, "timeout_to_scheduled_ms": timeout_to_scheduled,
            "sends": send_results, "channel": config.get("notification_mode"), "source": config.get("source"),
            "ended": session.end_reason}


def collect(session_ids: list[str], session_factory=None) -> list[dict]:
    out = []
    with (session_factory or database.SessionLocal)() as db:
        for sid in session_ids:
            session = db.get(SpatialSession, sid)
            if session is None or session.kind != "live":
                raise ValueError(f"no live session {sid}")
            for row in db.query(AlertEvent).filter(AlertEvent.session_id == sid).order_by(AlertEvent.id):
                if row.decision_source != "voice_drill":
                    out.append(incident(db, session, row))
    return out


def summarize(incidents: list[dict]) -> dict:
    branches = {}
    for name, text in BRANCHES.items():
        seen = [i for i in incidents if name in i["tags"]]
        real = [i for i in seen if i["channel"] == "telegram"]
        branches[name] = {"text": text, "seen": len(seen), "real_telegram": len(real),
                          "status": "Seen with real Telegram" if real else "Seen with a stand-in" if seen else "Not seen"}
    steps = [a["classified_to_scheduled_ms"] for i in incidents for a in i["answers"] if "classified_to_scheduled_ms" in a]
    steps += [i["timeout_to_scheduled_ms"] for i in incidents if i["timeout_to_scheduled_ms"] is not None]
    timing = {"samples": len(steps), "max_ms": max(steps) if steps else None, "target_ms": SCHEDULE_TARGET_MS,
              "within_target": all(s <= SCHEDULE_TARGET_MS for s in steps) if steps else None}
    sent = [s for i in incidents for s in i["sends"] if i["channel"] == "telegram" and s["status"] == "sent" and s["done_ms"] is not None]
    telegram_ms = sorted(s["done_ms"] - s["scheduled_ms"] for s in sent)
    return {"incidents": len(incidents), "branches": branches, "schedule_timing": timing,
            "telegram_send_ms": {"n": len(telegram_ms), "min": telegram_ms[0] if telegram_ms else None,
                                 "max": telegram_ms[-1] if telegram_ms else None},
            "not_measured": ["phone receipt time of a Telegram message", "behaviour under load from other models"],
            "complete": all(b["status"] == "Seen with real Telegram" for b in branches.values()),
            "trials": incidents}


def m2_report(session_ids: list[str], session_factory=None) -> dict:
    return summarize(collect(session_ids, session_factory))
