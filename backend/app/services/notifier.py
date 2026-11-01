"""
notifier.py

Where caregiver messages go. The policy decides and records each send first; a
notifier only delivers the rendered text and reports what happened:
"sent" (the API accepted it), "failed" (it said no), "unknown" (no answer in time)
or "simulated" (nothing left the process).
"""

from __future__ import annotations


class SimulatedNotifier:
    channel = "simulated"

    def configured_contacts(self) -> set[str]:
        return {"contact_1", "contact_2"}

    def send(self, contact: str, text: str, incident_id: int, ack_button: bool) -> dict:
        return {"status": "simulated"}
