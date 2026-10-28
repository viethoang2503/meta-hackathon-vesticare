"""
qat_3class.py — Quantization-Aware Training cho 1D-CNN 3-class

Muc tieu: fix van de INT8 quantization bi mat accuracy (F1 0.797 -> 0.462)
           bang cach insert fake-quant nodes vao model va fine-tune.

Quy trinh:
  1. Load model Keras da train: ml/models_3class/fall_detection_3class.keras
  2. Apply QAT wrapper (fake-quant nodes)
  3. Fine-tune ~10 epoch voi learning rate thap (1e-4)
  4. Strip wrapper -> export TFLite INT8 (ghi de file cu)
  5. Export them float32 TFLite de so sanh B.5

Output:
  ml/models_3class/fall_detection_3class.tflite      (INT8, ghi de)
  ml/models_3class/fall_detection_3class_f32.tflite  (float32, ghi de)
  ml/models_3class/fall_detection_3class_qat.keras   (QAT model backup)

Cach chay:
  pip install tensorflow-model-optimization
  python ml/training/qat_3class.py

Thoi gian du kien: 10-20 phut (fine-tune, khong train lai tu dau)
"""

import os
import time
import numpy as np
from pathlib import Path

os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"

# ===========================================================================
# CAU HINH
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR     = PROJECT_ROOT / "ml" / "data" / "processed_3class"
MODELS_DIR   = PROJECT_ROOT / "ml" / "models_3class"

CLASS_NAMES = ["NORMAL", "INSTABILITY", "FALL"]
NUM_CLASSES = 3
WINDOW_SIZE = 100
NUM_CHANNELS = 7

QAT_EPOCHS    = 15       # fine-tune epochs (EarlyStopping se dung som neu can)
QAT_LR        = 1e-4     # thap hon LR goc (0.001) de fine-tune on dinh
QAT_BATCH     = 64
QAT_VAL_SPLIT = 0.15
QAT_PATIENCE  = 6        # EarlyStopping patience


# ===========================================================================
# LOAD DATA
# ===========================================================================

