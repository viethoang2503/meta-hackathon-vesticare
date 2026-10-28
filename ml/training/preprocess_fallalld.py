"""
preprocess_fallalld.py

Tien xu ly FallAllD dataset de them vao pipeline 3-class.

Nguon: https://ieee-dataport.org/open-access/fallalld-comprehensive-dataset-human-falls-and-activities-daily-living
Format file: {Subject}_{Device}_{Activity}_{Trial}_{Sensor}.dat
  Subject : S01-S15 (15 nguoi)
  Device  : D1 (waist) | D2 (wrist) | D3 (ankle)  --> chi dung D1
  Activity: A001-A044 (ADL) | A101-A135 (Falls)
  Sensor  : A (Accel) | G (Gyro) | B (Baro) | M (Mag)

Label mapping:
  A001-A044 --> NORMAL (0)
  A101-A135 --> FALL   (2)
  Khong co INSTABILITY -- FallAllD khong co vestibular activity data

Sensor specs (MPU-9250 trong FallAllD):
  Accel: +-8G range  --> scale = 4096 LSB/G  --> chia 4096
  Gyro : +-2000 deg/s --> scale = 16.384 LSB/(deg/s) --> chia 16.384
  Rate : 238 Hz --> downsample ve 50 Hz

Subject split:
  Train: S01-S12 (12 nguoi, 80%)
  Test : S13-S15 (3 nguoi, 20%)

Output:
  ml/data/processed_3class/X_fallalld_train.npz
  ml/data/processed_3class/y_fallalld_train.npz
  ml/data/processed_3class/X_fallalld_test.npz
  ml/data/processed_3class/y_fallalld_test.npz

  Sau do merge vao X_train.npz / X_test.npz (backup tu dong).

Chay tu thu muc goc project:
  python ml/training/preprocess_fallalld.py
"""

import sys
import numpy as np
from pathlib import Path
from scipy.signal import resample

# ===========================================================================
# CAU HINH
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FALLALLD_DIR = PROJECT_ROOT / "ml" / "data" / "raw" / "FALLALLD" / "FallAllD"
OUTPUT_DIR   = PROJECT_ROOT / "ml" / "data" / "processed_3class"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEVICE       = "D1"           # Waist -- gan nhat voi SisFall
ORIGINAL_RATE = 238           # Hz
TARGET_RATE   = 50            # Hz (khop voi SisFall va backend)
WINDOW_SIZE   = 100           # 100 mau = 2 giay @ 50Hz
WINDOW_STEP   = 50            # overlap 50%

# Scale factors MPU-9250 (FallAllD dung +-8G, +-2000 deg/s)
ACCEL_SCALE  = 4096.0         # LSB/G
GYRO_SCALE   = 16.384         # LSB/(deg/s)

# Activity label mapping
ADL_RANGE    = range(1, 45)   # A001-A044 --> NORMAL (0)
FALL_RANGE   = range(101, 136) # A101-A135 --> FALL (2)

# Subject split
TEST_SUBJECTS = {"S13", "S14", "S15"}

CLASS_NAMES = ["NORMAL", "INSTABILITY", "FALL"]


# ===========================================================================
# LOAD 1 TRIAL (pair A.dat + G.dat)
# ===========================================================================

