"""
train_fall_detection.py

Huan luyen model phat hien nga (Fall Detection) - PHIEN BAN TOI UU:
1. Load du lieu da tien xu ly tu processed/
2. Data Augmentation (tang cuong du lieu)
3. Normalization (chuan hoa du lieu)
4. Train Baseline: Random Forest
5. Train 1D-CNN (doi uu kien truc + training config)
6. Xuat model TFLite (full INT8 quantized, muc tieu < 100KB)
7. Luu model vao ml/models/
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
DATA_DIR = PROJECT_ROOT / "ml" / "data" / "processed"
MODELS_DIR = PROJECT_ROOT / "ml" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)


def load_data():
    """Load du lieu da tien xu ly."""
    print("  Dang load du lieu...")
    X_train = np.load(DATA_DIR / "X_train.npz")['data']
    y_train = np.load(DATA_DIR / "y_train.npz")['data']
    X_test = np.load(DATA_DIR / "X_test.npz")['data']
    y_test = np.load(DATA_DIR / "y_test.npz")['data']

    print(f"  Train: {X_train.shape}, Test: {X_test.shape}")
    print(f"  Train labels - NORMAL: {np.sum(y_train==0)}, FALL: {np.sum(y_train==1)}")
    print(f"  Test labels  - NORMAL: {np.sum(y_test==0)}, FALL: {np.sum(y_test==1)}")

    return X_train, y_train, X_test, y_test


# ===========================================================================
# DATA AUGMENTATION (TANG CUONG DU LIEU)
# ===========================================================================

def augment_data(X, y, augment_factor=2):
    """
    Tang cuong du lieu bang cach them bien the.
    Chi augment class FALL (class thieu) de can bang du lieu.
    
    Ky thuat:
    1. Jittering: Them nhieu Gaussian nhe (gia lap rung lac cam bien)
    2. Scaling: Nhan bien do voi he so ngau nhien (moi nguoi nga manh/nhe khac nhau)
    3. Time shift: Dich cua so sang trai/phai vai mau
    """
    print("\n  TANG CUONG DU LIEU (Data Augmentation)...")
    
    # Chi augment class FALL
    fall_mask = (y == 1)
    X_fall = X[fall_mask]
    y_fall = y[fall_mask]
    
    augmented_X = [X]  # Giu nguyen du lieu goc
    augmented_y = [y]
    
    for i in range(augment_factor):
        X_new = X_fall.copy()
        
        # 1. Jittering - them nhieu nhe
        noise_level = 0.05
        noise = np.random.normal(0, noise_level, X_new.shape)
        X_jittered = X_new + noise
        
        # 2. Scaling - thay doi bien do
        scale_factor = np.random.uniform(0.85, 1.15, (len(X_new), 1, 1))
        X_scaled = X_new * scale_factor
        
        # 3. Time shift - dich 1-3 mau
        shift = np.random.randint(-3, 4)
        X_shifted = np.roll(X_new, shift, axis=1)
        
        # Chon ngau nhien 1 trong 3 ky thuat cho moi batch
        choice = np.random.randint(0, 3)
        if choice == 0:
            augmented_X.append(X_jittered)
        elif choice == 1:
            augmented_X.append(X_scaled)
        else:
            augmented_X.append(X_shifted)
        
        augmented_y.append(y_fall)
    
    X_aug = np.concatenate(augmented_X, axis=0)
    y_aug = np.concatenate(augmented_y, axis=0)
    
    # Shuffle
    perm = np.random.permutation(len(X_aug))
    X_aug = X_aug[perm]
    y_aug = y_aug[perm]
    
    print(f"  Truoc augmentation: {len(X)} windows (FALL: {np.sum(y==1)})")
    print(f"  Sau augmentation : {len(X_aug)} windows (FALL: {np.sum(y_aug==1)})")
    
    return X_aug, y_aug


# ===========================================================================
# NORMALIZATION (CHUAN HOA DU LIEU)
# ===========================================================================

def normalize_data(X_train, X_test):
    """
    Chuan hoa du lieu theo tung kenh (channel).
    Ap dung StandardScaler: z = (x - mean) / std
    Luu scaler de dung lai khi inference.
    """
    print("\n  CHUAN HOA DU LIEU (Normalization)...")
    
    # Tinh mean va std tu train set
    # X shape: (num_windows, 100, 7)
    mean = X_train.mean(axis=(0, 1))  # shape: (7,)
    std = X_train.std(axis=(0, 1))    # shape: (7,)
    std[std == 0] = 1.0  # Tranh chia cho 0
    
    X_train_norm = (X_train - mean) / std
    X_test_norm = (X_test - mean) / std
    
    # Luu scaler parameters de dung khi inference
    scaler_params = {'mean': mean, 'std': std}
    np.savez(MODELS_DIR / "scaler_params.npz", **scaler_params)
    
    print(f"  Mean per channel: {mean}")
    print(f"  Std per channel : {std}")
    print(f"  Scaler da luu vao: scaler_params.npz")
    
    return X_train_norm, X_test_norm


# ===========================================================================
# MODEL 1: RANDOM FOREST (BASELINE)
# ===========================================================================

def extract_features(X):
    """
    Trich xuat features tu raw windows cho Random Forest.
    Tu moi window (100 mau x 7 kenh), tao ra 1 vector features.
    """
    features_list = []

    for window in X:
        ax, ay, az = window[:, 0], window[:, 1], window[:, 2]
        gx, gy, gz = window[:, 3], window[:, 4], window[:, 5]

        # SVM (Signal Vector Magnitude)
        svm = np.sqrt(ax**2 + ay**2 + az**2)

        # Gyro magnitude
        gyro_mag = np.sqrt(gx**2 + gy**2 + gz**2)

        # Jerk (bien thien cua SVM)
        jerk = np.diff(svm)

        # Tilt angle (goc nghieng)
        tilt = np.arctan2(ay, az)

        features = {
            # SVM features
            'svm_mean': np.mean(svm),
            'svm_max': np.max(svm),
            'svm_min': np.min(svm),
            'svm_std': np.std(svm),
            'svm_range': np.max(svm) - np.min(svm),

            # Accelerometer per-axis
            'ax_mean': np.mean(ax),
            'ay_mean': np.mean(ay),
            'az_mean': np.mean(az),
            'ax_std': np.std(ax),
            'ay_std': np.std(ay),
            'az_std': np.std(az),

            # Gyroscope
            'gyro_mag_mean': np.mean(gyro_mag),
            'gyro_mag_max': np.max(gyro_mag),
            'gyro_mag_std': np.std(gyro_mag),

            # Jerk
            'jerk_mean': np.mean(np.abs(jerk)),
            'jerk_max': np.max(np.abs(jerk)),

            # Tilt
            'tilt_mean': np.mean(tilt),
            'tilt_std': np.std(tilt),
        }

        features_list.append(list(features.values()))

    return np.array(features_list)


def train_random_forest(X_train, y_train, X_test, y_test):
    """Train Random Forest baseline model."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score, classification_report

    print("\n" + "-" * 60)
    print("  TRAINING: RANDOM FOREST (BASELINE)")
    print("-" * 60)

    # Trich xuat features (dung du lieu CHUA chuan hoa)
    print("  Dang trich xuat features...")
    t0 = time.time()
    X_train_feat = extract_features(X_train)
    X_test_feat = extract_features(X_test)
    print(f"  Feature shape: {X_train_feat.shape}")
    print(f"  Thoi gian trich xuat: {time.time()-t0:.1f}s")

    # Train
    print("  Dang train Random Forest...")
    t0 = time.time()
    rf = RandomForestClassifier(
        n_estimators=200,        # Tang tu 100 len 200 cay
        max_depth=20,            # Tang tu 15 len 20
        min_samples_leaf=5,
        random_state=42,
        n_jobs=-1,
        class_weight='balanced'
    )
    rf.fit(X_train_feat, y_train)
    print(f"  Thoi gian train: {time.time()-t0:.1f}s")

    # Danh gia
    y_pred = rf.predict(X_test_feat)
    acc = accuracy_score(y_test, y_pred)
    print(f"\n  ACCURACY: {acc:.4f} ({acc*100:.1f}%)")
    print("\n  Classification Report:")
    print(classification_report(y_test, y_pred, target_names=['NORMAL', 'FALL']))

    # Luu model
    model_path = MODELS_DIR / "random_forest.pkl"
    with open(model_path, 'wb') as f:
        pickle.dump(rf, f)
    model_size = model_path.stat().st_size / 1024
    print(f"  Model da luu: {model_path.name} ({model_size:.0f} KB)")

    # Luu features test de dung lai trong evaluate.py
    np.savez_compressed(DATA_DIR / "X_test_features.npz", data=X_test_feat)

    return rf, acc


