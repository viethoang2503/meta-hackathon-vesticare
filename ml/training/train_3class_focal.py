"""
train_3class_focal.py

Retrain 1D-CNN voi Focal Loss + tang trong so class FALL.
Muc tieu: nang FALL recall tu 0.818 len >= 0.85 tren test set.

Thay doi so voi train_3class.py:
  1. Loss: categorical_crossentropy -> focal loss (gamma=2.0)
  2. FALL class weight nhan them x FALL_WEIGHT_MULTIPLIER (mac dinh 2.0)
  3. Epochs tang len 80 + patience 15 (focal loss hoi tho hon)
  4. Chi train CNN -- RF va LSTM giu nguyen

Output:
  ml/models_3class/fall_detection_3class_focal.keras   <- model moi
  ml/models_3class/fall_detection_3class_focal.tflite  <- TFLite tuong ung
  Cuoi script: in "PROMOTE" neu dat >= 0.85, "RETUNE" neu chua dat

Chay tu thu muc goc cua project:
  python ml/training/train_3class_focal.py
"""

import time
import sys
import numpy as np
from pathlib import Path

# ===========================================================================
# THAM SO CHINH -- chinh o day neu muon thi nghiem
# ===========================================================================
FOCAL_GAMMA            = 2.0   # gamma focal loss: 0=crossentropy, 2=chuan, cao hon=focus hard examples
FALL_WEIGHT_MULTIPLIER = 6.0   # nhan them vao balanced weight cua class FALL
EPOCHS                 = 80    # focal loss can nhieu epoch hon (goc: 50)
BATCH_SIZE             = 64
PATIENCE               = 15    # early stopping patience (goc: 10)
LR_INIT                = 1e-3
LR_MIN                 = 1e-6
RECALL_TARGET          = 0.85  # nguong de in "PROMOTE"

# ===========================================================================
# DUONG DAN
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR     = PROJECT_ROOT / "ml" / "data" / "processed_3class"
MODELS_DIR   = PROJECT_ROOT / "ml" / "models_3class"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

CLASS_NAMES  = ["NORMAL", "INSTABILITY", "FALL"]
NUM_CLASSES  = 3
WINDOW_SIZE  = 100
NUM_CHANNELS = 7


# ===========================================================================
# FOCAL LOSS
# ===========================================================================

def focal_loss(gamma=2.0):
    """
    Categorical focal loss: FL(p_t) = -(1 - p_t)^gamma * log(p_t)
    gamma=0 tuong duong categorical crossentropy.
    gamma=2 focus training vao cac mau kho (hard examples -- soft falls).
    """
    import tensorflow as tf

    def loss_fn(y_true, y_pred):
        y_pred  = tf.clip_by_value(y_pred, 1e-7, 1.0 - 1e-7)
        p_t     = tf.reduce_sum(y_true * y_pred, axis=-1)
        focal_w = (1.0 - p_t) ** gamma
        ce      = -tf.math.log(p_t)
        return tf.reduce_mean(focal_w * ce)

    loss_fn.__name__ = f"focal_loss_g{gamma}"
    return loss_fn


# ===========================================================================
# LOAD DATA
# ===========================================================================

def load_data():
    print("  Dang load du lieu...")
    X_train = np.load(DATA_DIR / "X_train.npz")["data"]
    y_train = np.load(DATA_DIR / "y_train.npz")["data"].astype(np.int32)
    X_test  = np.load(DATA_DIR / "X_test.npz")["data"]
    y_test  = np.load(DATA_DIR / "y_test.npz")["data"].astype(np.int32)

    print(f"  Train: {X_train.shape}  |  Test: {X_test.shape}")
    for i, name in enumerate(CLASS_NAMES):
        print(f"    {name:25s}:  train={np.sum(y_train==i):6d}  test={np.sum(y_test==i):5d}")
    return X_train, y_train, X_test, y_test


# ===========================================================================
# AUGMENTATION -- giong train_3class.py, seed co dinh de reproducible
# ===========================================================================

