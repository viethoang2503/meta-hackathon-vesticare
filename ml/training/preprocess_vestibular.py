"""
preprocess_vestibular.py

Tien xu ly bo du lieu Vestibular (BV/UV/HS) va ket hop voi SisFall
de tao dataset 3 class cho bai toan phat hien tien dinh:

  NORMAL (0)                  -- hoat dong binh thuong
  VESTIBULAR_INSTABILITY (1)  -- mat on dinh van dong dac trung tien dinh
  FALL (2)                    -- su kien te nga (tu SisFall)

LUU Y (M2 narrow repair):
  * Truoc day module lay mau thua 128Hz -> 64Hz (DOWNSAMPLE_FACTOR = 128 // 50).
    Bug nay lam mot cua so 100 mau chi dai 1.5625 s thay vi 2 s. Da thay bang
    resample huu ty chong chong (anti-aliased polyphase) up=25 / down=64 qua
    ml.common.resampling.
  * Nhan NORMAL / VESTIBULAR_INSTABILITY o day KHONG phai nhan canonical M1 va
    KHONG du dieu kien lam bang chung INSTABILITY cho M4/M5. Manifest ghi ro co
    legacy_label_semantics_ineligible_for_instability = True.
  * Import module khong con tao thu muc output; chi ham main() moi ghi file.
"""

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Mapping, Optional, Tuple

import numpy as np
import scipy

from ml.common.resampling import (
    RESAMPLING_SCHEMA_VERSION,
    resample_128_hz_to_50_hz,
)

# ===========================================================================
# CAU HINH
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

VEST_DIR = PROJECT_ROOT / "ml" / "data" / "raw" / "vestibular_dataset"
SISFALL_DIR = PROJECT_ROOT / "ml" / "data" / "processed"
OUTPUT_DIR = PROJECT_ROOT / "ml" / "data" / "processed_3class"

# Sampling
ORIGINAL_RATE = 128  # Hz cua vestibular dataset
TARGET_RATE = 50     # Hz cua ESP32 va SisFall pipeline
_RESAMPLE_UP = 25
_RESAMPLE_DOWN = 64

# Window
WINDOW_SIZE = 100  # 100 mau = 2 giay tai 50Hz
WINDOW_STEP = 50   # overlap 50%

# So mau nguon toi thieu de resample ra du 100 mau 50Hz:
# ceil(n * 25 / 64) >= 100  =>  n >= 256
MIN_SOURCE_SAMPLES_FOR_WINDOW = 256

# Labels (legacy, KHONG phai canonical M1)
LABEL_NORMAL = 0
LABEL_INSTAB = 1
LABEL_FALL = 2

# ===========================================================================
# PHAN LOAI ACTIVITY -> LABEL
# ===========================================================================

INSTABILITY_ACTIVITIES = {
    "Darkness walk",
    "Wood beam",
    "Uneven ground",
    "Stairs",
    "Stepladder",
    "Inclined plane",
    "Bus",
    "Pants",
    "Shoes",
    "Tray",
    "Heavy load",
}

NORMAL_ACTIVITIES_PATIENT = {
    "Walk",
    "Bed",
    "Sorting",
    "Picture recognition",
}

# ===========================================================================
# TRAIN / TEST SPLIT (theo participant ID de tranh leakage)
# ===========================================================================

BV_TEST = {f"Participant_{i:02d}_BV" for i in range(15, 20)}
UV_TEST = {f"Participant_{i:02d}_UV" for i in range(35, 40)}
HS_TEST = {f"Participant_{i:02d}_HS" for i in range(55, 60)}
TEST_PARTICIPANTS = BV_TEST | UV_TEST | HS_TEST

BV_TRAIN = {f"Participant_{i:02d}_BV" for i in range(1, 15)}
UV_TRAIN = {f"Participant_{i:02d}_UV" for i in range(20, 35)}
HS_TRAIN = {f"Participant_{i:02d}_HS" for i in range(40, 55)}
TRAIN_PARTICIPANTS = BV_TRAIN | UV_TRAIN | HS_TRAIN


# ===========================================================================
# RESAMPLING (M2 narrow repair)
# ===========================================================================

