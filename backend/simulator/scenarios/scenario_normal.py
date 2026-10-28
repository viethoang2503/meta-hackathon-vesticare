"""
scenario_normal.py

Kich ban 1: Luong du lieu binh thuong lien tuc.

Muc tieu:
    Khong co su kien nga -> khong co canh bao nao duoc tao.

Pass khi:
    alerts_created == 0
    samples_failed == 0
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

SIMULATOR_DIR = Path(__file__).resolve().parent.parent
if str(SIMULATOR_DIR) not in sys.path:
    sys.path.insert(0, str(SIMULATOR_DIR))

from fake_esp32 import run_simulation  # noqa: E402

NAME = "normal"
DESCRIPTION = "Baseline normal stream — expects 0 alerts"

# 10 seconds = 500 samples = 5 full windows at 50 Hz
_DURATION_S = 10.0


def run(
    url: str = "http://localhost:8000",
    patient_id: int | None = None,
    seed: int | None = 42,
    quiet: bool = False,
) -> dict:
    # Use a timestamp-derived ID so each run starts with a clean
    # buffer, cooldown, and database state.
    if patient_id is None:
        patient_id = (int(time.time() * 1000) % 89999) + 10001

    print(f"\n{'='*55}")
    print(f"  SCENARIO: {NAME.upper()}")
    print(f"  {DESCRIPTION}")
    print(f"{'='*55}")
    print(f"  patient_id={patient_id}  duration={_DURATION_S}s  seed={seed}")

    result = run_simulation(
        mode="NORMAL",
        patient_id=patient_id,
        duration_s=_DURATION_S,
        rate=50,
        url=url,
        seed=seed,
        fall_at_s=0.0,
        fall_duration_s=0.0,
        quiet=quiet,
    )

    passed = result.alerts_created == 0 and result.samples_failed == 0

    details = {
        **result.summary(),
        "expected_alerts": 0,
        "actual_alerts": result.alerts_created,
    }

    _print_verdict(passed, details)
    return {"name": NAME, "passed": passed, "details": details}


def _print_verdict(passed: bool, details: dict) -> None:
    label = "PASS" if passed else "FAIL"
    print(f"\n  [{label}] alerts={details['actual_alerts']} "
          f"(expected 0)  "
          f"sent={details['samples_sent']}  "
          f"failed={details['samples_failed']}")
