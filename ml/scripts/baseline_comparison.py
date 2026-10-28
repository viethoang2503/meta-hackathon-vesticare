"""
baseline_comparison.py

So sanh 4 model tren cung tap test set (subject-independent split).

Models:
  1. Rule-based   -- SVM threshold: peak>=2.5G -> FALL, mean>=1.5G -> INSTABILITY
  2. Random Forest -- sklearn RF tren handcrafted features
  3. LSTM          -- load tu models_3class/fall_detection_3class_lstm.keras (neu co)
  4. 1D-CNN Focal  -- model hien tai dang dung production

Output: bang so sanh 4x6 (model x metric) va luu results/baseline_comparison.json

Chay tu thu muc goc project:
  python ml/scripts/baseline_comparison.py
"""

from __future__ import annotations

import json
import time
import warnings
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.preprocessing import LabelEncoder

warnings.filterwarnings("ignore")

# ===========================================================================
# DUONG DAN
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR     = PROJECT_ROOT / "ml" / "data" / "processed_3class"
MODELS_DIR   = PROJECT_ROOT / "ml" / "models_3class"
RESULTS_DIR  = PROJECT_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CLASS_NAMES = ["NORMAL", "INSTABILITY", "FALL"]

# Thresholds khop voi backend/app/config.py
SVM_FALL_THRESHOLD       = 2.5   # G
SVM_SWAY_THRESHOLD       = 1.5   # G
ML_CONFIDENCE_THRESHOLD  = 0.45
ML_INST_THRESHOLD        = 0.50


# ===========================================================================
# LOAD DATA
# ===========================================================================

def load_data():
    print("Loading test/train data...")
    X_train = np.load(DATA_DIR / "X_train.npz")["data"].astype(np.float32)
    y_train = np.load(DATA_DIR / "y_train.npz")["data"].astype(np.int32)
    X_test  = np.load(DATA_DIR / "X_test.npz")["data"].astype(np.float32)
    y_test  = np.load(DATA_DIR / "y_test.npz")["data"].astype(np.int32)

    scaler = np.load(MODELS_DIR / "scaler_params.npz")
    mean, std = scaler["mean"].astype(np.float32), scaler["std"].astype(np.float32)
    std[std == 0] = 1.0

    X_train_norm = (X_train - mean) / std
    X_test_norm  = (X_test  - mean) / std

    print(f"  Train: {X_train.shape}  Test: {X_test.shape}")
    for i, n in enumerate(CLASS_NAMES):
        print(f"    {n:25s}: train={int((y_train==i).sum()):6d}  test={int((y_test==i).sum()):5d}")

    return X_train, X_train_norm, y_train, X_test, X_test_norm, y_test


# ===========================================================================
# FEATURE EXTRACTION FOR RANDOM FOREST
# ===========================================================================

def extract_features(X: np.ndarray) -> np.ndarray:
    """
    Extract handcrafted features from windows of shape (N, 100, 7).
    Returns array of shape (N, n_features).

    Channels: ax, ay, az, gx, gy, gz, svm
    """
    N = X.shape[0]
    feats = []

    for i in range(N):
        w = X[i]   # (100, 7)
        f = []

        # Per-channel stats
        for ch in range(7):
            col = w[:, ch]
            f += [
                col.mean(),
                col.std(),
                col.max(),
                col.min(),
                np.percentile(col, 95),
                np.abs(np.diff(col)).mean(),   # mean absolute derivative (jerk proxy)
            ]

        # Derived features
        accel = w[:, :3]
        svm   = np.sqrt((accel ** 2).sum(axis=1))
        f.append(svm.max())                    # peak SVM
        f.append(svm.mean())                   # mean SVM
        f.append(svm.std())                    # SVM variability

        gyro_mag = np.sqrt((w[:, 3:6] ** 2).sum(axis=1))
        f.append(gyro_mag.max())
        f.append(gyro_mag.mean())

        # Sway frequency proxy: std of SVM in 0.5-2Hz range (low-freq energy)
        f.append(np.std(svm[:25]))             # first 0.5s SVM std
        f.append(np.std(svm[75:]))             # last 0.5s SVM std

        feats.append(f)

    return np.array(feats, dtype=np.float32)


# ===========================================================================
# MODEL 1 — RULE-BASED (SVM THRESHOLD)
# ===========================================================================

def eval_rule_based(X_test: np.ndarray, y_test: np.ndarray) -> dict:
    """Apply SVM-only threshold, same logic as original backend heuristic."""
    print("\n[1/4] Rule-based (SVM threshold)...")
    t0 = time.perf_counter()

    peak_svm = X_test[:, :, 6].max(axis=1)   # channel 6 = svm
    mean_svm = X_test[:, :, 6].mean(axis=1)

    y_pred = np.zeros(len(y_test), dtype=np.int32)
    y_pred[mean_svm >= SVM_SWAY_THRESHOLD] = 1   # INSTABILITY
    y_pred[peak_svm >= SVM_FALL_THRESHOLD] = 2   # FALL (overrides INSTABILITY)

    inf_ms = (time.perf_counter() - t0) * 1000 / len(y_test)
    return _metrics(y_test, y_pred, "Rule-based SVM", inf_ms)


