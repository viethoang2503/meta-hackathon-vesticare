"""
evaluate_3class.py

Bang so sanh model B.3 (finalroadmap.md):
  Rule-based (SVM threshold) | Random Forest | 1D-CNN TFLite INT8 | LSTM TFLite

Va B.5 (quantization impact):
  1D-CNN float32 vs INT8

Output:
  ml/evaluation_3class/
    comparison_table.txt   -- bang so sanh (dien vao bai bao)
    confusion_matrices.png -- 4 confusion matrix 3x3
    quantization_table.txt -- B.5 float32 vs INT8
"""

import os
import time
import pickle
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# ===========================================================================
# CAU HINH
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR     = PROJECT_ROOT / "ml" / "data" / "processed_3class"
MODELS_DIR   = PROJECT_ROOT / "ml" / "models_3class"
EVAL_DIR     = PROJECT_ROOT / "ml" / "evaluation_3class"
EVAL_DIR.mkdir(parents=True, exist_ok=True)

CLASS_NAMES = ["NORMAL", "INSTABILITY", "FALL"]

# Rule-based SVM thresholds (g) -- tuong duong backend logic cu
SVM_FALL_THRESHOLD        = 2.5   # peak SVM >= 2.5g -> FALL
SVM_INSTABILITY_THRESHOLD = 1.5   # peak SVM >= 1.5g -> INSTABILITY

# So window dung do inference time (tren subset de nhanh)
N_INFERENCE_BENCH = 500


# ===========================================================================
# LOAD DATA
# ===========================================================================

def load_data():
    X_test = np.load(DATA_DIR / "X_test.npz")["data"]          # raw, (N, 100, 7)
    y_test = np.load(DATA_DIR / "y_test.npz")["data"].astype(int)
    scaler = np.load(MODELS_DIR / "scaler_params.npz")
    X_norm = (X_test - scaler["mean"]) / scaler["std"]          # normalized
    X_feat = np.load(DATA_DIR / "X_test_features.npz")["data"]  # 19 features for RF
    return X_test, X_norm, X_feat, y_test


def extract_features(X):
    """19 features -- phai giong het extract_features trong train_3class.py."""
    feats = []
    for w in X:
        ax, ay, az = w[:, 0], w[:, 1], w[:, 2]
        gx, gy, gz = w[:, 3], w[:, 4], w[:, 5]
        svm      = np.sqrt(ax**2 + ay**2 + az**2)
        gyro_mag = np.sqrt(gx**2 + gy**2 + gz**2)
        jerk     = np.abs(np.diff(svm))
        tilt     = np.arctan2(ay, az)
        sway_freq_power = float(np.mean(np.abs(np.fft.rfft(svm)[1:5]) ** 2))
        feats.append([
            svm.mean(), svm.max(), svm.min(), svm.std(),
            svm.max() - svm.min(),
            ax.mean(), ay.mean(), az.mean(),
            ax.std(),  ay.std(),  az.std(),
            gyro_mag.mean(), gyro_mag.max(), gyro_mag.std(),
            jerk.mean(), jerk.max(),
            tilt.mean(), tilt.std(),
            sway_freq_power,
        ])
    return np.array(feats)


# ===========================================================================
# METRICS HELPER
# ===========================================================================

def per_class_metrics(y_true, y_pred, class_idx=2):
    """Tra ve precision, recall, f1 cho mot class cu the."""
    from sklearn.metrics import precision_recall_fscore_support
    p, r, f, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=[class_idx], average=None, zero_division=0
    )
    return float(p[0]), float(r[0]), float(f[0])


def accuracy(y_true, y_pred):
    return float((y_true == y_pred).mean())


# ===========================================================================
# MODEL 1: RULE-BASED (SVM THRESHOLD)
# ===========================================================================