def load_trial(accel_path: Path, gyro_path: Path):
    """
    Doc 1 cap file A.dat + G.dat, convert don vi, downsample ve 50Hz.
    Tra ve array shape (N_50Hz, 7): [ax, ay, az, gx, gy, gz, svm]
    """
    try:
        accel_raw = np.loadtxt(accel_path, delimiter=",")  # (N, 3)
        gyro_raw  = np.loadtxt(gyro_path,  delimiter=",")  # (N, 3)
    except Exception as e:
        return None

    if accel_raw.ndim != 2 or accel_raw.shape[1] < 3:
        return None
    if gyro_raw.ndim != 2 or gyro_raw.shape[1] < 3:
        return None

    # Lay cung so sample (tranh lech do ghi)
    n = min(len(accel_raw), len(gyro_raw))
    if n < TARGET_RATE:  # < 1 giay
        return None

    accel_raw = accel_raw[:n, :3]
    gyro_raw  = gyro_raw[:n,  :3]

    # Convert don vi
    accel = accel_raw / ACCEL_SCALE   # --> G
    gyro  = gyro_raw  / GYRO_SCALE    # --> deg/s

    # Downsample tu ORIGINAL_RATE ve TARGET_RATE
    # Dung scipy.signal.resample (resampling trong mien tan so)
    n_target = int(round(n * TARGET_RATE / ORIGINAL_RATE))
    if n_target < WINDOW_SIZE:
        return None

    accel_ds = resample(accel, n_target, axis=0)
    gyro_ds  = resample(gyro,  n_target, axis=0)

    # Tinh SVM (Signal Vector Magnitude)
    svm = np.sqrt(np.sum(accel_ds ** 2, axis=1, keepdims=True))

    # Ghep thanh (N, 7)
    data = np.concatenate([accel_ds, gyro_ds, svm], axis=1).astype(np.float32)
    return data


# ===========================================================================
# SLIDING WINDOW
# ===========================================================================

def sliding_window(data: np.ndarray, label: int):
    """Trich xuat tat ca windows tu 1 trial."""
    windows = []
    labels  = []
    n = len(data)
    for start in range(0, n - WINDOW_SIZE + 1, WINDOW_STEP):
        windows.append(data[start:start + WINDOW_SIZE])
        labels.append(label)
    return windows, labels


# ===========================================================================
# MAIN PREPROCESSING
# ===========================================================================

