"""Gate G1, false alarms by SisFall daily activity: which ADLs raise a FALL incident?

    PYTHONPATH=. python ml/scripts/gate_g1_daily_by_activity.py

Same path and subjects as gate_g1_incidents.py (StreamMonitor, TEST split of the
v2 builder, training overlap unverified), broken down by SisFall activity code.
Activity names are from SisFall_dataset/Readme.txt.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.services.fall_detection import FallDetectionService  # noqa: E402
from app.services.monitoring import StreamMonitor  # noqa: E402
from ml.scripts.gate_g1_incidents import recordings  # noqa: E402
from ml.scripts.gate_g1_labels import test_subjects  # noqa: E402
from ml.training import preprocess as sisfall_pp  # noqa: E402

ACTIVITIES = {
    "D01": "Walking slowly",
    "D02": "Walking quickly",
    "D03": "Jogging slowly",
    "D04": "Jogging quickly",
    "D05": "Walking upstairs and downstairs slowly",
    "D06": "Walking upstairs and downstairs quickly",
    "D07": "Slowly sit in a half height chair and up slowly",
    "D08": "Quickly sit in a half height chair and up quickly",
    "D09": "Slowly sit in a low height chair and up slowly",
    "D10": "Quickly sit in a low height chair and up quickly",
    "D11": "Trying to get up, collapse into a chair",
    "D12": "Lying slowly, then sit again",
    "D13": "Lying quickly, then sit again",
    "D14": "On one's back, change to lateral position and back",
    "D15": "Standing, slowly bending at knees, getting up",
    "D16": "Standing, slowly bending without bending knees",
    "D17": "Get into a car, remain seated, get out",
    "D18": "Stumble while walking",
    "D19": "Gently jump without falling",
}


def daily_files(subject_ids):
    for sid in subject_ids:
        for path in sorted((sisfall_pp.DATASET_DIR / sid).glob("D*.txt")):
            data = sisfall_pp.load_sisfall_file(path)
            if data is None or len(data) < sisfall_pp.DOWNSAMPLE_FACTOR * sisfall_pp.WINDOW_SIZE:
                continue
            data = sisfall_pp.downsample(data, sisfall_pp.DOWNSAMPLE_FACTOR)
            yield (path.name[:3], sid[:2]), sisfall_pp.create_windows(data, sisfall_pp.WINDOW_SIZE, sisfall_pp.WINDOW_STEP)


def main():
    detector = FallDetectionService()
    assert detector.model_source == "keras", detector.model_source
    subjects = test_subjects([p.name for p in sorted(sisfall_pp.DATASET_DIR.iterdir()) if p.is_dir()])

    totals = {}
    for n, (key, samples) in enumerate(recordings(daily_files(subjects))):
        monitor = StreamMonitor(stream_key=f"g1d:{n}", detector=detector)
        for i, sample in enumerate(samples):
            monitor.add_sample(i * 20, sample)
        monitor.close()
        files, hit, sources = totals.get(key, (0, 0, {}))
        for inc in monitor.incidents:
            sources[inc.decision_source] = sources.get(inc.decision_source, 0) + 1
        totals[key] = (files + 1, hit + bool(monitor.incidents), sources)

    print(f"| Code | Activity | SA files with incident | SE files with incident | Incident sources |")
    print("|---|---|---|---|---|")
    for code, name in ACTIVITIES.items():
        cells, merged = [], {}
        for group in ("SA", "SE"):
            files, hit, sources = totals.get((code, group), (0, 0, {}))
            cells.append(f"{hit}/{files}" if files else "not performed")
            for k, v in sources.items():
                merged[k] = merged.get(k, 0) + v
        print(f"| {code} | {name} | {cells[0]} | {cells[1]} | {', '.join(f'{k} {v}' for k, v in sorted(merged.items())) or '-'} |")


if __name__ == "__main__":
    main()
