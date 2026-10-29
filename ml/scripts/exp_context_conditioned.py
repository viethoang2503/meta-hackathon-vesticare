"""Experiment D: given the activity (what a camera or authored zone would supply),
can IMU tell a vestibular patient's movement apart from a healthy person's?

    PYTHONPATH=. python ml/scripts/exp_context_conditioned.py

Label is cohort, not activity: BV/UV = 1, HS = 0, for every activity both groups
performed. The activity is a model input, so the only thing left to learn is
how movement differs within the same activity. 5-fold cross-validation grouped
by participant. Reports per-activity AUC on held-out people (0.5 = no
separation, 1.0 = perfect) and the healthy/patient flag rates at 0.5.
"""

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import tensorflow as tf
from scipy.stats import rankdata

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ml.scripts.exp_instability_within_vestibular import FOLDS, SEED, load, participant_folds  # noqa: E402
from ml.training import preprocess_vestibular as vest_pp  # noqa: E402


def auc(scores, labels):
    """Probability a random patient window outscores a random healthy one; ties count half (Mann-Whitney)."""
    ranks = rankdata(scores, method="average")
    pos = labels == 1
    n_pos, n_neg = pos.sum(), (~pos).sum()
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return (ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def build_model(n_activities):
    imu = tf.keras.layers.Input((vest_pp.WINDOW_SIZE, 7))
    act = tf.keras.layers.Input((), dtype="int32")
    x = tf.keras.layers.Conv1D(32, 7, padding="same", activation="relu")(imu)
    x = tf.keras.layers.MaxPooling1D(2)(x)
    x = tf.keras.layers.Conv1D(64, 5, padding="same", activation="relu")(x)
    x = tf.keras.layers.MaxPooling1D(2)(x)
    x = tf.keras.layers.Conv1D(64, 3, padding="same", activation="relu")(x)
    x = tf.keras.layers.GlobalAveragePooling1D()(x)
    a = tf.keras.layers.Embedding(n_activities, 8)(act)
    h = tf.keras.layers.Concatenate()([x, a])
    h = tf.keras.layers.Dropout(0.3)(h)
    h = tf.keras.layers.Dense(32, activation="relu")(h)
    out = tf.keras.layers.Dense(1, activation="sigmoid")(h)
    return tf.keras.Model([imu, act], out)


def main():
    tf.keras.utils.set_random_seed(SEED)
    X, _proxy, who, group, activity = load()
    patient = (group != "HS").astype(np.float32)
    names = sorted(set(activity))
    act_id = np.array([names.index(a) for a in activity], dtype=np.int32)
    fold = participant_folds(who, group)

    score = np.zeros(len(patient), dtype=np.float32)
    for k in range(FOLDS):
        tr, te = fold != k, fold == k
        mean, std = X[tr].reshape(-1, 7).mean(0), X[tr].reshape(-1, 7).std(0) + 1e-6
        model = build_model(len(names))
        model.compile(optimizer=tf.keras.optimizers.Adam(1e-3), loss="binary_crossentropy")
        pos = patient[tr].mean()
        model.fit(
            [(X[tr] - mean) / std, act_id[tr]], patient[tr],
            epochs=15, batch_size=128, verbose=0,
            class_weight={0: 0.5 / (1 - pos), 1: 0.5 / pos},
        )
        score[te] = model.predict([(X[te] - mean) / std, act_id[te]], verbose=0).reshape(-1)
        print(f"fold {k} done")

    flagged = score >= 0.5
    print(f"\n{'activity':22s} {'AUC':>5s}  {'HS flagged':>10s} {'BV flagged':>10s} {'UV flagged':>10s}")
    for a in names:
        m = activity == a
        rates = [100 * flagged[m & (group == g)].mean() for g in ("HS", "BV", "UV")]
        print(f"{a:22s} {auc(score[m], patient[m]):5.2f}  {rates[0]:9.1f}% {rates[1]:9.1f}% {rates[2]:9.1f}%")
    print(f"\nOVERALL AUC {auc(score, patient):.2f}")

    per_person = defaultdict(list)
    for p, s in zip(who, score):
        per_person[p].append(s)
    people = sorted(per_person)
    p_score = np.array([np.mean(per_person[p]) for p in people])
    p_label = np.array([0.0 if p.endswith("_HS") else 1.0 for p in people])
    print(f"PERSON-LEVEL AUC (mean score over all their windows) {auc(p_score, p_label):.2f}  (n={len(people)})")


if __name__ == "__main__":
    main()
