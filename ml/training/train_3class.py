"""
train_3class.py

Huan luyen 3 model phat hien 3 class:
  NORMAL (0)                  -- hoat dong binh thuong
  VESTIBULAR_INSTABILITY (1)  -- mat on dinh tien dinh
  FALL (2)                    -- te nga

Du lieu: ml/data/processed_3class/  (output cua preprocess_vestibular.py)
Output : ml/models_3class/

3 model duoc train:
  1. Random Forest            -- baseline nhanh, interpretable
  2. 1D-CNN                   -- model chinh, export sang TFLite INT8
  3. LSTM                     -- baseline deep learning de so sanh

Ket qua duoc in ra cuoi cung de dien vao bang so sanh trong bai bao.
"""

import os
import sys
import numpy as np
import pickle
import time
from pathlib import Path

# ===========================================================================
# CAU HINH
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
# LOAD DATA
# ===========================================================================

def load_data():
    print("  Dang load du lieu...")
    X_train = np.load(DATA_DIR / "X_train.npz")["data"]
    y_train = np.load(DATA_DIR / "y_train.npz")["data"].astype(np.int32)
    X_test  = np.load(DATA_DIR / "X_test.npz")["data"]
    y_test  = np.load(DATA_DIR / "y_test.npz")["data"].astype(np.int32)

    print(f"  Train: {X_train.shape}  Test: {X_test.shape}")
    for i, name in enumerate(CLASS_NAMES):
        print(f"  Train {name:25s}: {np.sum(y_train==i):6d}  |  "
              f"Test: {np.sum(y_test==i):5d}")
    return X_train, y_train, X_test, y_test


# ===========================================================================
# NORMALIZATION
# ===========================================================================

def normalize_data(X_train, X_test):
    """
    StandardScaler tren tung kenh (channel-wise).
    Luu scaler_params.npz de backend dung khi inference.
    """
    print("\n  Chuan hoa du lieu (channel-wise StandardScaler)...")
    mean = X_train.mean(axis=(0, 1))   # shape (7,)
    std  = X_train.std(axis=(0, 1))    # shape (7,)
    std[std == 0] = 1.0

    X_train_norm = (X_train - mean) / std
    X_test_norm  = (X_test  - mean) / std

    np.savez(MODELS_DIR / "scaler_params.npz", mean=mean, std=std)
    print(f"  Scaler luu: {MODELS_DIR / 'scaler_params.npz'}")
    return X_train_norm, X_test_norm, mean, std


# ===========================================================================
# DATA AUGMENTATION
# ===========================================================================