def main():
    print("=" * 60)
    print("  PREPROCESS FALLALLD")
    print(f"  Device: {DEVICE}  |  {ORIGINAL_RATE}Hz -> {TARGET_RATE}Hz")
    print("=" * 60)

    if not FALLALLD_DIR.exists():
        print(f"\nLOI: Khong tim thay {FALLALLD_DIR}")
        print("Kiem tra lai duong dan extract data.")
        sys.exit(1)

    # Tim tat ca file Accel cua D1
    all_accel_files = sorted(FALLALLD_DIR.glob(f"*_{DEVICE}_*_A.dat"))
    print(f"\nTim thay {len(all_accel_files)} file accel ({DEVICE})")

    train_X, train_y = [], []
    test_X,  test_y  = [], []

    stats = {"NORMAL": {"train": 0, "test": 0},
             "FALL":   {"train": 0, "test": 0},
             "skip":   0}

    for accel_path in all_accel_files:
        name   = accel_path.stem  # e.g. S01_D1_A013_T01_A
        parts  = name.split("_")
        if len(parts) < 4:
            stats["skip"] += 1
            continue

        subject  = parts[0]              # S01
        act_code = parts[2]              # A013
        act_num  = int(act_code[1:])     # 13

        # Xac dinh label
        if act_num in ADL_RANGE:
            label = 0   # NORMAL
            cls   = "NORMAL"
        elif act_num in FALL_RANGE:
            label = 2   # FALL
            cls   = "FALL"
        else:
            stats["skip"] += 1
            continue

        # Tim file Gyro tuong ung
        gyro_path = accel_path.parent / accel_path.name.replace("_A.dat", "_G.dat")
        if not gyro_path.exists():
            stats["skip"] += 1
            continue

        # Load va xu ly
        data = load_trial(accel_path, gyro_path)
        if data is None:
            stats["skip"] += 1
            continue

        # Sliding window
        windows, labels_w = sliding_window(data, label)
        if not windows:
            continue

        # Phan train / test theo subject
        split = "test" if subject in TEST_SUBJECTS else "train"
        if split == "train":
            train_X.extend(windows)
            train_y.extend(labels_w)
        else:
            test_X.extend(windows)
            test_y.extend(labels_w)

        stats[cls][split] += len(windows)

    # Ket qua trich xuat
    print(f"\n  NORMAL windows:  train={stats['NORMAL']['train']}  test={stats['NORMAL']['test']}")
    print(f"  FALL   windows:  train={stats['FALL']['train']}  test={stats['FALL']['test']}")
    print(f"  Skip (loi/khong ro label): {stats['skip']}")

    if not train_X and not test_X:
        print("\nKHONG CO DU LIEU! Kiem tra lai duong dan hoac ten file.")
        sys.exit(1)

    # Chuyen sang numpy
    X_tr = np.array(train_X, dtype=np.float32) if train_X else np.empty((0, WINDOW_SIZE, 7), dtype=np.float32)
    y_tr = np.array(train_y, dtype=np.int32)   if train_y else np.empty((0,), dtype=np.int32)
    X_te = np.array(test_X,  dtype=np.float32) if test_X  else np.empty((0, WINDOW_SIZE, 7), dtype=np.float32)
    y_te = np.array(test_y,  dtype=np.int32)   if test_y  else np.empty((0,), dtype=np.int32)

    # Luu FallAllD-only files
    np.savez_compressed(OUTPUT_DIR / "X_fallalld_train.npz", data=X_tr)
    np.savez_compressed(OUTPUT_DIR / "y_fallalld_train.npz", data=y_tr)
    np.savez_compressed(OUTPUT_DIR / "X_fallalld_test.npz",  data=X_te)
    np.savez_compressed(OUTPUT_DIR / "y_fallalld_test.npz",  data=y_te)
    print(f"\n  Luu FallAllD processed: {OUTPUT_DIR}")

    # ===========================================================================
    # MERGE vao X_train.npz / X_test.npz chinh
    # ===========================================================================
    print("\n  Merge vao processed_3class chinh...")

    for split_name, X_new, y_new in [("train", X_tr, y_tr), ("test", X_te, y_te)]:
        x_path = OUTPUT_DIR / f"X_{split_name}.npz"
        y_path = OUTPUT_DIR / f"y_{split_name}.npz"

        if not x_path.exists():
            print(f"  WARN: {x_path.name} chua ton tai, bo qua merge {split_name}")
            continue

        # Backup
        x_bak = OUTPUT_DIR / f"X_{split_name}_pre_fallalld.npz"
        y_bak = OUTPUT_DIR / f"y_{split_name}_pre_fallalld.npz"
        if not x_bak.exists():
            import shutil
            shutil.copy2(x_path, x_bak)
            shutil.copy2(y_path, y_bak)
            print(f"  Backup: {x_bak.name}, {y_bak.name}")

        # Load existing
        X_old = np.load(x_path)["data"]
        y_old = np.load(y_path)["data"]

        if len(X_new) == 0:
            print(f"  {split_name}: khong co du lieu moi, giu nguyen")
            continue

        # Concatenate
        X_merged = np.concatenate([X_old, X_new], axis=0)
        y_merged = np.concatenate([y_old, y_new], axis=0)

        np.savez_compressed(x_path, data=X_merged)
        np.savez_compressed(y_path, data=y_merged)

        # In phan phoi sau merge
        print(f"\n  [{split_name.upper()}] sau merge:")
        print(f"    Total windows: {len(y_merged)}")
        for i, cls in enumerate(CLASS_NAMES):
            old_n = int((y_old == i).sum())
            new_n = int((y_new == i).sum())
            tot_n = int((y_merged == i).sum())
            if tot_n > 0:
                print(f"    {cls:25s}: {old_n:6d} + {new_n:5d} (FallAllD) = {tot_n:6d}")

    print("\n" + "=" * 60)
    print("  HOAN THANH! Buoc tiep theo:")
    print("  Chay lai: python ml/training/train_3class_focal.py")
    print("  (scaler se duoc tinh lai tu du lieu moi)")
    print("=" * 60)


if __name__ == "__main__":
    main()