# ===========================================================================
# MODEL 2: 1D-CNN (TOI UU)
# ===========================================================================

def train_1d_cnn(X_train_norm, y_train, X_test_norm, y_test):
    """Train 1D-CNN model toi uu voi TensorFlow/Keras."""
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import (
        Conv1D, MaxPooling1D, Dense, Dropout, 
        BatchNormalization, GlobalAveragePooling1D
    )
    from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
    from tensorflow.keras.utils import to_categorical

    print("\n" + "-" * 60)
    print("  TRAINING: 1D-CNN TOI UU")
    print("-" * 60)

    # Chuan bi du lieu
    num_classes = 2
    y_train_cat = to_categorical(y_train, num_classes)
    y_test_cat = to_categorical(y_test, num_classes)

    print(f"  Input shape: {X_train_norm.shape[1:]} (100 mau x 7 kenh)")
    print(f"  Output: {num_classes} class (NORMAL, FALL)")

    # --- XAY DUNG MODEL TOI UU ---
    # Thay doi so voi phien ban cu:
    # - Giam filter (32,64,64) -> (16,32,32): giam model size
    # - Dung GlobalAveragePooling1D thay Flatten: giam parameters + overfitting
    # - Tang Dropout tu 0.3 -> 0.4
    model = Sequential([
        # Block 1
        Conv1D(16, kernel_size=5, activation='relu', padding='same',
               input_shape=(X_train_norm.shape[1], X_train_norm.shape[2])),
        BatchNormalization(),
        MaxPooling1D(pool_size=2),
        Dropout(0.2),

        # Block 2
        Conv1D(32, kernel_size=3, activation='relu', padding='same'),
        BatchNormalization(),
        MaxPooling1D(pool_size=2),
        Dropout(0.2),

        # Block 3
        Conv1D(32, kernel_size=3, activation='relu', padding='same'),
        BatchNormalization(),

        # Dung GlobalAveragePooling thay Flatten
        # -> Giam manh so parameters, giup model nho hon
        GlobalAveragePooling1D(),

        # Classifier
        Dense(32, activation='relu'),
        Dropout(0.4),
        Dense(num_classes, activation='softmax')
    ])

    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
        loss='categorical_crossentropy',
        metrics=['accuracy']
    )

    model.summary()

    # --- CALLBACKS TOI UU ---
    callbacks = [
        EarlyStopping(
            monitor='val_loss',
            patience=10,            # Tang tu 5 len 10: cho model hoc lau hon
            restore_best_weights=True
        ),
        ReduceLROnPlateau(
            monitor='val_loss',
            factor=0.5,
            patience=4,             # Tang tu 3 len 4
            min_lr=1e-6,
            verbose=1
        )
    ]

    # --- TINH CLASS WEIGHTS ---
    from sklearn.utils.class_weight import compute_class_weight
    class_weights_vals = compute_class_weight(
        'balanced', classes=np.unique(y_train), y=y_train
    )
    class_weights = dict(enumerate(class_weights_vals))
    print(f"\n  Class weights: {class_weights}")

    # --- TRAIN ---
    print("\n  Bat dau training...")
    t0 = time.time()

    history = model.fit(
        X_train_norm, y_train_cat,
        epochs=50,                  # Tang tu 30 len 50
        batch_size=64,
        validation_split=0.15,
        callbacks=callbacks,
        class_weight=class_weights,
        verbose=1
    )

    train_time = time.time() - t0
    print(f"\n  Thoi gian training: {train_time:.1f}s")
    print(f"  So epoch thuc te : {len(history.history['accuracy'])}")

    # --- DANH GIA ---
    test_loss, test_acc = model.evaluate(X_test_norm, y_test_cat, verbose=0)
    print(f"  TEST ACCURACY: {test_acc:.4f} ({test_acc*100:.1f}%)")
    print(f"  TEST LOSS    : {test_loss:.4f}")

    # --- LUU MODEL KERAS ---
    keras_path = MODELS_DIR / "fall_detection.keras"
    model.save(keras_path)
    keras_size = keras_path.stat().st_size / 1024
    print(f"\n  Model Keras da luu: {keras_path.name} ({keras_size:.0f} KB)")

    # --- CHUYEN DOI SANG TFLITE (Full INT8) ---
    print("\n  Dang chuyen doi sang TFLite (full INT8 quantized)...")

    def representative_dataset():
        for i in range(min(500, len(X_train_norm))):
            yield [X_train_norm[i:i+1].astype(np.float32)]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = representative_dataset
    # Full INT8 quantization
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8

    tflite_model = converter.convert()
    tflite_path = MODELS_DIR / "fall_detection.tflite"
    with open(tflite_path, 'wb') as f:
        f.write(tflite_model)

    tflite_size = len(tflite_model) / 1024
    print(f"  Model TFLite da luu: {tflite_path.name} ({tflite_size:.1f} KB)")

    if tflite_size < 100:
        print(f"  PASS: Model < 100KB, phu hop voi ESP32!")
    else:
        print(f"  CANH BAO: Model > 100KB ({tflite_size:.1f} KB).")

    # Luu training history
    history_data = {
        'accuracy': history.history['accuracy'],
        'val_accuracy': history.history['val_accuracy'],
        'loss': history.history['loss'],
        'val_loss': history.history['val_loss'],
    }
    np.savez(MODELS_DIR / "training_history.npz", **history_data)

    return model, test_acc, history