def augment_minority_classes(X, y):
    """
    Augment class INSTABILITY (1) va FALL (2) de can bang voi NORMAL (0).
    Ky thuat: jitter, scaling, time shift (chon ngau nhien cho moi window).
    """
    print("\n  Augment minority classes...")
    normal_count = np.sum(y == 0)

    aug_X = [X]
    aug_y = [y]

    for cls in [1, 2]:
        mask  = (y == cls)
        X_cls = X[mask]
        count = len(X_cls)
        if count == 0:
            continue

        # So lan augment de tiep can so luong NORMAL
        repeats = max(1, (normal_count // count) - 1)
        print(f"  Class {CLASS_NAMES[cls]:15s}: {count} -> x{repeats+1} augment")

        for _ in range(repeats):
            X_new = X_cls.copy()
            choice = np.random.randint(0, 3, size=len(X_new))

            # Jitter
            jmask = (choice == 0)
            if jmask.any():
                X_new[jmask] += np.random.normal(0, 0.05, X_new[jmask].shape)

            # Scale
            smask = (choice == 1)
            if smask.any():
                scale = np.random.uniform(0.85, 1.15, (smask.sum(), 1, 1))
                X_new[smask] *= scale

            # Time shift
            tmask = (choice == 2)
            if tmask.any():
                shift = np.random.randint(-3, 4)
                X_new[tmask] = np.roll(X_new[tmask], shift, axis=1)

            aug_X.append(X_new)
            aug_y.append(np.full(len(X_new), cls, dtype=np.int32))

    X_aug = np.concatenate(aug_X, axis=0)
    y_aug = np.concatenate(aug_y, axis=0)

    perm  = np.random.permutation(len(X_aug))
    X_aug = X_aug[perm]
    y_aug = y_aug[perm]

    print(f"  Sau augment: {len(X_aug)} windows")
    for i, name in enumerate(CLASS_NAMES):
        print(f"    {name:25s}: {np.sum(y_aug==i)}")
    return X_aug, y_aug


# ===========================================================================
# MODEL 1: RANDOM FOREST
# ===========================================================================

def extract_features(X):
    """19 features tu moi window (100 x 7).

    Feature 19: sway_freq_power -- mean power trong bang 0.5-2 Hz cua SVM.
    Tinh bang FFT (rfft) tren 100 mau @ 50 Hz:
      do phan giai tan so = 50/100 = 0.5 Hz -> bin 1..4 tuong ung 0.5..2.0 Hz
    Day la dac trung dac trung cua dao dong tien dinh.
    """
    feats = []
    for w in X:
        ax, ay, az = w[:, 0], w[:, 1], w[:, 2]
        gx, gy, gz = w[:, 3], w[:, 4], w[:, 5]

        svm      = np.sqrt(ax**2 + ay**2 + az**2)
        gyro_mag = np.sqrt(gx**2 + gy**2 + gz**2)
        jerk     = np.abs(np.diff(svm))
        tilt     = np.arctan2(ay, az)
        # Sway frequency: mean power in 0.5-2 Hz band (bins 1-4 at 50Hz/100samples)
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


def train_random_forest(X_train, y_train, X_test, y_test):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import classification_report, accuracy_score

    print("\n" + "-" * 60)
    print("  MODEL 1: RANDOM FOREST")
    print("-" * 60)

    print("  Trich xuat features...")
    t0 = time.time()
    X_tr_f = extract_features(X_train)
    X_te_f = extract_features(X_test)
    print(f"  Feature shape: {X_tr_f.shape}  ({time.time()-t0:.1f}s)")

    print("  Training...")
    t0 = time.time()
    rf = RandomForestClassifier(
        n_estimators=200,
        max_depth=20,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    rf.fit(X_tr_f, y_train)
    train_time = time.time() - t0
    print(f"  Thoi gian train: {train_time:.1f}s")

    y_pred = rf.predict(X_te_f)
    acc    = accuracy_score(y_test, y_pred)
    print(f"\n  Accuracy: {acc:.4f}")
    print(classification_report(y_test, y_pred, target_names=CLASS_NAMES))

    path = MODELS_DIR / "random_forest_3class.pkl"
    with open(path, "wb") as f:
        pickle.dump(rf, f)
    print(f"  Luu: {path.name} ({path.stat().st_size/1024:.0f} KB)")

    # Luu features test de dung lai
    np.savez_compressed(DATA_DIR / "X_test_features.npz", data=X_te_f)
    return rf, acc, y_pred


# ===========================================================================
# MODEL 2: 1D-CNN (3 CLASS)
# ===========================================================================

def train_1d_cnn(X_train, y_train, X_test, y_test):
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import (
        Conv1D, MaxPooling1D, Dense, Dropout,
        BatchNormalization, GlobalAveragePooling1D,
    )
    from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
    from tensorflow.keras.utils import to_categorical
    from sklearn.utils.class_weight import compute_class_weight
    from sklearn.metrics import classification_report, accuracy_score

    print("\n" + "-" * 60)
    print("  MODEL 2: 1D-CNN (3 class)")
    print("-" * 60)

    y_tr_cat = to_categorical(y_train, NUM_CLASSES)
    y_te_cat = to_categorical(y_test,  NUM_CLASSES)

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
        Dense(NUM_CLASSES, activation="softmax"),   # <-- 3 class
    ])

    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )
    model.summary()

    cw_vals = compute_class_weight("balanced", classes=np.unique(y_train), y=y_train)
    class_weights = dict(enumerate(cw_vals))
    print(f"\n  Class weights: { {CLASS_NAMES[k]: f'{v:.2f}' for k, v in class_weights.items()} }")

    callbacks = [
        EarlyStopping(monitor="val_loss", patience=10, restore_best_weights=True),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=4,
                          min_lr=1e-6, verbose=1),
    ]

    print("\n  Bat dau training...")
    t0 = time.time()
    history = model.fit(
        X_train, y_tr_cat,
        epochs=50,
        batch_size=64,
        validation_split=0.15,
        callbacks=callbacks,
        class_weight=class_weights,
        verbose=1,
    )
    train_time = time.time() - t0
    print(f"\n  Thoi gian training : {train_time:.1f}s")
    print(f"  So epoch thuc te   : {len(history.history['accuracy'])}")

    _, test_acc = model.evaluate(X_test, y_te_cat, verbose=0)
    print(f"  Test accuracy      : {test_acc:.4f}")

    y_pred_prob = model.predict(X_test, verbose=0)
    y_pred      = np.argmax(y_pred_prob, axis=1)
    print(classification_report(y_test, y_pred, target_names=CLASS_NAMES))

    # Luu Keras
    keras_path = MODELS_DIR / "fall_detection_3class.keras"
    model.save(keras_path)
    print(f"  Luu Keras : {keras_path.name} ({keras_path.stat().st_size/1024:.0f} KB)")

    # Export TFLite INT8
    print("\n  Chuyen doi sang TFLite (INT8)...")
    # Lay 200 samples moi class de calibration dataset phan phoi deu 3 class.
    # Dung X_train goc (truoc augment) de tranh bias ve class nhieu hon.
    cal_idx = []
    for cls in range(NUM_CLASSES):
        cls_idx = np.where(y_train == cls)[0]
        chosen  = np.random.choice(cls_idx, size=min(200, len(cls_idx)), replace=False)
        cal_idx.extend(chosen.tolist())
    np.random.shuffle(cal_idx)
    X_ref = X_train[cal_idx].astype(np.float32)
    print(f"  Calibration dataset: {len(X_ref)} samples "
          f"({sum(y_train[cal_idx]==0)} NORMAL, "
          f"{sum(y_train[cal_idx]==1)} INST, "
          f"{sum(y_train[cal_idx]==2)} FALL)")

    def rep_dataset():
        for i in range(len(X_ref)):
            yield [X_ref[i:i+1]]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = rep_dataset
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type  = tf.int8
    converter.inference_output_type = tf.int8

    tflite_bytes = converter.convert()
    tflite_path  = MODELS_DIR / "fall_detection_3class.tflite"
    tflite_path.write_bytes(tflite_bytes)
    tflite_kb = len(tflite_bytes) / 1024
    print(f"  Luu TFLite: {tflite_path.name} ({tflite_kb:.1f} KB)")
    if tflite_kb < 100:
        print("  PASS: < 100 KB, phu hop ESP32")
    else:
        print(f"  WARN: {tflite_kb:.1f} KB > 100 KB")

    np.savez(MODELS_DIR / "cnn_training_history.npz",
             accuracy=history.history["accuracy"],
             val_accuracy=history.history["val_accuracy"],
             loss=history.history["loss"],
             val_loss=history.history["val_loss"])

    return model, test_acc, y_pred


