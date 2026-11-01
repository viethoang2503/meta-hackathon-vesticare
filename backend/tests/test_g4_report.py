import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import g4_report  # noqa: E402
from app.services.spatial_runs import JOURNAL_COLUMNS  # noqa: E402

TRAIN_MEAN = [-0.161, -0.557, -0.065, -0.882, 3.249, -0.293, 1.037]


def journal(n=3000, gravity=(0.0, -1.0, 0.0), skip=(), step_ms=20, edit=None):
    rows = []
    for seq in range(n):
        if seq in skip:
            continue
        ax, ay, az = gravity
        values = [ax, ay, az, 0.0, 0.0, 0.0, float(np.linalg.norm(gravity))]
        if edit:
            values = edit(seq, values)
        rows.append([seq * step_ms, seq, 10_000 + seq * step_ms, 0, seq * step_ms, *values])
    assert len(rows[0]) == len(JOURNAL_COLUMNS)
    return np.array(rows, dtype=float)


STATS = {"dropped_at_source": 0, "duplicates": 0, "rejected_batches": 0, "coverage": 0.99,
         "unavailable_ms": 600, "first_warmup_ms": 1980, "reset_count": 0, "resets": []}


def test_a_clean_recording_passes_every_row_but_simulated_data_is_never_evidence():
    table = journal()
    real = g4_report.analyse(table, STATS, [], {"rest": (5, 35)}, "esp32", TRAIN_MEAN)
    assert real["status"] == "Pass"
    r = real["rows"]
    assert (r["rest"]["mean_svm_g"], r["rest"]["gravity_axis"], r["rest"]["note"]) == (1.0, "-y", None)
    assert (r["acquisition"]["rate_hz"], r["acquisition"]["p95_interval_ms"]) == (50.0, 20.0)
    assert (r["transport"]["lost"], r["range"]["clipped_samples"]) == (0, 0)
    fake = g4_report.analyse(table, STATS, [], {"rest": (5, 35)}, "fake_esp32", TRAIN_MEAN)
    assert fake["status"] == "Simulated: not G4 evidence"


def test_gravity_on_another_axis_is_flagged_against_the_training_data():
    r = g4_report.analyse(journal(gravity=(0.0, 0.0, 1.0)), STATS, [], {"rest": (5, 35)}, "esp32", TRAIN_MEAN)["rows"]
    assert r["rest"]["gravity_axis"] == "+z"
    assert "orientation-sensitive" in r["rest"]["note"]


def test_losses_are_split_between_source_and_transport():
    table = journal(skip=set(range(1000, 1060)))          # 60 samples never arrived
    stats = {**STATS, "dropped_at_source": 20}           # the device itself dropped 20 of them
    t = g4_report.analyse(table, stats, [], {}, "esp32", TRAIN_MEAN)["rows"]["transport"]
    assert (t["lost"], t["of_acquired_kept"], t["pass"]) == (40, 2980, False)


def test_slow_sampling_fails_acquisition():
    a = g4_report.analyse(journal(step_ms=25), STATS, [], {}, "esp32", TRAIN_MEAN)["rows"]["acquisition"]
    assert (a["rate_hz"], a["pass"]) == (40.0, False)


def test_clipping_and_detector_trials():
    def spike(seq, v):
        if seq == 2000:
            v[0] = 16.0  # pinned at full scale
        if 2000 <= seq < 2010:
            v[6] = 3.4 if seq != 2000 else 16.0
        return v
    incidents = [{"id": 7, "source_start_ms": 40_100, "decision_source": "keras"},
                 {"id": 8, "source_start_ms": 10_000, "decision_source": "svm_rule"}]
    r = g4_report.analyse(journal(edit=spike), STATS, incidents, {"falls": [(39, 45)], "adl": [(5, 15), (20, 30)]},
                          "esp32", TRAIN_MEAN)["rows"]
    assert (r["range"]["clipped_samples"], r["range"]["pass"], r["range"]["fall_peak_svm_g"]) == (1, False, [16.0])
    assert (r["detector"]["falls_detected"], r["detector"]["adl_with_false_alarm"]) == (1, 1)
    assert r["detector"]["fall_trials"][0]["incidents"] == [{"id": 7, "decision_source": "keras"}]
