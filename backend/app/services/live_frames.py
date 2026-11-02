"""
live_frames.py

The camera side of a live session (docs/spatial/MVP2.md, "Camera và SAM"). In memory, no I/O.
iPhone 1 sends a 640x480 JPEG about once a second. A frame is matched to an IMU event by source
time, within 750 ms including the measured clock uncertainty. SAM runs on the latest frame only,
one request at a time, and its answer may become a spoken advisory only while it is fresh.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

MAX_FRAMES = 30
MATCH_TOLERANCE_MS = 750
SAM_MIN_START_GAP_S = 3
SAM_RESULT_MAX_AGE_S = 5
ADVISORY_COOLDOWN_S = 30
STAIRS_PROMPT = "stairs"


@dataclass(frozen=True)
class Frame:
    seq: int
    received_at: datetime
    received_mono: float            # seconds on the backend monotonic clock
    source_ms: float | None         # capture time on the IMU's source clock; None while a clock is unsynchronized
    uncertainty_ms: float | None
    data: bytes

    @property
    def id(self) -> str:
        return f"live-{self.seq}"


@dataclass(frozen=True)
class Match:
    status: str                     # Available | Unavailable | Unsynchronized
    frame: Frame | None             # the nearest frame; only evidence when status is Available
    delta_ms: float | None
    reason: str | None


@dataclass
class FrameStore:
    frames: deque = field(default_factory=lambda: deque(maxlen=MAX_FRAMES))
    received: int = 0

    def add(self, frame: Frame) -> None:
        self.frames.append(frame)
        self.received += 1

    def latest(self) -> Frame | None:
        return self.frames[-1] if self.frames else None

    def get(self, frame_id: str) -> Frame | None:
        return next((f for f in self.frames if f.id == frame_id), None)

    def match(self, source_ms: float, tolerance_ms: float = MATCH_TOLERANCE_MS) -> Match:
        if not self.frames:
            return Match("Unavailable", None, None, "no frame was received")
        synced = [f for f in self.frames if f.source_ms is not None]
        if not synced:
            return Match("Unsynchronized", self.latest(), None, "the phone or device clock is not synchronized")
        best = min(synced, key=lambda f: abs(f.source_ms - source_ms))
        delta = best.source_ms - source_ms
        if abs(delta) + best.uncertainty_ms > tolerance_ms:
            return Match("Unavailable", best, delta,
                         f"nearest frame is {abs(delta):.0f} ms away (±{best.uncertainty_ms:.0f} ms clock)")
        return Match("Available", best, delta, None)


@dataclass
class StairsGate:
    """Decides which frame SAM looks at and whether its answer may be spoken. Times are monotonic seconds."""
    candidate: Frame | None = None
    busy: bool = False
    last_start: float | None = None
    last_spoken: float | None = None

    def offer(self, frame: Frame) -> None:
        self.candidate = frame  # a newer frame replaces an older one that never ran

    def take(self, now: float) -> Frame | None:
        if self.busy or self.candidate is None:
            return None
        if self.last_start is not None and now - self.last_start < SAM_MIN_START_GAP_S:
            return None
        frame, self.candidate = self.candidate, None
        self.busy, self.last_start = True, now
        return frame

    def done(self) -> None:
        self.busy = False

    def may_announce(self, frame: Frame, now: float, incident_open: bool) -> bool:
        """True once per cooldown, for a fresh answer, outside an incident's response window."""
        if incident_open or now - frame.received_mono > SAM_RESULT_MAX_AGE_S:
            return False
        if self.last_spoken is not None and now - self.last_spoken < ADVISORY_COOLDOWN_S:
            return False
        self.last_spoken = now
        return True


def scene_status(analysis_status: str | None) -> str:
    """Spec words for a SAM analysis row's status (notify_templates.ALLOWED['scene_status'])."""
    return {"Complete": "Stairs visible", "NoMatch": "No stairs identified", "Running": "Pending",
            "Pending": "Pending", "Failed": "Failed", "TimedOut": "Timed out"}.get(analysis_status or "", "Unavailable")
