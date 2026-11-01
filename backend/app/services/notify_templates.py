"""
notify_templates.py

The caregiver message texts from docs/spatial/MVP2.md, "Bảng câu chữ P0". The spec
table is the source; this registry copies it word for word. Variables only take the
values the spec lists, so neither code nor the agent can put free text in a message.
"""

from __future__ import annotations

import re

TEMPLATES = {
    "notify_initial": (
        "Fall alert #{incident_id} at {event_time}. Source: waist IMU. Wearer response: {response_status}. "
        "Wearer channel: {wearer_channel_status}. Location: {location_label}. "
        "Please check on the wearer and acknowledge this alert."
    ),
    "notify_escalation": (
        "Escalated fall alert #{incident_id} at {event_time}. Reason: {escalation_reason}. "
        "Wearer response: {response_status}. Wearer channel: {wearer_channel_status}. Location: {location_label}. "
        "Please check on the wearer and acknowledge this alert."
    ),
    "notify_late_ok": (
        "Update for fall alert #{incident_id}: the wearer has now said they are OK. This is a self-report. "
        "Wearer channel: {wearer_channel_status}. The earlier alert has not been withdrawn."
    ),
    "notify_evidence": (
        "Evidence update for fall alert #{incident_id}. Wearer response: {response_status}. "
        "Wearer channel: {wearer_channel_status}. Camera: {camera_status}. Scene analysis: {scene_status}. "
        "Location: {location_label}."
    ),
    "ack_received": "Acknowledgement recorded for fall alert #{incident_id}.",
}

# Lines spoken on iPhone 1, with the incident states in which each may play. The
# ones that can play while the wearer is being listened to must not contain a trigger phrase.
SPOKEN = {
    "ask_ok": ("Are you all right? Please respond.", {"awaiting_wearer"}),
    "ask_ok_10": ("Are you all right? Please respond.", {"awaiting_wearer"}),
    "ask_ok_20": ("I have not received a clear response. Please respond.", {"awaiting_wearer"}),
    "cancel_confirmed": ("The alert is cancelled. I will keep listening until the response window closes.",
                         {"cancelled_by_wearer"}),
    "alert_pending": ("I am sending an alert to your caregiver.", {"alerted"}),
    "alert_sent": ("An alert has been sent to your caregiver.", {"alerted"}),
    "alert_unconfirmed": ("I could not confirm that the alert was sent. If you can, call out for help.", {"alerted"}),
    "caregiver_ack": ("Your caregiver has acknowledged the alert.", {"alerted"}),
    "late_ok_recorded": ("You said you are OK. The alert remains active.", {"alerted"}),
    "stairs_visible": ("Stairs visible ahead.", {None}),  # no incident open
}

ALLOWED = {
    "response_status": {"Help requested", "No clear response", "OK reported after alert"},
    "escalation_reason": {"No acknowledgement within the response window", "The first contact could not be notified"},
    "wearer_channel_status": {"Ready", "Phone offline", "Microphone unavailable", "STT failed", "Speech degraded", "Unknown"},
    "camera_status": {"Available", "Unavailable", "Unsynchronized"},
    "scene_status": {"Stairs visible", "No stairs identified", "Pending", "Failed", "Timed out", "Unavailable"},
}
_MANUAL = re.compile(r"^Manual: [A-Za-z0-9 ,.'-]{1,40}$")


def render(template_id: str, **values) -> str:
    """The message text, or ValueError for a missing variable or a value the spec does not allow."""
    text = TEMPLATES[template_id]
    needed = set(re.findall(r"{(\w+)}", text))
    if needed != set(values):
        raise ValueError(f"{template_id} needs {sorted(needed)}, got {sorted(values)}")
    for key, value in values.items():
        if key in ALLOWED and value not in ALLOWED[key]:
            raise ValueError(f"{key}={value!r} is not in the spec")
        if key == "location_label" and value != "Unknown" and not _MANUAL.match(value):
            raise ValueError(f"location_label={value!r}: only Unknown or Manual: <label>")
        if key == "incident_id" and not isinstance(value, int):
            raise ValueError("incident_id must be an int")
    return text.format(**values)