def evaluate_rule_based(X_raw, y_test):
    """Classify dua tren peak SVM cua moi window."""
    svm      = np.sqrt(X_raw[:, :, 0]**2 + X_raw[:, :, 1]**2 + X_raw[:, :, 2]**2)
    peak_svm = svm.max(axis=1)

    y_pred = np.zeros(len(y_test), dtype=int)  # NORMAL
    y_pred[peak_svm >= SVM_INSTABILITY_THRESHOLD] = 1   # INSTABILITY
    y_pred[peak_svm >= SVM_FALL_THRESHOLD]        = 2   # FALL

    # Inference time: tinh SVM + nguong (per window)
    bench = X_raw[:N_INFERENCE_BENCH]
    t0 = time.perf_counter()
    for w in bench:
        svm_w = np.sqrt(w[:, 0]**2 + w[:, 1]**2 + w[:, 2]**2)
        peak  = svm_w.max()
        _ = 2 if peak >= SVM_FALL_THRESHOLD else (1 if peak >= SVM_INSTABILITY_THRESHOLD else 0)
    inf_ms = (time.perf_counter() - t0) / N_INFERENCE_BENCH * 1000

    acc = accuracy(y_test, y_pred)
    p, r, f = per_class_metrics(y_test, y_pred, class_idx=2)     # FALL metrics
    pi, ri, fi = per_class_metrics(y_test, y_pred, class_idx=1)  # INSTABILITY metrics
    from sklearn.metrics import confusion_matrix
    cm = confusion_matrix(y_test, y_pred, labels=[0, 1, 2])

    return {
        "name":         "Rule-based (SVM)",
        "accuracy":     acc,
        "prec_fall":    p,
        "rec_fall":     r,
        "f1_fall":      f,
        "prec_inst":    pi,
        "rec_inst":     ri,
        "f1_inst":      fi,
        "inf_ms":       inf_ms,
        "file_kb":      None,
        "cm":           cm,
        "y_pred":       y_pred,
    }


# ===========================================================================
# MODEL 2: RANDOM FOREST
# ===========================================================================

def evaluate_random_forest(X_feat, y_test):
    with open(MODELS_DIR / "random_forest_3class.pkl", "rb") as fh:
        rf = pickle.load(fh)

    y_pred = rf.predict(X_feat)

    # Inference time: trich xuat feature + predict, 1 window moi lan
    bench_raw = np.load(DATA_DIR / "X_test.npz")["data"][:N_INFERENCE_BENCH]
    t0 = time.perf_counter()
    for w in bench_raw:
        feat = extract_features(w[np.newaxis])
        rf.predict(feat)
    inf_ms = (time.perf_counter() - t0) / N_INFERENCE_BENCH * 1000

    model_path = MODELS_DIR / "random_forest_3class.pkl"
    acc = accuracy(y_test, y_pred)
    p, r, f = per_class_metrics(y_test, y_pred, class_idx=2)
    pi, ri, fi = per_class_metrics(y_test, y_pred, class_idx=1)
    from sklearn.metrics import confusion_matrix
    cm = confusion_matrix(y_test, y_pred, labels=[0, 1, 2])

    return {
        "name":         "Random Forest",
        "accuracy":     acc,
        "prec_fall":    p,
        "rec_fall":     r,
        "f1_fall":      f,
        "prec_inst":    pi,
        "rec_inst":     ri,
        "f1_inst":      fi,
        "inf_ms":       inf_ms,
        "file_kb":      model_path.stat().st_size / 1024,
        "cm":           cm,
        "y_pred":       y_pred,
    }


# ===========================================================================
# TFLITE HELPER
# ===========================================================================

def run_tflite(tflite_path, X_norm, y_test):
    """Chay TFLite interpreter, tra ve y_pred va inf_ms."""
    import tensorflow as tf

    interp = tf.lite.Interpreter(model_path=str(tflite_path))
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]
    out = interp.get_output_details()[0]

    # Kiem tra input dtype (INT8 vs FLOAT32)
    is_int8 = (inp["dtype"] == np.int8)
    if is_int8:
        scale, zero_point = inp["quantization"]

    def infer_one(x):
        if is_int8:
            x_q = (x / scale + zero_point).astype(np.int8)
            interp.set_tensor(inp["index"], x_q[np.newaxis])
        else:
            interp.set_tensor(inp["index"], x[np.newaxis].astype(np.float32))
        interp.invoke()
        raw = interp.get_tensor(out["index"])
        if raw.dtype == np.int8:
            o = interp.get_output_details()[0]
            s_out, zp_out = o["quantization"]
            raw = (raw.astype(np.float32) - zp_out) * s_out
        return int(np.argmax(raw))

    y_pred = np.array([infer_one(X_norm[i]) for i in range(len(y_test))])

    # Do inference time tren N_INFERENCE_BENCH window
    bench = X_norm[:N_INFERENCE_BENCH]
    t0 = time.perf_counter()
    for x in bench:
        infer_one(x)
    inf_ms = (time.perf_counter() - t0) / N_INFERENCE_BENCH * 1000

    from sklearn.metrics import confusion_matrix
    cm = confusion_matrix(y_test, y_pred, labels=[0, 1, 2])
    acc = accuracy(y_test, y_pred)
    p, r, f = per_class_metrics(y_test, y_pred, class_idx=2)
    pi, ri, fi = per_class_metrics(y_test, y_pred, class_idx=1)

    return {
        "accuracy":  acc,
        "prec_fall": p,
        "rec_fall":  r,
        "f1_fall":   f,
        "prec_inst": pi,
        "rec_inst":  ri,
        "f1_inst":   fi,
        "inf_ms":    inf_ms,
        "file_kb":   tflite_path.stat().st_size / 1024,
        "cm":        cm,
        "y_pred":    y_pred,
    }


