"""
scenario_multi_fall.py

Kich ban 3: Nhieu su kien nga lien tiep.

Muc tieu:
    Kiem tra co che cooldown va de-duplication cua he thong.

Cau truc:
    Round A  — nga lan 1 -> 1 canh bao duoc tao
    Round B  — nga ngay sau Round A (van trong thoi gian cooldown) -> bi chan
    [cho het cooldown — 12 giay]
    Round C  — nga lan 2 sau khi het cooldown -> 1 canh bao nua duoc tao

Pass khi:
    Round A: alerts_created >= 1
    Round B: alerts_created == 0  (bi chan boi cooldown)
    Round C: alerts_created >= 1
    Tong alerts: round_a + round_c (round_b khong tinh)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

SIMULATOR_DIR = Path(__file__).resolve().parent.parent
if str(SIMULATOR_DIR) not in sys.path:
    sys.path.insert(0, str(SIMULATOR_DIR))

from fake_esp32 import run_simulation  # noqa: E402

NAME = "multi_fall"
DESCRIPTION = "Multiple fall events — verifies cooldown suppression and recovery"

_COOLDOWN_S   = 10      # must match backend ALERT_COOLDOWN_SECONDS
_COOLDOWN_WAIT = 12     # wait slightly longer than cooldown to be safe


def run(
    url: str = "http://localhost:8000",
    patient_id: int | None = None,
    seed: int | None = 42,
    quiet: bool = False,
) -> dict:
    # Single ID shared across all rounds so cooldown carries over
    # between rounds A/B/C, but isolated from other test runs.
    if patient_id is None:
        patient_id = (int(time.time() * 1000) % 89999) + 10001
    print(f"\n{'='*55}")
    print(f"  SCENARIO: {NAME.upper()}")
    print(f"  {DESCRIPTION}")
    print(f"{'='*55}")
    print(f"  patient_id={patient_id}  cooldown={_COOLDOWN_S}s  seed={seed}")

    # ------------------------------------------------------------------
    # Round A: fill buffer then inject fall -> expect 1 alert
    # ------------------------------------------------------------------
    print(f"\n  [Round A] Normal stream + fall at t=5s ...")
    round_a = run_simulation(
        mode="FALL",
        patient_id=patient_id,
        duration_s=12.0,   # 5s normal -> 1s fall -> 6s normal
        rate=50,
        url=url,
        seed=seed,
        fall_at_s=5.0,
        fall_duration_s=1.0,
        quiet=quiet,
    )
    print(f"  [Round A] alerts={round_a.alerts_created}  "
          f"sent={round_a.samples_sent}  failed={round_a.samples_failed}")

    # ------------------------------------------------------------------
    # Round B: fall immediately after Round A -> cooldown still active
    # fall_at=0.5s so it triggers quickly (buffer already full from Round A)
    # ------------------------------------------------------------------
    print(f"\n  [Round B] Immediate fall (within cooldown window) ...")
    round_b = run_simulation(
        mode="FALL",
        patient_id=patient_id,
        duration_s=3.0,
        rate=50,
        url=url,
        seed=(seed or 0) + 1,
        fall_at_s=0.5,
        fall_duration_s=0.5,
        quiet=quiet,
    )
    print(f"  [Round B] alerts={round_b.alerts_created}  "
          f"suppressed={round_b.samples_sent - round_b.samples_failed}  "
          f"(should be 0 alerts — cooldown active)")

    # ------------------------------------------------------------------
    # Wait for cooldown to expire
    # ------------------------------------------------------------------
    print(f"\n  [Wait] Sleeping {_COOLDOWN_WAIT}s for cooldown to expire ...")
    for remaining in range(_COOLDOWN_WAIT, 0, -1):
        print(f"    {remaining}s remaining...", end="\r")
        time.sleep(1)
    print()

    # ------------------------------------------------------------------
    # Round C: fall after cooldown -> expect new alert
    # fall_at=2s to let a few normal samples arrive first
    # ------------------------------------------------------------------
    print(f"\n  [Round C] Fall after cooldown expires ...")
    round_c = run_simulation(
        mode="FALL",
        patient_id=patient_id,
        duration_s=7.0,
        rate=50,
        url=url,
        seed=(seed or 0) + 2,
        fall_at_s=2.0,
        fall_duration_s=1.0,
        quiet=quiet,
    )
    print(f"  [Round C] alerts={round_c.alerts_created}  "
          f"sent={round_c.samples_sent}  failed={round_c.samples_failed}")

    # ------------------------------------------------------------------
    # Evaluate
    # ------------------------------------------------------------------
    a_ok = round_a.alerts_created >= 1 and round_a.samples_failed == 0
    b_ok = round_b.alerts_created == 0 and round_b.samples_failed == 0
    c_ok = round_c.alerts_created >= 1 and round_c.samples_failed == 0
    passed = a_ok and b_ok and c_ok

    details = {
        "round_a": round_a.summary(),
        "round_b": round_b.summary(),
        "round_c": round_c.summary(),
        "criteria": {
            "round_a_alert_created": a_ok,
            "round_b_suppressed":    b_ok,
            "round_c_alert_created": c_ok,
        },
        "total_alerts": round_a.alerts_created + round_c.alerts_created,
    }

    label = "PASS" if passed else "FAIL"
    print(f"\n  [{label}]  "
          f"A={round_a.alerts_created} alert(s)  "
          f"B={round_b.alerts_created} (suppressed)  "
          f"C={round_c.alerts_created} alert(s)")
    if not a_ok:
        print(f"  [FAIL reason] Round A: alerts={round_a.alerts_created} failed={round_a.samples_failed}")
    if not b_ok:
        print(f"  [FAIL reason] Round B: alerts={round_b.alerts_created} (expect 0) failed={round_b.samples_failed}")
    if not c_ok:
        print(f"  [FAIL reason] Round C: alerts={round_c.alerts_created} failed={round_c.samples_failed}")

    return {"name": NAME, "passed": passed, "details": details}
