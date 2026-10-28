"""
scenario_bad_payload.py

Kich ban 4: Payload bi hong va du lieu nhieu.

Muc tieu:
    Dam bao he thong xu ly payload loi mot cach ro rang, khong crash.

Cac truong hop kiem tra:

    Malformed payloads (ket qua mong doi: HTTP 422):
        1. Thieu truong `ax`
        2. patient_id = 0 (khong hop le, phai > 0)
        3. patient_id = -1
        4. `ax` la chuoi chu (sai kieu du lieu)
        5. Batch voi mang samples rong
        6. Batch voi patient_id khong hop le

    Noisy payloads (ket qua mong doi: HTTP 200, khong crash):
        7. Gia tri gia toc cuc lon (100g)
        8. Gia tri am lon
        9. SVM = 0.0

Pass khi:
    Tat ca truong hop malformed tra ve 422
    Tat ca truong hop noisy tra ve 200
    Khong co request nao khien server crash (503/500 khong duoc xuat hien)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx

SIMULATOR_DIR = Path(__file__).resolve().parent.parent
if str(SIMULATOR_DIR) not in sys.path:
    sys.path.insert(0, str(SIMULATOR_DIR))

NAME = "bad_payload"
DESCRIPTION = "Malformed and noisy payloads — expects 422 on bad, 200 on noisy, no crashes"

_PATIENT_ID = 103

# ---------------------------------------------------------------------------
# A valid baseline payload to build test cases from
# ---------------------------------------------------------------------------
_VALID = {
    "patient_id": _PATIENT_ID,
    "ax": 0.0, "ay": 0.0, "az": 1.0,
    "gx": 0.0, "gy": 0.0, "gz": 0.0,
    "svm": 1.0,
}

# ---------------------------------------------------------------------------
# Test case definitions
# ---------------------------------------------------------------------------

def _without(key: str) -> dict:
    return {k: v for k, v in _VALID.items() if k != key}


def _with(**overrides) -> dict:
    return {**_VALID, **overrides}


MALFORMED_CASES: list[tuple[str, str, dict | Any]] = [
    # (label, endpoint, payload)
    ("missing ax",             "/api/sensor/data",  _without("ax")),
    ("missing ay",             "/api/sensor/data",  _without("ay")),
    ("missing az",             "/api/sensor/data",  _without("az")),
    ("patient_id=0",           "/api/sensor/data",  _with(patient_id=0)),
    ("patient_id=-1",          "/api/sensor/data",  _with(patient_id=-1)),
    ("ax is string",           "/api/sensor/data",  _with(ax="not_a_float")),
    ("empty batch",            "/api/sensor/batch", {"patient_id": _PATIENT_ID, "samples": []}),
    ("batch invalid patient",  "/api/sensor/batch", {"patient_id": -5, "samples": [
        {"ax": 0.0, "ay": 0.0, "az": 1.0, "gx": 0.0, "gy": 0.0, "gz": 0.0}
    ]}),
]

NOISY_CASES: list[tuple[str, str, dict]] = [
    # (label, endpoint, payload)
    ("extreme high accel (100g)",  "/api/sensor/data", _with(ax=100.0, ay=100.0, az=100.0, svm=173.2)),
    ("extreme negative accel",     "/api/sensor/data", _with(ax=-50.0, ay=-50.0, az=-50.0, svm=86.6)),
    ("svm=0.0",                    "/api/sensor/data", _with(ax=0.0, ay=0.0, az=0.0, svm=0.0)),
    ("very large gyro values",     "/api/sensor/data", _with(gx=2000.0, gy=2000.0, gz=2000.0)),
]


def run(
    url: str = "http://localhost:8000",
    patient_id: int = _PATIENT_ID,  # not used — these tests don't use run_simulation
    seed: int | None = None,         # not used
    quiet: bool = False,
) -> dict:
    print(f"\n{'='*55}")
    print(f"  SCENARIO: {NAME.upper()}")
    print(f"  {DESCRIPTION}")
    print(f"{'='*55}")

    results: list[dict] = []

    with httpx.Client(base_url=url, timeout=5.0) as client:
        # ------------------------------------------------------------------
        # Malformed payloads — expect 422
        # ------------------------------------------------------------------
        print(f"\n  --- Malformed payloads (expect HTTP 422) ---")
        for label, endpoint, payload in MALFORMED_CASES:
            status, ok, note = _send(client, endpoint, payload, expect=422)
            results.append({
                "label": label,
                "type": "malformed",
                "endpoint": endpoint,
                "status": status,
                "passed": ok,
                "note": note,
            })
            _log(quiet, label, status, ok, "422")

        # ------------------------------------------------------------------
        # Noisy but valid payloads — expect 200
        # ------------------------------------------------------------------
        print(f"\n  --- Noisy payloads (expect HTTP 200, no crash) ---")
        for label, endpoint, payload in NOISY_CASES:
            status, ok, note = _send(client, endpoint, payload, expect=200)
            results.append({
                "label": label,
                "type": "noisy",
                "endpoint": endpoint,
                "status": status,
                "passed": ok,
                "note": note,
            })
            _log(quiet, label, status, ok, "200")

    passed_count = sum(1 for r in results if r["passed"])
    total = len(results)
    passed = passed_count == total

    details = {
        "cases_total": total,
        "cases_passed": passed_count,
        "cases_failed": total - passed_count,
        "results": results,
    }

    label = "PASS" if passed else "FAIL"
    print(f"\n  [{label}] {passed_count}/{total} cases passed")
    if not passed:
        for r in results:
            if not r["passed"]:
                print(f"    FAIL: {r['label']} — got HTTP {r['status']}, expected "
                      f"{'422' if r['type'] == 'malformed' else '200'}")

    return {"name": NAME, "passed": passed, "details": details}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send(
    client: httpx.Client,
    endpoint: str,
    payload: Any,
    expect: int,
) -> tuple[int, bool, str]:
    """
    POST payload to endpoint. Returns (status_code, passed, note).
    passed=True when status_code == expect and server did not 500/503.
    """
    try:
        r = client.post(endpoint, json=payload)
        status = r.status_code
        if status in (500, 503):
            return status, False, f"Server error: {r.text[:200]}"
        ok = status == expect
        return status, ok, ""
    except httpx.RequestError as exc:
        return 0, False, f"Connection error: {exc}"


def _log(quiet: bool, label: str, status: int, ok: bool, expected: str) -> None:
    if not quiet:
        mark = "OK" if ok else "FAIL"
        print(f"    [{mark}] {label:<40} HTTP {status} (expected {expected})")
