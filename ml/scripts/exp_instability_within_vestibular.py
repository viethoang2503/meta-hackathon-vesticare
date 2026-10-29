"""Experiment C: can an instability model trained only on the vestibular dataset
tell patients on hard activities apart from healthy people, with the sensor held fixed?

    PYTHONPATH=. python ml/scripts/exp_instability_within_vestibular.py

Two classes from the existing proxy labels (`preprocess_vestibular.get_activity_label`):
NORMAL = every HS activity plus BV/UV easy activities, INSTABILITY = BV/UV hard
activities. 5-fold cross-validation grouped by participant, so every person is
scored by a model that never saw them. Prints the INSTABILITY rate per
(group, activity) pooled over folds, which is the number the gate is judged on.
"""

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import tensorflow as tf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ml.training import preprocess_vestibular as vest_pp  # noqa: E402

FOLDS = 5
SEED = 42
INSTAB = 1


def load():
    """Windows with their participant, group, activity and proxy label."""
    X, y, who, group, activity = [], [], [], [], []
    for p_dir in sorted(vest_pp.VEST_DIR.iterdir()):
        g = vest_pp.get_participant_group(p_dir.name)
        if g is None or not (p_dir / ".csv").is_dir():
            continue
        for csv_file in sorted((p_dir / ".csv").glob("IMU_*.csv")):
            act = csv_file.stem.replace("IMU_", "")
            label = vest_pp.get_activity_label(g, act)
            if label is None:
                continue
            data = vest_pp.load_vest_file(csv_file)
            if data is None or len(data) < vest_pp.MIN_SOURCE_SAMPLES_FOR_WINDOW:
                continue
            data = vest_pp.add_svm_channel(vest_pp.resample_to_target_rate(data))
            w = vest_pp.create_windows(data, vest_pp.WINDOW_SIZE, vest_pp.WINDOW_STEP)
            n = len(w)
            if n == 0:
                continue
            X.append(w)
            y += [label] * n
            who += [p_dir.name] * n
            group += [g] * n
            activity += [act] * n
    return (np.concatenate(X).astype(np.float32), np.array(y), np.array(who), np.array(group), np.array(activity))


def participant_folds(who, group):
    """Assign whole participants to folds, dealing each group round-robin so every fold holds HS, BV and UV."""
    rng = np.random.default_rng(SEED)
    fold_of = {}
    for g in ("HS", "BV", "UV"):
        people = sorted(set(who[group == g]))
        rng.shuffle(people)
        for i, p in enumerate(people):
            fold_of[p] = i % FOLDS
    return np.array([fold_of[p] for p in who])


def build_model():
    return tf.keras.Sequential([
        tf.keras.layers.Input((vest_pp.WINDOW_SIZE, 7)),
        tf.keras.layers.Conv1D(32, 7, padding="same", activation="relu"),
        tf.keras.layers.MaxPooling1D(2),
        tf.keras.layers.Conv1D(64, 5, padding="same", activation="relu"),
        tf.keras.layers.MaxPooling1D(2),
        tf.keras.layers.Conv1D(64, 3, padding="same", activation="relu"),
        tf.keras.layers.GlobalAveragePooling1D(),
        tf.keras.layers.Dropout(0.3),
        tf.keras.layers.Dense(32, activation="relu"),
        tf.keras.layers.Dense(1, activation="sigmoid"),
    ])


def main():
    tf.keras.utils.set_random_seed(SEED)
    X, y, who, group, activity = load()
    fold = participant_folds(who, group)
    print(f"windows={len(y)}  participants={len(set(who))}  instab share={np.mean(y == INSTAB):.2f}")

    pred = np.zeros(len(y), dtype=np.float32)
    for k in range(FOLDS):
        tr, te = fold != k, fold == k
        mean, std = X[tr].reshape(-1, 7).mean(0), X[tr].reshape(-1, 7).std(0) + 1e-6
        model = build_model()
        model.compile(optimizer=tf.keras.optimizers.Adam(1e-3), loss="binary_crossentropy")
        pos = np.mean(y[tr] == INSTAB)
        model.fit(
            (X[tr] - mean) / std, (y[tr] == INSTAB).astype(np.float32),
            epochs=15, batch_size=128, verbose=0,
            class_weight={0: 0.5 / (1 - pos), 1: 0.5 / pos},
        )
        pred[te] = model.predict((X[te] - mean) / std, verbose=0).reshape(-1)
        people = sorted(set(who[te]))
        print(f"fold {k}: test participants {len(people)} ({', '.join(sorted({p[-2:] for p in people}))})")

    flagged = pred >= 0.5
    rows = defaultdict(lambda: [0, 0])
    for g, a, f in zip(group, activity, flagged):
        rows[(g, a)][0] += 1
        rows[(g, a)][1] += int(f)
    print(f"\n{'group':5s} {'activity':22s} {'proxy label':12s} {'windows':>7s} {'flagged INSTAB':>15s}")
    for (g, a), (n, f) in sorted(rows.items()):
        label = "INSTAB" if vest_pp.get_activity_label(g, a) == INSTAB else "NORMAL"
        print(f"{g:5s} {a:22s} {label:12s} {n:7d} {100 * f / n:14.1f}%")

    for name, mask in [
        ("HS, all activities", group == "HS"),
        ("HS Walk + Stairs", (group == "HS") & np.isin(activity, ["Walk", "Stairs"])),
        ("BV/UV hard activities", y == INSTAB),
        ("BV/UV easy activities", (group != "HS") & (y != INSTAB)),
    ]:
        print(f"SUMMARY {name:24s} flagged INSTAB {100 * flagged[mask].mean():5.1f}%  (n={mask.sum()})")


if __name__ == "__main__":
    main()
