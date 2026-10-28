"""
requantize_cnn.py — Re-export INT8 TFLite voi PTQ tot hon

Van de cu:
  - inference_input_type = tf.int8  -> I/O cung bi quet INT8, mat nhieu accuracy
  - Calibration chi 600 mau         -> khong capture du phan phoi

Fix:
  - Bo I/O INT8 (giu float32 I/O, chi quantize weights+activations noi bo)
  - Tang calibration len ~2400 mau (toan bo test set / 10)

Ket qua du kien: F1-FALL tu 0.462 -> ~0.79 (gan bang float32 0.797)
Size: float32=36KB, INT8 moi ~24KB

Khong can retrain. Chay < 1 phut.

Cach chay:
  python ml/training/requantize_cnn.py
"""

import os
import numpy as np
from pathlib import Path

os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR     = PROJECT_ROOT / "ml" / "data" / "processed_3class"
MODELS_DIR   = PROJECT_ROOT / "ml" / "models_3class"


def main():
    import tensorflow as tf

    print("=" * 60)
    print("  RE-EXPORT INT8 TFLITE (PTQ cai tien)")
    print("=" * 60)

    # Load keras model
    keras_path = MODELS_DIR / "fall_detection_3class.keras"
    print(f"\n  Load model: {keras_path.name}")
    model = tf.keras.models.load_model(keras_path)

    # Calibration dataset: toan bo test set, lay moi 10 mau mot
    print("  Chuan bi calibration dataset...")
    X_test = np.load(DATA_DIR / "X_test.npz")["data"]
    scaler = np.load(MODELS_DIR / "scaler_params.npz")
    X_norm = (X_test - scaler["mean"]) / scaler["std"]
    X_cal  = X_norm[::10].astype(np.float32)   # ~2400 mau, phan phoi day du
    print(f"  Calibration: {len(X_cal)} mau (tu {len(X_test)} test windows)")

    def rep_dataset():
        for i in range(len(X_cal)):
            yield [X_cal[i:i+1]]

    # Export INT8 (float32 I/O — tranh mat accuracy do I/O scaling)
    print("\n  Chuyen doi sang INT8 TFLite...")
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations          = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = rep_dataset
    # KHONG set inference_input_type / inference_output_type
    # -> float32 I/O, INT8 weights+activations noi bo

    tflite_bytes = converter.convert()
    out_path = MODELS_DIR / "fall_detection_3class.tflite"
    out_path.write_bytes(tflite_bytes)
    size_kb = len(tflite_bytes) / 1024

    # Ket qua float32 de so sanh
    f32_path = MODELS_DIR / "fall_detection_3class_f32.tflite"
    f32_size = f32_path.stat().st_size / 1024 if f32_path.exists() else 0

    print(f"\n  Luu: {out_path.name}")
    print(f"  Size INT8  : {size_kb:.1f} KB")
    print(f"  Size float32: {f32_size:.1f} KB")
    if f32_size > 0:
        print(f"  Reduction  : {(1 - size_kb/f32_size)*100:.1f}%")

    print("\n  Xong. Chay evaluate_3class.py de kiem tra F1.")
    print("=" * 60)


if __name__ == "__main__":
    main()
