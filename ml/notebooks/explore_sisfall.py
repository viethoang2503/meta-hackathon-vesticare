"""
explore_sisfall.py

Kham pha bo du lieu SisFall that:
- Doc 1 file nga (Fall) va 1 file di bo (ADL)
- Ve bieu do SVM (Signal Vector Magnitude) that
- Thong ke tong quan dataset
"""

import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

# ===========================================================================
# CAU HINH
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATASET_DIR = PROJECT_ROOT / "ml" / "data" / "raw" / "SisFall_dataset"
EVAL_DIR = PROJECT_ROOT / "ml" / "evaluation"
EVAL_DIR.mkdir(parents=True, exist_ok=True)

# He so chuyen doi theo datasheet cua SisFall
ADXL345_SCALE = 256.0    # raw -> G (gravity)
ITG3200_SCALE = 14.375   # raw -> degree/s
SAMPLING_RATE = 200      # Hz


def load_sisfall_file(filepath):
    """
    Doc 1 file .txt cua SisFall.
    
    Moi dong co 9 gia tri phan cach boi dau phay, ket thuc bang dau ';'
    Cot 1-3: ADXL345 accelerometer (Ax, Ay, Az)
    Cot 4-6: ITG3200 gyroscope (Gx, Gy, Gz)
    Cot 7-9: MMA8451Q accelerometer (bo qua)
    
    Returns:
        DataFrame voi cot: ax, ay, az, gx, gy, gz (da chuyen doi don vi)
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

    data = np.array(rows, dtype=np.float64)

    # Chuyen doi don vi
    data[:, 0:3] /= ADXL345_SCALE   # Accelerometer: raw -> G
    data[:, 3:6] /= ITG3200_SCALE   # Gyroscope: raw -> deg/s

    df = pd.DataFrame(data, columns=['ax', 'ay', 'az', 'gx', 'gy', 'gz'])
    return df


def compute_svm(df):
    """Tinh Signal Vector Magnitude (SVM) tu 3 truc gia toc."""
    return np.sqrt(df['ax']**2 + df['ay']**2 + df['az']**2)


def count_dataset_files():
    """Dem tong so file trong dataset, phan loai ADL va Fall."""
    adl_count = 0
    fall_count = 0
    subjects = []

    for subject_dir in sorted(DATASET_DIR.iterdir()):
        if not subject_dir.is_dir():
            continue
        subjects.append(subject_dir.name)
        for f in subject_dir.glob("*.txt"):
            name = f.stem  # e.g., D01_SA01_R01
            if name.startswith('D'):
                adl_count += 1
            elif name.startswith('F'):
                fall_count += 1

    return adl_count, fall_count, subjects


def main():
    print("=" * 60)
    print("  KHAM PHA BO DU LIEU SISFALL")
    print("=" * 60)

    # --- THONG KE TONG QUAN ---
    print("\n[1/3] Dang dem toan bo file trong dataset...")
    adl_count, fall_count, subjects = count_dataset_files()
    total = adl_count + fall_count

    print(f"\n  Tong so nguoi tham gia : {len(subjects)}")
    print(f"  - Nguoi tre (SA)       : {sum(1 for s in subjects if s.startswith('SA'))}")
    print(f"  - Nguoi gia (SE)       : {sum(1 for s in subjects if s.startswith('SE'))}")
    print(f"\n  Tong so file du lieu   : {total}")
    print(f"  - Hoat dong binh thuong (ADL) : {adl_count}")
    print(f"  - Nga (Fall)                  : {fall_count}")
    print(f"  - Ti le Fall/ADL              : {fall_count/adl_count:.2f}")

    # --- DOC 1 FILE DI BO VA 1 FILE NGA ---
    print("\n[2/3] Dang doc file mau...")

    # Tim 1 file ADL (di bo cham - D01) va 1 file Fall (nga truot - F01)
    adl_file = DATASET_DIR / "SA01" / "D01_SA01_R01.txt"
    fall_file = DATASET_DIR / "SA01" / "F01_SA01_R01.txt"

    df_adl = load_sisfall_file(adl_file)
    df_fall = load_sisfall_file(fall_file)

    svm_adl = compute_svm(df_adl)
    svm_fall = compute_svm(df_fall)

    # Tao truc thoi gian (giay)
    time_adl = np.arange(len(svm_adl)) / SAMPLING_RATE
    time_fall = np.arange(len(svm_fall)) / SAMPLING_RATE

    print(f"\n  File ADL  : {adl_file.name}")
    print(f"    So dong : {len(df_adl)}")
    print(f"    Thoi gian: {time_adl[-1]:.1f} giay")
    print(f"    SVM min : {svm_adl.min():.3f} G")
    print(f"    SVM max : {svm_adl.max():.3f} G")
    print(f"    SVM mean: {svm_adl.mean():.3f} G")

    print(f"\n  File FALL : {fall_file.name}")
    print(f"    So dong : {len(df_fall)}")
    print(f"    Thoi gian: {time_fall[-1]:.1f} giay")
    print(f"    SVM min : {svm_fall.min():.3f} G")
    print(f"    SVM max : {svm_fall.max():.3f} G")
    print(f"    SVM mean: {svm_fall.mean():.3f} G")

    # --- VE BIEU DO ---
    print("\n[3/3] Dang ve bieu do...")

    fig, axes = plt.subplots(3, 1, figsize=(14, 12))

    # Bieu do 1: SVM - Di bo binh thuong (chi lay 10 giay dau)
    max_samples_10s = 10 * SAMPLING_RATE
    ax1 = axes[0]
    ax1.plot(time_adl[:max_samples_10s], svm_adl[:max_samples_10s],
             color='#2ecc71', linewidth=0.8, alpha=0.9)
    ax1.axhline(y=2.5, color='#e74c3c', linestyle='--', alpha=0.7, label='Nguong nga (2.5G)')
    ax1.set_title('Di bo binh thuong (D01 - Walking slowly) - 10 giay dau',
                  fontsize=13, fontweight='bold')
    ax1.set_ylabel('SVM (G)')
    ax1.set_xlabel('Thoi gian (giay)')
    ax1.legend(loc='upper right')
    ax1.set_ylim(0, 4.5)
    ax1.grid(True, alpha=0.3)

    # Bieu do 2: SVM - Nga (toan bo 15 giay)
    ax2 = axes[1]
    ax2.plot(time_fall, svm_fall, color='#e74c3c', linewidth=0.8, alpha=0.9)
    ax2.axhline(y=2.5, color='#e74c3c', linestyle='--', alpha=0.7, label='Nguong nga (2.5G)')
    ax2.set_title('Nga do truot chan (F01 - Fall forward caused by slip) - Toan bo',
                  fontsize=13, fontweight='bold')
    ax2.set_ylabel('SVM (G)')
    ax2.set_xlabel('Thoi gian (giay)')
    ax2.legend(loc='upper right')
    ax2.set_ylim(0, max(svm_fall.max() * 1.1, 5))
    ax2.grid(True, alpha=0.3)

    # Highlight vung nga
    fall_mask = svm_fall > 2.5
    if fall_mask.any():
        fall_start_idx = np.argmax(fall_mask)
        fall_start_time = fall_start_idx / SAMPLING_RATE
        ax2.axvspan(fall_start_time - 0.5, fall_start_time + 1.5,
                    color='red', alpha=0.1, label='Vung phat hien nga')

    # Bieu do 3: So sanh 3 truc gia toc khi nga
    ax3 = axes[2]
    ax3.plot(time_fall, df_fall['ax'], color='#3498db', linewidth=0.7, alpha=0.8, label='Ax')
    ax3.plot(time_fall, df_fall['ay'], color='#e67e22', linewidth=0.7, alpha=0.8, label='Ay')
    ax3.plot(time_fall, df_fall['az'], color='#9b59b6', linewidth=0.7, alpha=0.8, label='Az')
    ax3.set_title('3 truc gia toc khi nga (F01 - Chi tiet)', fontsize=13, fontweight='bold')
    ax3.set_ylabel('Gia toc (G)')
    ax3.set_xlabel('Thoi gian (giay)')
    ax3.legend(loc='upper right')
    ax3.grid(True, alpha=0.3)

    plt.tight_layout()
    chart_path = EVAL_DIR / "sisfall_exploration.png"
    plt.savefig(chart_path, dpi=150)
    plt.close()

    print(f"\n  Bieu do da luu tai: {chart_path}")
    print("\n" + "=" * 60)
    print("  HOAN THANH KHAM PHA DU LIEU!")
    print("=" * 60)


if __name__ == "__main__":
    main()
