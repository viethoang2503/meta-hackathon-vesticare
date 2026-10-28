"""
preprocess.py

Tien xu ly bo du lieu SisFall cho bai toan Fall Detection (v2):
1. Doc tat ca file .txt tu SisFall_dataset
2. Chuyen doi don vi (raw -> G, raw -> deg/s)
3. Downsample tu 200Hz xuong 50Hz
4. Them kenh SVM (Signal Vector Magnitude) lam kenh thu 7
5. Sliding window: 2 giay (100 mau), overlap 50%
6. Gan nhan: D* = NORMAL (0), F* = FALL (1)
7. Chia train/test theo Subject (tranh data leakage)
8. Luu ket qua vao ml/data/processed/
"""

import os
import sys
import numpy as np
from pathlib import Path
import time

# ===========================================================================
# CAU HINH
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATASET_DIR = PROJECT_ROOT / "ml" / "data" / "raw" / "SisFall_dataset"
OUTPUT_DIR = PROJECT_ROOT / "ml" / "data" / "processed"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# He so chuyen doi
ADXL345_SCALE = 256.0       # raw -> G
ITG3200_SCALE = 14.375      # raw -> deg/s

# Tham so xu ly
ORIGINAL_RATE = 200          # Hz (tan so goc cua SisFall)
TARGET_RATE = 50             # Hz (tan so muc tieu, giong ESP32)
DOWNSAMPLE_FACTOR = ORIGINAL_RATE // TARGET_RATE  # = 4

WINDOW_SIZE = 100            # 100 mau = 2 giay tai 50Hz (bat tron 3 pha cua cu nga)
WINDOW_STEP = 50             # Buoc nhay 50 mau = overlap 50%

# Chia train/test theo Subject
# Train: SA01-SA18 + SE01-SE12 (30 nguoi)
# Test:  SA19-SA23 + SE13-SE15 (8 nguoi)
TEST_SUBJECTS = {'SA19', 'SA20', 'SA21', 'SA22', 'SA23', 'SE13', 'SE14', 'SE15'}


# ===========================================================================
# HAM XU LY
# ===========================================================================

def load_sisfall_file(filepath):
    """
    Doc 1 file .txt cua SisFall, lay 6 cot dau (ADXL345 + ITG3200),
    chuyen doi don vi, va them kenh SVM.
    
    Returns:
        numpy array shape (N, 7): [ax, ay, az, gx, gy, gz, svm]
        ax, ay, az: don vi G
        gx, gy, gz: don vi deg/s
    """
    rows = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip().rstrip(';').strip()
            if not line:
                continue
            parts = line.split(',')
            if len(parts) >= 6:
                try:
                    values = [int(p.strip()) for p in parts[:6]]
                    rows.append(values)
                except ValueError:
                    continue

    if len(rows) == 0:
        return None

    data = np.array(rows, dtype=np.float64)

    # Chuyen doi don vi
    data[:, 0:3] /= ADXL345_SCALE   # Accelerometer -> G
    data[:, 3:6] /= ITG3200_SCALE   # Gyroscope -> deg/s

    # Tinh SVM (Signal Vector Magnitude) va them lam kenh thu 7
    ax, ay, az = data[:, 0], data[:, 1], data[:, 2]
    svm = np.sqrt(ax**2 + ay**2 + az**2).reshape(-1, 1)
    data = np.hstack([data, svm])  # shape: (N, 7)

    return data


def downsample(data, factor):
    """Giam tan so lay mau bang cach lay moi 'factor' dong."""
    return data[::factor]


def create_windows(data, window_size, step):
    """
    Cat chuoi du lieu thanh cac cua so (windows).
    
    Args:
        data: numpy array (N, 6)
        window_size: so mau moi cua so
        step: buoc nhay

    Returns:
        numpy array (num_windows, window_size, 6)
    """
    windows = []
    for start in range(0, len(data) - window_size + 1, step):
        window = data[start:start + window_size]
        windows.append(window)

    if len(windows) == 0:
        return np.array([]).reshape(0, window_size, data.shape[1])

    return np.array(windows)


def get_label_from_filename(filename):
    """
    Xac dinh nhan tu ten file.
    D* -> NORMAL (0)
    F* -> FALL (1)
    """
    name = Path(filename).stem  # e.g., D01_SA01_R01
    if name.startswith('D'):
        return 0  # NORMAL
    elif name.startswith('F'):
        return 1  # FALL
    else:
        return -1  # Khong xac dinh


def get_subject_from_filename(filename):
    """Lay Subject ID tu ten file. VD: D01_SA01_R01 -> SA01"""
    name = Path(filename).stem
    parts = name.split('_')
    if len(parts) >= 2:
        return parts[1]  # SA01, SE06, ...
    return None


