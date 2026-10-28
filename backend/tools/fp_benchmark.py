"""
fp_benchmark.py

False-positive benchmark runner for M2.2.

Runs 4 scenario types against the live backend and records per-run metrics
(false positives, false negatives, alerts per minute, pass/fail) then writes
a CSV row-per-run and a JSON summary.

Usage:
    python backend/tools/fp_benchmark.py
    python backend/tools/fp_benchmark.py --scenarios normal instability
    python backend/tools/fp_benchmark.py --runs 10 --url http://localhost:8000
    python backend/tools/fp_benchmark.py --quick          # 3 runs, shorter durations
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Path setup — allow "python backend/tools/fp_benchmark.py" from project root
# ---------------------------------------------------------------------------
_TOOLS_DIR     = Path(__file__).resolve().parent          # backend/tools/
_BACKEND_DIR   = _TOOLS_DIR.parent                        # backend/
_SIMULATOR_DIR = _BACKEND_DIR / "simulator"               # backend/simulator/
_PROJECT_ROOT  = _BACKEND_DIR.parent                      # project root
_RESULTS_DIR   = _PROJECT_ROOT / "results"                # <root>/results/
_ML_DATA_DIR   = _PROJECT_ROOT / "ml" / "data" / "processed_3class"

if str(_SIMULATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_SIMULATOR_DIR))

from fake_esp32 import run_simulation, SimulationResult  # noqa: E402

# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

# Each entry describes one scenario class.
# Keys used at runtime:
#   name            — short identifier, used in CSV / JSON and --scenarios filter
#   mode            — fake_esp32 mode string: NORMAL | SWAY | FALL
#   duration_s      — default simulation duration in seconds
#   fall_at_s       — seconds until fall injection (FALL mode)
#   fall_duration_s — seconds the fall spike lasts (FALL mode)
#   expected_min    — minimum alerts expected to count as non-FN (0 for no-fall scenarios)
#   expected_exact  — if not None, actual_alerts must equal this value for PASS
#   patient_base    — starting patient_id; run i uses patient_base + i
#   quick_duration  — shorter duration used with --quick flag

SCENARIO_DEFS: list[dict[str, Any]] = [
    {
        "name":            "normal",
        "mode":            "NORMAL",
        "duration_s":      300.0,   # 5 minutes of normal activity
        "fall_at_s":       0.0,
        "fall_duration_s": 0.0,
        "expected_min":    0,       # no falls expected
        "expected_exact":  0,       # must be exactly 0 to pass
        "real_expected_min":   0,
        "real_expected_exact": 0,
        "patient_base":    20000,
        "quick_duration":  30.0,
    },
    {
        "name":            "instability",
        "mode":            "SWAY",
        "duration_s":      60.0,    # 1 minute of vestibular instability
        "fall_at_s":       0.0,
        "fall_duration_s": 0.0,
        "expected_min":    0,
        "expected_exact":  0,       # SWAY must NOT trigger false alerts (synthetic mode)
        # In --real-data mode: INSTABILITY class data should trigger >=1 INSTABILITY alert
        "real_expected_min":   1,
        "real_expected_exact": None,
        "patient_base":    21000,
        "quick_duration":  20.0,
    },
    {
        "name":            "one_fall",
        "mode":            "FALL",
        "duration_s":      30.0,    # 30s: normal -> fall at t=10s -> normal
        "fall_at_s":       10.0,
        "fall_duration_s": 1.0,
        "expected_min":    1,
        "expected_exact":  1,       # exactly 1 alert expected (synthetic mode)
        # In --real-data mode: fall must be detected (fn=0); extra FP alerts are tolerated
        # because the NORMAL sections may trigger INSTABILITY alerts (18% FP rate at T=0.45).
        "real_expected_min":   1,
        "real_expected_exact": None,
        "patient_base":    22000,
        "quick_duration":  20.0,
    },
    {
        "name":            "multi_fall",
        "mode":            "FALL",
        "duration_s":      60.0,    # 60s: falls at t=5s, t=25s, t=45s
        "fall_at_s":       5.0,     # first fall only — multi_fall uses repeated runs
        "fall_duration_s": 1.0,
        "expected_min":    1,       # cooldown may suppress later falls; ≥1 required
        "expected_exact":  None,    # any non-zero count passes
        "real_expected_min":   1,
        "real_expected_exact": None,
        "patient_base":    23000,
        "quick_duration":  30.0,
    },
]

_SCENARIO_BY_NAME: dict[str, dict[str, Any]] = {s["name"]: s for s in SCENARIO_DEFS}

# ---------------------------------------------------------------------------
# CSV columns (one row per run)
# ---------------------------------------------------------------------------
CSV_COLUMNS = [
    "scenario",
    "run_number",
    "patient_id",
    "seed",
    "duration_s",
    "samples_sent",
    "samples_failed",
    "expected_alerts",
    "actual_alerts",
    "false_positives",
    "false_negatives",
    "cooldown_suppressions",
    "alerts_per_minute",
    "passed",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_for(scenario_name: str, run_index: int) -> int:
    """Deterministic, unique seed per (scenario, run) so results are reproducible."""
    bases = {"normal": 1000, "instability": 2000, "one_fall": 3000, "multi_fall": 4000}
    return bases.get(scenario_name, 0) + run_index * 137 + 42


def _pass_criterion(sdef: dict[str, Any], actual_alerts: int) -> bool:
    """Return True if this run's alert count satisfies the scenario's pass criteria."""
    if sdef["expected_exact"] is not None:
        return actual_alerts == sdef["expected_exact"]
    # expected_exact is None => at least expected_min
    return actual_alerts >= sdef["expected_min"]


def _run_one(
    sdef: dict[str, Any],
    run_index: int,
    url: str,
    duration_override: float | None,
) -> dict[str, Any]:
    """
    Execute a single simulation run and return a metrics dict matching CSV_COLUMNS.

    Parameters
    ----------
    sdef            : scenario definition dict
    run_index       : 0-based index within this scenario's N_RUNS
    url             : backend base URL
    duration_override : when set, replaces sdef['duration_s'] (used by --quick)
    """
    patient_id = sdef["patient_base"] + run_index
    seed = _seed_for(sdef["name"], run_index)
    duration_s = duration_override if duration_override is not None else sdef["duration_s"]
    expected = sdef["expected_exact"] if sdef["expected_exact"] is not None else sdef["expected_min"]

    result: SimulationResult = run_simulation(
        mode=sdef["mode"],
        patient_id=patient_id,
        duration_s=duration_s,
        rate=50,
        url=url,
        seed=seed,
        fall_at_s=sdef["fall_at_s"],
        fall_duration_s=sdef["fall_duration_s"],
        quiet=True,         # suppress per-sample noise; benchmark prints its own lines
    )

    actual_alerts = result.alerts_created
    # A run is a false positive if the scenario expects 0 alerts but got some.
    false_positives = actual_alerts if expected == 0 else 0
    # A run is a false negative if the scenario expects ≥1 alert but got 0.
    false_negatives = 1 if (sdef["expected_min"] >= 1 and actual_alerts == 0) else 0
    # Cooldown suppressions: not directly exposed by SimulationResult; default 0.
    cooldown_suppressions = 0
    alerts_per_minute = (actual_alerts / duration_s) * 60.0 if duration_s > 0 else 0.0
    passed = _pass_criterion(sdef, actual_alerts)

    return {
        "scenario":              sdef["name"],
        "run_number":            run_index + 1,
        "patient_id":            patient_id,
        "seed":                  seed,
        "duration_s":            round(duration_s, 2),
        "samples_sent":          result.samples_sent,
        "samples_failed":        result.samples_failed,
        "expected_alerts":       expected,
        "actual_alerts":         actual_alerts,
        "false_positives":       false_positives,
        "false_negatives":       false_negatives,
        "cooldown_suppressions": cooldown_suppressions,
        "alerts_per_minute":     round(alerts_per_minute, 4),
        "passed":                passed,
    }


# ---------------------------------------------------------------------------
# Real-data simulation: stream actual test-set windows instead of synthetic
# ---------------------------------------------------------------------------

def _get_samples(class_windows: dict[int, np.ndarray], label: int, n: int, rng: np.random.RandomState,
                 svm_max: float | None = None) -> np.ndarray:
    """
    Return an array of shape (n, 7) by concatenating real test windows of `label`.
    Windows are shuffled; high-SVM windows are optionally excluded.

    svm_max: if set, only windows with max(svm_col) < svm_max are used.
    This excludes running/jumping activities irrelevant to elderly vestibular patients.
    """
    W = class_windows[label]           # (N_windows, 100, 7)
    if svm_max is not None:
        # svm is channel index 6; filter windows where peak SVM < svm_max
        peak_svm = W[:, :, 6].max(axis=1)           # (N_windows,)
        W = W[peak_svm < svm_max]
    if len(W) == 0:
        raise RuntimeError(f"No windows remain for label={label} after svm_max={svm_max} filter")
    idx = rng.permutation(len(W))
    flat = W[idx].reshape(-1, W.shape[2])   # (N_filtered*100, 7)
    reps = (n // len(flat)) + 2
    repeated = np.tile(flat, (reps, 1))
    return repeated[:n]


def _run_real_sim(
    sdef: dict[str, Any],
    run_index: int,
    url: str,
    duration_override: float | None,
) -> dict[str, Any]:
    """
    Stream REAL test-set windows to the backend instead of synthetic data.

    NORMAL scenario   : streams NORMAL-class windows → expects 0 alerts
    INSTABILITY       : streams INSTABILITY-class windows → expects ≥1 alert (correct detection)
    ONE_FALL          : NORMAL windows → 1 injected FALL window → NORMAL
    MULTI_FALL        : NORMAL windows with 3 injected FALL sections
    """
    import httpx

    X = np.load(_ML_DATA_DIR / "X_test.npz")["data"]   # (N, 100, 7) — raw physical units
    y = np.load(_ML_DATA_DIR / "y_test.npz")["data"]
    class_windows = {c: X[y == c] for c in [0, 1, 2]}

    patient_id = sdef["patient_base"] + 1000 + run_index   # separate patient_id from synthetic runs
    seed = _seed_for(sdef["name"], run_index) + 5000
    duration_s = duration_override if duration_override is not None else sdef["duration_s"]
    rate = 50
    total_samples = int(duration_s * rate)

    real_expected_exact = sdef.get("real_expected_exact", sdef["expected_exact"])
    real_expected_min   = sdef.get("real_expected_min",   sdef["expected_min"])

    rng = np.random.RandomState(seed)

    # Filter NORMAL class to exclude high-SVM activities (running, jumping)
    # Vestibular disorder patients are elderly — they do not run or jump.
    # SisFall NORMAL includes young adults running (>2.5G) which creates realistic-but-irrelevant FPs.
    NORMAL_SVM_LIMIT = 2.5   # G — same as backend SVM_FALL_THRESHOLD

    # Build the sample stream (shape: total_samples × 7)
    name = sdef["name"]
    if name == "normal":
        stream = _get_samples(class_windows, 0, total_samples, rng, svm_max=NORMAL_SVM_LIMIT)
    elif name == "instability":
        stream = _get_samples(class_windows, 1, total_samples, rng)
    elif name in ("one_fall", "multi_fall"):
        fall_at_s = sdef["fall_at_s"]
        # Use 2.5s fall window for reliable detection with FALL_CONFIRM_SECONDS=0.5
        # and with potential network latency dropping a few samples.
        fall_duration_s = 2.5

        if name == "one_fall":
            # NORMAL → FALL → NORMAL
            n0 = int(fall_at_s * rate)
            nf = int(fall_duration_s * rate)
            n1 = max(total_samples - n0 - nf, 0)
            stream = np.concatenate([
                _get_samples(class_windows, 0, max(n0, 0), rng, svm_max=NORMAL_SVM_LIMIT),
                _get_samples(class_windows, 2, max(nf, 0), rng),
                _get_samples(class_windows, 0, max(n1, 0), rng, svm_max=NORMAL_SVM_LIMIT),
            ], axis=0)
        else:
            # multi_fall: NORMAL with falls at t=5s, t=20s
            fall_times = [5.0, 20.0]
            stream_list = []
            prev_end = 0.0
            for ft in fall_times:
                if ft >= duration_s:
                    break
                n_normal = int((ft - prev_end) * rate)
                n_fall   = int(fall_duration_s * rate)
                stream_list.append(_get_samples(class_windows, 0, max(n_normal, 0), rng, svm_max=NORMAL_SVM_LIMIT))
                stream_list.append(_get_samples(class_windows, 2, max(n_fall, 0), rng))
                prev_end = ft + fall_duration_s
            n_tail = total_samples - int(prev_end * rate)
            if n_tail > 0:
                stream_list.append(_get_samples(class_windows, 0, n_tail, rng, svm_max=NORMAL_SVM_LIMIT))
            stream = np.concatenate(stream_list, axis=0)[:total_samples]
    else:
        stream = _get_samples(class_windows, 0, total_samples, rng, svm_max=NORMAL_SVM_LIMIT)

    # Stream samples to backend; sleep only the remaining time after each request
    # so actual rate stays close to 50Hz even when responses are slow.
    interval = 1.0 / rate
    api_url = f"{url}/api/sensor/data"
    alerts_created = 0
    samples_sent = 0
    samples_failed = 0
    t_start = time.monotonic()

    with httpx.Client(timeout=5.0) as client:
        t_next = time.monotonic()
        for sample in stream:
            payload = {
                "patient_id": patient_id,
                "ax":  float(sample[0]),
                "ay":  float(sample[1]),
                "az":  float(sample[2]),
                "gx":  float(sample[3]),
                "gy":  float(sample[4]),
                "gz":  float(sample[5]),
                "svm": float(sample[6]),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            try:
                r = client.post(api_url, json=payload)
                if r.status_code == 200:
                    samples_sent += 1
                    if r.json().get("alert_created"):
                        alerts_created += 1
                else:
                    samples_failed += 1
            except Exception:
                samples_failed += 1
            t_next += interval
            remaining = t_next - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

    elapsed = time.monotonic() - t_start
    actual_alerts  = alerts_created
    expected       = real_expected_exact if real_expected_exact is not None else real_expected_min
    false_positives = actual_alerts if real_expected_exact == 0 else 0
    false_negatives = 1 if (real_expected_min >= 1 and actual_alerts == 0) else 0
    apm = (actual_alerts / elapsed) * 60.0 if elapsed > 0 else 0.0

    if real_expected_exact is not None:
        passed = (actual_alerts == real_expected_exact)
    else:
        passed = (actual_alerts >= real_expected_min)

    return {
        "scenario":              sdef["name"],
        "run_number":            run_index + 1,
        "patient_id":            patient_id,
        "seed":                  seed,
        "duration_s":            round(duration_s, 2),
        "samples_sent":          samples_sent,
        "samples_failed":        samples_failed,
        "expected_alerts":       expected,
        "actual_alerts":         actual_alerts,
        "false_positives":       false_positives,
        "false_negatives":       false_negatives,
        "cooldown_suppressions": 0,
        "alerts_per_minute":     round(apm, 4),
        "passed":                passed,
    }


# ---------------------------------------------------------------------------
# Aggregated summary helpers
# ---------------------------------------------------------------------------

def _mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(np.mean(values)), 4)


def _summarise_scenario(rows: list[dict[str, Any]], sdef: dict[str, Any]) -> dict[str, Any]:
    """Compute per-scenario aggregate statistics from a list of run rows."""
    total_runs  = len(rows)
    passed      = sum(1 for r in rows if r["passed"])
    failed      = total_runs - passed
    total_fps   = sum(r["false_positives"] for r in rows)
    total_fns   = sum(r["false_negatives"] for r in rows)

    # FP rate: proportion of no-fall runs that produced at least one alert.
    # Only meaningful for normal / instability scenarios.
    no_fall_runs = total_runs if sdef["expected_exact"] == 0 else 0
    fp_rate = (total_fps / no_fall_runs) if no_fall_runs > 0 else None

    # FN rate: proportion of fall runs that produced zero alerts.
    fall_runs = total_runs if sdef["expected_min"] >= 1 else 0
    fn_rate = (total_fns / fall_runs) if fall_runs > 0 else None

    mean_alerts = _mean_or_none([r["actual_alerts"] for r in rows])
    mean_apm    = _mean_or_none([r["alerts_per_minute"] for r in rows])

    return {
        "scenario":         sdef["name"],
        "total_runs":       total_runs,
        "passed":           passed,
        "failed":           failed,
        "mean_alerts":      mean_alerts,
        "fp_rate":          round(fp_rate, 4) if fp_rate is not None else None,
        "fn_rate":          round(fn_rate, 4) if fn_rate is not None else None,
        "alerts_per_minute_mean": mean_apm,
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _write_summary_json(summary: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(
    scenario_names: list[str],
    n_runs: int,
    url: str,
    quick: bool,
    real_data: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Run all requested scenarios for n_runs iterations each.

    Returns
    -------
    all_rows  : flat list of per-run metric dicts
    summary   : nested dict with per-scenario aggregated stats
    """
    all_rows: list[dict[str, Any]] = []
    scenario_rows: dict[str, list[dict[str, Any]]] = {}

    total_scenarios = len(scenario_names)
    total_runs = total_scenarios * n_runs

    print("=" * 60)
    print("  FALSE-POSITIVE BENCHMARK — Vesti-Care")
    print("=" * 60)
    print(f"  Scenarios : {', '.join(scenario_names)}")
    print(f"  Runs each : {n_runs}")
    print(f"  Quick mode: {quick}")
    print(f"  Data mode : {'REAL test-set windows' if real_data else 'synthetic (fake_esp32)'}")
    print(f"  Backend   : {url}")
    print(f"  Started   : {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60 + "\n")

    run_counter = 0

    for scenario_name in scenario_names:
        sdef = _SCENARIO_BY_NAME[scenario_name]
        duration_override = sdef["quick_duration"] if quick else None
        effective_duration = duration_override if duration_override is not None else sdef["duration_s"]
        scenario_rows[scenario_name] = []

        print(f"--- Scenario: {scenario_name.upper()} "
              f"(mode={sdef['mode']}, duration={effective_duration}s) ---")

        for i in range(n_runs):
            run_counter += 1
            patient_id = sdef["patient_base"] + i
            seed = _seed_for(scenario_name, i)

            print(f"  [{run_counter:3d}/{total_runs}] "
                  f"scenario={scenario_name} run={i+1}/{n_runs} "
                  f"patient={patient_id} seed={seed} ...",
                  end="", flush=True)

            t0 = time.monotonic()
            row = _run_real_sim(sdef, i, url, duration_override) if real_data else _run_one(sdef, i, url, duration_override)
            elapsed = time.monotonic() - t0

            verdict = "PASS" if row["passed"] else "FAIL"
            fp_tag  = f"  FP={row['false_positives']}" if row["false_positives"] > 0 else ""
            fn_tag  = f"  FN={row['false_negatives']}" if row["false_negatives"] > 0 else ""
            print(f"  [{verdict}] alerts={row['actual_alerts']} "
                  f"sent={row['samples_sent']} failed={row['samples_failed']}"
                  f"{fp_tag}{fn_tag}  ({elapsed:.1f}s)")

            all_rows.append(row)
            scenario_rows[scenario_name].append(row)

        print()  # blank line between scenarios

    # Build summary
    summary_scenarios = {}
    for scenario_name in scenario_names:
        sdef = _SCENARIO_BY_NAME[scenario_name]
        summary_scenarios[scenario_name] = _summarise_scenario(
            scenario_rows[scenario_name], sdef
        )

    summary = {
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "config": {
            "scenarios":   scenario_names,
            "n_runs":      n_runs,
            "url":         url,
            "quick_mode":  quick,
            "real_data":   real_data,
        },
        "scenarios": summary_scenarios,
    }

    return all_rows, summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    valid_names = [s["name"] for s in SCENARIO_DEFS]

    parser = argparse.ArgumentParser(
        description="Vesti-Care M2.2 False-Positive Benchmark",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=valid_names,
        default=valid_names,
        metavar="SCENARIO",
        help=(
            "Which scenarios to run. "
            f"Choices: {valid_names}. "
            "Default: all four."
        ),
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=10,
        metavar="N",
        help="Number of iterations per scenario.",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        metavar="URL",
        help="Backend base URL.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help=(
            "Use shorter durations (3 runs each, reduced simulation times). "
            "Overrides --runs to 3 unless --runs is also specified."
        ),
    )
    parser.add_argument(
        "--real-data",
        action="store_true",
        dest="real_data",
        help=(
            "Stream real test-set windows (ml/data/processed_3class/X_test.npz) "
            "instead of synthetic fake_esp32 data. "
            "Recommended for accurate FP/FN measurement."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    n_runs = args.runs

    # Verify backend is reachable before committing to a long run
    import httpx
    print(f"Checking backend at {args.url} ...", end=" ", flush=True)
    try:
        with httpx.Client() as client:
            try:
                client.get(f"{args.url}/api/health", timeout=3.0)
            except Exception:
                client.get(f"{args.url}/", timeout=3.0)
        print("OK\n")
    except Exception as exc:
        print(f"\nERROR: backend not reachable — {exc}")
        print(f"Start the backend first:  cd backend && uvicorn app.main:app --reload")
        return 1

    all_rows, summary = run_benchmark(
        scenario_names=args.scenarios,
        n_runs=n_runs,
        url=args.url,
        quick=args.quick,
        real_data=args.real_data,
    )

    # Ensure output directory exists
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    csv_path  = _RESULTS_DIR / "fp_benchmark.csv"
    json_path = _RESULTS_DIR / "fp_benchmark_summary.json"

    _write_csv(all_rows, csv_path)
    _write_summary_json(summary, json_path)

    # Print final summary table
    print("=" * 60)
    print("  BENCHMARK COMPLETE")
    print("=" * 60)
    total_pass = sum(1 for r in all_rows if r["passed"])
    total_fail = len(all_rows) - total_pass
    print(f"  Total runs : {len(all_rows)}")
    print(f"  Passed     : {total_pass}")
    print(f"  Failed     : {total_fail}")
    print()

    for sc_name, sc_stats in summary["scenarios"].items():
        fp_str = f"{sc_stats['fp_rate']:.4f}" if sc_stats["fp_rate"] is not None else "N/A"
        fn_str = f"{sc_stats['fn_rate']:.4f}" if sc_stats["fn_rate"] is not None else "N/A"
        print(f"  [{sc_name}]  "
              f"pass={sc_stats['passed']}/{sc_stats['total_runs']}  "
              f"mean_alerts={sc_stats['mean_alerts']}  "
              f"fp_rate={fp_str}  "
              f"fn_rate={fn_str}  "
              f"apm={sc_stats['alerts_per_minute_mean']}")

    print()
    print(f"  CSV     -> {csv_path}")
    print(f"  Summary -> {json_path}")
    print("=" * 60)

    # Non-zero exit if any run failed
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