def evaluate_cnn_int8(X_norm, y_test):
    result = run_tflite(MODELS_DIR / "fall_detection_3class.tflite", X_norm, y_test)
    result["name"] = "1D-CNN TFLite INT8"
    return result


def evaluate_lstm_keras(X_norm, y_test):
    """
    LSTM TFLite yeu cau Flex delegate (FlexTensorListReserve).
    Dung Keras model de evaluate, lay file size tu .tflite.
    """
    import tensorflow as tf
    from sklearn.metrics import confusion_matrix

    model = tf.keras.models.load_model(MODELS_DIR / "lstm_3class.keras")
    y_prob = model.predict(X_norm, batch_size=256, verbose=0)
    y_pred = np.argmax(y_prob, axis=1)

    # Inference time: single-window predict
    bench = X_norm[:N_INFERENCE_BENCH]
    t0 = time.perf_counter()
    for x in bench:
        model.predict(x[np.newaxis], verbose=0)
    inf_ms = (time.perf_counter() - t0) / N_INFERENCE_BENCH * 1000

    tflite_path = MODELS_DIR / "lstm_3class.tflite"
    keras_path  = MODELS_DIR / "lstm_3class.keras"
    file_kb = tflite_path.stat().st_size / 1024 if tflite_path.exists() else keras_path.stat().st_size / 1024

    cm  = confusion_matrix(y_test, y_pred, labels=[0, 1, 2])
    acc = accuracy(y_test, y_pred)
    p, r, f   = per_class_metrics(y_test, y_pred, class_idx=2)
    pi, ri, fi = per_class_metrics(y_test, y_pred, class_idx=1)

    return {
        "name":      "LSTM (Keras)",
        "accuracy":  acc,
        "prec_fall": p,  "rec_fall": r,  "f1_fall": f,
        "prec_inst": pi, "rec_inst": ri, "f1_inst": fi,
        "inf_ms":    inf_ms,
        "file_kb":   file_kb,
        "cm":        cm,
        "y_pred":    y_pred,
    }


# ===========================================================================
# B.5 QUANTIZATION: FLOAT32 vs INT8
# ===========================================================================

def evaluate_quant_comparison(X_norm, y_test):
    print("\n  Danh gia float32 vs INT8 (B.5)...")
    f32 = run_tflite(MODELS_DIR / "fall_detection_3class_f32.tflite", X_norm, y_test)
    i8  = run_tflite(MODELS_DIR / "fall_detection_3class.tflite",     X_norm, y_test)
    return f32, i8


# ===========================================================================
# VE BIEU DO CONFUSION MATRIX
# ===========================================================================

def plot_confusion_matrices(results):
    n = len(results)
    fig, axes = plt.subplots(1, n, figsize=(5 * n, 4))
    if n == 1:
        axes = [axes]

    for ax, r in zip(axes, results):
        cm_pct = r["cm"].astype(float)
        row_sum = cm_pct.sum(axis=1, keepdims=True)
        row_sum[row_sum == 0] = 1
        cm_pct = cm_pct / row_sum * 100

        sns.heatmap(
            cm_pct, annot=True, fmt=".1f", cmap="Blues",
            xticklabels=CLASS_NAMES, yticklabels=CLASS_NAMES,
            ax=ax, cbar=False,
            annot_kws={"size": 9},
        )
        ax.set_title(
            f"{r['name']}\nAcc={r['accuracy']:.1%}  F1-FALL={r['f1_fall']:.3f}",
            fontsize=10, fontweight="bold",
        )
        ax.set_ylabel("Actual")
        ax.set_xlabel("Predicted")

    plt.tight_layout()
    path = EVAL_DIR / "confusion_matrices.png"
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"  Luu: {path}")


