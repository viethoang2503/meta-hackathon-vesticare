"""
reliability_benchmark.py

Reliability and degradation test suite for M2.3 — Vesti-Care Backend.

Tests how the system handles failure conditions. Each test sends HTTP requests
to a running backend and verifies expected behavior. After each destructive
test a health-check confirms the backend is still reachable.

Requirements:
    Backend running at http://localhost:8000

Usage:
    python backend/tools/reliability_benchmark.py
    python backend/tools/reliability_benchmark.py --url http://localhost:8000
    python backend/tools/reliability_benchmark.py --test R01 R05 R10
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "results"
MATRIX_MD   = RESULTS_DIR / "reliability_matrix.md"
MATRIX_JSON = RESULTS_DIR / "reliability_matrix.json"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_URL      = "http://localhost:8000"
SENSOR_ENDPOINT  = "/api/sensor/data"
HEALTH_ENDPOINT  = "/api/health"

VALID_PATIENT_ID = 99999   # safe sentinel — unlikely to conflict with real data

# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _ts() -> str:
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def valid_payload(
    patient_id: int = VALID_PATIENT_ID,
    svm: float = 1.0,
    gx: float = 0.0,
    gy: float = 0.0,
    gz: float = 0.0,
    ax: float = 0.1,
    ay: float = 0.0,
    az: float = 1.0,
    timestamp: str | None = None,
) -> dict:
    """Return a minimal valid sensor payload."""
    return {
        "patient_id": patient_id,
        "ax": ax,
        "ay": ay,
        "az": az,
        "gx": gx,
        "gy": gy,
        "gz": gz,
        "svm": svm,
        "timestamp": timestamp if timestamp is not None else _ts(),
    }


# ---------------------------------------------------------------------------
# Result record
# ---------------------------------------------------------------------------


@dataclass
class TestResult:
    test_id: str
    name: str
    passed: bool
    status_code: int | None
    response_time_ms: float
    notes: str
    backend_alive_after: bool = True


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


class ReliabilityBenchmark:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.sensor_url = self.base_url + SENSOR_ENDPOINT
        self.health_url = self.base_url + HEALTH_ENDPOINT
        self.results: list[TestResult] = []

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _post(
        self,
        client: httpx.Client,
        payload: dict | None = None,
        *,
        content_type: str | None = None,
        raw_body: bytes | str | None = None,
        timeout: float = 10.0,
    ) -> tuple[int | None, float, str]:
        """
        POST to the sensor endpoint.
        Returns (status_code, elapsed_ms, response_text).
        On connection error returns (None, elapsed_ms, error_message).
        """
        t0 = time.perf_counter()
        try:
            if raw_body is not None:
                headers = {"Content-Type": content_type or "text/plain"}
                resp = client.post(
                    self.sensor_url,
                    content=raw_body if isinstance(raw_body, bytes) else raw_body.encode(),
                    headers=headers,
                    timeout=timeout,
                )
            elif content_type is not None:
                headers = {"Content-Type": content_type}
                body = json.dumps(payload or {}).encode()
                resp = client.post(self.sensor_url, content=body, headers=headers, timeout=timeout)
            else:
                resp = client.post(self.sensor_url, json=payload, timeout=timeout)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return resp.status_code, round(elapsed_ms, 2), resp.text[:500]
        except httpx.RequestError as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return None, round(elapsed_ms, 2), f"RequestError: {exc}"

    def _health_check(self, client: httpx.Client, timeout: float = 5.0) -> bool:
        """Return True if backend is reachable."""
        try:
            resp = client.get(self.health_url, timeout=timeout)
            return resp.status_code < 500
        except httpx.RequestError:
            # Fallback: try root
            try:
                resp = client.get(self.base_url + "/", timeout=timeout)
                return resp.status_code < 500
            except httpx.RequestError:
                return False

    def _record(self, result: TestResult) -> None:
        status = "PASS" if result.passed else "FAIL"
        alive  = "alive" if result.backend_alive_after else "DOWN"
        code   = str(result.status_code) if result.status_code is not None else "---"
        print(
            f"  [{status}] {result.test_id} | {result.name} | "
            f"HTTP {code} | {result.response_time_ms:.1f} ms | "
            f"backend={alive} | {result.notes}"
        )
        self.results.append(result)

    # ------------------------------------------------------------------
    # Individual tests
    # ------------------------------------------------------------------

    def test_R01(self, client: httpx.Client) -> TestResult:
        """R01 — Malformed payload: missing ax/ay/az."""
        payload = {
            "patient_id": VALID_PATIENT_ID,
            # ax, ay, az intentionally absent
            "gx": 0.0, "gy": 0.0, "gz": 0.0,
            "svm": 1.0,
            "timestamp": _ts(),
        }
        code, elapsed, _ = self._post(client, payload)
        alive = self._health_check(client)
        passed = code == 422 and alive
        return TestResult(
            test_id="R01",
            name="Malformed payload (missing ax/ay/az)",
            passed=passed,
            status_code=code,
            response_time_ms=elapsed,
            notes=f"expected 422, got {code}",
            backend_alive_after=alive,
        )

    def test_R02(self, client: httpx.Client) -> TestResult:
        """R02 — Invalid patient_id (negative)."""
        payload = valid_payload(patient_id=-1)
        code, elapsed, _ = self._post(client, payload)
        alive = self._health_check(client)
        passed = code in (400, 422) and alive
        return TestResult(
            test_id="R02",
            name="Invalid patient_id (negative)",
            passed=passed,
            status_code=code,
            response_time_ms=elapsed,
            notes=f"expected 400 or 422, got {code}",
            backend_alive_after=alive,
        )

    def test_R03(self, client: httpx.Client) -> TestResult:
        """R03 — Extreme sensor values (SVM=999g, gx=9999)."""
        payload = valid_payload(svm=999.0, gx=9999.0, ax=850.0, ay=0.0, az=0.0)
        code, elapsed, body = self._post(client, payload)
        alive = self._health_check(client)
        # Any non-5xx response that doesn't crash is acceptable
        passed = code is not None and code < 500 and alive
        return TestResult(
            test_id="R03",
            name="Extreme sensor values (SVM=999g, gx=9999)",
            passed=passed,
            status_code=code,
            response_time_ms=elapsed,
            notes=f"got {code}, crash={not alive}",
            backend_alive_after=alive,
        )

    def test_R04(self, client: httpx.Client) -> TestResult:
        """R04 — Duplicate timestamps (same ts 50 times)."""
        fixed_ts = _ts()
        failures = 0
        server_errors = 0
        elapsed_total = 0.0
        last_code: int | None = None

        for _ in range(50):
            payload = valid_payload(timestamp=fixed_ts)
            code, elapsed, _ = self._post(client, payload)
            elapsed_total += elapsed
            last_code = code
            if code is None:
                failures += 1
            elif code >= 500:
                server_errors += 1

        alive = self._health_check(client)
        passed = server_errors == 0 and failures == 0 and alive
        return TestResult(
            test_id="R04",
            name="Duplicate timestamps (same ts 50x)",
            passed=passed,
            status_code=last_code,
            response_time_ms=round(elapsed_total / 50, 2),
            notes=f"server_errors={server_errors} connect_fails={failures}",
            backend_alive_after=alive,
        )

    def test_R05(self, client: httpx.Client) -> TestResult:
        """R05 — Rapid burst (500 requests as fast as possible)."""
        n = 500
        success = 0
        fail_5xx = 0
        connect_fail = 0
        elapsed_times: list[float] = []

        for i in range(n):
            payload = valid_payload(patient_id=VALID_PATIENT_ID + (i % 20))
            code, elapsed, _ = self._post(client, payload, timeout=15.0)
            elapsed_times.append(elapsed)
            if code is None:
                connect_fail += 1
            elif code < 400:
                success += 1
            elif code >= 500:
                fail_5xx += 1

        alive = self._health_check(client)
        success_rate = success / n
        passed = success_rate >= 0.90 and alive   # 90 % threshold
        avg_ms = sum(elapsed_times) / len(elapsed_times) if elapsed_times else 0.0
        return TestResult(
            test_id="R05",
            name="Rapid burst (500 requests)",
            passed=passed,
            status_code=None,
            response_time_ms=round(avg_ms, 2),
            notes=(
                f"success={success}/{n} ({success_rate*100:.1f}%) "
                f"5xx={fail_5xx} connect_fail={connect_fail}"
            ),
            backend_alive_after=alive,
        )

    def test_R06(self, client: httpx.Client) -> TestResult:
        """R06 — Missing required field: no timestamp (timestamp is optional per schema)."""
        # Per sensor.py schema, timestamp is Optional — the backend fills it in.
        # The required fields that must be present are: patient_id, ax, ay, az, gx, gy, gz.
        # R06 tests omitting the timestamp field explicitly; the backend should accept it (200)
        # OR return 422.  We call it a PASS if the backend stays alive regardless.
        payload = {
            "patient_id": VALID_PATIENT_ID,
            "ax": 0.1, "ay": 0.0, "az": 1.0,
            "gx": 0.0, "gy": 0.0, "gz": 0.0,
            "svm": 1.0,
            # timestamp intentionally absent
        }
        code, elapsed, _ = self._post(client, payload)
        alive = self._health_check(client)
        # Acceptable: 200 (server auto-fills ts) or 422 (server rejects it)
        passed = code in (200, 422) and alive
        return TestResult(
            test_id="R06",
            name="Missing required field (no timestamp)",
            passed=passed,
            status_code=code,
            response_time_ms=elapsed,
            notes=f"timestamp omitted; got {code} (200 or 422 acceptable)",
            backend_alive_after=alive,
        )

    def test_R07(self, client: httpx.Client) -> TestResult:
        """R07 — Wrong Content-Type (text/plain instead of application/json)."""
        raw = json.dumps(valid_payload())
        code, elapsed, _ = self._post(
            client,
            raw_body=raw,
            content_type="text/plain",
        )
        alive = self._health_check(client)
        passed = code in (415, 422) and alive
        return TestResult(
            test_id="R07",
            name="Wrong Content-Type (text/plain)",
            passed=passed,
            status_code=code,
            response_time_ms=elapsed,
            notes=f"expected 415 or 422, got {code}",
            backend_alive_after=alive,
        )

    def test_R08(self, client: httpx.Client) -> TestResult:
        """R08 — Oversized payload (10 000 samples in one batch POST)."""
        samples = [
            {
                "ax": 0.1, "ay": 0.0, "az": 1.0,
                "gx": 0.0, "gy": 0.0, "gz": 0.0,
                "svm": 1.0,
                "timestamp": _ts(),
            }
            for _ in range(10_000)
        ]
        batch_payload = {"patient_id": VALID_PATIENT_ID, "samples": samples}
        t0 = time.perf_counter()
        try:
            resp = client.post(
                self.base_url + "/api/sensor/batch",
                json=batch_payload,
                timeout=60.0,
            )
            elapsed = (time.perf_counter() - t0) * 1000
            code: int | None = resp.status_code
            text = resp.text[:200]
        except httpx.RequestError as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            code = None
            text = f"RequestError: {exc}"

        alive = self._health_check(client)
        # No crash = any non-5xx (200 OK, or 413 Request Too Large, or even 422)
        passed = code is not None and code < 500 and alive
        return TestResult(
            test_id="R08",
            name="Oversized payload (10 000 samples, batch endpoint)",
            passed=passed,
            status_code=code,
            response_time_ms=round(elapsed, 2),
            notes=f"got {code}; crash={not alive}",
            backend_alive_after=alive,
        )

    def test_R09(self, client: httpx.Client) -> TestResult:
        """R09 — Normal stream after errors: send R01-style error then valid stream."""
        # Step 1: trigger a validation error
        bad_payload = {
            "patient_id": VALID_PATIENT_ID,
            "gx": 0.0, "gy": 0.0, "gz": 0.0,
            "svm": 1.0,
            "timestamp": _ts(),
        }
        code_bad, _, _ = self._post(client, bad_payload)

        # Step 2: send 10 valid samples and count successes
        recovery_ok = 0
        last_valid_code: int | None = None
        for _ in range(10):
            code_good, _, _ = self._post(client, valid_payload())
            last_valid_code = code_good
            if code_good is not None and code_good < 400:
                recovery_ok += 1

        alive = self._health_check(client)
        passed = code_bad == 422 and recovery_ok == 10 and alive
        return TestResult(
            test_id="R09",
            name="Normal stream after errors",
            passed=passed,
            status_code=last_valid_code,
            response_time_ms=0.0,
            notes=(
                f"error_resp={code_bad}(want 422) "
                f"recovery={recovery_ok}/10"
            ),
            backend_alive_after=alive,
        )

    def test_R10(self, client: httpx.Client) -> TestResult:
        """R10 — Concurrent patients (10 threads × 10 patient IDs)."""
        N_THREADS   = 10
        N_SAMPLES   = 20   # per thread
        BASE_PID    = 88000

        results_per_thread: list[dict] = [{}] * N_THREADS
        errors_lock = threading.Lock()

        def worker(thread_idx: int) -> None:
            patient_id = BASE_PID + thread_idx
            ok = 0
            err_5xx = 0
            conn_fail = 0
            # Each thread creates its own client to avoid shared-state issues
            with httpx.Client() as thr_client:
                for _ in range(N_SAMPLES):
                    payload = valid_payload(patient_id=patient_id)
                    code, _, _ = self._post(thr_client, payload, timeout=15.0)
                    if code is None:
                        conn_fail += 1
                    elif code < 400:
                        ok += 1
                    elif code >= 500:
                        err_5xx += 1
            with errors_lock:
                results_per_thread[thread_idx] = {
                    "ok": ok, "5xx": err_5xx, "conn_fail": conn_fail
                }

        threads = [
            threading.Thread(target=worker, args=(i,), daemon=True)
            for i in range(N_THREADS)
        ]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60.0)
        elapsed = (time.perf_counter() - t0) * 1000

        total_ok       = sum(r.get("ok", 0)       for r in results_per_thread)
        total_5xx      = sum(r.get("5xx", 0)       for r in results_per_thread)
        total_conn_fail= sum(r.get("conn_fail", 0) for r in results_per_thread)
        total_requests = N_THREADS * N_SAMPLES

        alive = self._health_check(client)
        success_rate = total_ok / total_requests
        passed = success_rate >= 0.90 and total_5xx == 0 and alive

        return TestResult(
            test_id="R10",
            name="Concurrent patients (10 threads)",
            passed=passed,
            status_code=None,
            response_time_ms=round(elapsed, 2),
            notes=(
                f"success={total_ok}/{total_requests} ({success_rate*100:.1f}%) "
                f"5xx={total_5xx} conn_fail={total_conn_fail}"
            ),
            backend_alive_after=alive,
        )

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

    _ALL_TESTS: dict[str, Callable[["ReliabilityBenchmark", httpx.Client], TestResult]] = {
        "R01": test_R01,
        "R02": test_R02,
        "R03": test_R03,
        "R04": test_R04,
        "R05": test_R05,
        "R06": test_R06,
        "R07": test_R07,
        "R08": test_R08,
        "R09": test_R09,
        "R10": test_R10,
    }

    def run(self, test_ids: list[str] | None = None) -> list[TestResult]:
        if test_ids:
            unknown = [t for t in test_ids if t not in self._ALL_TESTS]
            if unknown:
                print(f"[ERROR] Unknown test IDs: {unknown}")
                print(f"        Valid IDs: {sorted(self._ALL_TESTS)}")
                sys.exit(1)
            to_run = {k: v for k, v in self._ALL_TESTS.items() if k in test_ids}
        else:
            to_run = self._ALL_TESTS

        print("=" * 70)
        print("  RELIABILITY BENCHMARK — Vesti-Care Backend  (M2.3)")
        print("=" * 70)
        print(f"  Backend : {self.base_url}")
        print(f"  Tests   : {', '.join(to_run)}")
        print("-" * 70)

        with httpx.Client() as client:
            # Pre-flight: backend must be reachable before we begin
            if not self._health_check(client):
                print("\n[ERROR] Backend is not reachable at", self.base_url)
                print("        Start the backend first:")
                print("        cd backend && uvicorn app.main:app --reload")
                sys.exit(1)
            print("  Pre-flight health check: OK\n")

            for test_id, fn in to_run.items():
                try:
                    result = fn(self, client)
                except Exception as exc:
                    # Test itself raised — record as failure, keep going
                    result = TestResult(
                        test_id=test_id,
                        name=self._ALL_TESTS[test_id].__doc__ or test_id,
                        passed=False,
                        status_code=None,
                        response_time_ms=0.0,
                        notes=f"EXCEPTION: {exc}",
                        backend_alive_after=self._health_check(client),
                    )
                self._record(result)

        print("\n" + "-" * 70)
        passed = sum(1 for r in self.results if r.passed)
        total  = len(self.results)
        print(f"  Result: {passed}/{total} passed")
        print("=" * 70)

        return self.results


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def _md_row(r: TestResult) -> str:
    status = "PASS" if r.passed else "FAIL"
    code   = str(r.status_code) if r.status_code is not None else "—"
    alive  = "yes" if r.backend_alive_after else "**NO**"
    return (
        f"| {r.test_id} | {r.name} | {status} | {code} | "
        f"{r.response_time_ms:.1f} | {alive} | {r.notes} |"
    )


def save_markdown(results: list[TestResult], path: Path) -> None:
    run_ts = datetime.now(timezone.utc).isoformat()
    header = (
        "# Reliability Matrix\n\n"
        f"Generated: {run_ts}\n\n"
        "| ID | Test | Result | HTTP | Time (ms) | Backend alive | Notes |\n"
        "|----|------|--------|------|-----------|---------------|-------|\n"
    )
    rows = "\n".join(_md_row(r) for r in results)
    passed = sum(1 for r in results if r.passed)
    footer = f"\n\n**{passed}/{len(results)} tests passed**\n"
    path.write_text(header + rows + footer, encoding="utf-8")
    print(f"\n  Saved: {path}")


def save_json(results: list[TestResult], path: Path) -> None:
    run_ts = datetime.now(timezone.utc).isoformat()
    data = {
        "generated_at": run_ts,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
        },
        "tests": [
            {
                "test_id": r.test_id,
                "name": r.name,
                "passed": r.passed,
                "status_code": r.status_code,
                "response_time_ms": r.response_time_ms,
                "backend_alive_after": r.backend_alive_after,
                "notes": r.notes,
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  Saved: {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vesti-Care Reliability Benchmark (M2.3)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="Backend base URL",
    )
    parser.add_argument(
        "--test",
        nargs="+",
        metavar="ID",
        default=None,
        help="Run only these test IDs (e.g. --test R01 R05 R10)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    bench = ReliabilityBenchmark(base_url=args.url)
    results = bench.run(test_ids=args.test)

    # Ensure results directory exists
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    save_markdown(results, MATRIX_MD)
    save_json(results, MATRIX_JSON)

    failed = sum(1 for r in results if not r.passed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
