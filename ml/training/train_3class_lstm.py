"""
train_3class_lstm.py

Train LSTM 3-class (NORMAL / INSTABILITY / FALL) de lam baseline so sanh voi 1D-CNN.

Kien truc: LSTM(64) -> Dropout(0.3) -> LSTM(32) -> Dense(32, relu) -> Dense(3, softmax)
Loss: focal loss (gamma=2.0), class weight balanced x FALL_WEIGHT_MULTIPLIER

Output:
  ml/models_3class/fall_detection_3class_lstm.keras
  ml/models_3class/fall_detection_3class_lstm.tflite

Chay tu thu muc goc project:
  python ml/training/train_3class_lstm.py
"""

import time
import sys
import numpy as np
from pathlib import Path

# ===========================================================================
# THAM SO
# ===========================================================================
FOCAL_GAMMA            = 2.0
FALL_WEIGHT_MULTIPLIER = 6.0
EPOCHS                 = 60
BATCH_SIZE             = 64
PATIENCE               = 12
LR_INIT                = 1e-3
LR_MIN                 = 1e-6
RECALL_TARGET          = 0.85

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
# FOCAL LOSS (giong train_3class_focal.py)
# ===========================================================================

def focal_loss(gamma=2.0):
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
# LOAD & AUGMENT (tai su dung logic tu train_3class_focal.py)
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


def augment_minority_classes(X, y, seed=42):
    rng = np.random.default_rng(seed)
    normal_count = np.sum(y == 0)
    aug_X, aug_y = [X], [y]
    for cls in [1, 2]:
        mask  = (y == cls)
        X_cls = X[mask]
        count = len(X_cls)
        if count == 0:
            continue
        repeats = max(1, (normal_count // count) - 1)
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
    X_aug = np.concatenate(aug_X)
    y_aug = np.concatenate(aug_y)
    perm  = rng.permutation(len(X_aug))
    return X_aug[perm], y_aug[perm]


def normalize_data(X_train_aug, X_test):
    mean = X_train_aug.mean(axis=(0, 1))
    std  = X_train_aug.std(axis=(0, 1))
    std[std == 0] = 1.0
    return (X_train_aug - mean) / std, (X_test - mean) / std


# ===========================================================================
# KIEN TRUC LSTM
# ===========================================================================

def build_lstm(input_shape=(100, 7), num_classes=3):
    import tensorflow as tf
    from tensorflow.keras import layers, models, regularizers

    inp = layers.Input(shape=input_shape)
    x   = layers.LSTM(64, return_sequences=True,
                      kernel_regularizer=regularizers.l2(1e-4))(inp)
    x   = layers.Dropout(0.3)(x)
    x   = layers.LSTM(32, return_sequences=False,
                      kernel_regularizer=regularizers.l2(1e-4))(x)
    x   = layers.Dropout(0.2)(x)
    x   = layers.Dense(32, activation="relu")(x)
    out = layers.Dense(num_classes, activation="softmax")(x)

    model = models.Model(inputs=inp, outputs=out, name="lstm_3class")
    return model


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    import tensorflow as tf
    from sklearn.utils.class_weight import compute_class_weight

    print("=" * 60)
    print("  TRAIN LSTM 3-CLASS (BASELINE)")
    print(f"  gamma={FOCAL_GAMMA}  FALL_W={FALL_WEIGHT_MULTIPLIER}  epochs={EPOCHS}")
    print("=" * 60)

    X_train, y_train, X_test, y_test = load_data()

    print("\n  Augment minority classes...")
    X_train_aug, y_train_aug = augment_minority_classes(X_train, y_train)

    print("\n  Normalize...")
    X_tr_norm, X_te_norm = normalize_data(X_train_aug, X_test)

    # Class weights
    cw_vals = compute_class_weight("balanced",
                                   classes=np.arange(NUM_CLASSES),
                                   y=y_train_aug)
    cw_vals[2] *= FALL_WEIGHT_MULTIPLIER
    class_weight = {i: float(w) for i, w in enumerate(cw_vals)}
    print(f"\n  Class weights: {class_weight}")

    y_tr_cat = tf.keras.utils.to_categorical(y_train_aug, NUM_CLASSES)
    y_te_cat = tf.keras.utils.to_categorical(y_test,      NUM_CLASSES)

    model = build_lstm()
    model.summary()

    model.compile(
        optimizer=tf.keras.optimizers.Adam(LR_INIT),
        loss=focal_loss(FOCAL_GAMMA),
        metrics=["accuracy"],
    )

    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=PATIENCE,
            restore_best_weights=True, verbose=1,
        ),
        tf.keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss", factor=0.5,
            patience=5, min_lr=LR_MIN, verbose=1,
        ),
    ]

    print("\n  Training LSTM...")
    t_start = time.time()
    history = model.fit(
        X_tr_norm, y_tr_cat,
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        validation_data=(X_te_norm, y_te_cat),
        class_weight=class_weight,
        callbacks=callbacks,
        verbose=1,
    )
    print(f"\n  Training done in {(time.time()-t_start)/60:.1f} minutes")

    # Evaluate
    from sklearn.metrics import classification_report, confusion_matrix

    probs  = model.predict(X_te_norm, batch_size=512, verbose=0)
    y_pred = np.zeros(len(y_test), dtype=np.int32)
    y_pred[probs[:, 1] >= 0.50] = 1
    y_pred[probs[:, 2] >= 0.45] = 2

    cm = confusion_matrix(y_test, y_pred, labels=[0, 1, 2])
    print("\nConfusion Matrix:")
    print(f"  {'':12s}  PRED_NRM  PRED_INST  PRED_FALL")
    for i, name in enumerate(CLASS_NAMES):
        print(f"  TRUE_{name:10s}  {cm[i,0]:8d}  {cm[i,1]:9d}  {cm[i,2]:9d}")

    fall_total = float(cm[2].sum())
    fall_tp    = float(cm[2, 2])
    fall_fp    = float(cm[0, 2] + cm[1, 2])
    fall_recall    = fall_tp / fall_total if fall_total > 0 else 0
    fall_precision = fall_tp / (fall_tp + fall_fp) if (fall_tp + fall_fp) > 0 else 0
    fall_f1        = (2 * fall_precision * fall_recall / (fall_precision + fall_recall)
                      if (fall_precision + fall_recall) > 0 else 0)
    inst_recall    = float(cm[1, 1]) / float(cm[1].sum()) if cm[1].sum() > 0 else 0

    print(f"\n  FALL  recall={fall_recall:.4f}  prec={fall_precision:.4f}  F1={fall_f1:.4f}")
    print(f"  INST  recall={inst_recall:.4f}")

    # Save Keras
    keras_path = MODELS_DIR / "fall_detection_3class_lstm.keras"
    model.save(keras_path)
    print(f"\n  Saved: {keras_path.name}")

    # Convert to TFLite (float32)
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    tflite_model = converter.convert()
    tflite_path = MODELS_DIR / "fall_detection_3class_lstm.tflite"
    tflite_path.write_bytes(tflite_model)
    print(f"  Saved: {tflite_path.name}  ({len(tflite_model)/1024:.1f} KB)")

    status = "PASS" if fall_recall >= RECALL_TARGET else "BELOW_TARGET"
    print(f"\n  [{status}] FALL recall={fall_recall:.4f}  (target={RECALL_TARGET})")
    print("  Run baseline_comparison.py to include LSTM in comparison table.")


if __name__ == "__main__":
    main()
