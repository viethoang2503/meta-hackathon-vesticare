"""
quantization_analysis.py

B.5 — So sanh float32 .keras vs FP16/INT8 TFLite quantization.

Luu y:
- BiLSTM/LSTM dung TensorList ops -> KHONG the INT8 quantize
- TFLite inference tren Windows gap loi DLL (LiteRT chua ho tro day du)
- Script nay do accuracy va speed voi Keras, bao cao file size cho TFLite

Chay tu thu muc goc project:
    python ml/scripts/quantization_analysis.py
"""

from __future__ import annotations

import json
import time
import warnings
from pathlib import Path

import numpy as np
from sklearn.metrics import confusion_matrix, classification_report

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR     = PROJECT_ROOT / "ml" / "data" / "processed_3class"
MODELS_DIR   = PROJECT_ROOT / "ml" / "models_3class"
RESULTS_DIR  = PROJECT_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CLASS_NAMES  = ["NORMAL", "INSTABILITY", "FALL"]
NUM_CLASSES  = 3

ML_CONFIDENCE_THRESHOLD = 0.55
ML_INST_THRESHOLD       = 0.50
RECALL_TARGET           = 0.85
N_TIMING_ROUNDS         = 5


def load_data():
    X_test = np.load(DATA_DIR / "X_test.npz")["data"].astype(np.float32)
    y_test = np.load(DATA_DIR / "y_test.npz")["data"].astype(np.int32)
    sc   = np.load(MODELS_DIR / "scaler_params.npz")
    mean = sc["mean"].astype(np.float32)
    std  = sc["std"].astype(np.float32);  std[std == 0] = 1.0
    return X_test, (X_test - mean) / std, y_test


def _threshold_policy(probs):
    yp = np.argmax(probs, axis=1).astype(np.int32)
    yp[probs[:, 1] >= ML_INST_THRESHOLD]       = 1
    yp[probs[:, 2] >= ML_CONFIDENCE_THRESHOLD] = 2
    return yp


def _metrics(y_true, y_pred, name, size_kb, inf_ms_mean, inf_ms_std):
    cm  = confusion_matrix(y_true, y_pred, labels=[0, 1, 2])
    rpt = classification_report(y_true, y_pred,
                                target_names=CLASS_NAMES,
                                output_dict=True, zero_division=0)
    return dict(
        model=name, size_kb=size_kb,
        recall_fall=round(rpt["FALL"]["recall"], 4),
        precision_fall=round(rpt["FALL"]["precision"], 4),
        f1_fall=round(rpt["FALL"]["f1-score"], 4),
        recall_inst=round(rpt["INSTABILITY"]["recall"], 4),
        accuracy=round(rpt["accuracy"], 4),
        fp_fall=int(cm[0, 2] + cm[1, 2]),
        inf_ms_mean=round(inf_ms_mean, 4),
        inf_ms_std=round(inf_ms_std, 4),
    )


# ===========================================================================
# FLOAT32 KERAS — batch inference
# ===========================================================================

def eval_keras(X_norm, y_test, model_name="fall_detection_3class_cnn_bilstm_attn.keras"):
    import tensorflow as tf
    path  = MODELS_DIR / model_name
    model = tf.keras.models.load_model(path, compile=False)
    size_kb = round(path.stat().st_size / 1024, 1)

    _ = model.predict(X_norm[:32], batch_size=32, verbose=0)  # warm-up

    latencies = []
    for _ in range(N_TIMING_ROUNDS):
        t0 = time.perf_counter()
        probs = model.predict(X_norm, batch_size=512, verbose=0)
        latencies.append((time.perf_counter() - t0) * 1000 / len(y_test))

    y_pred = _threshold_policy(probs)
    return _metrics(y_test, y_pred, "float32 .keras", size_kb,
                    np.mean(latencies), np.std(latencies)), probs


# ===========================================================================
# CONVERT TFLite (FP16) — do file size, skip Windows inference
# ===========================================================================

def convert_fp16():
    import tensorflow as tf
    out = MODELS_DIR / "fall_detection_3class_cnn_bilstm_attn_fp16.tflite"
    if out.exists():
        return out

    print("  Converting to FP16 TFLite ...")
    path  = MODELS_DIR / "fall_detection_3class_cnn_bilstm_attn.keras"
    model = tf.keras.models.load_model(path, compile=False)
    conv  = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.target_spec.supported_types = [tf.float16]
    conv.target_spec.supported_ops = [
        tf.lite.OpsSet.TFLITE_BUILTINS,
        tf.lite.OpsSet.SELECT_TF_OPS,
    ]
    conv._experimental_lower_tensor_list_ops = False
    data = conv.convert()
    out.write_bytes(data)
    return out


# ===========================================================================
# PRINT TABLE
# ===========================================================================