def main():
    print("=" * 60)
    print("  HUAN LUYEN MODEL FALL DETECTION (PHIEN BAN TOI UU)")
    print("=" * 60)

    # Kiem tra du lieu
    if not (DATA_DIR / "X_train.npz").exists():
        print("\n  LOI: Chua co du lieu tien xu ly.")
        print("  Hay chay preprocess.py truoc!")
        sys.exit(1)

    # Load du lieu
    X_train, y_train, X_test, y_test = load_data()

    # === CAI THIEN 1: Data Augmentation ===
    X_train_aug, y_train_aug = augment_data(X_train, y_train, augment_factor=2)

    # === CAI THIEN 2: Normalization ===
    X_train_norm, X_test_norm = normalize_data(X_train_aug, X_test)

    # 1. Train Random Forest (dung du lieu goc, chua chuan hoa)
    rf_model, rf_acc = train_random_forest(X_train_aug, y_train_aug, X_test, y_test)

    # 2. Train 1D-CNN (dung du lieu da chuan hoa)
    cnn_model, cnn_acc, cnn_history = train_1d_cnn(
        X_train_norm, y_train_aug, X_test_norm, y_test
    )

    # --- SO SANH KET QUA ---
    print("\n" + "=" * 60)
    print("  SO SANH KET QUA (PHIEN BAN TOI UU)")
    print("=" * 60)
    print(f"\n  {'Model':<20} {'Accuracy':>10}")
    print(f"  {'-'*30}")
    print(f"  {'Random Forest':<20} {rf_acc:>9.1%}")
    print(f"  {'1D-CNN (toi uu)':<20} {cnn_acc:>9.1%}")
    print()

    winner = "1D-CNN" if cnn_acc > rf_acc else "Random Forest"
    print(f"  Model tot nhat: {winner}")

    # So sanh voi phien ban cu
    print(f"\n  So voi phien ban cu:")
    print(f"    1D-CNN cu : 85.6% | 117.5 KB")
    print(f"    1D-CNN moi: {cnn_acc*100:.1f}% | kiem tra TFLite size phia tren")

    print("\n" + "=" * 60)
    print("  HOAN THANH HUAN LUYEN!")
    print("=" * 60)


if __name__ == "__main__":
    main()