# ===========================================================================
# IN BANG TONG KET
# ===========================================================================

def print_and_save_table(results, quant_f32, quant_i8):
    lines = []

    # --- B.3 Model comparison ---
    hdr = (
        f"\n{'Model':<22} {'Prec-FALL':>10} {'Rec-FALL':>9} "
        f"{'F1-FALL':>8} {'Acc':>7} {'Inf(ms)':>9} {'Size(KB)':>9}"
    )
    sep = "-" * 78
    lines.append("=" * 78)
    lines.append("  B.3 MODEL COMPARISON (3-CLASS: NORMAL / INSTABILITY / FALL)")
    lines.append("=" * 78)
    lines.append(hdr)
    lines.append(sep)

    for r in results:
        size_str = f"{r['file_kb']:.0f}" if r["file_kb"] else "—"
        lines.append(
            f"  {r['name']:<20} {r['prec_fall']:>10.3f} {r['rec_fall']:>9.3f} "
            f"{r['f1_fall']:>8.3f} {r['accuracy']:>7.3f} {r['inf_ms']:>9.3f} {size_str:>9}"
        )

    lines.append(sep)
    lines.append("  (subject-independent split — 3 class vestibular pipeline)")

    # INSTABILITY metrics
    lines.append("\n" + "=" * 78)
    lines.append("  INSTABILITY CLASS METRICS")
    lines.append("=" * 78)
    hdr2 = f"  {'Model':<22} {'Prec-INST':>10} {'Rec-INST':>9} {'F1-INST':>8}"
    lines.append(hdr2)
    lines.append(sep)
    for r in results:
        lines.append(
            f"  {r['name']:<22} {r['prec_inst']:>10.3f} {r['rec_inst']:>9.3f} {r['f1_inst']:>8.3f}"
        )

    # --- B.5 Quantization ---
    lines.append("\n" + "=" * 78)
    lines.append("  B.5 QUANTIZATION: 1D-CNN float32 vs INT8")
    lines.append("=" * 78)
    lines.append(
        f"  {'Format':<20} {'Prec-FALL':>10} {'Rec-FALL':>9} "
        f"{'F1-FALL':>8} {'Acc':>7} {'Inf(ms)':>9} {'Size(KB)':>9}"
    )
    lines.append(sep)
    for label, r in [("float32", quant_f32), ("INT8 TFLite", quant_i8)]:
        lines.append(
            f"  {label:<20} {r['prec_fall']:>10.3f} {r['rec_fall']:>9.3f} "
            f"{r['f1_fall']:>8.3f} {r['accuracy']:>7.3f} {r['inf_ms']:>9.3f} {r['file_kb']:>9.1f}"
        )
    lines.append(sep)

    output = "\n".join(lines)
    print(output)

    path = EVAL_DIR / "comparison_table.txt"
    path.write_text(output, encoding="utf-8")
    print(f"\n  Luu bang: {path}")


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print("=" * 60)
    print("  DANH GIA 3-CLASS MODEL (B.3 + B.5)")
    print("=" * 60)

    print("\n  Dang load du lieu...")
    X_raw, X_norm, X_feat, y_test = load_data()
    print(f"  Test set: {len(y_test)} windows")
    for i, name in enumerate(CLASS_NAMES):
        print(f"    {name}: {(y_test==i).sum()}")

    results = []

    print("\n--- Rule-based ---")
    results.append(evaluate_rule_based(X_raw, y_test))

    print("\n--- Random Forest ---")
    results.append(evaluate_random_forest(X_feat, y_test))

    print("\n--- 1D-CNN TFLite INT8 ---")
    results.append(evaluate_cnn_int8(X_norm, y_test))

    print("\n--- LSTM (Keras) ---")
    results.append(evaluate_lstm_keras(X_norm, y_test))

    print("\n--- Quantization comparison (B.5) ---")
    quant_f32, quant_i8 = evaluate_quant_comparison(X_norm, y_test)

    print("\n--- Ve confusion matrix ---")
    plot_confusion_matrices(results)

    print_and_save_table(results, quant_f32, quant_i8)

    print("\n  Xong. Ket qua luu tai:", EVAL_DIR)


if __name__ == "__main__":
    main()