def resample_to_target_rate(data: np.ndarray) -> np.ndarray:
    """128 Hz -> 50 Hz bang anti-aliased polyphase up=25 / down=64.

    Nhan mang (N, C) float; tra ve (ceil(N * 25 / 64), C) cung dtype.
    """
    array = np.asarray(data)
    if array.ndim != 2:
        raise ValueError("resample_to_target_rate_expects_2d")
    if array.dtype.kind != "f":
        array = array.astype(np.float64)
    series = resample_128_hz_to_50_hz(array, start_timestamp_ns=0)
    return series.values


@dataclass(frozen=True)
class ObservedSplit:
    """Participants actually routed, as opposed to the declared sets.

    ``unlisted`` names folders present in neither declared set. The routing rule
    is ``not in TEST_PARTICIPANTS``, so those are trained on; recording them is
    what keeps the provenance record honest about who was trained on.
    """

    train: FrozenSet[str]
    test: FrozenSet[str]
    unlisted: Tuple[str, ...] = ()


def build_legacy_source_split_manifest(
    observed: Optional[ObservedSplit] = None,
) -> Mapping[str, object]:
    """Manifest hai chieu legacy: KHONG phai canonical M1 fold."""
    # Reported only when a real pass happened. Absent rather than empty, because
    # an empty `observed_unlisted_participants` would read as "no unlisted
    # folders found" when in fact nothing was inspected.
    observed_fields: Mapping[str, object] = (
        {}
        if observed is None
        else {
            "observed_train_participants": sorted(observed.train),
            "observed_held_out_participants": sorted(observed.test),
            "observed_unlisted_participants": list(observed.unlisted),
        }
    )
    return {
        **observed_fields,
        "schema_version": RESAMPLING_SCHEMA_VERSION,
        "source_rate_hz": ORIGINAL_RATE,
        "target_rate_hz": TARGET_RATE,
        "resample_up": _RESAMPLE_UP,
        "resample_down": _RESAMPLE_DOWN,
        "scipy_version": scipy.__version__,
        "train_participants": sorted(TRAIN_PARTICIPANTS),
        "held_out_participants": sorted(TEST_PARTICIPANTS),
        "participant_group_ids": sorted(TRAIN_PARTICIPANTS | TEST_PARTICIPANTS),
        "canonical_m1_folds": False,
        "eligibility": "legacy_public_pretraining_only",
        "legacy_label_semantics_ineligible_for_instability": True,
    }


# ===========================================================================
# HAM XU LY
# ===========================================================================

def get_participant_group(folder_name):
    if folder_name.endswith("_BV"):
        return "BV"
    if folder_name.endswith("_UV"):
        return "UV"
    if folder_name.endswith("_HS"):
        return "HS"
    return None


def get_activity_label(group, activity_name):
    if group == "HS":
        return LABEL_NORMAL
    if group in ("BV", "UV"):
        if activity_name in INSTABILITY_ACTIVITIES:
            return LABEL_INSTAB
        if activity_name in NORMAL_ACTIVITIES_PATIENT:
            return LABEL_NORMAL
        return None
    return None


def load_vest_file(csv_path):
    """Doc 1 file CSV vestibular, lay 6 kenh TR (Trunk sensor) + kenh SVM."""
    import pandas as pd  # lazy: pandas khong nam trong M2 pretrain lock

    df = pd.read_csv(
        csv_path,
        usecols=[
            "Acc_x_g_TR", "Acc_y_g_TR", "Acc_z_g_TR",
            "Gyr_x_deg/s_TR", "Gyr_y_deg/s_TR", "Gyr_z_deg/s_TR",
        ],
    )
    df = df.dropna()
    if len(df) == 0:
        return None

    # Six axes only. The SVM magnitude channel is appended *after* resampling by
    # `add_svm_channel`, because a magnitude is nonlinear: low-pass filtering
    # sqrt(ax^2+ay^2+az^2) does not equal the magnitude of the filtered axes, so
    # computing it here and then polyphase-filtering it with the rest left
    # channel 7 inconsistent with channels 1-3 and with the SisFall convention
    # these windows are concatenated with.
    return df.values.astype(np.float64)  # (N, 6)


