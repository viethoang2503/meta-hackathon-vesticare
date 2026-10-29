"""
monitoring.py

Turns one replayed IMU stream into fall incidents. Pure: no database, no
network, no clock. The replay runner persists what this returns, and
ml/scripts/build_scenarios.py runs the same code offline to check a scenario
before it is used in a demo.

Only confirmed FALL states open incidents. The INSTABILITY output is kept on
each step for the record but never opens one (docs/spatial/GATES.md, G1/C/D).
Incidents are grouped by source time, not by the detector's alert cooldown, so
a cooldown consumed by an earlier window can never hide a fall.

A confirmed FALL first becomes a candidate. It opens an incident only if the
wearer is still for `StillnessRule.seconds` within `window_seconds` after it;
otherwise it is rejected and kept in `rejected` for the record. Walking, stairs
and bending keep moving after their peak, a real fall usually does not.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Hashable, Literal

import numpy as np

from app.config import (
    FALL_STILLNESS_GYRO_MEAN_MAX,
    FALL_STILLNESS_SECONDS,
    FALL_STILLNESS_SVM_STD_MAX,
    FALL_STILLNESS_WINDOW_SECONDS,
    SAMPLING_RATE,
    SPATIAL_FALL_MERGE_SECONDS,
)
from app.services.fall_detection import FallDetectionService

FALL_MERGE_MS = SPATIAL_FALL_MERGE_SECONDS * 1000
# Consecutive samples are 1000 / SAMPLING_RATE ms apart; a larger gap starts a new FALL burst.
SAMPLE_GAP_MS = 2 * 1000 / SAMPLING_RATE


@dataclass(frozen=True)
class StillnessRule:
    seconds: float = FALL_STILLNESS_SECONDS
    window_seconds: float = FALL_STILLNESS_WINDOW_SECONDS
    svm_std_max: float = FALL_STILLNESS_SVM_STD_MAX  # g
    gyro_mean_max: float = FALL_STILLNESS_GYRO_MEAN_MAX  # deg/s, mean |gyro| over the still span

    def is_still(self, recent: np.ndarray) -> bool:
        """`recent` holds the last `seconds` of samples, columns ax ay az gx gy gz svm."""
        return (
            float(np.std(recent[:, 6])) <= self.svm_std_max
            and float(np.mean(np.linalg.norm(recent[:, 3:6], axis=1))) <= self.gyro_mean_max
        )


@dataclass
class Incident:
    index: int
    source_start_ms: int
    source_end_ms: int
    decision_source: str  # FallDetectionService.fall_decision_source of the opening window
    max_fall_probability: float
    peak_svm: float


@dataclass(frozen=True)
class IncidentEvent:
    kind: Literal["opened", "extended"]
    incident: Incident


@dataclass
class StreamMonitor:
    """Detector context for one stream. Never shared between sessions."""

    stream_key: Hashable
    detector: FallDetectionService
    merge_ms: int = FALL_MERGE_MS
    stillness: StillnessRule | None = field(default_factory=StillnessRule)
    incidents: list[Incident] = field(default_factory=list)
    rejected: list[Incident] = field(default_factory=list)
    state_counts: dict[str, int] = field(default_factory=dict)
    _candidate: Incident | None = None
    _since_candidate: deque = field(default_factory=deque)

    def add_sample(self, source_ms: int, sample: np.ndarray) -> IncidentEvent | None:
        prediction = self.detector.add_sample(self.stream_key, sample)
        self.state_counts[prediction.state] = self.state_counts.get(prediction.state, 0) + 1
        is_fall = prediction.state == "FALL"
        fall_p = prediction.fall_probability or 0.0
        peak = prediction.peak_svm or 0.0

        current = self.incidents[-1] if self.incidents else None
        if is_fall and current is not None and source_ms - current.source_end_ms <= self.merge_ms:
            _absorb(current, source_ms, fall_p, peak)
            return IncidentEvent("extended", current)

        if self._candidate is not None:
            return self._advance_candidate(source_ms, sample, is_fall, fall_p, peak, prediction.decision_source)

        if not is_fall:
            return None
        draft = Incident(
            index=len(self.incidents),
            source_start_ms=source_ms,
            source_end_ms=source_ms,
            decision_source=prediction.decision_source,
            max_fall_probability=fall_p,
            peak_svm=peak,
        )
        if self.stillness is None:
            return self._open(draft)
        self._candidate = draft
        self._since_candidate = deque(maxlen=max(1, round(self.stillness.seconds * SAMPLING_RATE)))
        return None

    def _advance_candidate(self, source_ms, sample, is_fall, fall_p, peak, decision_source) -> IncidentEvent | None:
        """Stillness is judged on the raw signal over the last `seconds`, so it can only
        hold once the impact has left that span; the model often keeps saying FALL while
        the person lies still, so FALL states do not reset it. The deadline runs from the
        latest FALL and the incident starts at the latest FALL burst: measuring from the
        first FALL let an early false trigger swallow a later real impact and reject both
        (S2: walking at 14.7 s, impact at 24.0 s).
        """
        draft, rule = self._candidate, self.stillness
        if is_fall:
            if source_ms - draft.source_end_ms > SAMPLE_GAP_MS:
                draft.source_start_ms, draft.decision_source = source_ms, decision_source
                draft.max_fall_probability, draft.peak_svm = fall_p, peak
            _absorb(draft, source_ms, fall_p, peak)
        self._since_candidate.append(sample)
        window = self._since_candidate
        if len(window) == window.maxlen and rule.is_still(np.asarray(window)):
            self._candidate = None
            return self._open(draft)
        if source_ms - draft.source_end_ms > rule.window_seconds * 1000:
            self._candidate = None
            self.rejected.append(draft)
        return None

    def _open(self, draft: Incident) -> IncidentEvent:
        draft.index = len(self.incidents)
        self.incidents.append(draft)
        return IncidentEvent("opened", draft)

    def close(self) -> None:
        self.detector.reset_patient(self.stream_key)


def _absorb(incident: Incident, source_ms: int, fall_p: float, peak: float) -> None:
    incident.source_end_ms = max(incident.source_end_ms, source_ms)
    incident.max_fall_probability = max(incident.max_fall_probability, fall_p)
    incident.peak_svm = max(incident.peak_svm, peak)
