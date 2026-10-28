"""
build_subject_independent_dataset.py

Rebuilds the 3-class (NORMAL / INSTABILITY / FALL) dataset with a genuine
subject-independent TRAIN / VAL / TEST split across all three source
datasets (SisFall, KFall, VestibularDataset). This fixes three problems the
2026-09 paper audit found in the existing `preprocess*.py` -> Colab pipeline:

  1. KFall was previously merged into TRAIN only ("Test set giu nguyen de
     dam bao fairness" in the old `preprocess_kfall.py`) -- the reported
     TEST set never contained a single KFall window, so no reported metric
     ever measured generalisation to KFall-style recordings.
  2. There was no genuine held-out VALIDATION split at all: the Colab
     notebook's `model.fit(..., validation_data=(X_test_n, y_te_cat))` used
     the TEST set itself as the early-stopping signal, and the committed
     threshold-sweep script also ran on the test set. Both are leakage --
     model selection and threshold selection must never see test data.
  3. INSTABILITY windows come exclusively from VestibularDataset, never from
     KFall (KFall's own label routing is NORMAL/FALL only). This script
     keeps that composition explicit and auditable in the manifest instead
     of letting later documentation imply otherwise.

This script does NOT change any per-file feature extraction, resampling, or
windowing logic. It imports and reuses, unchanged, the existing per-subject
loaders already fixed by the M2 vestibular-resampling repair:
  - `preprocess.py`            (SisFall: 200 Hz -> 50 Hz)
  - `preprocess_kfall.py`      (KFall: 100 Hz -> 50 Hz)
  - `preprocess_vestibular.py` (VestibularDataset: 128 Hz -> 50 Hz, already
    fixed to a true anti-aliased polyphase resample in commit cc7b2c7)
It only replaces *how subjects are assigned to splits*.

Run from the project root, after all three raw datasets are unpacked under
`ml/data/raw/` exactly as the three underlying scripts expect
(`SisFall_dataset/`, `KFALL_dataset/`, `vestibular_dataset/`):

  python -m ml.training.build_subject_independent_dataset

Output, under `ml/data/processed_3class_v2/`:
  X_train.npz / y_train.npz
  X_val.npz   / y_val.npz
  X_test.npz  / y_test.npz
  dataset_manifest.json   -- fills the paper's Methods dataset table
                             programmatically; never hand-type these numbers.

Upload the whole `processed_3class_v2/` directory to Google Drive for the
Colab training notebook (`train_models_colab_v2.ipynb`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from ml.training import preprocess as sisfall_pp
from ml.training import preprocess_kfall as kfall_pp
from ml.training import preprocess_vestibular as vest_pp

# ===========================================================================
# CONFIG
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = PROJECT_ROOT / "ml" / "data" / "processed_3class_v2"

SPLIT_SEED = 42
VAL_FRACTION = 0.15
TEST_FRACTION = 0.15
# TRAIN_FRACTION is whatever remains (~0.70).

CLASS_NAMES = ["NORMAL", "INSTABILITY", "FALL"]
WINDOW_SIZE = 100
WINDOW_STEP = 50
TARGET_RATE_HZ = 50

# Official published cohort sizes, for the Methods table's "Original
# subjects" column -- distinct from "subjects used", which is whatever this
# run actually found under ml/data/raw/. If these ever disagree, the
# manifest's `subject_count_mismatch` flag below is what should be believed.
SISFALL_OFFICIAL_SUBJECTS = 38  # Sucerquia et al. 2017: 23 young (SA) + 15 elderly (SE)
KFALL_OFFICIAL_SUBJECTS = 32    # Yu et al. 2021: SA06-SA38, missing SA34
VESTIBULAR_OFFICIAL_SUBJECTS = 59  # "Inertial data of daily living tasks in
# bilateral and unilateral vestibulopathy patients and controls", Zenodo,
# DOI 10.5281/zenodo.15081742: 19 bilateral (BV) + 20 unilateral (UV) +
# 20 healthy control (HS). Confirmed by exact match against this pipeline's
# own hardcoded BV_TRAIN/BV_TEST/UV_TRAIN/UV_TEST/HS_TRAIN/HS_TEST participant
# ID ranges in preprocess_vestibular.py (19+20+20=59) and its 128 Hz source
# rate. This is a public dataset, not custom-collected by this study --
# despite what this module's manifest previously claimed.


# ===========================================================================
# PER-SOURCE SUBJECT COLLECTION
# ===========================================================================


def _sisfall_subjects() -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """One (windows, labels) pair per SisFall subject folder.

    Reuses `preprocess.load_sisfall_file` / `.downsample` / `.create_windows`
    / `.get_label_from_filename` exactly as `preprocess.process_all_files()`
    does per file; the only difference is grouping by subject instead of by
    a hardcoded TEST_SUBJECTS set.
    """
    if not sisfall_pp.DATASET_DIR.exists():
        raise FileNotFoundError(
            f"SisFall dataset not found at {sisfall_pp.DATASET_DIR}"
        )

    per_subject: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for subject_dir in sorted(sisfall_pp.DATASET_DIR.iterdir()):
        if not subject_dir.is_dir():
            continue
        subject_id = subject_dir.name
        X_list, y_list = [], []
        for filepath in sorted(subject_dir.glob("*.txt")):
            if filepath.name.lower() in ("readme.txt", "desktop.ini"):
                continue
            data = sisfall_pp.load_sisfall_file(filepath)
            if data is None or len(data) < sisfall_pp.DOWNSAMPLE_FACTOR * WINDOW_SIZE:
                continue
            data = sisfall_pp.downsample(data, sisfall_pp.DOWNSAMPLE_FACTOR)
            windows = sisfall_pp.create_windows(data, WINDOW_SIZE, WINDOW_STEP)
            if len(windows) == 0:
                continue
            label = sisfall_pp.get_label_from_filename(filepath.name)
            if label == -1:
                continue
            X_list.append(windows)
            y_list.append(np.full(len(windows), label, dtype=np.int32))
        if X_list:
            per_subject[subject_id] = (
                np.concatenate(X_list, axis=0).astype(np.float32),
                np.concatenate(y_list, axis=0),
            )
    return per_subject


def _kfall_subjects() -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """One (windows, labels) pair per KFall subject folder.

    Reuses `preprocess_kfall.process_subject()` unchanged -- it already
    returns exactly this shape for one subject.
    """
    if not kfall_pp.KFALL_DIR.exists():
        raise FileNotFoundError(f"KFall dataset not found at {kfall_pp.KFALL_DIR}")

    per_subject: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for subject_dir in sorted(kfall_pp.KFALL_DIR.iterdir()):
        if not subject_dir.is_dir():
            continue
        X_list, y_list = kfall_pp.process_subject(subject_dir)
        if X_list:
            per_subject[subject_dir.name] = (
                np.array(X_list, dtype=np.float32),
                np.array(y_list, dtype=np.int32),
            )
    return per_subject


def _vestibular_subjects() -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """One (windows, labels) pair per vestibular-study participant folder.

    Reuses `preprocess_vestibular.process_participant()` unchanged.
    """
    if not vest_pp.VEST_DIR.exists():
        raise FileNotFoundError(f"Vestibular dataset not found at {vest_pp.VEST_DIR}")

    per_subject: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for p_dir in sorted(vest_pp.VEST_DIR.iterdir()):
        if not p_dir.is_dir():
            continue
        windows, labels, _file_count, _skipped = vest_pp.process_participant(p_dir)
        if windows is not None and len(windows) > 0:
            per_subject[p_dir.name] = (windows.astype(np.float32), labels)
    return per_subject


# ===========================================================================
# SUBJECT-INDEPENDENT SPLIT
# ===========================================================================


def split_subject_ids(
    subject_ids: List[str], *, seed: int, val_fraction: float, test_fraction: float
) -> Tuple[List[str], List[str], List[str]]:
    """Deterministically assign whole subjects to train/val/test.

    Every subject's windows go entirely to one split -- this is the one
    property the prior pipeline violated for none of its splits (SisFall and
    the vestibular dataset were already subject-disjoint train/test; the
    defect was the *absence* of a val split and KFall's all-into-train
    routing, not within-subject leakage). Rounds val/test up to at least one
    subject each so a small source is never silently absorbed into train.
    """
    ids = sorted(subject_ids)
    n = len(ids)
    if n < 3:
        raise ValueError(
            f"cannot make a 3-way subject split from only {n} subject(s): {ids}"
        )
    rng = np.random.default_rng(seed)
    order = rng.permutation(n)
    shuffled = [ids[i] for i in order]

    n_val = max(1, round(n * val_fraction))
    n_test = max(1, round(n * test_fraction))
    n_train = n - n_val - n_test
    if n_train < 1:
        raise ValueError(
            f"val_fraction+test_fraction leave no train subjects for n={n}"
        )

    train = shuffled[:n_train]
    val = shuffled[n_train : n_train + n_val]
    test = shuffled[n_train + n_val :]
    assert sorted(train + val + test) == ids
    return train, val, test


# ===========================================================================
# ASSEMBLY
# ===========================================================================


def _stack(per_subject: Dict[str, Tuple[np.ndarray, np.ndarray]], subject_ids: List[str]):
    if not subject_ids:
        return np.empty((0, WINDOW_SIZE, 7), dtype=np.float32), np.empty((0,), dtype=np.int32)
    X = np.concatenate([per_subject[s][0] for s in subject_ids], axis=0)
    y = np.concatenate([per_subject[s][1] for s in subject_ids], axis=0)
    return X, y


def _class_counts(y: np.ndarray) -> Dict[str, int]:
    return {name: int((y == i).sum()) for i, name in enumerate(CLASS_NAMES)}


def build() -> None:
    print("=" * 70)
    print("  BUILD SUBJECT-INDEPENDENT 3-CLASS DATASET (v2)")
    print("=" * 70)

    print("\n[1/3] Loading per-subject windows...")
    sources = {
        "sisfall": _sisfall_subjects(),
        "kfall": _kfall_subjects(),
        "vestibular": _vestibular_subjects(),
    }
    for name, per_subject in sources.items():
        total = sum(len(y) for _X, y in per_subject.values())
        print(f"  {name:12s}: {len(per_subject):3d} subjects, {total:7,d} windows")

    print("\n[2/3] Splitting subjects (train/val/test), source-stratified...")
    splits: Dict[str, Dict[str, List[str]]] = {}
    for name, per_subject in sources.items():
        train_ids, val_ids, test_ids = split_subject_ids(
            list(per_subject.keys()),
            seed=SPLIT_SEED,
            val_fraction=VAL_FRACTION,
            test_fraction=TEST_FRACTION,
        )
        splits[name] = {"train": train_ids, "val": val_ids, "test": test_ids}
        print(
            f"  {name:12s}: train={len(train_ids):3d}  val={len(val_ids):3d}  "
            f"test={len(test_ids):3d}"
        )

    print("\n[3/3] Assembling and saving...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_sources = {}
    final = {}
    for split_name in ("train", "val", "test"):
        X_parts, y_parts, origin_parts = [], [], []
        for source_name, per_subject in sources.items():
            ids = splits[source_name][split_name]
            X, y = _stack(per_subject, ids)
            X_parts.append(X)
            y_parts.append(y)
            origin_parts.append(np.full(len(y), source_name))
        X_all = np.concatenate(X_parts, axis=0)
        y_all = np.concatenate(y_parts, axis=0)
        origin_all = np.concatenate(origin_parts, axis=0)

        # Shuffle within the split so no source forms a contiguous block --
        # purely cosmetic for batch composition, never crosses split boundaries.
        rng = np.random.default_rng(SPLIT_SEED + {"train": 0, "val": 1, "test": 2}[split_name])
        perm = rng.permutation(len(X_all))
        X_all, y_all, origin_all = X_all[perm], y_all[perm], origin_all[perm]

        final[split_name] = (X_all, y_all)
        np.savez_compressed(OUTPUT_DIR / f"X_{split_name}.npz", data=X_all)
        np.savez_compressed(OUTPUT_DIR / f"y_{split_name}.npz", data=y_all)
        # Per-window source dataset ('sisfall'/'kfall'/'vestibular') -- not
        # consumed by ordinary training, only by the Finding-4 domain-origin
        # confound check (does a classifier trained on this label alone do
        # suspiciously well, which would suggest the 3-class model could be
        # keying on acquisition-pipeline artefacts rather than motion).
        np.savez_compressed(OUTPUT_DIR / f"origin_{split_name}.npz", data=origin_all)
        print(f"  {split_name}: {X_all.shape}  classes={_class_counts(y_all)}")

    for source_name, per_subject in sources.items():
        manifest_sources[source_name] = {
            "subjects_used": len(per_subject),
            "subjects_by_split": {
                split_name: sorted(ids) for split_name, ids in splits[source_name].items()
            },
            "windows_by_split": {
                split_name: _class_counts(_stack(per_subject, ids)[1])
                for split_name, ids in splits[source_name].items()
            },
        }

    manifest = {
        "schema_version": 2,
        "generated_by": "ml/training/build_subject_independent_dataset.py",
        "split_method": "subject-independent, source-stratified (each source split "
        f"independently at {1 - VAL_FRACTION - TEST_FRACTION:.0%}/"
        f"{VAL_FRACTION:.0%}/{TEST_FRACTION:.0%} train/val/test by subject count, "
        "then concatenated across sources)",
        "split_seed": SPLIT_SEED,
        "window_size_samples": WINDOW_SIZE,
        "window_step_samples": WINDOW_STEP,
        "target_rate_hz": TARGET_RATE_HZ,
        "class_names": CLASS_NAMES,
        "datasets": {
            "sisfall": {
                "original_subjects_published": SISFALL_OFFICIAL_SUBJECTS,
                "subjects_used": manifest_sources["sisfall"]["subjects_used"],
                "subject_count_mismatch": (
                    manifest_sources["sisfall"]["subjects_used"] != SISFALL_OFFICIAL_SUBJECTS
                ),
                "original_sampling_rate_hz": 200,
                "processing_applied": "decimation 200Hz->50Hz (every 4th sample), "
                "SVM channel appended, 2s/100-sample windows at 50% overlap",
                "classes_contributed": ["NORMAL", "FALL"],
                **manifest_sources["sisfall"],
            },
            "kfall": {
                "original_subjects_published": KFALL_OFFICIAL_SUBJECTS,
                "subjects_used": manifest_sources["kfall"]["subjects_used"],
                "subject_count_mismatch": (
                    manifest_sources["kfall"]["subjects_used"] != KFALL_OFFICIAL_SUBJECTS
                ),
                "original_sampling_rate_hz": 100,
                "processing_applied": "decimation 100Hz->50Hz (every 2nd sample), "
                "SVM channel appended, 2s/100-sample windows at 50% overlap; "
                "FALL windows selected from labelled onset/impact frames",
                "classes_contributed": ["NORMAL", "FALL"],
                "note": "KFall never contributes an INSTABILITY window in this "
                "pipeline -- its task routing is exactly ADL(<=T19)->NORMAL, "
                "fall tasks(>=T20)->FALL.",
                **manifest_sources["kfall"],
            },
            "vestibular": {
                "source_citation": "Inertial data of daily living tasks in bilateral "
                "and unilateral vestibulopathy patients and controls, Zenodo, "
                "DOI 10.5281/zenodo.15081742",
                "original_subjects_published": VESTIBULAR_OFFICIAL_SUBJECTS,
                "subjects_used": manifest_sources["vestibular"]["subjects_used"],
                "subject_count_mismatch": (
                    manifest_sources["vestibular"]["subjects_used"]
                    != VESTIBULAR_OFFICIAL_SUBJECTS
                ),
                "original_sampling_rate_hz": 128,
                "processing_applied": "anti-aliased polyphase resample 128Hz->50Hz "
                "(up=25/down=64, ml.common.resampling), SVM channel "
                "appended after resampling, 2s/100-sample windows at 50% overlap",
                "classes_contributed": ["NORMAL", "INSTABILITY"],
                "note": "Sole source of INSTABILITY windows in the merged corpus. "
                "This is a public dataset (19 bilateral + 20 unilateral "
                "vestibulopathy + 20 healthy control, 128 Hz, 9-IMU), not data "
                "collected by this study -- cite it explicitly in Methods "
                "instead of describing it as an in-house collection.",
                **manifest_sources["vestibular"],
            },
        },
        "merged_totals": {
            split_name: _class_counts(final[split_name][1]) for split_name in ("train", "val", "test")
        },
    }

    manifest_path = OUTPUT_DIR / "dataset_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"\n  Manifest: {manifest_path}")
    print("\n  Methods-table summary:")
    print(f"  {'Dataset':<12} {'Orig. subj.':>11} {'Used':>5} {'Orig. Hz':>9} {'Classes':<20}")
    for name, info in manifest["datasets"].items():
        orig = info["original_subjects_published"]
        orig_str = str(orig) if orig is not None else "N/A (custom)"
        print(
            f"  {name:<12} {orig_str:>11} {info['subjects_used']:>5} "
            f"{info['original_sampling_rate_hz']:>9} {','.join(info['classes_contributed']):<20}"
        )

    print("\n" + "=" * 70)
    print("  DONE. Upload the whole processed_3class_v2/ directory to Drive.")
    print("=" * 70)


if __name__ == "__main__":
    try:
        build()
    except FileNotFoundError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
