"""Choose the post-fall stillness rule on TRAIN subjects, then report it once on TEST.

    PYTHONPATH=. python ml/scripts/tune_stillness.py

For every SisFall recording the detector runs once (model inference batched, then
fed through FallDetectionService.add_sample so confirmation logic is unchanged)
and its per-sample predictions are cached. Each candidate StillnessRule is then
replayed through StreamMonitor over the cache. Selection uses TRAIN only: keep
fall-file recall >= MIN_RECALL, then minimise daily-activity files with an
incident. TEST is reported for the chosen rule and the no-stillness baseline.
Authoritative TEST numbers come from gate_g1_incidents.py with the config set.
"""

import itertools
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.config import WINDOW_SIZE  # noqa: E402
from app.services.fall_detection import FallDetectionService, PredictionResult  # noqa: E402
from app.services.monitoring import StillnessRule, StreamMonitor  # noqa: E402
from ml.training import build_subject_independent_dataset as split  # noqa: E402
from ml.training import preprocess as sisfall_pp  # noqa: E402

MIN_RECALL = 0.98
GRID = {
    "seconds": [1.0, 2.0, 3.0],
    "svm_std_max": [0.03, 0.05, 0.1],
    "gyro_mean_max": [10.0, 20.0, 40.0],
    "window_seconds": [10.0],
}


def subjects():
    ids = [p.name for p in sorted(sisfall_pp.DATASET_DIR.iterdir()) if p.is_dir()]
    train, val, test = split.split_subject_ids(ids, seed=split.SPLIT_SEED, val_fraction=split.VAL_FRACTION, test_fraction=split.TEST_FRACTION)
    return {"train": train + val, "test": test}


def recordings(subject_ids):
    for sid in subject_ids:
        for path in sorted((sisfall_pp.DATASET_DIR / sid).glob("*.txt")):
            if path.name[0] not in "DF":
                continue
            data = sisfall_pp.load_sisfall_file(path)
            if data is None or len(data) < sisfall_pp.DOWNSAMPLE_FACTOR * WINDOW_SIZE:
                continue
            yield path.name[0], sisfall_pp.downsample(data, sisfall_pp.DOWNSAMPLE_FACTOR).astype(np.float32)


def cached_predictions(detector, samples, key):
    """Per-sample PredictionResults identical in logic to a live run, with batched model calls."""
    windows = np.stack([samples[i - WINDOW_SIZE + 1:i + 1] for i in range(WINDOW_SIZE - 1, len(samples))])
    probs = detector.keras_model.predict((windows - detector.scaler_mean) / detector.scaler_std, batch_size=1024, verbose=0)
    feed = iter((float(p[2]), float(p[1])) for p in probs)
    original = detector.predict_fall_probability
    detector.predict_fall_probability = lambda _window: next(feed)
    try:
        out = [detector.add_sample(key, s) for s in samples]
    finally:
        detector.predict_fall_probability = original
        detector.reset_patient(key)
    return out


class Replay:
    """Stands in for the detector: hands StreamMonitor the cached predictions in order."""

    model_source = "keras"

    def __init__(self, predictions: list[PredictionResult]):
        self._it = iter(predictions)

    def add_sample(self, _key, _sample, **_threshold):
        return next(self._it)

    def reset_patient(self, _key):
        pass


def score(cache, rule):
    hit = {"F": [0, 0], "D": [0, 0]}
    for kind, samples, predictions in cache:
        monitor = StreamMonitor("tune", Replay(predictions), stillness=rule)
        for i, s in enumerate(samples):
            monitor.add_sample(i * 20, s)
        hit[kind][0] += 1
        hit[kind][1] += bool(monitor.incidents)
    return hit["F"][1] / hit["F"][0], hit["D"][1] / hit["D"][0], hit


OPERATING_POINTS = {
    "safest (TRAIN recall 1.000)": StillnessRule(seconds=1.0, svm_std_max=0.1, gyro_mean_max=20.0),
    "middle (TRAIN recall 0.998)": StillnessRule(seconds=2.0, svm_std_max=0.05, gyro_mean_max=10.0),
    "strictest (TRAIN recall 0.993)": StillnessRule(seconds=3.0, svm_std_max=0.03, gyro_mean_max=10.0),
}


def report_operating_points():
    """TEST only, for operating points defined from TRAIN numbers."""
    detector = FallDetectionService()
    cache = [(kind, s, cached_predictions(detector, s, f"op:{n}")) for n, (kind, s) in enumerate(recordings(subjects()["test"]))]
    for label, rule in [("no stillness", None), *OPERATING_POINTS.items()]:
        recall, fp, counts = score(cache, rule)
        print(f"TEST {label}: fall recall {recall:.3f}  daily files with incident {fp:.3f}  {counts}", flush=True)


def main():
    if "--operating-points" in sys.argv:
        return report_operating_points()
    detector = FallDetectionService()
    assert detector.model_source == "keras", detector.model_source
    caches = {}
    for name, ids in subjects().items():
        caches[name] = [(kind, s, cached_predictions(detector, s, f"tune:{name}:{n}")) for n, (kind, s) in enumerate(recordings(ids))]
        print(f"{name}: {len(caches[name])} recordings cached", flush=True)

    base = score(caches["train"], None)
    print(f"TRAIN no stillness: fall recall {base[0]:.3f}  daily files with incident {base[1]:.3f}  {base[2]}", flush=True)
    results = []
    for values in itertools.product(*GRID.values()):
        rule = StillnessRule(**dict(zip(GRID, values)))
        recall, fp, counts = score(caches["train"], rule)
        results.append((rule, recall, fp))
        print(f"TRAIN {rule}: recall {recall:.3f}  daily {fp:.3f}", flush=True)

    eligible = [r for r in results if r[1] >= MIN_RECALL]
    chosen = min(eligible, key=lambda r: (r[2], -r[1])) if eligible else max(results, key=lambda r: r[1])
    print(f"\nCHOSEN on TRAIN: {chosen[0]}  recall {chosen[1]:.3f}  daily {chosen[2]:.3f}")
    for label, rule in [("no stillness", None), ("chosen", chosen[0])]:
        recall, fp, counts = score(caches["test"], rule)
        print(f"TEST {label}: fall recall {recall:.3f}  daily files with incident {fp:.3f}  {counts}")


if __name__ == "__main__":
    main()
