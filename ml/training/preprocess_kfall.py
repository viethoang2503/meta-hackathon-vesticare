"""
preprocess_kfall.py

Tich hop KFall dataset vao pipeline 3-class (NORMAL / INSTABILITY / FALL).

KFall dac diem:
  - 32 subjects (SA06-SA38, missing SA34)
  - 100 Hz (can downsample xuong 50 Hz)
  - Gyroscope don vi rad/s (can chuyen sang deg/s)
  - Task T01-T19 = ADL (NORMAL), T20-T34 = FALL
  - Label xlsx co Fall_onset_frame va Fall_impact_frame (o 100 Hz)

Output: them window moi vao X_train.npz, y_train.npz
        Test set giu nguyen de dam bao fairness.

Chay tu thu muc goc project:
  python ml/training/preprocess_kfall.py
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ===========================================================================
# THAM SO
# ===========================================================================
WINDOW_SIZE  = 100          # 2 giay o 50 Hz
WINDOW_STEP  = 50           # overlap 50% (khop voi preprocess.py)
SOURCE_HZ    = 100          # KFall sampling rate
TARGET_HZ    = 50           # pipeline sampling rate
DOWNSAMPLE   = SOURCE_HZ // TARGET_HZ   # = 2, lay moi sample thu 2

# KFall gyroscope da o don vi deg/s (LPMS-B2 output), KHONG can convert

# Task ID: T01-T19 = ADL (NORMAL=0), T20-T34 = FALL (FALL=2)
ADL_TASK_MAX  = 19
FALL_TASK_MIN = 20

# Bao nhieu frame (o 50Hz) sau impact thi ket thuc vung FALL
FALL_POST_IMPACT = 50       # 1 giay

# ===========================================================================
# DUONG DAN
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
KFALL_DIR    = PROJECT_ROOT / "ml" / "data" / "raw" / "KFALL_dataset"
DATA_DIR     = PROJECT_ROOT / "ml" / "data" / "processed_3class"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ===========================================================================
# HELPERS
# ===========================================================================

def load_csv(path: Path) -> np.ndarray:
    """
    Doc CSV KFall, downsample 100->50 Hz, convert gyro rad/s->deg/s,
    tinh SVM, tra ve array shape (N, 7): [ax, ay, az, gx, gy, gz, svm]
    """
    df = pd.read_csv(path)
    # Downsample: lay moi sample thu DOWNSAMPLE
    df = df.iloc[::DOWNSAMPLE].reset_index(drop=True)

    ax = df["AccX"].values.astype(np.float32)
    ay = df["AccY"].values.astype(np.float32)
    az = df["AccZ"].values.astype(np.float32)
    gx = df["GyrX"].values.astype(np.float32)
    gy = df["GyrY"].values.astype(np.float32)
    gz = df["GyrZ"].values.astype(np.float32)
    svm = np.sqrt(ax**2 + ay**2 + az**2).astype(np.float32)

    return np.stack([ax, ay, az, gx, gy, gz, svm], axis=1)   # (N, 7)


def parse_label_xlsx(xlsx_path: Path) -> dict[tuple[int, int], tuple[int, int]]:
    """
    Doc label file, tra ve dict: {(task_id, trial_id): (onset_50hz, impact_50hz)}
    """
    lb = pd.read_excel(xlsx_path)
    result = {}
    current_task = None
    current_task_id = None

    for _, row in lb.iterrows():
        task_str = row["Task Code (Task ID)"]
        if pd.notna(task_str):
            m = re.search(r"\((\d+)\)", str(task_str))
            current_task_id = int(m.group(1)) if m else None
            current_task    = task_str

        trial    = int(row["Trial ID"])
        onset_100 = int(row["Fall_onset_frame"])
        impact_100 = int(row["Fall_impact_frame"])

        # Chuyen frame 100Hz -> 50Hz
        onset_50  = onset_100  // DOWNSAMPLE
        impact_50 = impact_100 // DOWNSAMPLE

        if current_task_id is not None:
            result[(current_task_id, trial)] = (onset_50, impact_50)

    return result


def sliding_windows(data: np.ndarray, label: int) -> tuple[list, list]:
    """Sliding window tren toan bo file, tra ve (windows, labels)."""
    X, y = [], []
    for start in range(0, len(data) - WINDOW_SIZE + 1, WINDOW_STEP):
        X.append(data[start : start + WINDOW_SIZE])
        y.append(label)
    return X, y


def fall_windows(data: np.ndarray,
                 onset_50: int,
                 impact_50: int) -> tuple[list, list]:
    """
    Trich xuat windows tu file FALL:
      - Truoc onset: NORMAL
      - Tu onset den impact + FALL_POST_IMPACT: FALL
      - Sau do: NORMAL (giai doan recovery)
    """
    fall_end = min(impact_50 + FALL_POST_IMPACT, len(data))

    X_norm, y_norm = [], []
    X_fall, y_fall = [], []

    for start in range(0, len(data) - WINDOW_SIZE + 1, WINDOW_STEP):
        end = start + WINDOW_SIZE
        win = data[start:end]

        # Window trung voi vung fall?
        overlap_start = max(start, onset_50)
        overlap_end   = min(end,   fall_end)

        if overlap_end > overlap_start:
            X_fall.append(win)
            y_fall.append(2)   # FALL
        elif end <= onset_50:
            X_norm.append(win)
            y_norm.append(0)   # NORMAL (truoc fall)

    return X_norm + X_fall, y_norm + y_fall


# ===========================================================================
# MAIN PROCESSING
# ===========================================================================

def process_subject(subject_dir: Path) -> tuple[list, list]:
    """Xu ly 1 subject, tra ve (X_list, y_list)."""
    subj_id = subject_dir.name   # e.g. SA06
    num = subj_id[2:]            # e.g. 06

    # Tim label file
    label_path = subject_dir.parent / f"{subj_id}_label.xlsx"
    if not label_path.exists():
        print(f"  [WARN] label file not found for {subj_id}, skip")
        return [], []

    labels = parse_label_xlsx(label_path)

    X_all, y_all = [], []

    for csv_path in sorted(subject_dir.glob("*.csv")):
        m = re.match(r"S(\d+)T(\d+)R(\d+)", csv_path.stem)
        if not m:
            continue
        task_id  = int(m.group(2))
        trial_id = int(m.group(3))

        try:
            data = load_csv(csv_path)
        except Exception as e:
            print(f"  [WARN] {csv_path.name}: {e}")
            continue

        if task_id <= ADL_TASK_MAX:
            # ADL -> NORMAL windows
            Xw, yw = sliding_windows(data, label=0)
        elif task_id >= FALL_TASK_MIN:
            # FALL file: dung label de phan loai
            key = (task_id, trial_id)
            if key not in labels:
                # Khong co label -> bo qua
                continue
            onset_50, impact_50 = labels[key]
            if onset_50 >= len(data) or impact_50 >= len(data):
                continue
            Xw, yw = fall_windows(data, onset_50, impact_50)
        else:
            continue

        X_all.extend(Xw)
        y_all.extend(yw)

    return X_all, y_all


def main():
    print("=" * 60)
    print("  PREPROCESS KFALL DATASET")
    print(f"  Source: {KFALL_DIR}")
    print("=" * 60)

    subjects = sorted([d for d in KFALL_DIR.iterdir() if d.is_dir()])
    print(f"  Found {len(subjects)} subjects")

    X_new, y_new = [], []

    for subj in subjects:
        Xs, ys = process_subject(subj)
        if Xs:
            X_new.extend(Xs)
            y_new.extend(ys)
            n0 = sum(1 for v in ys if v == 0)
            n2 = sum(1 for v in ys if v == 2)
            print(f"  {subj.name}: {len(Xs):5d} windows  (NORMAL={n0}, FALL={n2})")

    X_new = np.array(X_new, dtype=np.float32)
    y_new = np.array(y_new, dtype=np.int32)

    print(f"\n  KFall windows: {len(X_new):,}")
    for cls, name in [(0, "NORMAL"), (2, "FALL")]:
        print(f"    {name:15s}: {int((y_new==cls).sum()):6,}")

    # Load existing train set
    print("\n  Loading existing train set...")
    X_old = np.load(DATA_DIR / "X_train.npz")["data"]
    y_old = np.load(DATA_DIR / "y_train.npz")["data"].astype(np.int32)
    print(f"  Existing: {len(X_old):,} windows")

    # Merge
    X_merged = np.concatenate([X_old, X_new], axis=0)
    y_merged = np.concatenate([y_old, y_new], axis=0)

    # Shuffle
    rng  = np.random.default_rng(42)
    perm = rng.permutation(len(X_merged))
    X_merged = X_merged[perm]
    y_merged = y_merged[perm]

    print(f"\n  Merged train set: {len(X_merged):,} windows")
    for cls, name in [(0, "NORMAL"), (1, "INSTABILITY"), (2, "FALL")]:
        print(f"    {name:15s}: {int((y_merged==cls).sum()):6,}")

    # Backup old files
    bak_X = DATA_DIR / "X_train_pre_kfall.npz"
    bak_y = DATA_DIR / "y_train_pre_kfall.npz"
    if not bak_X.exists():
        import shutil
        shutil.copy(DATA_DIR / "X_train.npz", bak_X)
        shutil.copy(DATA_DIR / "y_train.npz", bak_y)
        print(f"\n  Backup saved: X_train_pre_kfall.npz, y_train_pre_kfall.npz")

    # Save merged
    np.savez_compressed(DATA_DIR / "X_train.npz", data=X_merged)
    np.savez_compressed(DATA_DIR / "y_train.npz", data=y_merged)
    print(f"  Saved: X_train.npz ({(DATA_DIR/'X_train.npz').stat().st_size/1024/1024:.1f} MB)")
    print(f"  Saved: y_train.npz")

    print("\n  Buoc tiep theo:")
    print("  -> Upload X_train.npz moi len Google Drive")
    print("  -> Chay lai train_models_colab.ipynb (Cell 9: CNN+BiLSTM+Attn)")
    print("  -> Sau khi train xong chay lai baseline_comparison.py")


if __name__ == "__main__":
    main()
