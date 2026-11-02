"""
live_stream.py

Continuity of one device stream in a live session (docs/spatial/MVP2_ARCHITECTURE.md,
"Firmware và đồng hồ"). Pure: no database, no clock. It decides, sample by sample,
whether the detector's window is still continuous, and keeps the coverage numbers
G4 publishes (MVP2.md, "G4 và bảng thử nghiệm").

Time comes from the device: `device_ms` is the ESP32's monotonic clock and
`sample_seq` counts samples it acquired. HTTP arrival time is never a sample
interval, so a slow batch with continuous seq and device time does not reset
anything. Source time is device time since the first accepted sample; mapping it
onto the backend clock is a separate step.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from app.config import WINDOW_SIZE

MAX_SAMPLE_GAP_MS = 40
WARMUP_NOMINAL_MS = 2000
_RECENT_SEQS = 4096


class BatchRejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DeviceSample:
    seq: int
    device_ms: int
    values: np.ndarray  # ax ay az gx gy gz svm


@dataclass(frozen=True)
class Step:
    """One new sample for the detector. `reset` names why the window restarts before it."""

    source_ms: int
    seq: int
    device_ms: int
    values: np.ndarray
    reset: str | None


@dataclass
class LiveStream:
    boot_id: str
    samples_accepted: int = 0
    duplicates: int = 0
    rejected_batches: int = 0
    dropped_at_source: int = 0   # the device's counter, cumulative since boot
    dropped_first: int | None = None  # that counter at the first batch: drops before the session are not the session's
    resets: list[dict] = field(default_factory=list)
    _origin_ms: int | None = None
    _last_seq: int | None = None
    _last_device_ms: int | None = None
    _recent: deque = field(default_factory=lambda: deque(maxlen=_RECENT_SEQS))
    _recent_set: set = field(default_factory=set)
    _fill: int = 0
    _ever_ready: bool = False
    _ready_since: int | None = None
    _available: list[list[int]] = field(default_factory=list)  # closed [start, end] spans with a full window
    _first_ready_ms: int | None = None
    _last_source_ms: int | None = None

    @property
    def origin_ms(self) -> int | None:
        """Device millis() of the first sample: source time 0."""
        return self._origin_ms

    def accept(self, boot_id: str, samples: list[DeviceSample], dropped_at_source: int = 0) -> list[Step]:
        """Steps for the samples not seen before, in order. Raises BatchRejected and accepts nothing
        for another boot or for a batch whose order cannot be trusted."""
        try:
            if boot_id != self.boot_id:
                raise BatchRejected("boot_changed")
            if any(b.seq <= a.seq for a, b in zip(samples, samples[1:])):
                raise BatchRejected("reordered_within_batch")
            fresh = []
            for s in samples:
                if self._last_seq is not None and s.seq <= self._last_seq:
                    if s.seq not in self._recent_set:
                        raise BatchRejected("arrived_after_newer_samples")
                    continue
                fresh.append(s)
            # millis() never runs backwards within one boot. Checked on its own, so a sequence gap
            # cannot hide it; such samples would give negative source time.
            previous = self._last_device_ms
            for s in fresh:
                if previous is not None and s.device_ms < previous:
                    raise BatchRejected("device_clock_went_back")
                previous = s.device_ms
        except BatchRejected:
            self.rejected_batches += 1
            raise
        self.duplicates += len(samples) - len(fresh)
        if self.dropped_first is None:
            self.dropped_first = dropped_at_source
        self.dropped_at_source = max(self.dropped_at_source, dropped_at_source)
        return [self._step(s) for s in fresh]

    def _step(self, s: DeviceSample) -> Step:
        reset = None
        if self._last_seq is None:
            self._origin_ms = s.device_ms
        elif s.seq != self._last_seq + 1:
            reset = "sequence_gap"
        elif s.device_ms - self._last_device_ms > MAX_SAMPLE_GAP_MS:
            reset = "time_gap"
        source_ms = s.device_ms - self._origin_ms

        if reset is not None:
            self.resets.append({"reason": reset, "source_ms": source_ms, "seq": s.seq})
            if self._ready_since is not None:
                self._available.append([self._ready_since, self._last_source_ms])
                self._ready_since = None
            self._fill = 0
        self._fill += 1
        if self._fill >= WINDOW_SIZE and self._ready_since is None:
            self._ready_since = source_ms
            if not self._ever_ready:
                self._ever_ready, self._first_ready_ms = True, source_ms

        self._last_seq, self._last_device_ms, self._last_source_ms = s.seq, s.device_ms, source_ms
        if len(self._recent) == self._recent.maxlen:
            self._recent_set.discard(self._recent[0])
        self._recent.append(s.seq)
        self._recent_set.add(s.seq)
        self.samples_accepted += 1
        return Step(source_ms, s.seq, s.device_ms, s.values, reset)

    @property
    def readiness(self) -> str:
        if self._ready_since is not None:
            return "ready"
        return "detection_unavailable" if self._ever_ready else "warming_up"

    def coverage(self) -> dict:
        """MVP2.md coverage: share of source time after the first nominal 2 s that had a full window."""
        end = self._last_source_ms
        spans = self._available + ([[self._ready_since, end]] if self._ready_since is not None else [])
        start = WARMUP_NOMINAL_MS
        denominator = (end - start) if end is not None else 0
        covered = sum(max(0, b - max(a, start)) for a, b in spans) if denominator > 0 else 0
        return {
            "coverage": round(covered / denominator, 4) if denominator > 0 else None,
            "denominator_ms": max(denominator, 0),
            "unavailable_ms": max(denominator - covered, 0),
            "first_warmup_ms": self._first_ready_ms,
        }

    def stats(self) -> dict:
        return {
            "boot_id": self.boot_id,
            "readiness": self.readiness,
            "samples_accepted": self.samples_accepted,
            "duplicates": self.duplicates,
            "rejected_batches": self.rejected_batches,
            "dropped_at_source": self.dropped_at_source,
            "dropped_in_session": self.dropped_at_source - (self.dropped_first or 0),
            "resets": self.resets[-50:],
            "reset_count": len(self.resets),
            "last_source_ms": self._last_source_ms,
            **self.coverage(),
        }