# ===========================================================================
# MODEL 2 — RANDOM FOREST
# ===========================================================================

def eval_random_forest(X_train: np.ndarray, y_train: np.ndarray,
                       X_test: np.ndarray,  y_test: np.ndarray) -> dict:
    print("\n[2/4] Random Forest (training on handcrafted features)...")

    print("  Extracting features (train)...")
    F_train = extract_features(X_train)
    print("  Extracting features (test)...")
    F_test  = extract_features(X_test)

    print(f"  Feature dim: {F_train.shape[1]}")
    rf = RandomForestClassifier(
        n_estimators=200,
        max_depth=None,
        class_weight="balanced",
        n_jobs=-1,
        random_state=42,
    )
    t_fit = time.perf_counter()
    rf.fit(F_train, y_train)
    print(f"  Train time: {time.perf_counter() - t_fit:.1f}s")

    t0 = time.perf_counter()
    y_pred = rf.predict(F_test)
    inf_ms = (time.perf_counter() - t0) * 1000 / len(y_test)
    return _metrics(y_test, y_pred, "Random Forest", inf_ms)


# ===========================================================================
# MODEL 3 — LSTM (load pre-trained if exists)
# ===========================================================================

def eval_lstm(X_test_norm: np.ndarray, y_test: np.ndarray) -> dict | None:
    lstm_path = MODELS_DIR / "fall_detection_3class_lstm.keras"
    if not lstm_path.exists():
        print(f"\n[3/4] LSTM — model not found at {lstm_path.name}")
        print("  Run: python ml/training/train_3class_lstm.py  then re-run this script.")
        return None

    print(f"\n[3/4] LSTM (loading {lstm_path.name})...")
    import tensorflow as tf
    model = tf.keras.models.load_model(lstm_path, compile=False)

    t0 = time.perf_counter()
    probs = model.predict(X_test_norm, batch_size=512, verbose=0)
    inf_ms = (time.perf_counter() - t0) * 1000 / len(y_test)

    y_pred = _apply_threshold_policy(probs, X_test_norm)
    return _metrics(y_test, y_pred, "LSTM", inf_ms)


# ===========================================================================
# MODEL 4 — BiLSTM
# ===========================================================================

def eval_bilstm(X_test_norm: np.ndarray, y_test: np.ndarray) -> dict | None:
    path = MODELS_DIR / "fall_detection_3class_bilstm.keras"
    if not path.exists():
        print(f"\n[4/6] BiLSTM — model not found at {path.name}")
        print("  Run: train_models_colab.ipynb (Cell 8) then copy .keras here.")
        return None

    print(f"\n[4/6] BiLSTM (loading {path.name})...")
    import tensorflow as tf
    model = tf.keras.models.load_model(path, compile=False)

    t0 = time.perf_counter()
    probs = model.predict(X_test_norm, batch_size=512, verbose=0)
    inf_ms = (time.perf_counter() - t0) * 1000 / len(y_test)

    y_pred = _apply_threshold_policy(probs, X_test_norm)
    return _metrics(y_test, y_pred, "BiLSTM", inf_ms)


# ===========================================================================
# MODEL 5 — CNN + BiLSTM + Attention (proposed)
# ===========================================================================

def eval_cnn_bilstm_attn(X_test_norm: np.ndarray, y_test: np.ndarray) -> dict | None:
    path = MODELS_DIR / "fall_detection_3class_cnn_bilstm_attn.keras"
    if not path.exists():
        print(f"\n[5/6] CNN+BiLSTM+Attn — model not found at {path.name}")
        print("  Run: train_models_colab.ipynb (Cell 9) then copy .keras here.")
        return None

    print(f"\n[5/6] CNN+BiLSTM+Attention (loading {path.name})...")
    import tensorflow as tf
    model = tf.keras.models.load_model(path, compile=False)

    t0 = time.perf_counter()
    probs = model.predict(X_test_norm, batch_size=512, verbose=0)
    inf_ms = (time.perf_counter() - t0) * 1000 / len(y_test)

    y_pred = _apply_threshold_policy(probs, X_test_norm)
    return _metrics(y_test, y_pred, "CNN+BiLSTM+Attn", inf_ms)


# ===========================================================================
# MODEL 6 — 1D-CNN FOCAL (production model)
# ===========================================================================

