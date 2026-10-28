"""
latency_benchmark.py

Do latency cua backend qua 30 lan chay simulation FALL.

Yeu cau: Backend dang chay tai http://localhost:8000

Chay:
    python backend/tools/latency_benchmark.py

Ket qua duoc in ra man hinh va luu vao results_latency.json
"""
from __future__ import annotations

import csv
import json
import math
import os
import platform
import random
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

# ===========================================================================
# CAU HINH
# ===========================================================================
BACKEND_URL   = "http://localhost:8000/api/sensor/data"
N_RUNS        = 30          # so lan lap
PATIENT_BASE  = 9000        # patient_id bat dau tu day de tranh conflict
WINDOW_SIZE   = 100         # phai khop voi backend config
NORMAL_BEFORE = 115         # so NORMAL samples gui truoc (>= WINDOW_SIZE)

SVM_NORMAL    = 1.0         # g
SVM_FALL      = 3.5         # g (tren nguong 2.5G)

OUTPUT_FILE   = Path(__file__).resolve().parent.parent.parent / "results_latency.json"
RESULTS_DIR   = Path(__file__).resolve().parent.parent.parent / "results"
CSV_FILE      = RESULTS_DIR / "system_latency.csv"


# ===========================================================================
# HELPERS
# ===========================================================================

def make_sample(patient_id: int, svm: float, rng: random.Random) -> dict:
    """Tao payload cam bien gia lap voi SVM cho truoc."""
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
        "ax": round(ax, 4), "ay": round(ay, 4), "az": round(az, 4),
        "gx": round(gx, 4), "gy": round(gy, 4), "gz": round(gz, 4),
        "svm": round(max(0.5, svm), 4),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def post_sample(client: httpx.Client, payload: dict) -> dict | None:
    try:
        resp = client.post(BACKEND_URL, json=payload, timeout=10.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"_error": str(e)}


# ===========================================================================
# BENCHMARK
# ===========================================================================

def run_benchmark() -> dict:
    collected = {
        "backend_processing_latency_ms": [],
        "inference_latency_ms":          [],
        "prediction_to_alert_ms":        [],
        "alert_to_telegram_ms":          [],
        "total_from_request_ms":         [],
    }
    alerts_found = 0
    runs_no_alert = []
    # Per-run rows for CSV: one row per FALL run
    run_rows = []

    print(f"Backend : {BACKEND_URL}")
    print(f"Runs    : {N_RUNS}")
    print(f"Strategy: {NORMAL_BEFORE} NORMAL samples -> FALL samples until alert")
    print("-" * 55)

    with httpx.Client() as client:

        # Kiem tra backend co chay khong
        try:
            client.get("http://localhost:8000/api/health", timeout=3.0)
        except Exception:
            # Thu endpoint khac
            try:
                client.get("http://localhost:8000/", timeout=3.0)
            except Exception:
                print("\nLOI: Backend khong chay tai localhost:8000")
                print("Hay chay backend truoc: cd backend && uvicorn app.main:app --reload")
                sys.exit(1)

        for run in range(N_RUNS):
            pid = PATIENT_BASE + run
            rng = random.Random(run * 137 + 42)

            # --- Phase 1: gui NORMAL de lap day buffer ---
            for _ in range(NORMAL_BEFORE):
                svm = max(0.8, SVM_NORMAL + rng.gauss(0, 0.05))
                body = post_sample(client, make_sample(pid, svm, rng))
                if body and "_error" not in body and body.get("window_ready") and "timing" in body:
                    t = body["timing"]
                    collected["backend_processing_latency_ms"].append(
                        t.get("backend_processing_latency_ms", 0)
                    )
                    collected["inference_latency_ms"].append(
                        t.get("inference_latency_ms", 0)
                    )

            # --- Phase 2: gui FALL cho den khi co alert ---
            alert_found = False
            run_backend_ms = None
            run_inference_ms = None
            run_pred_alert_ms = None
            run_alert_tg_ms = None
            run_total_ms = None

            for _ in range(30):  # toi da 30 FALL samples
                svm = max(2.6, SVM_FALL + rng.gauss(0, 0.1))
                body = post_sample(client, make_sample(pid, svm, rng))
                if body and "_error" not in body and body.get("window_ready") and "timing" in body:
                    t = body["timing"]
                    run_backend_ms = t.get("backend_processing_latency_ms", 0)
                    run_inference_ms = t.get("inference_latency_ms", 0)
                    collected["backend_processing_latency_ms"].append(run_backend_ms)
                    collected["inference_latency_ms"].append(run_inference_ms)
                    if body.get("alert_created") and not alert_found:
                        alert_found = True
                        alerts_found += 1
                        run_pred_alert_ms = t.get("prediction_to_alert_ms")
                        run_alert_tg_ms = t.get("alert_to_telegram_ms")
                        run_total_ms = t.get("total_from_request_ms")
                        if run_pred_alert_ms is not None:
                            collected["prediction_to_alert_ms"].append(run_pred_alert_ms)
                        if run_alert_tg_ms is not None:
                            collected["alert_to_telegram_ms"].append(run_alert_tg_ms)
                        if run_total_ms is not None:
                            collected["total_from_request_ms"].append(run_total_ms)
                if alert_found:
                    break

            status = "ALERT ✓" if alert_found else "no alert ✗"
            if not alert_found:
                runs_no_alert.append(run + 1)
            print(f"  Run {run+1:2d}/{N_RUNS} | patient={pid} | {status}")

            run_rows.append({
                "run_number":             run + 1,
                "patient_id":             pid,
                "scenario":               "FALL",
                "alert_found":            alert_found,
                "backend_processing_ms":  run_backend_ms,
                "inference_ms":           run_inference_ms,
                "prediction_to_alert_ms": run_pred_alert_ms,
                "alert_to_telegram_ms":   run_alert_tg_ms,
                "total_ms":               run_total_ms,
            })

    return collected, alerts_found, runs_no_alert, run_rows