# ===========================================================================
# MODEL 3: LSTM (BASELINE)
# ===========================================================================

def train_lstm(X_train, y_train, X_test, y_test):
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import LSTM, Dense, Dropout, BatchNormalization
    from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
    from tensorflow.keras.utils import to_categorical
    from sklearn.utils.class_weight import compute_class_weight
    from sklearn.metrics import classification_report, accuracy_score

    print("\n" + "-" * 60)
    print("  MODEL 3: LSTM (baseline)")
    print("-" * 60)

    y_tr_cat = to_categorical(y_train, NUM_CLASSES)
    y_te_cat = to_categorical(y_test,  NUM_CLASSES)

    # LSTM nho gon: 1 layer LSTM + Dense, du de so sanh voi CNN
    model = Sequential([
        LSTM(32, input_shape=(WINDOW_SIZE, NUM_CHANNELS), return_sequences=False),
        BatchNormalization(),
        Dropout(0.3),
        Dense(32, activation="relu"),
        Dropout(0.3),
        Dense(NUM_CLASSES, activation="softmax"),
    ])

    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )
    model.summary()

    cw_vals = compute_class_weight("balanced", classes=np.unique(y_train), y=y_train)
    class_weights = dict(enumerate(cw_vals))
    print(f"\n  Class weights: { {CLASS_NAMES[k]: f'{v:.2f}' for k, v in class_weights.items()} }")

    callbacks = [
        EarlyStopping(monitor="val_loss", patience=10, restore_best_weights=True),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=4,
                          min_lr=1e-6, verbose=1),
    ]

    print("\n  Bat dau training...")
    t0 = time.time()
    history = model.fit(
        X_train, y_tr_cat,
        epochs=50,
        batch_size=64,
        validation_split=0.15,
        callbacks=callbacks,
        class_weight=class_weights,
        verbose=1,
    )
    train_time = time.time() - t0
    print(f"\n  Thoi gian training : {train_time:.1f}s")
    print(f"  So epoch thuc te   : {len(history.history['accuracy'])}")

    _, test_acc = model.evaluate(X_test, y_te_cat, verbose=0)
    print(f"  Test accuracy      : {test_acc:.4f}")

    y_pred_prob = model.predict(X_test, verbose=0)
    y_pred      = np.argmax(y_pred_prob, axis=1)
    print(classification_report(y_test, y_pred, target_names=CLASS_NAMES))

    # Luu Keras (LSTM khong export TFLite INT8 vi LSTM chua support day du)
    keras_path = MODELS_DIR / "lstm_3class.keras"
    model.save(keras_path)
    print(f"  Luu Keras : {keras_path.name} ({keras_path.stat().st_size/1024:.0f} KB)")

    np.savez(MODELS_DIR / "lstm_training_history.npz",
             accuracy=history.history["accuracy"],
             val_accuracy=history.history["val_accuracy"],
             loss=history.history["loss"],
             val_loss=history.history["val_loss"])

    return model, test_acc, y_pred


