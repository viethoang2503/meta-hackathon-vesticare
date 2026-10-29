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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Hashable, Literal

import numpy as np

from app.config import ML_CONFIDENCE_THRESHOLD, SPATIAL_FALL_MERGE_SECONDS
from app.services.fall_detection import FallDetectionService

FALL_MERGE_MS = SPATIAL_FALL_MERGE_SECONDS * 1000

DecisionSource = Literal["model", "svm_rule"]


@dataclass
class Incident:
    index: int
    source_start_ms: int
    source_end_ms: int
    decision_source: DecisionSource
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
    incidents: list[Incident] = field(default_factory=list)
    state_counts: dict[str, int] = field(default_factory=dict)

    def add_sample(self, source_ms: int, sample: np.ndarray) -> IncidentEvent | None:
        prediction = self.detector.add_sample(self.stream_key, sample)
        self.state_counts[prediction.state] = self.state_counts.get(prediction.state, 0) + 1
        if prediction.state != "FALL":
            return None

        fall_p = prediction.fall_probability or 0.0
        peak = prediction.peak_svm or 0.0
        current = self.incidents[-1] if self.incidents else None
        if current is not None and source_ms - current.source_end_ms <= self.merge_ms:
            current.source_end_ms = source_ms
            current.max_fall_probability = max(current.max_fall_probability, fall_p)
            current.peak_svm = max(current.peak_svm, peak)
            return IncidentEvent("extended", current)

        incident = Incident(
            index=len(self.incidents),
            source_start_ms=source_ms,
            source_end_ms=source_ms,
            decision_source="model" if fall_p >= ML_CONFIDENCE_THRESHOLD else "svm_rule",
            max_fall_probability=fall_p,
            peak_svm=peak,
        )
        self.incidents.append(incident)
        return IncidentEvent("opened", incident)

    def close(self) -> None:
        self.detector.reset_patient(self.stream_key)