def eval_cnn_focal(X_test_norm: np.ndarray, y_test: np.ndarray) -> dict:
    print("\n[6/6] 1D-CNN Focal (production model)...")
    import tensorflow as tf

    model_path = MODELS_DIR / "fall_detection_3class.keras"
    print(f"  Loading {model_path.name}...")
    model = tf.keras.models.load_model(model_path, compile=False)

    t0 = time.perf_counter()
    probs = model.predict(X_test_norm, batch_size=512, verbose=0)
    inf_ms = (time.perf_counter() - t0) * 1000 / len(y_test)

    # Apply same threshold policy as backend
    X_raw = np.load(DATA_DIR / "X_test.npz")["data"]
    peak_svm = X_raw[:, :, 6].max(axis=1)

    y_pred = np.argmax(probs, axis=1).astype(np.int32)
    y_pred[probs[:, 1] >= ML_INST_THRESHOLD] = 1
    y_pred[probs[:, 2] >= ML_CONFIDENCE_THRESHOLD] = 2
    y_pred[peak_svm >= SVM_FALL_THRESHOLD] = 2   # SVM hard fallback

    return _metrics(y_test, y_pred, "1D-CNN Focal", inf_ms)


# ===========================================================================
# HELPERS
# ===========================================================================

def _apply_threshold_policy(probs: np.ndarray, X_norm: np.ndarray) -> np.ndarray:
    y_pred = np.argmax(probs, axis=1).astype(np.int32)
    y_pred[probs[:, 1] >= ML_INST_THRESHOLD] = 1
    y_pred[probs[:, 2] >= ML_CONFIDENCE_THRESHOLD] = 2
    return y_pred


def _metrics(y_true: np.ndarray, y_pred: np.ndarray,
             name: str, inf_ms_per_window: float) -> dict:
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1, 2])
    report = classification_report(y_true, y_pred,
                                   target_names=CLASS_NAMES,
                                   output_dict=True, zero_division=0)

    fall_r   = report["FALL"]["recall"]
    fall_p   = report["FALL"]["precision"]
    fall_f1  = report["FALL"]["f1-score"]
    inst_r   = report["INSTABILITY"]["recall"]
    norm_r   = report["NORMAL"]["recall"]
    accuracy = report["accuracy"]

    # FP count for FALL (from NORMAL + INSTABILITY rows)
    fp_fall  = int(cm[0, 2] + cm[1, 2])

    return {
        "model":         name,
        "recall_fall":   round(fall_r,  4),
        "precision_fall":round(fall_p,  4),
        "f1_fall":       round(fall_f1, 4),
        "recall_inst":   round(inst_r,  4),
        "recall_normal": round(norm_r,  4),
        "accuracy":      round(accuracy,4),
        "fp_fall":       fp_fall,
        "inf_ms":        round(inf_ms_per_window, 4),
        "confusion_matrix": cm.tolist(),
    }


def _print_table(results: list[dict]) -> None:
    header = (
        f"{'Model':22s}  {'Rec-FALL':>9}  {'Prec-FALL':>10}  {'F1-FALL':>8}  "
        f"{'Rec-INST':>9}  {'Rec-NRM':>8}  {'Acc':>6}  {'FP-FALL':>8}  {'ms/win':>7}"
    )
    sep = "-" * len(header)
    print("\n" + "=" * len(header))
    print("  BASELINE COMPARISON — 3-class NORMAL / INSTABILITY / FALL")
    print("=" * len(header))
    print(header)
    print(sep)
    for r in results:
        mark = " ◀" if r["recall_fall"] >= 0.85 else ""
        print(
            f"  {r['model']:20s}  {r['recall_fall']:>9.4f}  {r['precision_fall']:>10.4f}  "
            f"{r['f1_fall']:>8.4f}  {r['recall_inst']:>9.4f}  {r['recall_normal']:>8.4f}  "
            f"{r['accuracy']:>6.4f}  {r['fp_fall']:>8d}  {r['inf_ms']:>7.4f}{mark}"
        )
    print("=" * len(header))
    print("  mark ◀ = FALL recall >= 0.85 target")


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print("=" * 65)
    print("  B.3 BASELINE COMPARISON")
    print("=" * 65)

    X_train, X_train_norm, y_train, X_test, X_test_norm, y_test = load_data()

    results = []

    results.append(eval_rule_based(X_test, y_test))
    results.append(eval_random_forest(X_train, y_train, X_test, y_test))

    for fn in [eval_lstm, eval_bilstm, eval_cnn_bilstm_attn]:
        r = fn(X_test_norm, y_test)
        if r:
            results.append(r)

    results.append(eval_cnn_focal(X_test_norm, y_test))

    _print_table(results)

    # Save JSON
    out = RESULTS_DIR / "baseline_comparison.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results saved -> {out}")


if __name__ == "__main__":
    main()
