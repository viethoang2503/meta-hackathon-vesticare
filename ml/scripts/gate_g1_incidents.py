"""Gate G1, incident level: how many recordings produce at least one FALL incident?

    PYTHONPATH=. python ml/scripts/gate_g1_incidents.py

gate_g1_labels.py counts raw windows. This replays each whole recording sample by
sample through StreamMonitor, the backend's own path (0.5 s confirmation, then
incident grouping), so the counts are what a deployed run would raise.

Subjects are the TEST split of build_subject_independent_dataset.py (seed 42).
Whether the deployed weights were trained on that split is unverified, so these
are test-split subjects, not proven held-out ones.
"""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.services.fall_detection import FallDetectionService  # noqa: E402
from app.services.monitoring import StreamMonitor  # noqa: E402
from ml.scripts.gate_g1_labels import sisfall_files, test_subjects, vestibular_files  # noqa: E402
from ml.training import preprocess as sisfall_pp  # noqa: E402
from ml.training import preprocess_vestibular as vest_pp  # noqa: E402


def recordings(windows_by_file):
    """gate_g1_labels yields windows; rebuild the continuous recording from window step 50."""
    for key, windows in windows_by_file:
        if len(windows) == 0:
            continue
        step = sisfall_pp.WINDOW_STEP
        yield key, np.concatenate([windows[0], *[w[-step:] for w in windows[1:]]])


def main():
    detector = FallDetectionService()
    assert detector.model_source == "keras", detector.model_source
    vest_ids = [p.name for p in sorted(vest_pp.VEST_DIR.iterdir()) if (p / ".csv").is_dir()]
    sis_ids = [p.name for p in sorted(sisfall_pp.DATASET_DIR.iterdir()) if p.is_dir()]

    totals = {}
    stream = [*recordings(sisfall_files(test_subjects(sis_ids))), *recordings(vestibular_files(test_subjects(vest_ids)))]
    for n, (key, samples) in enumerate(stream):
        monitor = StreamMonitor(stream_key=f"g1:{n}", detector=detector)
        for i, sample in enumerate(samples):
            monitor.add_sample(i * 20, sample)
        monitor.close()
        files, with_incident, sources = totals.get(key, (0, 0, {}))
        for inc in monitor.incidents:
            sources[inc.decision_source] = sources.get(inc.decision_source, 0) + 1
        totals[key] = (files + 1, with_incident + bool(monitor.incidents), sources)

    print(f"{'source':10s} {'group':5s} {'recording kind':24s} {'files':>5s} {'with >=1 incident':>18s}  incident sources")
    for (source, group, kind), (files, hit, sources) in sorted(totals.items()):
        print(f"{source:10s} {group:5s} {kind:24s} {files:5d} {hit:8d} ({100 * hit / files:5.1f}%)  {sources}")


if __name__ == "__main__":
    main()
