"""
responder.py

The P0 agent for a live incident (docs/spatial/MVP2.md, "Agent P0"). Muse Spark reads the
evidence, writes a cited explanation for the laptop, and may do two things: send one evidence
update to a contact and have the phone say one of four fixed lines. It chooses ids. Code owns
every word that leaves the laptop and decides, at the moment of acting, whether the state
allows it. There is no tool to cancel, escalate, advise, ask about movement or sound an alarm,
and a request for one is refused and logged.

The run manager supplies three callables, each of which re-reads the incident under the run
lock and calls check_send / check_say before it acts:
  read_facts()                   -> the facts dict described below
  send(contact, ids, seen)       -> {"scheduled": True} or {"refused": reason}
  say(template_id)               -> {"said": template_id} or {"refused": reason}

facts: incident_id, state, acknowledged, sends {contact: status of its newest alert send},
agent_updates [contacts], said [template ids], and evidence {event, scene, delivery}, each a
list of items with an evidence_id.
"""

from __future__ import annotations

from app.services.incident_agent import AgentResult, run_tool_loop
from app.services.incident_policy import ALERTED, DELIVERED

CONTACTS = ("contact_1", "contact_2")
REASSURE = ("alert_pending", "alert_sent", "alert_unconfirmed", "caregiver_ack")

SYSTEM_PROMPT = """You help after a fall alert has gone out for a person wearing a waist sensor and a head-mounted phone.
Tools:
- get_event_context, get_scene_analysis, get_delivery_status read evidence. Cite evidence only by the evidence_id values they returned.
- send_message(contact, evidence_ids) sends one fixed-format evidence update to a caregiver. You choose the contact and the evidence it cites. You cannot write its text. Each contact can receive one update from you, and only after their alert went out.
- reassure_wearer(template_id) makes the phone say one fixed line: alert_pending, alert_sent, alert_unconfirmed or caregiver_ack. Choose the line that matches the delivery status. Do not say alert_sent unless the status is sent. Do not say caregiver_ack unless an acknowledgement exists.
Rules:
- Gather evidence first. Use at most one send_message per contact and say each line at most once.
- You cannot cancel the alert, alert anyone else, give first aid or medical advice, or ask about movement. Do not try.
- Location is Unknown. The camera frame is a real phone image only when the scene tool says it is Available.
- State what the evidence cannot show (for example why the person fell) as an uncertainty.
- Be brief: at most 4 observations and 3 uncertainties, each under 25 words.
Finish with one JSON object and nothing else:
{"summary": "<= 2 sentences", "observations": [{"text": "...", "evidence_ids": ["..."]}], "uncertainties": ["..."]}"""

