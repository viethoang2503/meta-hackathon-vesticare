"""
soak_test.py

Continuous ingestion soak test for M2.4 — Vesti-Care Backend.

Runs mixed-traffic cycles for a configurable duration (default 30 min),
collecting system health checkpoints every 60 seconds.

Traffic pattern per cycle (150 s total):
    60 s  NORMAL            (SVM ~1.0 g)
    20 s  VESTIBULAR_SWAY   (SVM ~1.8 g)
    60 s  NORMAL            (SVM ~1.0 g)
    10 s  FALL              (SVM ~3.5 g peak)

Usage:
    python backend/tools/soak_test.py                    # 30 min default
    python backend/tools/soak_test.py --duration 1800    # 30 min explicit
    python backend/tools/soak_test.py --duration 7200    # 2 hours (paper target)
    python backend/tools/soak_test.py --duration 120     # 2 min quick test
    python backend/tools/soak_test.py --url http://localhost:8000
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Optional psutil
# ---------------------------------------------------------------------------

try:
    import psutil as _psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SAMPLE_RATE_HZ      = 50            # target sample rate
SAMPLE_INTERVAL     = 1.0 / SAMPLE_RATE_HZ  # 0.02 s

# Cycle durations (seconds)
PHASE_NORMAL1       = 60
PHASE_SWAY          = 20
PHASE_NORMAL2       = 60
PHASE_FALL          = 10
CYCLE_DURATION      = PHASE_NORMAL1 + PHASE_SWAY + PHASE_NORMAL2 + PHASE_FALL  # 150 s

CHECKPOINT_INTERVAL = 60.0          # seconds between health checkpoints

# SVM values (match fake_esp32.py constants)
SVM_NORMAL          = 1.0
SVM_NORMAL_NOISE    = 0.05
SVM_SWAY            = 1.8
SVM_SWAY_NOISE      = 0.15
SVM_FALL_PEAK       = 3.5
SVM_FALL_NOISE      = 0.10

DEFAULT_BACKEND_URL = "http://localhost:8000"
SENSOR_ENDPOINT     = "/api/sensor/data"
HEALTH_ENDPOINT     = "/api/health"

RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "results"
JSON_OUTPUT  = RESULTS_DIR / "soak_test.json"
CSV_OUTPUT   = RESULTS_DIR / "soak_test_checkpoints.csv"

CSV_FIELDS = [
    "checkpoint_number",
    "elapsed_s",
    "requests_sent",
    "requests_failed",
    "success_rate",
    "effective_sample_rate",
    "alerts_created",
    "memory_mb",
    "cpu_percent",
    "backend_latency_mean_ms",
]

# ---------------------------------------------------------------------------
# Signal generation (mirrors latency_benchmark.py / fake_esp32.py)
# ---------------------------------------------------------------------------

def make_sample(patient_id: int, svm: float, rng: random.Random) -> dict:
    """Generate a realistic sensor payload for the given SVM magnitude."""
    az = svm * 0.85
    rem = max(0.0, svm ** 2 - az ** 2)
    ax = math.sqrt(rem / 2) * rng.choice([-1, 1]) + rng.gauss(0, 0.02)
    ay = math.sqrt(rem / 2) * rng.choice([-1, 1]) + rng.gauss(0, 0.02)
    scale = 5.0 if svm < 2.0 else 80.0
    gx = rng.uniform(-scale, scale)
    gy = rng.uniform(-scale, scale)
    gz = rng.uniform(-scale * 0.3, scale * 0.3)
    return {
        "patient_id": patient_id,
        "ax": round(ax, 4),
        "ay": round(ay, 4),
        "az": round(az, 4),
        "gx": round(gx, 4),
        "gy": round(gy, 4),
        "gz": round(gz, 4),
        "svm": round(max(0.5, svm), 4),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def svm_for_cycle_offset(offset_s: float, rng: random.Random) -> float:
    """Return the SVM value for the given offset within a 150 s cycle."""
    if offset_s < PHASE_NORMAL1:
        # NORMAL phase 1
        return max(0.8, SVM_NORMAL + rng.gauss(0, SVM_NORMAL_NOISE))
    elif offset_s < PHASE_NORMAL1 + PHASE_SWAY:
        # SWAY phase
        return max(1.3, SVM_SWAY + rng.gauss(0, SVM_SWAY_NOISE))
    elif offset_s < PHASE_NORMAL1 + PHASE_SWAY + PHASE_NORMAL2:
        # NORMAL phase 2
        return max(0.8, SVM_NORMAL + rng.gauss(0, SVM_NORMAL_NOISE))
    else:
        # FALL phase
        return max(2.6, SVM_FALL_PEAK + rng.gauss(0, SVM_FALL_NOISE))


# ---------------------------------------------------------------------------
# psutil helpers
# ---------------------------------------------------------------------------

def _get_memory_mb() -> Optional[float]:
    if not _PSUTIL_AVAILABLE:
        return None
    try:
        return _psutil.Process().memory_info().rss / 1024 ** 2
    except Exception:
        return None


def _get_cpu_percent() -> Optional[float]:
    if not _PSUTIL_AVAILABLE:
        return None
    try:
        return _psutil.Process().cpu_percent(interval=None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _safe_mean(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _safe_min(values: list[float]) -> Optional[float]:
    return min(values) if values else None


def _safe_max(values: list[float]) -> Optional[float]:
    return max(values) if values else None


# ---------------------------------------------------------------------------
# Checkpoint printer
# ---------------------------------------------------------------------------

def print_checkpoint(cp: dict) -> None:
    print(
        f"\n[CHECKPOINT #{cp['checkpoint_number']:3d}] "
        f"elapsed={cp['elapsed_s']:.0f}s | "
        f"sent={cp['requests_sent']} | "
        f"failed={cp['requests_failed']} | "
        f"success={cp['success_rate']:.1%} | "
        f"rate={cp['effective_sample_rate']:.1f} Hz | "
        f"alerts={cp['alerts_created']}"
    )
    if cp.get("memory_mb") is not None:
        print(f"             memory={cp['memory_mb']:.1f} MB | cpu={cp.get('cpu_percent', '?')}%")
    if cp.get("backend_latency_mean_ms") is not None:
        print(f"             backend_latency_mean={cp['backend_latency_mean_ms']:.1f} ms")


# ---------------------------------------------------------------------------
# Main soak runner
# ---------------------------------------------------------------------------

def run_soak(duration_s: float, url: str) -> dict:
    """
    Run the soak test for `duration_s` seconds against `url`.
    Returns a summary dict with embedded checkpoint list.
    """
    sensor_url = url.rstrip("/") + SENSOR_ENDPOINT

    # Verify backend is reachable before starting
    health_url = url.rstrip("/") + HEALTH_ENDPOINT
    with httpx.Client(timeout=5.0) as probe:
        try:
            probe.get(health_url)
        except Exception:
            # Try root endpoint as fallback
            try:
                probe.get(url.rstrip("/") + "/")
            except Exception as exc:
                print(f"\nERROR: Backend not reachable at {url}")
                print(f"       {exc}")
                print("Start the backend first: cd backend && uvicorn app.main:app --reload")
                sys.exit(1)

    print("=" * 65)
    print("  SOAK TEST — Vesti-Care Backend (M2.4)")
    print("=" * 65)
    print(f"  Backend  : {url}")
    print(f"  Duration : {duration_s:.0f} s ({duration_s / 60:.1f} min)")
    print(f"  Rate     : {SAMPLE_RATE_HZ} Hz")
    print(f"  Cycle    : {PHASE_NORMAL1}s NORMAL → {PHASE_SWAY}s SWAY → "
          f"{PHASE_NORMAL2}s NORMAL → {PHASE_FALL}s FALL ({CYCLE_DURATION}s/cycle)")
    print(f"  psutil   : {'available' if _PSUTIL_AVAILABLE else 'not installed (skipping memory/CPU)'}")
    print("=" * 65 + "\n")

    # Warm up cpu_percent measurement (first call always returns 0.0)
    if _PSUTIL_AVAILABLE:
        _psutil.Process().cpu_percent(interval=None)

    rng = random.Random(42)

    total_sent    = 0
    total_failed  = 0
    total_alerts  = 0
    latencies_ms: list[float] = []   # backend_processing_latency_ms across whole test

    checkpoints: list[dict] = []
    checkpoint_latencies: list[float] = []   # latencies since last checkpoint

    # For checkpoint deltas
    cp_sent_at_last   = 0
    cp_failed_at_last = 0
    cp_alerts_at_last = 0

    start_time       = time.monotonic()
    next_checkpoint  = start_time + CHECKPOINT_INTERVAL
    checkpoint_number = 0

    initial_memory_mb = _get_memory_mb()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc).isoformat()

    try:
        with httpx.Client(timeout=10.0) as client:
            sample_count = 0
            cycle_number = 0

            while True:
                now = time.monotonic()
                elapsed = now - start_time

                if elapsed >= duration_s:
                    break

                # Determine which cycle and phase we are in
                cycle_offset = elapsed % CYCLE_DURATION
                if elapsed > 0 and int(elapsed / CYCLE_DURATION) > cycle_number:
                    cycle_number = int(elapsed / CYCLE_DURATION)

                patient_id = 30000 + cycle_number

                svm = svm_for_cycle_offset(cycle_offset, rng)
                payload = make_sample(patient_id, svm, rng)

                # Accurate timing: compute when next sample should fire
                next_sample_time = start_time + (sample_count + 1) * SAMPLE_INTERVAL

                # Send sample
                req_start = time.monotonic()
                try:
                    resp = client.post(sensor_url, json=payload)
                    resp.raise_for_status()
                    body = resp.json()
                    total_sent += 1

                    # Collect backend latency if available
                    timing = body.get("timing") or {}
                    bl = timing.get("backend_processing_latency_ms")
                    if bl is not None:
                        latencies_ms.append(float(bl))
                        checkpoint_latencies.append(float(bl))

                    if body.get("alert_created"):
                        total_alerts += 1

                except Exception as exc:
                    total_failed += 1
                    _ = exc  # suppress unused warning

                sample_count += 1

                # Checkpoint?
                now_after = time.monotonic()
                if now_after >= next_checkpoint:
                    elapsed_cp = now_after - start_time
                    sent_delta   = total_sent   - cp_sent_at_last
                    failed_delta = total_failed - cp_failed_at_last
                    total_at_cp  = sent_delta + failed_delta
                    success_rate = (sent_delta / total_at_cp) if total_at_cp > 0 else 0.0
                    effective_rate = total_sent / elapsed_cp if elapsed_cp > 0 else 0.0

                    checkpoint_number += 1
                    cp = {
                        "checkpoint_number":        checkpoint_number,
                        "elapsed_s":                round(elapsed_cp, 1),
                        "requests_sent":            total_sent,
                        "requests_failed":          total_failed,
                        "success_rate":             round(success_rate, 4),
                        "effective_sample_rate":    round(effective_rate, 3),
                        "alerts_created":           total_alerts,
                        "memory_mb":                round(_get_memory_mb(), 2) if _get_memory_mb() is not None else None,
                        "cpu_percent":              _get_cpu_percent(),
                        "backend_latency_mean_ms":  round(_safe_mean(checkpoint_latencies), 3)
                                                    if checkpoint_latencies else None,
                    }
                    checkpoints.append(cp)
                    print_checkpoint(cp)

                    cp_sent_at_last   = total_sent
                    cp_failed_at_last = total_failed
                    cp_alerts_at_last = total_alerts
                    checkpoint_latencies = []
                    next_checkpoint += CHECKPOINT_INTERVAL

                # Accurate rate control — sleep until next slot
                sleep_for = next_sample_time - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)

    except KeyboardInterrupt:
        print("\n\n[soak_test] Interrupted by user — saving partial results...")

    # Build final summary
    finished_at  = datetime.now(timezone.utc).isoformat()
    total_elapsed = time.monotonic() - start_time
    final_memory  = _get_memory_mb()
    memory_growth = (
        round(final_memory - initial_memory_mb, 2)
        if (final_memory is not None and initial_memory_mb is not None)
        else None
    )
    total_requests = total_sent + total_failed
    overall_success = (total_sent / total_requests) if total_requests > 0 else 0.0

    summary = {
        "started_at":           started_at,
        "finished_at":          finished_at,
        "config": {
            "target_duration_s":    duration_s,
            "backend_url":          url,
            "sample_rate_hz":       SAMPLE_RATE_HZ,
            "cycle_duration_s":     CYCLE_DURATION,
            "checkpoint_interval_s": CHECKPOINT_INTERVAL,
        },
        "total_duration_s":         round(total_elapsed, 2),
        "total_requests":           total_requests,
        "total_sent":               total_sent,
        "total_failed":             total_failed,
        "overall_success_rate":     round(overall_success, 4),
        "total_alerts":             total_alerts,
        "memory_growth_mb":         memory_growth,
        "backend_latency_ms": {
            "min":  round(_safe_min(latencies_ms), 3) if latencies_ms else None,
            "mean": round(_safe_mean(latencies_ms), 3) if latencies_ms else None,
            "max":  round(_safe_max(latencies_ms), 3) if latencies_ms else None,
            "n":    len(latencies_ms),
        },
        "checkpoints": checkpoints,
    }

    return summary


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def save_json(summary: dict) -> None:
    JSON_OUTPUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n  JSON saved  : {JSON_OUTPUT}")


def save_csv(checkpoints: list[dict]) -> None:
    with CSV_OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(checkpoints)
    print(f"  CSV saved   : {CSV_OUTPUT}")


def print_final_summary(summary: dict) -> None:
    sep = "=" * 65
    print("\n" + sep)
    print("  SOAK TEST COMPLETE — Final Summary")
    print(sep)
    print(f"  Duration          : {summary['total_duration_s']:.1f} s "
          f"({summary['total_duration_s'] / 60:.1f} min)")
    print(f"  Total requests    : {summary['total_requests']}")
    print(f"  Failed requests   : {summary['total_failed']}")
    print(f"  Success rate      : {summary['overall_success_rate']:.2%}")
    print(f"  Total alerts      : {summary['total_alerts']}")
    if summary.get("memory_growth_mb") is not None:
        print(f"  Memory growth     : {summary['memory_growth_mb']:.1f} MB")
    lat = summary["backend_latency_ms"]
    if lat["mean"] is not None:
        print(f"  Latency (min/mean/max): "
              f"{lat['min']:.1f} / {lat['mean']:.1f} / {lat['max']:.1f} ms "
              f"(n={lat['n']})")
    print(f"  Checkpoints       : {len(summary['checkpoints'])}")
    print(sep)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vesti-Care Soak Test — continuous ingestion health monitor (M2.4)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--duration", type=float, default=1800.0, metavar="SECONDS",
        help="Total test duration in seconds (1800 = 30 min, 7200 = 2 h)",
    )
    parser.add_argument(
        "--url", default=DEFAULT_BACKEND_URL,
        help="Backend base URL",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = run_soak(duration_s=args.duration, url=args.url)
    print_final_summary(summary)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    save_json(summary)
    save_csv(summary["checkpoints"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
