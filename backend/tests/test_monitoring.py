"""Post-fall stillness: a confirmed FALL becomes an incident only if the wearer then lies still."""

import sys
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.fall_detection import fall_detection_service  # noqa: E402
from app.services.monitoring import StillnessRule, StreamMonitor  # noqa: E402

RATE = 50
RULE = StillnessRule(seconds=2.0, window_seconds=10.0, svm_std_max=0.05, gyro_mean_max=20.0)
# The detector confirms FALL after 0.5 s of FALL windows, so an incident starts up to that long after the impact.
CONFIRM_MS = 500


def samples(kind: str, seconds: float, seed: int = 0) -> np.ndarray:
    """ax ay az gx gy gz svm at 50 Hz. 'impact' is a 0.6 s burst peaking above the 2.5 g SVM rule."""
    n = int(seconds * RATE)
    rng = np.random.default_rng(seed)
    out = np.zeros((n, 7), dtype=np.float32)
    if kind == "still":
        out[:, 1] = -1.0
    elif kind == "moving":
        t = np.arange(n) / RATE
        out[:, 1] = -1.0 + 0.35 * np.sin(2 * np.pi * 2 * t)
        out[:, 3:6] = rng.normal(0, 60, (n, 3))
    elif kind == "impact":
        out[:, 1] = -1.0 - 2.5 * np.hanning(n)
        out[:, 3:6] = rng.normal(0, 200, (n, 3))
    out[:, 6] = np.linalg.norm(out[:, :3], axis=1)
    return out


def run(*parts):
    stream = np.concatenate([samples(kind, sec, seed=i) for i, (kind, sec) in enumerate(parts)])
    monitor = StreamMonitor(f"test:monitor:{parts}", fall_detection_service, stillness=RULE)
    for i, s in enumerate(stream):
        monitor.add_sample(i * 1000 // RATE, s)
    monitor.close()
    return monitor


def test_impact_then_lying_still_opens_one_incident_at_the_impact():
    monitor = run(("still", 4), ("impact", 0.6), ("still", 8))
    assert [i.decision_source for i in monitor.incidents] == ["svm_rule"]
    assert 4000 <= monitor.incidents[0].source_start_ms <= 4600 + CONFIRM_MS
    assert monitor.rejected == []


def test_impact_then_moving_on_is_rejected():
    monitor = run(("still", 4), ("impact", 0.6), ("moving", 14))
    assert monitor.incidents == []
    assert len(monitor.rejected) == 1


def test_an_earlier_trigger_does_not_swallow_a_later_real_impact():
    monitor = run(("still", 4), ("impact", 0.6), ("moving", 5), ("impact", 0.6), ("still", 8))
    assert len(monitor.incidents) == 1
    assert 9600 <= monitor.incidents[0].source_start_ms <= 10200 + CONFIRM_MS


def test_without_a_stillness_rule_the_first_fall_opens_immediately():
    stream = np.concatenate([samples("still", 4), samples("impact", 0.6, seed=1), samples("moving", 14, seed=2)])
    monitor = StreamMonitor("test:monitor:nostill", fall_detection_service, stillness=None)
    for i, s in enumerate(stream):
        monitor.add_sample(i * 1000 // RATE, s)
    monitor.close()
    assert len(monitor.incidents) == 1
