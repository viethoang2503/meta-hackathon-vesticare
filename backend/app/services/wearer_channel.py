"""
wearer_channel.py

The speaker and microphone of iPhone 1 in one live session (docs/spatial/MVP2.md,
"Kênh tiếng nói"; MVP2_ARCHITECTURE.md, "WearerChannel và STT"). In memory, no I/O: the
run manager changes it under the run lock and the phone reads it by polling.

The phone plays queued lines in order and records fixed turns while `listen` is set.
It never cuts a turn to play a line; it plays pending lines between turns. Only the
newest reminder is kept, and a line whose incident has moved on is dropped before it
plays. Listening never extends the backend's deadline.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.notify_templates import SPOKEN

TURN_MS = 4000
GUARD_MS = 500
# The phone is silent while it records a turn, so offline means quiet for longer than a turn
# plus the spec's 3 s. Found in the first live run, which reported a recording phone as offline.
PHONE_OFFLINE_SECONDS = TURN_MS / 1000 + 3
DEGRADED_SECONDS = 30
REMINDERS = ("ask_ok_10", "ask_ok_20")
# Display order when several problems hold at once (MVP2.md, "Trạng thái người đeo và trạng thái kênh").
STATUS_ORDER = ("Phone offline", "Microphone unavailable", "STT failed", "Speech degraded")


@dataclass
class Utterance:
    id: int
    template_id: str
    text: str
    incident_id: int | None
    source: str  # code | agent
    created_at: datetime
    status: str = "queued"  # queued | played | dropped
    reason: str | None = None


@dataclass
class Listen:
    incident_id: int
    mode: str        # awaiting | help_only
    until: datetime  # the incident's deadline D; never moved


@dataclass
class WearerChannel:
    token: str = field(default_factory=lambda: secrets.token_urlsafe(18))
    utterances: list[Utterance] = field(default_factory=list)
    listen: Listen | None = None
    last_seen: datetime | None = None  # any authenticated request from the phone
    microphone_error: str | None = None
    stt_failed: bool = False
    last_drop: datetime | None = None
    drops: int = 0
    turns: int = 0

    def say(self, template_id: str, incident_id: int | None, now: datetime, source: str = "code") -> Utterance:
        if template_id in REMINDERS:
            for u in self.utterances:
                if u.status == "queued" and u.template_id in REMINDERS:
                    u.status, u.reason = "dropped", "newer_reminder"
        u = Utterance(len(self.utterances) + 1, template_id, SPOKEN[template_id][0], incident_id, source, now)
        self.utterances.append(u)
        return u

    def pending(self, after: int, state_of, deadline_of, now: datetime) -> list[Utterance]:
        """Queued lines after `after`, minus those that no longer fit their incident's state or
        whose deadline passed. `state_of(incident_id)` and `deadline_of(incident_id)` read the policy."""
        out = []
        for u in self.utterances:
            if u.id <= after or u.status != "queued":
                continue
            state = state_of(u.incident_id) if u.incident_id is not None else None
            if state not in SPOKEN[u.template_id][1]:
                u.status, u.reason = "dropped", f"incident is {state}"
                continue
            if u.template_id in ("ask_ok", *REMINDERS, "cancel_confirmed") and now >= deadline_of(u.incident_id):
                u.status, u.reason = "dropped", "past the deadline"
                continue
            out.append(u)
        return out

    def played(self, utterance_id: int) -> bool:
        u = next((u for u in self.utterances if u.id == utterance_id), None)
        if u is None or u.status != "queued":
            return False
        u.status = "played"
        return True

    def listening_for(self, now: datetime) -> Listen | None:
        if self.listen is not None and now >= self.listen.until:
            self.listen = None
        return self.listen

    def speech_dropped(self, now: datetime) -> None:
        self.drops += 1
        self.last_drop = now

    def status(self, now: datetime) -> str:
        if self.last_seen is None:
            return "Unknown"
        flags = {
            "Phone offline": now - self.last_seen > timedelta(seconds=PHONE_OFFLINE_SECONDS),
            "Microphone unavailable": self.microphone_error is not None,
            "STT failed": self.stt_failed,
            "Speech degraded": self.last_drop is not None and now - self.last_drop <= timedelta(seconds=DEGRADED_SECONDS),
        }
        return next((name for name in STATUS_ORDER if flags[name]), "Ready")

    def snapshot(self, now: datetime) -> dict:
        listen = self.listening_for(now)
        return {
            "status": self.status(now),
            "listening": None if listen is None else {"incident_id": listen.incident_id, "mode": listen.mode,
                                                      "until": listen.until.isoformat()},
            "spoken": [{"id": u.id, "template_id": u.template_id, "text": u.text, "status": u.status,
                        "reason": u.reason, "source": u.source} for u in self.utterances[-20:]],
            "speech_drops": self.drops,
            "turns": self.turns,
        }