def add_svm_channel(data: np.ndarray) -> np.ndarray:
    """Append the signal vector magnitude of the three accelerometer axes.

    Must run on the series at its final rate. Kept separate from loading so the
    identity ``channel 7 == magnitude(channels 1..3)`` holds in the output.
    """

    ax, ay, az = data[:, 0], data[:, 1], data[:, 2]
    svm = np.sqrt(ax ** 2 + ay ** 2 + az ** 2).reshape(-1, 1)
    return np.hstack([data, svm])  # (N, 7)


def create_windows(data, window_size, step):
    windows = []
    for start in range(0, len(data) - window_size + 1, step):
        windows.append(data[start:start + window_size])
    if not windows:
        return np.empty((0, window_size, data.shape[1]))
    return np.array(windows)


# ===========================================================================
# XU LY VESTIBULAR DATASET
# ===========================================================================

def process_participant(p_dir: Path):
    """Process one participant folder in isolation.

    Returns ``(windows, labels, file_count, skipped_count)`` for exactly this
    participant, with no train/test routing decision -- callers decide that.
    ``windows``/``labels`` are ``None`` when the folder is not a recognised
    group or has no ``.csv`` subdirectory (not merely "zero usable files").
    """
    group = get_participant_group(p_dir.name)
    if group is None:
        return None, None, 0, 0
    csv_dir = p_dir / ".csv"
    if not csv_dir.exists():
        return None, None, 0, 0

    X_list, y_list = [], []
    file_count = 0
    skipped = 0

    for csv_file in sorted(csv_dir.glob("IMU_*.csv")):
        activity = csv_file.stem.replace("IMU_", "")
        label = get_activity_label(group, activity)
        if label is None:
            skipped += 1
            continue

        data = load_vest_file(csv_file)
        if data is None or len(data) < MIN_SOURCE_SAMPLES_FOR_WINDOW:
            skipped += 1
            continue

        # Resample the six measured axes, then derive SVM at the target rate.
        data = resample_to_target_rate(data)
        data = add_svm_channel(data)
        windows = create_windows(data, WINDOW_SIZE, WINDOW_STEP)
        if len(windows) == 0:
            skipped += 1
            continue

        X_list.append(windows)
        y_list.append(np.full(len(windows), label, dtype=np.int32))
        file_count += 1

    if not X_list:
        return (
            np.empty((0, WINDOW_SIZE, 7)),
            np.empty((0,), dtype=np.int32),
            file_count,
            skipped,
        )
    return (
        np.concatenate(X_list, axis=0),
        np.concatenate(y_list, axis=0),
        file_count,
        skipped,
    )


def process_vestibular():
    train_X, train_y = [], []
    test_X, test_y = [], []
    # Which participants were *actually* routed where. The manifest used to report
    # the hardcoded `TRAIN_PARTICIPANTS` set while the routing decision is
    # `not in TEST_PARTICIPANTS`, so a folder in neither list -- a new cohort, a
    # renamed directory -- silently joined TRAIN while being absent from the
    # provenance record.
    observed_train: set = set()
    observed_test: set = set()

    participants = sorted(VEST_DIR.iterdir())
    total_files = 0
    skipped = 0

    print(f"  Tim thay {len(participants)} participant folders")

    for p_dir in participants:
        if not p_dir.is_dir():
            continue
        windows, labels, file_count, file_skipped = process_participant(p_dir)
        skipped += file_skipped
        if windows is None or len(windows) == 0:
            continue

        total_files += file_count
        is_test = p_dir.name in TEST_PARTICIPANTS
        if is_test:
            test_X.append(windows)
            test_y.append(labels)
            observed_test.add(p_dir.name)
        else:
            train_X.append(windows)
            train_y.append(labels)
            observed_train.add(p_dir.name)

    print(f"  Files xu ly thanh cong : {total_files}")
    print(f"  Files bo qua           : {skipped}")

    X_tr = np.concatenate(train_X, axis=0) if train_X else np.empty((0, WINDOW_SIZE, 7))
    y_tr = np.concatenate(train_y, axis=0) if train_y else np.empty((0,), dtype=np.int32)
    X_te = np.concatenate(test_X, axis=0) if test_X else np.empty((0, WINDOW_SIZE, 7))
    y_te = np.concatenate(test_y, axis=0) if test_y else np.empty((0,), dtype=np.int32)
    unlisted = sorted((observed_train | observed_test) - (TRAIN_PARTICIPANTS | TEST_PARTICIPANTS))
    if unlisted:
        print(f"  CANH BAO: participant khong co trong danh sach khai bao: {unlisted}")
    return X_tr, y_tr, X_te, y_te, ObservedSplit(
        train=frozenset(observed_train),
        test=frozenset(observed_test),
        unlisted=tuple(unlisted),
    )


