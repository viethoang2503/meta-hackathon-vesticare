"""
M1.4 - Data Quality Checks
Run before any training to catch problems early.

Checks:
1. Required files exist
2. Array shapes consistent (windows x 100 x 7)
3. No NaN or infinite values
4. No duplicate windows
5. Label values in expected range [0, 1, 2]
6. Label distribution per class (warn if heavily imbalanced)
7. No subject leakage between train and test
8. Sample-rate assumption: window_size / sampling_rate = 2s

Usage:
    python -m ml.experiments.data_quality
    or imported by runner.py before training
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
CLASS_NAMES = ["NORMAL", "VESTIBULAR_INSTABILITY", "FALL"]


def _check(label: str, passed: bool, detail: str = "", warn: bool = False) -> dict:
    status = "PASS" if passed else ("WARN" if warn else "FAIL")
    icon = "✓" if passed else ("⚠" if warn else "✗")
    print(f"  [{icon}] {label}" + (f": {detail}" if detail else ""))
    return {"check": label, "status": status, "detail": detail}


def check_files_exist(data_dir: Path) -> list[dict]:
    results = []
    required = ["X_train.npz", "X_test.npz", "y_train.npz", "y_test.npz"]
    for f in required:
        path = data_dir / f
        results.append(_check(f"File exists: {f}", path.exists(),
                               str(path) if not path.exists() else ""))
    return results


def check_shapes(X_train, X_test, y_train, y_test, window_size=100, num_channels=7) -> list[dict]:
    results = []

    # 3D shape
    results.append(_check("X_train is 3D (N, window, channels)",
                           X_train.ndim == 3, str(X_train.shape)))
    results.append(_check("X_test is 3D (N, window, channels)",
                           X_test.ndim == 3, str(X_test.shape)))

    # Window size
    if X_train.ndim == 3:
        results.append(_check(f"Window size = {window_size}",
                               X_train.shape[1] == window_size,
                               f"actual={X_train.shape[1]}"))
        results.append(_check(f"Channels = {num_channels}",
                               X_train.shape[2] == num_channels,
                               f"actual={X_train.shape[2]}"))

    # y shape
    results.append(_check("y_train is 1D", y_train.ndim == 1, str(y_train.shape)))
    results.append(_check("y_test is 1D", y_test.ndim == 1, str(y_test.shape)))

    # N consistency
    if X_train.ndim == 3:
        results.append(_check("X_train N matches y_train N",
                               X_train.shape[0] == y_train.shape[0],
                               f"X={X_train.shape[0]}, y={y_train.shape[0]}"))
        results.append(_check("X_test N matches y_test N",
                               X_test.shape[0] == y_test.shape[0],
                               f"X={X_test.shape[0]}, y={y_test.shape[0]}"))

    return results


def check_nan_inf(X_train, X_test, y_train, y_test) -> list[dict]:
    results = []
    for name, arr in [("X_train", X_train), ("X_test", X_test)]:
        nan_count = int(np.isnan(arr).sum())
        inf_count = int(np.isinf(arr).sum())
        results.append(_check(f"No NaN in {name}", nan_count == 0, f"{nan_count} NaN found"))
        results.append(_check(f"No Inf in {name}", inf_count == 0, f"{inf_count} Inf found"))
    for name, arr in [("y_train", y_train), ("y_test", y_test)]:
        nan_count = int(np.isnan(arr.astype(float)).sum())
        results.append(_check(f"No NaN in {name}", nan_count == 0, f"{nan_count} NaN found"))
    return results


def check_labels(y_train, y_test, expected_labels=(0, 1, 2)) -> list[dict]:
    results = []
    for name, y in [("y_train", y_train), ("y_test", y_test)]:
        unique = set(np.unique(y).tolist())
        expected = set(expected_labels)
        results.append(_check(f"{name} labels in {expected_labels}",
                               unique.issubset(expected),
                               f"found {unique}"))
        results.append(_check(f"{name} has all expected labels",
                               expected.issubset(unique),
                               f"missing {expected - unique}" if not expected.issubset(unique) else ""))

    # Imbalance check (warn if any class < 10% of total)
    for name, y in [("y_train", y_train), ("y_test", y_test)]:
        total = len(y)
        dist = []
        for i, cname in enumerate(CLASS_NAMES):
            cnt = int((y == i).sum())
            pct = cnt / total * 100
            dist.append(f"{cname}={cnt}({pct:.1f}%)")
            if pct < 10:
                results.append(_check(f"{name} {cname} >= 10%", False,
                                       f"{pct:.1f}% — heavy imbalance", warn=True))
        print(f"    Distribution {name}: {', '.join(dist)}")
    return results


def check_duplicates(X_train, X_test) -> list[dict]:
    results = []
    # Check within train
    flat_train = X_train.reshape(len(X_train), -1)
    _, counts = np.unique(flat_train, axis=0, return_counts=True)
    dup_train = int((counts > 1).sum())
    results.append(_check("No duplicate windows in X_train",
                           dup_train == 0, f"{dup_train} duplicates",
                           warn=(dup_train > 0)))

    # Check train/test overlap
    flat_test = X_test.reshape(len(X_test), -1)
    train_set = set(map(tuple, flat_train.tolist()))
    overlap = sum(1 for row in flat_test.tolist() if tuple(row) in train_set)
    results.append(_check("No train/test window overlap",
                           overlap == 0, f"{overlap} windows overlap"))
    return results


def check_sampling_rate(window_size: int = 100, sampling_rate: int = 50) -> list[dict]:
    duration = window_size / sampling_rate
    results = [_check(f"Window duration = 2s ({window_size}/{sampling_rate}Hz)",
                       abs(duration - 2.0) < 0.01, f"actual={duration:.2f}s")]
    return results


def check_subject_leakage(data_dir: Path) -> list[dict]:
    """
    Check for subject leakage if a split manifest exists.
    If no manifest found, warn that leakage cannot be verified automatically.
    """
    manifest_path = data_dir / "split_manifest.json"
    if not manifest_path.exists():
        return [_check("Subject leakage: split manifest exists",
                        False,
                        "split_manifest.json not found — cannot verify subject independence. "
                        "Verify manually that preprocess_vestibular.py used subject-independent split.",
                        warn=True)]

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        train_subjects = set(manifest.get("train_subjects", []))
        test_subjects = set(manifest.get("test_subjects", []))
        overlap = train_subjects & test_subjects
        return [_check("No subject overlap train/test",
                        len(overlap) == 0,
                        f"Overlap: {overlap}" if overlap else
                        f"train={len(train_subjects)} subjects, test={len(test_subjects)} subjects")]
    except Exception as e:
        return [_check("Subject leakage manifest readable", False, str(e))]


def run(data_dir: Path | None = None, save_report: bool = True) -> dict:
    data_dir = data_dir or (ROOT / "ml" / "data" / "processed_3class")
    print(f"\n{'='*60}")
    print(f"  M1.4 Data Quality Report")
    print(f"  Directory: {data_dir.relative_to(ROOT)}")
    print(f"{'='*60}")

    all_checks = []

    # 1. Files
    print("\n  [1/7] File existence...")
    all_checks += check_files_exist(data_dir)

    # Stop if files missing
    if any(c["status"] == "FAIL" for c in all_checks):
        print("\n  FATAL: Required files missing. Stopping checks.")
        return _summarize(all_checks, data_dir, save_report)

    # Load data
    print("\n  Loading arrays...")
    X_train = np.load(data_dir / "X_train.npz")["data"]
    X_test  = np.load(data_dir / "X_test.npz")["data"]
    y_train = np.load(data_dir / "y_train.npz")["data"].astype(np.int32)
    y_test  = np.load(data_dir / "y_test.npz")["data"].astype(np.int32)
    print(f"  X_train: {X_train.shape}  X_test: {X_test.shape}")

    # 2. Shapes
    print("\n  [2/7] Shape checks...")
    all_checks += check_shapes(X_train, X_test, y_train, y_test)

    # 3. NaN/Inf
    print("\n  [3/7] NaN/Inf checks...")
    all_checks += check_nan_inf(X_train, X_test, y_train, y_test)

    # 4. Labels
    print("\n  [4/7] Label checks...")
    all_checks += check_labels(y_train, y_test)

    # 5. Sampling rate
    print("\n  [5/7] Sampling rate check...")
    all_checks += check_sampling_rate()

    # 6. Duplicate windows (skip if large — use sample)
    print("\n  [6/7] Duplicate check (sample 5000 windows)...")
    sample_size = min(5000, len(X_train))
    idx = np.random.choice(len(X_train), sample_size, replace=False)
    all_checks += check_duplicates(X_train[idx], X_test)

    # 7. Subject leakage
    print("\n  [7/7] Subject leakage check...")
    all_checks += check_subject_leakage(data_dir)

    return _summarize(all_checks, data_dir, save_report)


def _summarize(checks: list, data_dir: Path, save_report: bool) -> dict:
    passed = sum(1 for c in checks if c["status"] == "PASS")
    warned = sum(1 for c in checks if c["status"] == "WARN")
    failed = sum(1 for c in checks if c["status"] == "FAIL")

    print(f"\n{'='*60}")
    print(f"  Data Quality: {passed} PASS, {warned} WARN, {failed} FAIL")
    if failed > 0:
        print("  STATUS: FAIL — fix issues before training")
    elif warned > 0:
        print("  STATUS: WARN — review warnings before training")
    else:
        print("  STATUS: PASS — data ready for training")
    print(f"{'='*60}\n")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(data_dir.relative_to(ROOT)),
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "all_passed": failed == 0,
        "checks": checks,
    }

    if save_report:
        report_dir = ROOT / "docs"
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / "data_quality_report.json"
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Report saved: {path.relative_to(ROOT)}")

    return result


if __name__ == "__main__":
    run()
