"""
v1_instability_dataset_assessment.py

Phase V.1: Danh gia chat luong du lieu instability hien tai va tim huong mo rong.

Ket qua tra ve:
  1. So lieu du lieu INSTABILITY hien tai (vestibular_dataset)
  2. Phan phoi activity, so window moi subject
  3. Chat luong: phan ly SVM giua NORMAL/INSTABILITY/FALL
  4. Khuyen nghi: co can them dataset khong?

Chay tu thu muc goc project:
  python ml/scripts/v1_instability_dataset_assessment.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR     = PROJECT_ROOT / "ml" / "data" / "processed_3class"
VEST_DIR     = PROJECT_ROOT / "ml" / "data" / "raw" / "vestibular_dataset"
RESULTS_DIR  = PROJECT_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CLASS_NAMES = ["NORMAL", "INSTABILITY", "FALL"]


def analyze_test_set():
    """Phan tich phan phoi va SVM statistics cua tung class trong test set."""
    X = np.load(DATA_DIR / "X_test.npz")["data"]
    y = np.load(DATA_DIR / "y_test.npz")["data"].astype(np.int32)

    print("=" * 60)
    print("  V.1 INSTABILITY DATASET ASSESSMENT")
    print("=" * 60)
    print(f"\n[1/3] Test set statistics ({len(y)} windows total):")

    stats = {}
    for c, name in enumerate(CLASS_NAMES):
        mask     = (y == c)
        X_cls    = X[mask]
        peak_svm = X_cls[:, :, 6].max(axis=1)
        mean_svm = X_cls[:, :, 6].mean(axis=1)

        s = {
            "count":         int(mask.sum()),
            "pct":           round(100 * mask.sum() / len(y), 1),
            "peak_svm_mean": round(float(peak_svm.mean()), 3),
            "peak_svm_std":  round(float(peak_svm.std()),  3),
            "mean_svm_mean": round(float(mean_svm.mean()), 3),
            "mean_svm_std":  round(float(mean_svm.std()),  3),
            "peak_svm_p95":  round(float(np.percentile(peak_svm, 95)), 3),
        }
        stats[name] = s
        print(f"\n  {name} ({s['count']} windows = {s['pct']}%):")
        print(f"    peak_svm : mean={s['peak_svm_mean']:.3f}G  std={s['peak_svm_std']:.3f}G  p95={s['peak_svm_p95']:.3f}G")
        print(f"    mean_svm : mean={s['mean_svm_mean']:.3f}G  std={s['mean_svm_std']:.3f}G")

    # SVM separability check
    inst_peak = X[y == 1][:, :, 6].max(axis=1)
    norm_peak = X[y == 0][:, :, 6].max(axis=1)
    fall_peak = X[y == 2][:, :, 6].max(axis=1)

    overlap_ni = float(np.mean(inst_peak > norm_peak.mean()))
    overlap_if = float(np.mean(fall_peak > inst_peak.mean()))

    print(f"\n  SVM separability:")
    print(f"    INSTABILITY > NORMAL mean    : {overlap_ni*100:.1f}% of INSTABILITY windows")
    print(f"    FALL        > INSTABILITY mean: {overlap_if*100:.1f}% of FALL windows")
    print(f"    -> {'Tach biet tot' if overlap_ni > 0.6 and overlap_if > 0.6 else 'Can them features'}")

    return stats


def analyze_vestibular_raw():
    """Kiem tra vestibular_dataset: so participant, format file, cac activity."""
    if not VEST_DIR.exists():
        print(f"\n[2/3] vestibular_dataset not found at {VEST_DIR}")
        return {}

    participants = sorted([p for p in VEST_DIR.iterdir() if p.is_dir()])
    groups = {"BV": 0, "UV": 0, "HS": 0}
    for p in participants:
        for g in groups:
            if p.name.endswith(g):
                groups[g] += 1

    print(f"\n[2/3] Vestibular dataset raw:")
    print(f"  Participants: {len(participants)} total  (BV={groups['BV']}, UV={groups['UV']}, HS={groups['HS']})")

    # Sample 1 participant to get activity list
    sample_p = participants[0]
    csv_files = list(sample_p.glob("*.csv"))
    activities = set()
    for f in csv_files:
        # Filename format: Participant_01_BV_Walk_TR.csv
        parts = f.stem.split("_")
        if len(parts) >= 4:
            activities.add(parts[3])

    print(f"  Activities in sample participant ({sample_p.name}):")
    for a in sorted(activities):
        print(f"    - {a}")

    return {"participants": len(participants), "groups": groups, "activities": sorted(activities)}


def assess_umafall_readiness():
    """Kiem tra xem UMAFall da download chua va huong dan neu chua."""
    umafall_dir = PROJECT_ROOT / "ml" / "data" / "raw" / "UMAFall"

    print(f"\n[3/3] UMAFall dataset assessment:")

    if umafall_dir.exists():
        files = list(umafall_dir.rglob("*.csv"))
        print(f"  Found {len(files)} CSV files in {umafall_dir}")
        print(f"  -> Da co san, co the chay: python ml/training/preprocess_umafall.py")
        return {"status": "ready", "files": len(files)}
    else:
        print(f"  NOT FOUND at {umafall_dir}")
        print(f"\n  Huong dan download UMAFall:")
        print(f"  1. Truy cap: https://sites.google.com/up.edu.mx/har-up/")
        print(f"     hoac search Google: 'UMAFall dataset download'")
        print(f"  2. Download va giai nen vao:")
        print(f"     {umafall_dir}")
        print(f"  3. Kiem tra format: CSV voi cot Acceleration_X/Y/Z, Gyroscope_X/Y/Z")
        print(f"  4. Sau do chay: python ml/training/preprocess_umafall.py")
        print(f"\n  Thay the: PhysioNet gait datasets (co instability labels):")
        print(f"  -> https://physionet.org/content/gaitpdb/1.0.0/ (Parkinson gait)")
        print(f"     Luu y: can convert tu force plate/marker sang IMU-like features")
        print(f"\n  Khuyen nghi truoc mat:")
        print(f"  -> vestibular_dataset hien tai (20 participants, ~13K INSTABILITY windows)")
        print(f"     da du de train model. UMAFall them truoc khi viet paper.")
        return {"status": "not_found", "path": str(umafall_dir)}


def print_recommendation(test_stats: dict, vest_info: dict, umafall: dict):
    inst_count = test_stats.get("INSTABILITY", {}).get("count", 0)
    fall_count = test_stats.get("FALL", {}).get("count", 0)
    norm_count = test_stats.get("NORMAL", {}).get("count", 0)

    print("\n" + "=" * 60)
    print("  KETS QUA V.1 ASSESSMENT")
    print("=" * 60)

    print(f"\n  Du lieu instability hien tai: {inst_count:,} test windows")
    print(f"  Ti le: NORMAL={norm_count:,} | INST={inst_count:,} | FALL={fall_count:,}")

    if inst_count >= 3000:
        print(f"\n  [OK] So luong INSTABILITY du de train va evaluate model.")
        print(f"  Vestibular_dataset cung cap data instability chat luong cao:")
        print(f"    - 20 participants (BV/UV/HS) voi cac activity tiền đinh dac thu")
        print(f"    - Activities: Darkness walk, Wood beam, Uneven ground, Stairs...")
    else:
        print(f"\n  [WARN] INSTABILITY windows it (<3000). Can them data.")

    if umafall["status"] == "ready":
        print(f"\n  [NEXT] Chay preprocess_umafall.py de merge UMAFall vao train set")
    else:
        print(f"\n  [NEXT] Download UMAFall (optional) hoac dung vestibular_dataset hien tai")
        print(f"  -> Neu khong download duoc: document trong paper la limitation")
        print(f"     'Future work: validate with UMAFall/clinical vestibular data'")

    print(f"\n  Buoc tiep theo sau V.1:")
    print(f"  1. [B.3]  Chay baseline_comparison.py (sau khi train LSTM xong)")
    print(f"  2. [V.2]  Model 3-class da co san (1D-CNN focal), can them UMAFall neu muon")
    print(f"  3. [V.3]  Them tiền đình features (sway frequency, tilt angle)")
    print(f"  4. [Phase A] ESP32 firmware")


def main():
    test_stats = analyze_test_set()
    vest_info  = analyze_vestibular_raw()
    umafall    = assess_umafall_readiness()

    print_recommendation(test_stats, vest_info, umafall)

    # Save report
    report = {
        "test_set_stats":      test_stats,
        "vestibular_raw_info": vest_info,
        "umafall":             umafall,
    }
    out = RESULTS_DIR / "v1_instability_assessment.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report saved -> {out}")


if __name__ == "__main__":
    main()