# ===========================================================================
# MAIN
# ===========================================================================

def print_comparison(results):
    print("\n" + "=" * 70)
    print("  BANG SO SANH KET QUA (3 CLASS)")
    print("=" * 70)

    from sklearn.metrics import precision_recall_fscore_support

    header = f"  {'Model':<20} {'Acc':>6}  " \
             f"{'P-INST':>7} {'R-INST':>7} {'F1-INST':>8}  " \
             f"{'P-FALL':>7} {'R-FALL':>7} {'F1-FALL':>8}"
    print(header)
    print("  " + "-" * 68)

    for name, (acc, y_true, y_pred) in results.items():
        p, r, f1, _ = precision_recall_fscore_support(
            y_true, y_pred, labels=[0, 1, 2], zero_division=0
        )
        print(f"  {name:<20} {acc:>5.1%}  "
              f"{p[1]:>7.3f} {r[1]:>7.3f} {f1[1]:>8.3f}  "
              f"{p[2]:>7.3f} {r[2]:>7.3f} {f1[2]:>8.3f}")

    print("=" * 70)
    print("  Ghi chu:")
    print("  INST = VESTIBULAR_INSTABILITY (class 1)")
    print("  FALL = class 2")
    print("  Muc tieu: Recall FALL >= 0.85, F1 FALL >= 0.70")


def main():
    print("=" * 60)
    print("  HUAN LUYEN MODEL 3 CLASS")
    print("  NORMAL / VESTIBULAR_INSTABILITY / FALL")
    print("=" * 60)

    if not (DATA_DIR / "X_train.npz").exists():
        print(f"\n  LOI: Khong tim thay du lieu tai {DATA_DIR}")
        print("  Chay preprocess_vestibular.py truoc!")
        sys.exit(1)

    # 1. Load
    X_train, y_train, X_test, y_test = load_data()

    # 2. Augment minority classes
    X_train_aug, y_train_aug = augment_minority_classes(X_train, y_train)

    # 3. Normalize (fit tren aug train, apply len test)
    X_train_norm, X_test_norm, _, _ = normalize_data(X_train_aug, X_test)

    results = {}

    # 4. Random Forest (dung raw, chua chuan hoa)
    _, rf_acc, rf_pred = train_random_forest(X_train_aug, y_train_aug, X_test, y_test)
    results["Random Forest"] = (rf_acc, y_test, rf_pred)

    # 5. 1D-CNN (dung normalized)
    _, cnn_acc, cnn_pred = train_1d_cnn(X_train_norm, y_train_aug, X_test_norm, y_test)
    results["1D-CNN (INT8)"] = (cnn_acc, y_test, cnn_pred)

    # 6. LSTM (dung normalized)
    _, lstm_acc, lstm_pred = train_lstm(X_train_norm, y_train_aug, X_test_norm, y_test)
    results["LSTM"] = (lstm_acc, y_test, lstm_pred)

    # 7. In bang so sanh
    print_comparison(results)

    print(f"\n  Models luu tai: {MODELS_DIR}")
    print("=" * 60)
    print("  HOAN THANH!")
    print("=" * 60)


if __name__ == "__main__":
    main()