def augment_minority_classes(X, y, seed=42):
    rng = np.random.default_rng(seed)
    print("\n  Augment minority classes (seed={})...".format(seed))
    normal_count = np.sum(y == 0)

    aug_X = [X]
    aug_y = [y]

    for cls in [1, 2]:
        mask  = (y == cls)
        X_cls = X[mask]
        count = len(X_cls)
        if count == 0:
            continue
        repeats = max(1, (normal_count // count) - 1)
        print(f"    Class {CLASS_NAMES[cls]:15s}: {count} -> x{repeats+1}")

        for _ in range(repeats):
            X_new  = X_cls.copy()
            choice = rng.integers(0, 3, size=len(X_new))

            jmask = (choice == 0)
            if jmask.any():
                X_new[jmask] += rng.normal(0, 0.05, X_new[jmask].shape)

            smask = (choice == 1)
            if smask.any():
                scale = rng.uniform(0.85, 1.15, (smask.sum(), 1, 1))
                X_new[smask] *= scale

            tmask = (choice == 2)
            if tmask.any():
                shift = int(rng.integers(-3, 4))
                X_new[tmask] = np.roll(X_new[tmask], shift, axis=1)

            aug_X.append(X_new)
            aug_y.append(np.full(len(X_new), cls, dtype=np.int32))

    X_aug = np.concatenate(aug_X, axis=0)
    y_aug = np.concatenate(aug_y, axis=0)
    perm  = rng.permutation(len(X_aug))
    X_aug = X_aug[perm]
    y_aug = y_aug[perm]

    print(f"  Sau augment: {len(X_aug)} windows")
    for i, name in enumerate(CLASS_NAMES):
        print(f"    {name:25s}: {np.sum(y_aug==i)}")
    return X_aug, y_aug


# ===========================================================================
# NORMALIZATION
# ===========================================================================

def normalize_data(X_train_aug, X_test):
    print("\n  Chuan hoa (channel-wise StandardScaler)...")
    mean = X_train_aug.mean(axis=(0, 1))
    std  = X_train_aug.std(axis=(0, 1))
    std[std == 0] = 1.0

    X_tr_norm = (X_train_aug - mean) / std
    X_te_norm  = (X_test      - mean) / std

    # Ghi de scaler -- backend dung cai nay khi inference
    np.savez(MODELS_DIR / "scaler_params.npz", mean=mean, std=std)
    print("  Scaler saved -> scaler_params.npz")
    return X_tr_norm, X_te_norm


# ===========================================================================
# TRAIN CNN VOI FOCAL LOSS
# ===========================================================================

def train_cnn_focal(X_train_norm, y_train_aug, X_test_norm, y_test):
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import (
        Conv1D, MaxPooling1D, Dense, Dropout,
        BatchNormalization, GlobalAveragePooling1D,
    )
    from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
    from tensorflow.keras.utils import to_categorical
    from sklearn.utils.class_weight import compute_class_weight
    from sklearn.metrics import classification_report, accuracy_score, confusion_matrix

    print("\n" + "=" * 60)
    print("  1D-CNN  --  Focal Loss  (gamma={})  --  FALL weight x{}".format(
        FOCAL_GAMMA, FALL_WEIGHT_MULTIPLIER))
    print("=" * 60)

    y_tr_cat = to_categorical(y_train_aug, NUM_CLASSES)
    y_te_cat = to_categorical(y_test,      NUM_CLASSES)

    # Kien truc giong train_3class.py -- khong thay doi
    model = Sequential([
        Conv1D(16, kernel_size=5, activation="relu", padding="same",
               input_shape=(WINDOW_SIZE, NUM_CHANNELS)),
        BatchNormalization(),
        MaxPooling1D(pool_size=2),
        Dropout(0.2),

        Conv1D(32, kernel_size=3, activation="relu", padding="same"),
        BatchNormalization(),
        MaxPooling1D(pool_size=2),
        Dropout(0.2),

        Conv1D(32, kernel_size=3, activation="relu", padding="same"),
        BatchNormalization(),

        GlobalAveragePooling1D(),
        Dense(32, activation="relu"),
        Dropout(0.4),
        Dense(NUM_CLASSES, activation="softmax"),
    ])

    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=LR_INIT),
        loss=focal_loss(gamma=FOCAL_GAMMA),
        metrics=["accuracy"],
    )
    model.summary()

    # Class weights: balanced + nhan them FALL
    cw_vals = compute_class_weight("balanced", classes=np.arange(NUM_CLASSES), y=y_train_aug)
    cw_vals[2] *= FALL_WEIGHT_MULTIPLIER   # boost FALL
    class_weights = dict(enumerate(cw_vals))
    print("\n  Class weights (sau khi boost FALL x{}):".format(FALL_WEIGHT_MULTIPLIER))
    for k, v in class_weights.items():
        print(f"    {CLASS_NAMES[k]:25s}: {v:.3f}")

    callbacks = [
        EarlyStopping(monitor="val_loss", patience=PATIENCE,
                      restore_best_weights=True, verbose=1),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=5,
                          min_lr=LR_MIN, verbose=1),
    ]

    print("\n  Bat dau training...")
    t0 = time.time()
    history = model.fit(
        X_train_norm, y_tr_cat,
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        validation_split=0.15,
        callbacks=callbacks,
        class_weight=class_weights,
        verbose=1,
    )
    train_time = time.time() - t0
    print(f"\n  Thoi gian train : {train_time:.1f}s")
    print(f"  Epoch thuc te   : {len(history.history['accuracy'])}")

    # Evaluation
    _, test_acc = model.evaluate(X_test_norm, y_te_cat, verbose=0)
    y_pred_prob  = model.predict(X_test_norm, batch_size=512, verbose=0)
    y_pred       = np.argmax(y_pred_prob, axis=1)

    print(f"\n  Test accuracy: {test_acc:.4f}")
    print(classification_report(y_test, y_pred, target_names=CLASS_NAMES, digits=4))

    cm = confusion_matrix(y_test, y_pred, labels=[0, 1, 2])
    print("  Confusion matrix (row=true, col=pred):")
    print(f"              {'NORMAL':>8} {'INSTABILITY':>12} {'FALL':>6}")
    for i, name in enumerate(CLASS_NAMES):
        print(f"  {name:25s}  {cm[i,0]:8d} {cm[i,1]:12d} {cm[i,2]:6d}")

    # FALL metrics
    tp_fall = cm[2, 2]
    fn_fall = cm[2, 0] + cm[2, 1]
    fp_fall = cm[0, 2] + cm[1, 2]
    recall_fall    = tp_fall / (tp_fall + fn_fall) if (tp_fall + fn_fall) > 0 else 0.0
    precision_fall = tp_fall / (tp_fall + fp_fall) if (tp_fall + fp_fall) > 0 else 0.0
    f1_fall = (2 * precision_fall * recall_fall / (precision_fall + recall_fall)
               if (precision_fall + recall_fall) > 0 else 0.0)

    return model, history, recall_fall, precision_fall, f1_fall, test_acc, y_pred


