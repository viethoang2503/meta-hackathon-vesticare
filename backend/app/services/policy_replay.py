"""
policy_replay.py

M5b (docs/spatial/MVP2.md): the incident policy run again on a virtual clock from recorded
response events. Nothing here sleeps, listens, sends or asks a model; the same recorded events
always give the same chain. It reuses incident_policy's decisions, and tests hold it equal to the
real run manager's chain on the same inputs.

Times are milliseconds since the incident opened (the wearer window starts at 0, the deadline D is
wearer_cancel_s). When several things fall on the same millisecond, deadlines go first, then send
results, then wearer replies, then Acknowledge. So at exactly D a reply does not beat the timeout,
and an Ack at exactly the caregiver deadline does not stop the escalation.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.services.incident_policy import ALERTED, AWAITING, CANCELLED, decide_deadline, decide_reply, late_ok_targets

BASE = datetime(2000, 1, 1)
DEADLINE, SEND_RESULT, REPLY, ACK = 0, 1, 2, 3  # order within the same millisecond


@dataclass(frozen=True)
class PolicyConfig:
    wearer_cancel_s: float
    caregiver_ack_s: float
    grace_s: float


def _at(ms: float) -> datetime:
    return BASE + timedelta(milliseconds=ms)


def simulate(config: PolicyConfig, events: list[dict], sends: dict[str, dict]) -> dict:
    """`events`: {"kind": "reply", "reply": ok|help|unclear, "capture_end_ms": int|None, "policy_ms": int}
    and {"kind": "ack", "actor": str, "policy_ms": int}. `sends`: the recorded result of each alert send
    by contact, {"status": ..., "done_ms": int}. Returns the actions the policy took and the final state."""
    deadline_ms = round(config.wearer_cancel_s * 1000)
    grace = timedelta(seconds=config.grace_s)
    state, reason, acked, escalated, late_ok_reported = AWAITING, None, False, False, False
    alert_statuses: dict[str, str] = {}
    late_ok_done: set[str] = set()
    actions: list[dict] = []
    queue: list[tuple] = []
    order = 0

    def push(at_ms, priority, kind, payload=None):
        nonlocal order
        order += 1
        heapq.heappush(queue, (at_ms, priority, order, kind, payload))

    def late_ok_actions(at_ms):
        for contact in late_ok_targets(alert_statuses, late_ok_done):
            late_ok_done.add(contact)
            actions.append({"t_ms": at_ms, "kind": "late_ok", "target": contact, "status": "scheduled"})

    def send(contact, kind, at_ms, **detail):
        actions.append({"t_ms": at_ms, "kind": kind, "target": contact, **detail})
        recorded = sends.get(contact, {"status": "simulated"})
        push(max(at_ms, recorded.get("done_ms", at_ms)), SEND_RESULT, "send_result", (contact, kind, recorded["status"]))
        alert_statuses[contact] = "scheduled"

    def alert(why, at_ms):
        nonlocal state, reason
        state, reason = ALERTED, why
        send("contact_1", "notify", at_ms, alert_reason=why)
        push(at_ms + round(config.caregiver_ack_s * 1000), DEADLINE, "caregiver_deadline")

    def escalate(why, at_ms):
        nonlocal escalated
        if state != ALERTED or acked or escalated:
            return
        escalated = True
        send("contact_2", "escalate", at_ms, escalation_reason=why)
        if late_ok_reported:
            late_ok_done.add("contact_2")
            actions.append({"t_ms": at_ms, "kind": "late_ok", "target": "contact_2", "status": "included_in_alert"})

    push(deadline_ms, DEADLINE, "wearer_deadline")
    for e in events:
        push(e["policy_ms"], REPLY if e["kind"] == "reply" else ACK, e["kind"], e)

    while queue:
        at_ms, _, _, kind, payload = heapq.heappop(queue)
        if kind == "wearer_deadline":
            if (why := decide_deadline(state)) is not None:
                alert(why, at_ms)
        elif kind == "caregiver_deadline":
            escalate("no_ack", at_ms)
        elif kind == "send_result":
            contact, send_kind, status = payload
            alert_statuses[contact] = status
            if send_kind == "notify" and status == "failed":
                escalate("first_contact_failed", at_ms)
            if late_ok_reported:
                late_ok_actions(at_ms)
        elif kind == "reply":
            capture = None if payload["capture_end_ms"] is None else _at(payload["capture_end_ms"])
            decision = decide_reply(state, payload["reply"], _at(deadline_ms), grace, _at(at_ms), capture)
            if decision.alert:
                alert(decision.alert, at_ms)
            if decision.cancel:
                state = CANCELLED
            applied, why = decision.applied, decision.reason
            if decision.late_ok and late_ok_reported:
                applied, why = False, "already_recorded"
            actions.append({"t_ms": at_ms, "kind": "self_report", "target": "wearer", "response": payload["reply"],
                            "applied": applied, "reason": why, "cancelled_alert": decision.cancel,
                            "raised_alert": decision.alert, "late_ok": decision.late_ok and applied})
            if decision.late_ok and applied:
                late_ok_reported = True
                late_ok_actions(at_ms)
        elif kind == "ack":
            if state == CANCELLED or acked:
                continue
            acked = True
            actions.append({"t_ms": at_ms, "kind": "ack", "target": payload["actor"]})
    return {"actions": actions, "final": {"state": state, "alert_reason": reason, "acknowledged": acked}}


def chain(result: dict) -> list[tuple]:
    """The order and content of the actions without their times: what two runs must agree on."""
    return [tuple((k, v) for k, v in sorted(a.items()) if k != "t_ms") for a in result["actions"]]