def process_all_files():
    """
    Xu ly toan bo dataset:
    1. Doc tung file
    2. Downsample
    3. Tao sliding windows
    4. Gan nhan
    5. Chia train/test theo Subject
    """
    train_windows = []
    train_labels = []
    test_windows = []
    test_labels = []

    total_files = 0
    skipped_files = 0
    
    # Dem tong so file truoc
    all_files = []
    for subject_dir in sorted(DATASET_DIR.iterdir()):
        if not subject_dir.is_dir():
            continue
        for filepath in sorted(subject_dir.glob("*.txt")):
            if filepath.name.lower() in ('readme.txt', 'desktop.ini'):
                continue
            all_files.append(filepath)

    print(f"\n  Tong so file can xu ly: {len(all_files)}")
    print(f"  Downsample: {ORIGINAL_RATE}Hz -> {TARGET_RATE}Hz (he so {DOWNSAMPLE_FACTOR})")
    print(f"  Window: {WINDOW_SIZE} mau ({WINDOW_SIZE/TARGET_RATE:.1f}s), overlap {(1 - WINDOW_STEP/WINDOW_SIZE)*100:.0f}%")
    print(f"  Test subjects: {sorted(TEST_SUBJECTS)}")
    print()

    start_time = time.time()

    for i, filepath in enumerate(all_files):
        total_files += 1

        # Hien thi tien do
        if (i + 1) % 100 == 0 or (i + 1) == len(all_files):
            elapsed = time.time() - start_time
            pct = (i + 1) / len(all_files) * 100
            print(f"  [{i+1}/{len(all_files)}] ({pct:.0f}%) - {elapsed:.1f}s", end='\r')

        # Doc file
        data = load_sisfall_file(filepath)
        if data is None or len(data) < DOWNSAMPLE_FACTOR * WINDOW_SIZE:
            skipped_files += 1
            continue

        # Downsample
        data = downsample(data, DOWNSAMPLE_FACTOR)

        # Tao windows
        windows = create_windows(data, WINDOW_SIZE, WINDOW_STEP)
        if len(windows) == 0:
            skipped_files += 1
            continue

        # Gan nhan
        label = get_label_from_filename(filepath.name)
        if label == -1:
            skipped_files += 1
            continue

        labels = np.full(len(windows), label)

        # Chia vao train hoac test dua tren Subject
        subject = get_subject_from_filename(filepath.name)
        if subject in TEST_SUBJECTS:
            test_windows.append(windows)
            test_labels.append(labels)
        else:
            train_windows.append(windows)
            train_labels.append(labels)

    print()  # Xuong dong sau progress bar
    elapsed = time.time() - start_time
    print(f"\n  Thoi gian xu ly: {elapsed:.1f} giay")
    print(f"  File da xu ly  : {total_files - skipped_files}/{total_files}")
    print(f"  File bo qua    : {skipped_files}")

    # Gom tat ca windows lai
    X_train = np.concatenate(train_windows, axis=0)
    y_train = np.concatenate(train_labels, axis=0)
    X_test = np.concatenate(test_windows, axis=0)
    y_test = np.concatenate(test_labels, axis=0)

    return X_train, y_train, X_test, y_test


def main():
    print("=" * 60)
    print("  TIEN XU LY DU LIEU SISFALL")
    print("=" * 60)

    # Kiem tra dataset ton tai
    if not DATASET_DIR.exists():
        print(f"\n  LOI: Khong tim thay thu muc dataset tai:")
        print(f"  {DATASET_DIR}")
        print(f"  Hay giai nen SisFall vao thu muc ml/data/raw/SisFall_dataset/")
        sys.exit(1)

    # Xu ly
    X_train, y_train, X_test, y_test = process_all_files()

    # Thong ke ket qua
    print("\n" + "-" * 60)
    print("  KET QUA TIEN XU LY")
    print("-" * 60)
    print(f"\n  TRAIN SET:")
    print(f"    Tong so windows : {len(X_train)}")
    print(f"    NORMAL (0)      : {np.sum(y_train == 0)}")
    print(f"    FALL (1)        : {np.sum(y_train == 1)}")
    print(f"    Shape           : {X_train.shape}")
    print(f"    (= {X_train.shape[0]} windows x {X_train.shape[1]} mau x {X_train.shape[2]} kenh)")

    print(f"\n  TEST SET:")
    print(f"    Tong so windows : {len(X_test)}")
    print(f"    NORMAL (0)      : {np.sum(y_test == 0)}")
    print(f"    FALL (1)        : {np.sum(y_test == 1)}")
    print(f"    Shape           : {X_test.shape}")

    # Luu file
    print(f"\n  Dang luu vao: {OUTPUT_DIR}")
    np.savez_compressed(OUTPUT_DIR / "X_train.npz", data=X_train)
    np.savez_compressed(OUTPUT_DIR / "y_train.npz", data=y_train)
    np.savez_compressed(OUTPUT_DIR / "X_test.npz", data=X_test)
    np.savez_compressed(OUTPUT_DIR / "y_test.npz", data=y_test)

    # Kiem tra dung luong file
    total_size = 0
    for f in OUTPUT_DIR.glob("*.npz"):
        size = f.stat().st_size
        total_size += size
        print(f"    {f.name}: {size / 1024 / 1024:.1f} MB")
    print(f"    Tong: {total_size / 1024 / 1024:.1f} MB")

    print("\n" + "=" * 60)
    print("  HOAN THANH TIEN XU LY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
