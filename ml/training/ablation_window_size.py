"""
ablation_window_size.py  —  C.4 Ablation Study: Window Size

So sanh hieu nang CNN+BiLSTM+Attention voi 3 window size:
  50  samples = 1 giay  (phat hien nhanh, it context)
  100 samples = 2 giay  <- Production hien tai
  150 samples = 3 giay  (nhieu context, phat hien cham hon)

Cach tao input cho moi window size:
  w=50 : cat 50 sample dau tu moi window 100-sample hien co
  w=100: du lieu hien co (X_train.npz / X_test.npz)
  w=150: them 50 zero-pad cuoi moi window (ghi ro trong paper)

Chay tren Google Colab:
  1. Upload X_train.npz, y_train.npz, X_test.npz, y_test.npz len Drive
  2. Mount Drive va chinh DATA_DIR bên dưới
  3. Run All

Output:
  results/ablation_window_size.json
  results/ablation_window_size.csv
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

# ===========================================================================
# CONFIG
# ===========================================================================
DATA_DIR    = Path("ml/data/processed_3class")   # chinh neu chay tren Colab
RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

WINDOW_SIZES   = [50, 100, 150]
CLASS_NAMES    = ["NORMAL", "INSTABILITY", "FALL"]
NUM_CHANNELS   = 7
EPOCHS         = 60
BATCH_SIZE     = 64
PATIENCE       = 12
FOCAL_GAMMA    = 2.0
FALL_WEIGHT_MULTIPLIER = 6.0
FALL_THRESHOLD = 0.55   # production threshold


# ===========================================================================
# FOCAL LOSS
# ===========================================================================
def focal_loss(gamma=2.0):
    import tensorflow as tf
    def loss_fn(y_true, y_pred):
        y_pred = tf.clip_by_value(y_pred, 1e-7, 1.0 - 1e-7)
        p_t    = tf.reduce_sum(y_true * y_pred, axis=-1)
        return tf.reduce_mean(((1.0 - p_t) ** gamma) * (-tf.math.log(p_t)))
    loss_fn.__name__ = f"focal_loss_g{gamma}"
    return loss_fn


# ===========================================================================
# BUILD CBA MODEL
# ===========================================================================
def build_cba_model(window_size: int, num_channels: int = NUM_CHANNELS):
    """CNN + BiLSTM + Attention — 3-class classifier."""
    import tensorflow as tf
    from tensorflow.keras import layers, Model

    inp = layers.Input(shape=(window_size, num_channels))

    # CNN block
    x = layers.Conv1D(64, 5, padding="same", activation="relu")(inp)
    x = layers.BatchNormalization()(x)
    x = layers.Conv1D(128, 3, padding="same", activation="relu")(x)
    x = layers.BatchNormalization()(x)
    x = layers.Dropout(0.3)(x)

    # BiLSTM
    x = layers.Bidirectional(layers.LSTM(64, return_sequences=True))(x)
    x = layers.Dropout(0.3)(x)

    # Attention
    attn = layers.Dense(1, activation="tanh")(x)
    attn = layers.Flatten()(attn)
    attn = layers.Activation("softmax")(attn)
    attn = layers.RepeatVector(128)(attn)
    attn = layers.Permute([2, 1])(attn)
    x = layers.Multiply()([x, attn])
    x = layers.GlobalAveragePooling1D()(x)

    # Head
    x   = layers.Dense(64, activation="relu")(x)
    x   = layers.Dropout(0.2)(x)
    out = layers.Dense(3, activation="softmax")(x)

    return Model(inp, out)


# ===========================================================================
# RESIZE WINDOWS
# ===========================================================================
def resize_windows(X: np.ndarray, target_size: int) -> np.ndarray:
    """
    Chinh lai window size:
      target < current : cat bo cuoi (crop)
      target > current : them zero-pad cuoi
      target == current: giu nguyen
    """
    current = X.shape[1]
    if target_size == current:
        return X
    elif target_size < current:
        return X[:, :target_size, :]
    else:
        pad_len = target_size - current
        pad = np.zeros((X.shape[0], pad_len, X.shape[2]), dtype=X.dtype)
        return np.concatenate([X, pad], axis=1)


# ===========================================================================
# EVALUATE
# ===========================================================================
def evaluate_model(model, X_test, y_test_onehot, threshold=FALL_THRESHOLD):
    """Tinh metrics per-class va tong the."""
    from sklearn.metrics import classification_report, confusion_matrix

    y_true = np.argmax(y_test_onehot, axis=1)
    probs  = model.predict(X_test, verbose=0)

    # Apply FALL threshold (override argmax cho class FALL)
    y_pred = np.argmax(probs, axis=1)
    fall_mask = probs[:, 2] >= threshold
    y_pred[fall_mask] = 2

    report = classification_report(y_true, y_pred,
                                   target_names=CLASS_NAMES,
                                   output_dict=True)
    cm     = confusion_matrix(y_true, y_pred).tolist()

    return {
        "recall_fall":       round(report["FALL"]["recall"], 4),
        "precision_fall":    round(report["FALL"]["precision"], 4),
        "f1_fall":           round(report["FALL"]["f1-score"], 4),
        "recall_instab":     round(report["INSTABILITY"]["recall"], 4),
        "recall_normal":     round(report["NORMAL"]["recall"], 4),
        "macro_f1":          round(report["macro avg"]["f1-score"], 4),
        "accuracy":          round(report["accuracy"], 4),
        "confusion_matrix":  cm,
    }


def measure_inference_time(model, window_size: int, n_runs: int = 200) -> float:
    """Inference time (ms) trung binh tren 1 window."""
    dummy = np.random.randn(1, window_size, NUM_CHANNELS).astype(np.float32)
    # Warmup
    for _ in range(10):
        model.predict(dummy, verbose=0)
    # Measure
    t0 = time.perf_counter()
    for _ in range(n_runs):
        model.predict(dummy, verbose=0)
    return round((time.perf_counter() - t0) / n_runs * 1000, 3)


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    import tensorflow as tf
    from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau

    # Load dữ liệu gốc (window_size=100)
    print("Loading data...")

    def load_npz(path):
        d = np.load(path)
        return d[list(d.files)[0]]  # lấy key đầu tiên bất kể tên gì

    def to_onehot(y, n=3):
        return np.eye(n, dtype=np.float32)[y.astype(int)]

    X_train    = load_npz(DATA_DIR / "X_train.npz")
    y_raw      = load_npz(DATA_DIR / "y_train.npz")
    X_test     = load_npz(DATA_DIR / "X_test.npz")
    y_test_raw = load_npz(DATA_DIR / "y_test.npz")

    y_train = to_onehot(y_raw)      if y_raw.ndim == 1      else y_raw
    y_test  = to_onehot(y_test_raw) if y_test_raw.ndim == 1 else y_test_raw

    print(f"  X_train: {X_train.shape}  y_train: {y_train.shape}")
    print(f"  X_test:  {X_test.shape}   y_test:  {y_test.shape}")

    # Class weights cho focal loss
    counts      = np.bincount(np.argmax(y_train, axis=1))
    total       = len(y_train)
    base_weight = {i: total / (3 * c) for i, c in enumerate(counts)}
    base_weight[2] *= FALL_WEIGHT_MULTIPLIER  # boost FALL
    print(f"  Class weights: {base_weight}")

    results = []

    for ws in WINDOW_SIZES:
        print(f"\n{'='*55}")
        print(f"  Window size = {ws} samples ({ws/50:.1f}s)  {'← PRODUCTION' if ws==100 else ''}")
        print(f"{'='*55}")

        # Resize
        Xtr = resize_windows(X_train, ws)
        Xte = resize_windows(X_test,  ws)
        note = ("crop to first 50 samples" if ws < 100
                else "original"            if ws == 100
                else "zero-padded to 150 samples")
        print(f"  Data shape: {Xtr.shape}  ({note})")

        # Build + train
        tf.keras.backend.clear_session()
        model = build_cba_model(window_size=ws)
        model.compile(
            optimizer=tf.keras.optimizers.Adam(1e-3),
            loss=focal_loss(FOCAL_GAMMA),
            metrics=["accuracy"],
        )
        print(f"  Parameters: {model.count_params():,}")

        callbacks = [
            EarlyStopping(monitor="val_loss", patience=PATIENCE, restore_best_weights=True),
            ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=5, min_lr=1e-6),
        ]

        t_start = time.time()
        history = model.fit(
            Xtr, y_train,
            validation_split=0.1,
            epochs=EPOCHS,
            batch_size=BATCH_SIZE,
            class_weight=base_weight,
            callbacks=callbacks,
            verbose=1,
        )
        train_time = round(time.time() - t_start, 1)
        epochs_run = len(history.history["loss"])

        # Evaluate
        metrics = evaluate_model(model, Xte, y_test)
        infer_ms = measure_inference_time(model, ws)

        row = {
            "window_size":    ws,
            "duration_s":     ws / 50,
            "note":           note,
            "recall_fall":    metrics["recall_fall"],
            "precision_fall": metrics["precision_fall"],
            "f1_fall":        metrics["f1_fall"],
            "recall_instab":  metrics["recall_instab"],
            "recall_normal":  metrics["recall_normal"],
            "macro_f1":       metrics["macro_f1"],
            "accuracy":       metrics["accuracy"],
            "inference_ms":   infer_ms,
            "train_time_s":   train_time,
            "epochs_run":     epochs_run,
            "confusion_matrix": metrics["confusion_matrix"],
        }
        results.append(row)

        print(f"\n  FALL  recall={metrics['recall_fall']:.3f}  "
              f"prec={metrics['precision_fall']:.3f}  F1={metrics['f1_fall']:.3f}")
        print(f"  INSTAB recall={metrics['recall_instab']:.3f}")
        print(f"  Macro-F1={metrics['macro_f1']:.3f}  Accuracy={metrics['accuracy']:.3f}")
        print(f"  Inference: {infer_ms:.2f} ms/window")

        # Save checkpoint per window size
        model.save(RESULTS_DIR / f"ablation_cba_w{ws}.keras")
        print(f"  Model saved → results/ablation_cba_w{ws}.keras")

    # ── Summary table ──────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  C.4 ABLATION STUDY RESULTS")
    print(f"{'='*70}")
    header = f"{'Window':>8} {'Dur':>5} {'R-FALL':>7} {'P-FALL':>7} {'F1-FALL':>8} {'R-INST':>7} {'MacF1':>7} {'Infer':>8}"
    print(header)
    print("-" * 70)
    for r in results:
        flag = " ← prod" if r["window_size"] == 100 else ""
        print(f"  {r['window_size']:>4}s  {r['duration_s']:>4.1f}s  "
              f"{r['recall_fall']:>6.3f}  {r['precision_fall']:>6.3f}  "
              f"{r['f1_fall']:>7.3f}  {r['recall_instab']:>6.3f}  "
              f"{r['macro_f1']:>6.3f}  {r['inference_ms']:>6.2f}ms{flag}")

    # ── Save JSON ──────────────────────────────────────────────────────────────
    out_json = RESULTS_DIR / "ablation_window_size.json"
    with open(out_json, "w") as f:
        json.dump({"window_sizes": WINDOW_SIZES, "results": results}, f, indent=2)
    print(f"\n✓ JSON saved → {out_json}")

    # ── Save CSV ───────────────────────────────────────────────────────────────
    try:
        import csv
        out_csv = RESULTS_DIR / "ablation_window_size.csv"
        keys = ["window_size", "duration_s", "recall_fall", "precision_fall",
                "f1_fall", "recall_instab", "recall_normal", "macro_f1",
                "accuracy", "inference_ms", "train_time_s", "epochs_run", "note"]
        with open(out_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(results)
        print(f"✓ CSV saved → {out_csv}")
    except Exception as e:
        print(f"CSV save failed: {e}")


if __name__ == "__main__":
    main()