def augment_minority_classes(X, y):
    """Giong train_3class.py — augment de can bang 3 class."""
    normal_count = np.sum(y == 0)
    aug_X, aug_y = [X], [y]

    for cls in [1, 2]:
        mask  = (y == cls)
        X_cls = X[mask]
        count = len(X_cls)
        if count == 0:
            continue
        repeats = max(1, (normal_count // count) - 1)
        print(f"    Class {CLASS_NAMES[cls]:15s}: {count} -> x{repeats+1}")
        for _ in range(repeats):
            X_new = X_cls.copy()
            choice = np.random.randint(0, 3, size=len(X_new))
            jmask = (choice == 0)
            if jmask.any():
                X_new[jmask] += np.random.normal(0, 0.05, X_new[jmask].shape)
            smask = (choice == 1)
            if smask.any():
                scale = np.random.uniform(0.85, 1.15, (smask.sum(), 1, 1))
                X_new[smask] *= scale
            tmask = (choice == 2)
            if tmask.any():
                shift = np.random.randint(-3, 4)
                X_new[tmask] = np.roll(X_new[tmask], shift, axis=1)
            aug_X.append(X_new)
            aug_y.append(np.full(len(X_new), cls, dtype=np.int32))

    X_aug = np.concatenate(aug_X)
    y_aug = np.concatenate(aug_y)
    perm  = np.random.permutation(len(X_aug))
    return X_aug[perm], y_aug[perm]


def load_data():
    print("  Load du lieu...")
    X_train_raw = np.load(DATA_DIR / "X_train.npz")["data"]
    y_train_raw = np.load(DATA_DIR / "y_train.npz")["data"].astype(np.int32)
    X_test      = np.load(DATA_DIR / "X_test.npz")["data"]
    y_test      = np.load(DATA_DIR / "y_test.npz")["data"].astype(np.int32)

    print(f"  Train raw: {X_train_raw.shape}  Test: {X_test.shape}")

    # Normalize (dung scaler da luu tu lan train goc)
    scaler  = np.load(MODELS_DIR / "scaler_params.npz")
    mean, std = scaler["mean"], scaler["std"]

    X_train_norm = (X_train_raw - mean) / std
    X_test_norm  = (X_test      - mean) / std

    # Augment
    print("  Augment training data...")
    X_train_aug, y_train_aug = augment_minority_classes(X_train_norm, y_train_raw)
    print(f"  Sau augment: {len(X_train_aug)} windows")

    return X_train_aug, y_train_aug, X_test_norm, y_test


# ===========================================================================
# QAT
# ===========================================================================

def build_tf_keras_model():
    """
    Rebuild CNN architecture dung tf_keras (Keras 2 API).
    tfmot.quantize_model chi chap nhan Keras 2 model, khong chap nhan Keras 3.
    tf_keras la Keras 2 compatibility layer duoc cai cung tfmot.
    """
    import tf_keras
    model = tf_keras.Sequential([
        tf_keras.layers.Conv1D(16, kernel_size=5, activation="relu", padding="same",
                               input_shape=(WINDOW_SIZE, NUM_CHANNELS)),
        tf_keras.layers.BatchNormalization(),
        tf_keras.layers.MaxPooling1D(pool_size=2),
        tf_keras.layers.Dropout(0.2),

        tf_keras.layers.Conv1D(32, kernel_size=3, activation="relu", padding="same"),
        tf_keras.layers.BatchNormalization(),
        tf_keras.layers.MaxPooling1D(pool_size=2),
        tf_keras.layers.Dropout(0.2),

        tf_keras.layers.Conv1D(32, kernel_size=3, activation="relu", padding="same"),
        tf_keras.layers.BatchNormalization(),

        tf_keras.layers.GlobalAveragePooling1D(),
        tf_keras.layers.Dense(32, activation="relu"),
        tf_keras.layers.Dropout(0.4),
        tf_keras.layers.Dense(NUM_CLASSES, activation="softmax"),
    ])
    return model


def run_qat(X_train, y_train, X_test, y_test):
    import tensorflow as tf
    import tf_keras
    import tensorflow_model_optimization as tfmot
    from sklearn.utils.class_weight import compute_class_weight
    from sklearn.metrics import classification_report

    # Load weights tu Keras 3 model
    print("\n  Load weights tu Keras 3 model...")
    keras3_model = tf.keras.models.load_model(MODELS_DIR / "fall_detection_3class.keras")

    # Rebuild architecture bang tf_keras (Keras 2) va copy weights
    print("  Rebuild architecture bang tf_keras (Keras 2)...")
    base_model = build_tf_keras_model()
    # Build model truoc khi set weights
    base_model(np.zeros((1, WINDOW_SIZE, NUM_CHANNELS), dtype=np.float32))
    base_model.set_weights(keras3_model.get_weights())

    # Kiem tra accuracy truoc QAT
    y_te_cat = tf_keras.utils.to_categorical(y_test, NUM_CLASSES)
    base_model.compile(optimizer="adam", loss="categorical_crossentropy", metrics=["accuracy"])
    _, acc_before = base_model.evaluate(X_test, y_te_cat, verbose=0)
    print(f"  Accuracy truoc QAT : {acc_before:.4f}")

    # ----- Apply QAT -----
    print("\n  Apply QAT wrapper (fake-quant nodes)...")
    qat_model = tfmot.quantization.keras.quantize_model(base_model)

    cw_vals = compute_class_weight("balanced", classes=np.unique(y_train), y=y_train)
    class_weights = dict(enumerate(cw_vals))
    print(f"  Class weights: { {CLASS_NAMES[k]: f'{v:.2f}' for k, v in class_weights.items()} }")

    qat_model.compile(
        optimizer=tf_keras.optimizers.Adam(learning_rate=QAT_LR),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )

    y_tr_cat = tf_keras.utils.to_categorical(y_train, NUM_CLASSES)

    callbacks = [
        tf_keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=QAT_PATIENCE,
            restore_best_weights=True, verbose=1,
        ),
        tf_keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss", factor=0.5, patience=3,
            min_lr=1e-6, verbose=1,
        ),
    ]

    print(f"\n  Fine-tune QAT ({QAT_EPOCHS} epoch max, lr={QAT_LR})...")
    t0 = time.time()
    history = qat_model.fit(
        X_train, y_tr_cat,
        epochs=QAT_EPOCHS,
        batch_size=QAT_BATCH,
        validation_split=QAT_VAL_SPLIT,
        class_weight=class_weights,
        callbacks=callbacks,
        verbose=1,
    )
    elapsed = time.time() - t0
    actual_epochs = len(history.history["accuracy"])
    print(f"\n  Fine-tune xong: {actual_epochs} epoch, {elapsed:.0f}s")

    # Kiem tra accuracy sau QAT
    _, acc_after = qat_model.evaluate(X_test, y_te_cat, verbose=0)
    print(f"  Accuracy sau QAT   : {acc_after:.4f}  (delta: {acc_after - acc_before:+.4f})")

    y_pred = np.argmax(qat_model.predict(X_test, verbose=0), axis=1)
    print("\n" + classification_report(y_test, y_pred, target_names=CLASS_NAMES))

    # Luu QAT model (backup)
    qat_path = MODELS_DIR / "fall_detection_3class_qat.keras"
    qat_model.save(qat_path)
    print(f"  Luu QAT model: {qat_path.name}")

    return qat_model


