"""
scenario_one_fall.py

Kich ban 2: Mot su kien nga duy nhat.

Muc tieu:
    Normal stream trong 5 giay (day buffer) -> 1 spike nga -> tiep tuc normal.
    He thong phai tao dung 1 canh bao.

Pass khi:
    alerts_created == 1
    alert latency duoc ghi lai (thoi gian tu khi tiem nga den khi canh bao duoc tra ve)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

SIMULATOR_DIR = Path(__file__).resolve().parent.parent
if str(SIMULATOR_DIR) not in sys.path:
    sys.path.insert(0, str(SIMULATOR_DIR))

from fake_esp32 import run_simulation  # noqa: E402

NAME = "one_fall"
DESCRIPTION = "Single fall event injected at t=5s — expects exactly 1 alert"

_DURATION_S  = 12.0   # 5s normal -> 1s fall -> 6s normal
_FALL_AT_S   = 5.0    # inject after buffer is full (100 samples = 2s at 50Hz)
_FALL_DUR_S  = 1.0    # 1 second of fall-level SVM


def run(
    url: str = "http://localhost:8000",
    patient_id: int | None = None,
    seed: int | None = 42,
    quiet: bool = False,
) -> dict:
    if patient_id is None:
        patient_id = (int(time.time() * 1000) % 89999) + 10001
    print(f"\n{'='*55}")
    print(f"  SCENARIO: {NAME.upper()}")
    print(f"  {DESCRIPTION}")
    print(f"{'='*55}")
    print(f"  patient_id={patient_id}  duration={_DURATION_S}s  "
          f"fall_at={_FALL_AT_S}s  seed={seed}")

    result = run_simulation(
        mode="FALL",
        patient_id=patient_id,
        duration_s=_DURATION_S,
        rate=50,
        url=url,
        seed=seed,
        fall_at_s=_FALL_AT_S,
        fall_duration_s=_FALL_DUR_S,
        quiet=quiet,
    )

    passed = (
        result.alerts_created == 1
        and result.samples_failed == 0
        and len(result.alert_latencies_ms) > 0
    )

    avg_latency = (
        sum(result.alert_latencies_ms) / len(result.alert_latencies_ms)
        if result.alert_latencies_ms else None
    )

    details = {
        **result.summary(),
        "expected_alerts": 1,
        "actual_alerts": result.alerts_created,
        "avg_alert_latency_ms": round(avg_latency, 2) if avg_latency is not None else None,
    }

    _print_verdict(passed, details)
    return {"name": NAME, "passed": passed, "details": details}


def _print_verdict(passed: bool, details: dict) -> None:
    label = "PASS" if passed else "FAIL"
    latency = details["avg_alert_latency_ms"]
    latency_str = f"{latency}ms" if latency is not None else "N/A"
    print(f"\n  [{label}] alerts={details['actual_alerts']} "
          f"(expected 1)  "
          f"latency={latency_str}  "
          f"sent={details['samples_sent']}  "
          f"failed={details['samples_failed']}")