def load_sisfall_processed():
    X_tr = np.load(SISFALL_DIR / "X_train.npz")["data"]
    y_tr = np.load(SISFALL_DIR / "y_train.npz")["data"].astype(np.int32)
    X_te = np.load(SISFALL_DIR / "X_test.npz")["data"]
    y_te = np.load(SISFALL_DIR / "y_test.npz")["data"].astype(np.int32)
    y_tr[y_tr == 1] = LABEL_FALL
    y_te[y_te == 1] = LABEL_FALL
    return X_tr, y_tr, X_te, y_te


# ===========================================================================
# MAIN
# ===========================================================================

def _write_provenance_manifest(observed: Optional[ObservedSplit] = None) -> None:
    import json

    manifest = dict(build_legacy_source_split_manifest(observed))
    manifest["window_size"] = WINDOW_SIZE
    manifest["window_step"] = WINDOW_STEP
    manifest["min_source_samples_for_window"] = MIN_SOURCE_SAMPLES_FOR_WINDOW
    (OUTPUT_DIR / "legacy_vestibular_provenance.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def print_stats(label, y):
    print(f"  {label}:")
    print(f"    NORMAL (0)                 : {np.sum(y == 0)}")
    print(f"    VESTIBULAR_INSTABILITY (1) : {np.sum(y == 1)}")
    print(f"    FALL (2)                   : {np.sum(y == 2)}")
    print(f"    Tong                       : {len(y)}")


def main():
    print("=" * 60)
    print("  TIEN XU LY VESTIBULAR + SISFALL (3 CLASS, LEGACY)")
    print("=" * 60)

    if not VEST_DIR.exists():
        print(f"\n  LOI: Khong tim thay {VEST_DIR}")
        sys.exit(1)
    if not SISFALL_DIR.exists():
        print(f"\n  LOI: Khong tim thay {SISFALL_DIR}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("\n[1/3] Xu ly Vestibular dataset...")
    t0 = time.time()
    vX_tr, vy_tr, vX_te, vy_te, observed_split = process_vestibular()
    print(f"  Thoi gian: {time.time() - t0:.1f}s")

    print("\n[2/3] Load SisFall processed (remap FALL 1->2)...")
    sX_tr, sy_tr, sX_te, sy_te = load_sisfall_processed()

    print("\n[3/3] Merge va luu...")
    X_train = np.concatenate([vX_tr, sX_tr], axis=0)
    y_train = np.concatenate([vy_tr, sy_tr], axis=0)
    X_test = np.concatenate([vX_te, sX_te], axis=0)
    y_test = np.concatenate([vy_te, sy_te], axis=0)

    print_stats("TRAIN", y_train)
    print_stats("TEST ", y_test)

    np.savez_compressed(OUTPUT_DIR / "X_train.npz", data=X_train)
    np.savez_compressed(OUTPUT_DIR / "y_train.npz", data=y_train)
    np.savez_compressed(OUTPUT_DIR / "X_test.npz", data=X_test)
    np.savez_compressed(OUTPUT_DIR / "y_test.npz", data=y_test)
    _write_provenance_manifest(observed_split)

    print(f"\n  Da luu vao: {OUTPUT_DIR}")
    print("  Legacy label semantics: KHONG canonical, KHONG du dieu kien INSTABILITY.")


if __name__ == "__main__":
    main()