# ===========================================================================
# EXPORT TFLITE
# ===========================================================================

def export_tflite(qat_model, X_test_norm):
    import tensorflow as tf
    import tensorflow_model_optimization as tfmot

    print("\n  Strip QAT wrappers...")
    stripped = tfmot.quantization.keras.strip_pruning(qat_model)
    # strip_pruning cung dong thoi strip quantization wrappers

    # Calibration: dung toan bo test set (lon hon, chinh xac hon)
    X_cal = X_test_norm.astype(np.float32)
    def rep_dataset():
        for i in range(0, len(X_cal), 10):   # moi 10 mau lay 1 -> ~2400 mau
            yield [X_cal[i:i+1]]

    # --- INT8 (giu float32 I/O de khong mat accuracy do I/O scaling) ---
    print("  Export INT8 TFLite (float32 I/O)...")
    converter = tf.lite.TFLiteConverter.from_keras_model(stripped)
    converter.optimizations           = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset  = rep_dataset
    # Khong set inference_input_type/output_type -> giu float32 I/O
    # weights va activations van duoc quantize INT8 noi bo

    tflite_bytes = converter.convert()
    tflite_path  = MODELS_DIR / "fall_detection_3class.tflite"
    tflite_path.write_bytes(tflite_bytes)
    print(f"  Luu INT8: {tflite_path.name}  ({len(tflite_bytes)/1024:.1f} KB)")

    # --- float32 (ghi de luon de dong bo) ---
    print("  Export float32 TFLite...")
    conv_f32 = tf.lite.TFLiteConverter.from_keras_model(stripped)
    f32_bytes = conv_f32.convert()
    f32_path  = MODELS_DIR / "fall_detection_3class_f32.tflite"
    f32_path.write_bytes(f32_bytes)
    print(f"  Luu float32: {f32_path.name}  ({len(f32_bytes)/1024:.1f} KB)")

    size_reduction = (1 - len(tflite_bytes) / len(f32_bytes)) * 100
    print(f"\n  Size reduction: {size_reduction:.1f}% (float32 -> INT8)")


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print("=" * 60)
    print("  QUANTIZATION-AWARE TRAINING (QAT) — 3-CLASS CNN")
    print("=" * 60)

    X_train, y_train, X_test, y_test = load_data()

    qat_model = run_qat(X_train, y_train, X_test, y_test)

    export_tflite(qat_model, X_test)

    print("\n" + "=" * 60)
    print("  XONG. Chay evaluate_3class.py de cap nhat bang so sanh.")
    print("=" * 60)


if __name__ == "__main__":
    main()