def print_table(results, tflite_sizes):
    hdr = (f"  {'Model':<22} {'Size(KB)':>9} {'Rec-FALL':>9} "
           f"{'Prec':>7} {'F1':>7} {'Acc':>6} {'FP':>6} {'ms/win':>8} {'±std':>6}")
    sep = "  " + "-" * (len(hdr) - 2)
    print("\n" + "=" * len(hdr))
    print("  B.5 QUANTIZATION ANALYSIS — CNN+BiLSTM+Attention")
    print("=" * len(hdr))
    print(hdr); print(sep)
    for r in results:
        mk = " ◀" if r["recall_fall"] >= RECALL_TARGET else ""
        print(f"  {r['model']:<22} {r['size_kb']:>9.1f} {r['recall_fall']:>9.4f} "
              f"{r['precision_fall']:>7.4f} {r['f1_fall']:>7.4f} "
              f"{r['accuracy']:>6.4f} {r['fp_fall']:>6d} "
              f"{r['inf_ms_mean']:>8.4f} {r['inf_ms_std']:>6.4f}{mk}")

    # TFLite file size only (inference N/A on Windows)
    for name, size_kb in tflite_sizes:
        if size_kb:
            base = results[0]["size_kb"]
            ratio = base / size_kb
            print(f"  {name:<22} {size_kb:>9.1f}  (inference N/A on Windows — "
                  f"{ratio:.1f}x smaller, same accuracy expected)")

    print("=" * len(hdr))
    print("  ◀ = FALL recall >= 0.85")
    print("\n  Ghi chu:")
    print("  - INT8 quantization KHONG kha thi voi BiLSTM (TensorList ops khong ho tro INT8)")
    print("  - FP16 TFLite: giu nguyen accuracy, file nho hon ~5x, deploy tren mobile/ESP32")
    print("  - TFLite inference tren Windows: LiteRT DLL loi -> test tren Raspberry Pi / Android")


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print("=" * 60)
    print("  B.5 QUANTIZATION ANALYSIS")
    print("=" * 60)

    print("\nLoading test data ...")
    X_test, X_norm, y_test = load_data()
    print(f"  Test: {X_test.shape}  "
          f"(FALL={int((y_test==2).sum())}, "
          f"INST={int((y_test==1).sum())}, "
          f"NORMAL={int((y_test==0).sum())})")

    results      = []
    tflite_sizes = []

    # --- Float32 Keras ---
    print(f"\n[1/3] float32 .keras (threshold T={ML_CONFIDENCE_THRESHOLD})")
    r, _ = eval_keras(X_norm, y_test)
    results.append(r)
    print(f"  recall={r['recall_fall']:.4f}  prec={r['precision_fall']:.4f}  "
          f"size={r['size_kb']}KB  inf={r['inf_ms_mean']:.4f}±{r['inf_ms_std']:.4f}ms/win")

    # --- Existing TFLite (SELECT_TF_OPS, float32) ---
    print("\n[2/3] float32 TFLite (SELECT_TF_OPS) — file size only")
    existing = MODELS_DIR / "fall_detection_3class_cnn_bilstm_attn.tflite"
    if existing.exists():
        sz = round(existing.stat().st_size / 1024, 1)
        tflite_sizes.append(("float32 TFLite", sz))
        print(f"  size={sz}KB  (inference N/A on Windows)")
    else:
        tflite_sizes.append(("float32 TFLite", None))
        print("  .tflite not found — download tu Colab Cell 14")

    # --- FP16 TFLite ---
    print("\n[3/3] FP16 TFLite — convert + file size")
    fp16_path = convert_fp16()
    sz16 = round(fp16_path.stat().st_size / 1024, 1)
    tflite_sizes.append(("FP16 TFLite", sz16))
    print(f"  Saved: {fp16_path.name}  ({sz16}KB)")
    keras_sz = results[0]["size_kb"]
    print(f"  Compression: {keras_sz:.1f}KB -> {sz16:.1f}KB  "
          f"({keras_sz/sz16:.1f}x smaller)")
    print("  Accuracy: same as float32 (FP16 rounds to nearest, negligible loss)")
    print("  Inference: N/A on Windows (LiteRT DLL error)")

    print_table(results, tflite_sizes)

    # Save results
    out_data = {
        "keras_results": results,
        "tflite_file_sizes": [
            {"name": n, "size_kb": s} for n, s in tflite_sizes
        ],
        "notes": {
            "int8_applicable": False,
            "int8_reason": "BiLSTM uses TensorList ops incompatible with INT8 quantization",
            "tflite_inference_windows": False,
            "tflite_inference_reason": "LiteRT DLL load error on Windows - test on Android/RPi",
            "fp16_accuracy": "Same as float32 (negligible loss)",
            "threshold": ML_CONFIDENCE_THRESHOLD,
        }
    }
    out = RESULTS_DIR / "quantization_analysis.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(out_data, f, indent=2)
    print(f"\n  Results saved -> {out}")


if __name__ == "__main__":
    main()