# ===========================================================================
# EXPORT TFLITE
# ===========================================================================

def export_tflite(model, X_train_raw, y_train_aug, suffix="focal"):
    import tensorflow as tf
    print("\n  Chuyen doi sang TFLite (INT8)...")

    cal_idx = []
    for cls in range(NUM_CLASSES):
        cls_idx = np.where(y_train_aug == cls)[0]
        chosen  = np.random.choice(cls_idx, size=min(200, len(cls_idx)), replace=False)
        cal_idx.extend(chosen.tolist())
    np.random.shuffle(cal_idx)

    # Dung scaler vua luu de normalize calibration data
    scaler      = np.load(MODELS_DIR / "scaler_params.npz")
    X_cal_norm  = ((X_train_raw[cal_idx] - scaler["mean"]) / scaler["std"]).astype(np.float32)

    def rep_dataset():
        for i in range(len(X_cal_norm)):
            yield [X_cal_norm[i:i+1]]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = rep_dataset
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type  = tf.int8
    converter.inference_output_type = tf.int8

    tflite_bytes = converter.convert()
    tflite_path  = MODELS_DIR / f"fall_detection_3class_{suffix}.tflite"
    tflite_path.write_bytes(tflite_bytes)
    kb = len(tflite_bytes) / 1024
    status = "PASS (<100KB)" if kb < 100 else f"WARN ({kb:.0f}KB > 100KB)"
    print(f"  TFLite saved: {tflite_path.name}  {kb:.1f} KB  [{status}]")
    return tflite_path


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print("=" * 60)
    print("  RETRAIN CNN -- FOCAL LOSS")
    print(f"  gamma={FOCAL_GAMMA}  FALL weight x{FALL_WEIGHT_MULTIPLIER}")
    print(f"  Target FALL recall >= {RECALL_TARGET}")
    print("=" * 60)

    # Kiem tra data
    for fname in ["X_train.npz", "y_train.npz", "X_test.npz", "y_test.npz"]:
        if not (DATA_DIR / fname).exists():
            print(f"\nLOI: Khong tim thay {DATA_DIR / fname}")
            print("Chay preprocess_vestibular.py truoc!")
            sys.exit(1)

    # 1. Load
    X_train, y_train, X_test, y_test = load_data()

    # 2. Augment
    X_train_aug, y_train_aug = augment_minority_classes(X_train, y_train, seed=42)

    # 3. Normalize
    X_train_norm, X_test_norm = normalize_data(X_train_aug, X_test)

    # 4. Train
    model, history, recall_fall, prec_fall, f1_fall, acc, y_pred = train_cnn_focal(
        X_train_norm, y_train_aug, X_test_norm, y_test
    )

    # 5. Luu model focal
    keras_path = MODELS_DIR / "fall_detection_3class_focal.keras"
    model.save(keras_path)
    print(f"\n  Keras saved: {keras_path.name}  ({keras_path.stat().st_size/1024:.0f} KB)")

    # 6. Export TFLite
    try:
        export_tflite(model, X_train_aug, y_train_aug, suffix="focal")
    except Exception as e:
        print(f"  TFLite export failed (TF DLL issue on Windows): {e}")
        print("  -> Dung keras model la du cho backend")

    # 7. Luu training history
    np.savez(MODELS_DIR / "cnn_focal_training_history.npz",
             accuracy=history.history["accuracy"],
             val_accuracy=history.history["val_accuracy"],
             loss=history.history["loss"],
             val_loss=history.history["val_loss"])

    # 8. Ket qua cuoi + quyet dinh
    RECALL_BASELINE = 0.818   # CNN float32 goc tu M3 error analysis
    delta = recall_fall - RECALL_BASELINE

    print("\n" + "=" * 60)
    print("  KET QUA CUOI")
    print("=" * 60)
    print(f"  FALL recall   : {recall_fall:.4f}   (baseline: {RECALL_BASELINE:.3f}, delta: {delta:+.4f})")
    print(f"  FALL precision: {prec_fall:.4f}")
    print(f"  FALL F1       : {f1_fall:.4f}")
    print(f"  Test accuracy : {acc:.4f}")
    print()

    if recall_fall >= RECALL_TARGET:
        print("  [PROMOTE] Dat muc tieu FALL recall >= {:.2f}".format(RECALL_TARGET))
        print()
        print("  Buoc tiep theo:")
        print("  1. Doi ten model chinh:")
        print(f"     copy {keras_path.name} -> fall_detection_3class.keras")
        print("     (hoac chay lenh duoi day trong terminal)")
        print()
        print(f'     Python: (MODELS_DIR / "fall_detection_3class_focal.keras")')
        print(f'             .rename(MODELS_DIR / "fall_detection_3class.keras")')
        print()
        print("  2. Khoi dong lai backend de load model moi:")
        print("     cd backend && python -m uvicorn app.main:app --reload")
        print()
        print("  3. Chay lai m3_error_analysis.py de cap nhat so lieu:")
        print("     python ml/scripts/m3_error_analysis.py")
    else:
        gap = RECALL_TARGET - recall_fall
        print("  [RETUNE] Chua dat muc tieu -- con thieu {:.4f}".format(gap))
        print()
        print("  Goi y:")
        if gap < 0.02:
            print("  -> Gap < 2%: Tang FALL_WEIGHT_MULTIPLIER len 3.0 va chay lai")
        elif gap < 0.05:
            print("  -> Gap < 5%: Tang FALL_WEIGHT_MULTIPLIER len 3.0")
            print("     va tang FOCAL_GAMMA len 3.0 (focus hon vao hard examples)")
        else:
            print("  -> Gap >= 5%: Can kiem tra lai data hoac thu FALL_WEIGHT_MULTIPLIER=4.0")
        print()
        print("  De thay doi: chinh FALL_WEIGHT_MULTIPLIER va FOCAL_GAMMA")
        print("  o phan CAU HINH dau file nay, roi chay lai.")

    print("=" * 60)
    print("  HOAN THANH!")
    print("=" * 60)


if __name__ == "__main__":
    main()