# ===========================================================================
# STATS
# ===========================================================================

def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "std": None, "median": None, "p95": None, "min": None, "max": None}
    arr = np.array(values)
    return {
        "n":      int(len(arr)),
        "mean":   round(float(arr.mean()), 3),
        "std":    round(float(arr.std()), 3),
        "median": round(float(np.median(arr)), 3),
        "p95":    round(float(np.percentile(arr, 95)), 3),
        "min":    round(float(arr.min()), 3),
        "max":    round(float(arr.max()), 3),
    }


def print_stats(label: str, s: dict):
    if s["n"] == 0:
        print(f"  {label}: khong co du lieu")
        return
    print(f"  {label}:")
    print(f"    n      = {s['n']}")
    print(f"    mean   = {s['mean']:.3f} ms")
    print(f"    std    = {s['std']:.3f} ms")
    print(f"    median = {s['median']:.3f} ms")
    print(f"    p95    = {s['p95']:.3f} ms")
    print(f"    min    = {s['min']:.3f} ms")
    print(f"    max    = {s['max']:.3f} ms")


# ===========================================================================
# MAIN
# ===========================================================================

def _git_commit() -> str:
    """Lay git commit hash hien tai (7 ky tu), tra ve 'unknown' neu that bai."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def _machine_info() -> dict:
    """Thu thap thong tin may chu va moi truong."""
    return {
        "hostname":       socket.gethostname(),
        "platform":       platform.platform(),
        "cpu_count":      os.cpu_count(),
        "python_version": sys.version,
        "git_commit":     _git_commit(),
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    }


def _save_csv(run_rows: list[dict]) -> None:
    """Luu ket qua tung run vao CSV."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_number", "patient_id", "scenario", "alert_found",
        "backend_processing_ms", "inference_ms",
        "prediction_to_alert_ms", "alert_to_telegram_ms", "total_ms",
    ]
    with CSV_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(run_rows)


def main():
    print("=" * 55)
    print("  LATENCY BENCHMARK — Vesti-Care Backend")
    print("=" * 55 + "\n")

    collected, alerts_found, runs_no_alert, run_rows = run_benchmark()

    summary = {metric: stats(values) for metric, values in collected.items()}

    print("\n" + "=" * 55)
    print("  KET QUA")
    print("=" * 55)
    print(f"\n  Alerts duoc tao: {alerts_found}/{N_RUNS} runs")
    if runs_no_alert:
        print(f"  Runs khong co alert: {runs_no_alert}")

    print()
    print_stats("backend_processing_latency_ms", summary["backend_processing_latency_ms"])
    print()
    print_stats("inference_latency_ms",          summary["inference_latency_ms"])
    print()
    print_stats("prediction_to_alert_ms",        summary["prediction_to_alert_ms"])
    print()
    print_stats("alert_to_telegram_ms",          summary["alert_to_telegram_ms"])
    print()
    print_stats("total_from_request_ms",         summary["total_from_request_ms"])

    # Ket qua cho bai bao
    bp = summary["backend_processing_latency_ms"]
    if bp["mean"] is not None:
        print("\n" + "-" * 55)
        print("  KET QUA CHO BAI BAO (dien vao Table 5):")
        print(f"  backend_processing_latency: {bp['mean']:.1f} ± {bp['std']:.1f} ms")
        pt = summary["prediction_to_alert_ms"]
        if pt["mean"] is not None:
            print(f"  prediction_to_alert:        {pt['mean']:.1f} ± {pt['std']:.1f} ms")
        tg = summary["alert_to_telegram_ms"]
        if tg["mean"] is not None:
            print(f"  alert_to_telegram:          {tg['mean']:.1f} ± {tg['std']:.1f} ms")
        tr = summary["total_from_request_ms"]
        if tr["mean"] is not None:
            print(f"  total_from_request:         {tr['mean']:.1f} ± {tr['std']:.1f} ms")

    # Luu CSV
    _save_csv(run_rows)
    print(f"\n  Da luu CSV: {CSV_FILE}")

    # Luu JSON (backward compat: van luu results_latency.json tai root)
    machine = _machine_info()
    output = {
        "timestamp": machine["timestamp"],
        "environment": machine,
        "config": {
            "n_runs":        N_RUNS,
            "patient_base":  PATIENT_BASE,
            "normal_before": NORMAL_BEFORE,
            "svm_fall":      SVM_FALL,
        },
        "alerts_found": alerts_found,
        "runs_no_alert": runs_no_alert,
        "stats": summary,
        "raw": {k: v for k, v in collected.items()},
    }
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"  Da luu JSON: {OUTPUT_FILE.name}")
    print("=" * 55)


if __name__ == "__main__":
    main()
