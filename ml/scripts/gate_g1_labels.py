"""Gate G1: what does the deployed 3-class model predict on held-out recordings?

    PYTHONPATH=. python ml/scripts/gate_g1_labels.py

Rebuilds windows exactly as `build_subject_independent_dataset.py` does, keeps
only the TEST subjects of its seeded split, and labels every window with the
backend's own rule (`FallDetectionService.classify_state`: hard SVM threshold,
then ML thresholds). Prints one row per (source, group, activity).
"""

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import tensorflow as tf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.fall_detection import FallDetectionService  # noqa: E402
from ml.training import build_subject_independent_dataset as split  # noqa: E402
from ml.training import preprocess as sisfall_pp  # noqa: E402
from ml.training import preprocess_vestibular as vest_pp  # noqa: E402

STATES = ("NORMAL", "VESTIBULAR_INSTABILITY", "FALL")


def test_subjects(ids):
    _train, _val, test = split.split_subject_ids(
        list(ids), seed=split.SPLIT_SEED, val_fraction=split.VAL_FRACTION, test_fraction=split.TEST_FRACTION
    )
    return test


def vestibular_files(subject_ids):
    for sid in subject_ids:
        group = vest_pp.get_participant_group(sid)
        for csv_file in sorted((vest_pp.VEST_DIR / sid / ".csv").glob("IMU_*.csv")):
            activity = csv_file.stem.replace("IMU_", "")
            if vest_pp.get_activity_label(group, activity) is None:
                continue
            data = vest_pp.load_vest_file(csv_file)
            if data is None or len(data) < vest_pp.MIN_SOURCE_SAMPLES_FOR_WINDOW:
                continue
            data = vest_pp.add_svm_channel(vest_pp.resample_to_target_rate(data))
            yield ("vestibular", group, activity), vest_pp.create_windows(data, vest_pp.WINDOW_SIZE, vest_pp.WINDOW_STEP)


def sisfall_files(subject_ids):
    for sid in subject_ids:
        for path in sorted((sisfall_pp.DATASET_DIR / sid).glob("*.txt")):
            label = sisfall_pp.get_label_from_filename(path.name)
            if label == -1:
                continue
            data = sisfall_pp.load_sisfall_file(path)
            if data is None or len(data) < sisfall_pp.DOWNSAMPLE_FACTOR * sisfall_pp.WINDOW_SIZE:
                continue
            data = sisfall_pp.downsample(data, sisfall_pp.DOWNSAMPLE_FACTOR)
            kind = "fall trials (F*)" if path.name.startswith("F") else "daily activities (D*)"
            yield ("sisfall", sid[:2], kind), sisfall_pp.create_windows(data, sisfall_pp.WINDOW_SIZE, sisfall_pp.WINDOW_STEP)


def main():
    svc = FallDetectionService()
    assert svc.model_source == "keras", f"expected the Keras model, got {svc.model_source}"
    infer = tf.function(lambda x: svc.keras_model(x, training=False))

    vest_ids = [p.name for p in sorted(vest_pp.VEST_DIR.iterdir()) if (p / ".csv").is_dir()]
    sis_ids = [p.name for p in sorted(sisfall_pp.DATASET_DIR.iterdir()) if p.is_dir()]
    vest_test, sis_test = test_subjects(vest_ids), test_subjects(sis_ids)
    print("vestibular test subjects:", vest_test)
    print("sisfall test subjects:   ", sis_test)

    counts = defaultdict(lambda: np.zeros(3, dtype=int))
    model_counts = defaultdict(lambda: np.zeros(3, dtype=int))
    files_with_fall = defaultdict(lambda: [0, 0])
    for key, windows in [*vestibular_files(vest_test), *sisfall_files(sis_test)]:
        if len(windows) == 0:
            continue
        x = ((windows - svc.scaler_mean) / svc.scaler_std).astype(np.float32)
        probs = infer(tf.constant(x)).numpy()
        states = [
            svc.classify_state(float(p[2]), float(p[1]), float(np.max(w[:, 6])))
            for p, w in zip(probs, windows)
        ]
        for s in states:
            counts[key][STATES.index(s)] += 1
        for k in probs.argmax(axis=1):
            model_counts[key][k] += 1
        files_with_fall[key][0] += 1
        files_with_fall[key][1] += "FALL" in states

    print(f"\n{'source':10s} {'group':5s} {'activity':24s} {'windows':>7s}  {'NORMAL':>7s} {'INSTAB':>7s} {'FALL':>7s}  files with any FALL   model argmax N/I/F")
    for (source, group, activity), c in sorted(counts.items()):
        pct = 100 * c / c.sum()
        n_files, n_fall = files_with_fall[(source, group, activity)]
        print(f"{source:10s} {group:5s} {activity:24s} {c.sum():7d}  {pct[0]:6.1f}% {pct[1]:6.1f}% {pct[2]:6.1f}%  {n_fall}/{n_files:<5d}  " + "/".join(f"{v:.0f}%" for v in 100 * model_counts[(source, group, activity)] / c.sum()))


if __name__ == "__main__":
    main()
