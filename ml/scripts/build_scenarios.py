"""Build the MVP 1 replay scenarios from real recordings and check them with the detector.

    PYTHONPATH=. python ml/scripts/build_scenarios.py

Each scenario lists candidate splices of real recordings. Every candidate is run
through the same StreamMonitor the backend uses; the first one that meets the
scenario's expectation is written to assets/scenarios/<id>/, and
detector_check.json records every candidate tried, passing or not. Thresholds
are never adjusted to make a candidate pass.
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.services.fall_detection import FallDetectionService  # noqa: E402
from app.services.monitoring import StreamMonitor  # noqa: E402
from ml.training import preprocess as sisfall_pp  # noqa: E402
from ml.training import preprocess_vestibular as vest_pp  # noqa: E402

RATE_HZ = 50
OUT_DIR = ROOT / "assets" / "scenarios"


def vestibular(participant: str, activity: str) -> tuple[np.ndarray, dict]:
    path = vest_pp.VEST_DIR / participant / ".csv" / f"IMU_{activity}.csv"
    data = vest_pp.add_svm_channel(vest_pp.resample_to_target_rate(vest_pp.load_vest_file(path)))
    return data, {"dataset": "vestibular", "participant": participant, "activity": activity, "sensor": "TR (trunk)"}


def sisfall(subject: str, trial: str) -> tuple[np.ndarray, dict]:
    path = sisfall_pp.DATASET_DIR / subject / f"{trial}.txt"
    data = sisfall_pp.downsample(sisfall_pp.load_sisfall_file(path), sisfall_pp.DOWNSAMPLE_FACTOR)
    return data, {"dataset": "SisFall", "subject": subject, "trial": trial, "sensor": "waist"}


SCENARIOS = {
    "S1": {
        "title": "Evening walk toward the stairs, no fall",
        "notes": "Walking only. Candidates that included healthy stair climbing tripped the SVM >= 2.5 g rule; see detector_check.json.",
        "expect_incidents": 0,
        "candidates": [
            [("vestibular", "Participant_58_HS", "Walk"), ("vestibular", "Participant_58_HS", "Stairs"), ("vestibular", "Participant_44_HS", "Walk")],
            [("vestibular", "Participant_44_HS", "Walk"), ("vestibular", "Participant_44_HS", "Stairs"), ("vestibular", "Participant_58_HS", "Walk")],
            [("vestibular", "Participant_58_HS", "Walk"), ("vestibular", "Participant_44_HS", "Walk"), ("vestibular", "Participant_58_HS", "Walk")],
        ],
    },
    "S2": {
        "title": "Walk, then a forward fall near the stairs",
        "notes": "SisFall F01: fall forward while walking, caused by a slip.",
        "expect_incidents": 1,
        "fall_segment": 1,
        "candidates": [
            [("vestibular", "Participant_58_HS", "Walk"), ("sisfall", "SA03", "F01_SA03_R01")],
            [("vestibular", "Participant_58_HS", "Walk"), ("sisfall", "SA03", "F01_SA03_R02")],
            [("vestibular", "Participant_44_HS", "Walk"), ("sisfall", "SA02", "F01_SA02_R01")],
        ],
    },
}


def splice(parts):
    arrays, segments, offset = [], [], 0
    for kind, a, b in parts:
        data, meta = vestibular(a, b) if kind == "vestibular" else sisfall(a, b)
        n = len(data)
        segments.append({**meta, "source_start_ms": offset * 1000 // RATE_HZ, "source_end_ms": (offset + n) * 1000 // RATE_HZ})
        arrays.append(data)
        offset += n
    return np.concatenate(arrays).astype(np.float32), segments


def check(detector, imu, key):
    monitor = StreamMonitor(stream_key=key, detector=detector)
    for i, sample in enumerate(imu):
        monitor.add_sample(i * 1000 // RATE_HZ, sample)
    monitor.close()
    return monitor


def passes(spec, monitor, segments):
    if len(monitor.incidents) != spec["expect_incidents"]:
        return False
    if "fall_segment" in spec:
        seg = segments[spec["fall_segment"]]
        return all(seg["source_start_ms"] <= inc.source_start_ms < seg["source_end_ms"] for inc in monitor.incidents)
    return True


def main():
    detector = FallDetectionService()
    for sid, spec in SCENARIOS.items():
        tried, chosen = [], None
        for n, parts in enumerate(spec["candidates"]):
            imu, segments = splice(parts)
            monitor = check(detector, imu, f"build:{sid}:{n}")
            ok = passes(spec, monitor, segments)
            tried.append({
                "candidate": n,
                "segments": segments,
                "incidents": [asdict(i) for i in monitor.incidents],
                "state_counts": monitor.state_counts,
                "passes": ok,
            })
            print(f"{sid} candidate {n}: incidents={[(i.source_start_ms, i.decision_source) for i in monitor.incidents]} states={monitor.state_counts} {'PASS' if ok else 'fail'}")
            if ok and chosen is None:
                chosen = (n, imu, segments)

        out = OUT_DIR / sid
        out.mkdir(parents=True, exist_ok=True)
        (out / "detector_check.json").write_text(json.dumps({
            "scenario": sid,
            "expect_incidents": spec["expect_incidents"],
            "model_source": detector.model_source,
            "chosen_candidate": None if chosen is None else chosen[0],
            "candidates": tried,
        }, indent=2))
        if chosen is None:
            print(f"{sid}: no candidate passed; nothing written except detector_check.json")
            continue
        n, imu, segments = chosen
        ms = (np.arange(len(imu)) * 1000 // RATE_HZ).reshape(-1, 1)
        np.savetxt(out / "imu.csv", np.hstack([ms, imu]), delimiter=",", fmt=["%d"] + ["%.5f"] * 7,
                   header="source_ms,ax,ay,az,gx,gy,gz,svm", comments="")
        (out / "scenario.json").write_text(json.dumps({
            "id": sid,
            "title": spec["title"],
            "notes": spec["notes"],
            "patient_id": 1,
            "sample_rate_hz": RATE_HZ,
            "imu_file": "imu.csv",
            "duration_ms": len(imu) * 1000 // RATE_HZ,
            "pairing": "Scenario-paired: real recordings from different people and sensors spliced on one timeline.",
            "segments": segments,
        }, indent=2))
        print(f"{sid}: wrote candidate {n}, {len(imu)} samples")


if __name__ == "__main__":
    main()
