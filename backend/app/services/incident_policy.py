"""
incident_policy.py

What a fall incident does when the wearer answers or a deadline passes
(docs/spatial/MVP2.md, "Policy do code quyết định" and "Hợp đồng audio muộn").
Pure: the caller passes the state and the times, holds the run lock, and applies
the decision. Only a clear OK before the deadline cancels; capture time may make
an alert stronger, never weaker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

AWAITING, CANCELLED, ALERTED = "awaiting_wearer", "cancelled_by_wearer", "alerted"
Reply = Literal["ok", "help", "unclear"]
# A send counts as having reached the contact unless the API said it failed.
DELIVERED = ("sent", "unknown", "simulated")


@dataclass(frozen=True)
class ReplyDecision:
    alert: str | None = None  # alert reason to apply now: "timeout" or "wearer_help"
    cancel: bool = False
    late_ok: bool = False
    applied: bool = False
    reason: str | None = None  # why the reply changed nothing


def decide_deadline(state: str) -> str | None:
    return "timeout" if state == AWAITING else None


def decide_reply(state: str, reply: Reply, deadline: datetime, grace: timedelta, now: datetime,
                 capture_end: datetime | None) -> ReplyDecision:
    if state == AWAITING and now >= deadline:
        # The timeout won the race: alert first, then read the reply as one that came after it.
        late = decide_reply(ALERTED, reply, deadline, grace, now, capture_end)
        return ReplyDecision(alert="timeout", late_ok=late.late_ok, applied=late.applied, reason=late.reason)
    if state == AWAITING:
        if reply == "ok":
            return ReplyDecision(cancel=True, applied=True)
        if reply == "help":
            return ReplyDecision(alert="wearer_help", applied=True)
        return ReplyDecision(reason="unclear")
    if state == CANCELLED:
        if reply != "help":
            return ReplyDecision(reason="already_cancelled" if reply == "ok" else "unclear")
        if capture_end is None:
            return ReplyDecision(reason="capture_time_unknown")
        if capture_end > deadline:
            return ReplyDecision(reason="captured_after_deadline")
        if now > deadline + grace:
            return ReplyDecision(reason="processed_after_grace")
        return ReplyDecision(alert="wearer_help", applied=True)
    if reply == "ok":
        return ReplyDecision(late_ok=True, applied=True)
    return ReplyDecision(reason="already_alerted" if reply == "help" else "unclear")


def late_ok_targets(alert_sends: dict[str, str], late_ok_done: set[str]) -> list[str]:
    """Contacts owed a late-OK update: their alert went out (or may have) and they have not had one.
    A send still in flight is decided when its result arrives."""
    return [c for c, status in sorted(alert_sends.items()) if status in DELIVERED and c not in late_ok_done]