_ID = {"incident_id": {"type": "integer"}}
TOOLS = [
    {"type": "function", "function": {
        "name": "get_event_context",
        "description": "IMU summary, detector decision and the wearer's response for the incident.",
        "parameters": {"type": "object", "properties": _ID, "required": ["incident_id"]}}},
    {"type": "function", "function": {
        "name": "get_scene_analysis",
        "description": "The camera frame matched to the fall and the SAM stairs reading of it, with their status.",
        "parameters": {"type": "object", "properties": _ID, "required": ["incident_id"]}}},
    {"type": "function", "function": {
        "name": "get_delivery_status",
        "description": "Whether each caregiver alert was sent, failed or unknown, and any acknowledgement.",
        "parameters": {"type": "object", "properties": _ID, "required": ["incident_id"]}}},
    {"type": "function", "function": {
        "name": "send_message",
        "description": "Send one fixed-format evidence update to a contact who already received the alert.",
        "parameters": {"type": "object", "properties": {
            "contact": {"type": "string", "enum": list(CONTACTS)},
            "evidence_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["contact", "evidence_ids"]}}},
    {"type": "function", "function": {
        "name": "reassure_wearer",
        "description": "Have the phone say one fixed line to the wearer.",
        "parameters": {"type": "object", "properties": {
            "template_id": {"type": "string", "enum": list(REASSURE)}}, "required": ["template_id"]}}},
]


def check_send(facts: dict, contact, evidence_ids, seen: set[str]) -> str | None:
    """Why an update to `contact` may not go out now, or None."""
    if contact not in CONTACTS:
        return f"{contact!r} is not a contact"
    if facts["state"] != ALERTED:
        return f"the incident is {facts['state']}, not alerted"
    if not isinstance(evidence_ids, list) or not evidence_ids or not all(isinstance(e, str) for e in evidence_ids):
        return "evidence_ids must list the evidence the update cites"
    unknown = [e for e in evidence_ids if e not in seen]
    if unknown:
        return f"cites evidence no tool returned: {unknown}"
    status = facts["sends"].get(contact)
    if status is None:
        return "no alert was sent to this contact"
    if status == "scheduled":
        return "the alert to this contact is still being sent"
    if status not in DELIVERED:
        return f"the alert to this contact {status}"
    if contact in facts["agent_updates"]:
        return "this contact already has the agent's update"
    return None


def check_say(facts: dict, template_id) -> str | None:
    """Why the phone may not say `template_id` now, or None."""
    if template_id not in REASSURE:
        return f"{template_id!r} is not a line the agent may choose"
    if facts["state"] != ALERTED:
        return f"the incident is {facts['state']}, not alerted"
    if template_id in facts["said"]:
        return "this line was already said for the incident"
    statuses = list(facts["sends"].values())
    if template_id == "alert_pending":
        if "sent" in statuses:
            return "the alert has already been confirmed sent"
        return None if "scheduled" in statuses else "no alert is being sent"
    if template_id == "alert_sent":
        return None if "sent" in statuses else "no alert has been confirmed sent"
    if template_id == "alert_unconfirmed":
        if "sent" in statuses:
            return "an alert has been confirmed sent"
        return None if statuses and statuses[-1] in ("failed", "unknown") else "the latest send has not failed or gone unknown"
    return None if facts["acknowledged"] else "no caregiver has acknowledged"


class ResponderTools:
    def __init__(self, read_facts, send, say):
        self._read_facts, self._send, self._say = read_facts, send, say
        self.seen_ids: set[str] = set()

    def run(self, name: str, args: dict) -> dict:
        facts = self._read_facts()
        groups = {"get_event_context": "event", "get_scene_analysis": "scene", "get_delivery_status": "delivery"}
        if name in groups:
            if args.get("incident_id") != facts["incident_id"]:
                return {"error": f"unknown incident {args.get('incident_id')}"}
            items = facts["evidence"][groups[name]]
            self.seen_ids |= {i["evidence_id"] for i in items}
            return {"items": items or [{"note": "no evidence of this kind"}]}
        if name == "send_message":
            return self._send(args.get("contact"), args.get("evidence_ids"), self.seen_ids)
        if name == "reassure_wearer":
            return self._say(args.get("template_id"))
        return {"refused": f"{name} is not a tool the agent has"}


def _validate(answer: dict, tools: ResponderTools) -> str | None:
    if not isinstance(answer.get("summary"), str) or not answer["summary"].strip():
        return "summary missing"
    observations = answer.get("observations")
    if not isinstance(observations, list) or not observations:
        return "no observations"
    for obs in observations:
        cited = obs.get("evidence_ids") if isinstance(obs, dict) else None
        if not isinstance(cited, list) or not cited:
            return "an observation cites no evidence"
        unknown = [e for e in cited if e not in tools.seen_ids]
        if unknown:
            return f"cites evidence no tool returned: {unknown}"
    if not all(isinstance(u, str) for u in answer.get("uncertainties", [])):
        return "uncertainties must be strings"
    return None


def run_responder(tools: ResponderTools, incident_id: int, chat, *, max_tool_calls: int,
                  timeout_seconds: float) -> AgentResult:
    return run_tool_loop(SYSTEM_PROMPT, f"Fall alert {incident_id} has been sent. Explain the evidence and update the wearer and contacts as the tools allow.",
                         TOOLS, tools, _validate, chat, max_tool_calls=max_tool_calls, timeout_seconds=timeout_seconds)
