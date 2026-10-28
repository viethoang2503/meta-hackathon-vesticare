"""
run_scenario.py

Diem vao CLI de chay cac kich ban kiem thu he thong Vesti-Care.

Su dung:
    python run_scenario.py --scenario normal
    python run_scenario.py --scenario one_fall --seed 42
    python run_scenario.py --scenario multi_fall --quiet
    python run_scenario.py --scenario bad_payload
    python run_scenario.py --all                        # chay tat ca theo thu tu
    python run_scenario.py --all --output results.json  # luu ket qua

Cac kich ban co san:
    normal       — Luong binh thuong, ket qua mong doi: 0 canh bao
    one_fall     — 1 su kien nga, ket qua mong doi: 1 canh bao
    multi_fall   — Nhieu su kien nga, kiem tra cooldown suppression
    bad_payload  — Payload loi / du lieu nhieu, kiem tra xu ly loi
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SIMULATOR_DIR = Path(__file__).resolve().parent
if str(SIMULATOR_DIR) not in sys.path:
    sys.path.insert(0, str(SIMULATOR_DIR))

from scenarios import scenario_normal      # noqa: E402
from scenarios import scenario_one_fall    # noqa: E402
from scenarios import scenario_multi_fall  # noqa: E402
from scenarios import scenario_bad_payload # noqa: E402

# Ordered list — run in this order when --all is used
SCENARIOS: dict[str, object] = {
    scenario_normal.NAME:       scenario_normal,
    scenario_one_fall.NAME:     scenario_one_fall,
    scenario_multi_fall.NAME:   scenario_multi_fall,
    scenario_bad_payload.NAME:  scenario_bad_payload,
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vesti-Care scenario test runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()),
        metavar="NAME",
        help=f"Scenario to run. Choices: {', '.join(SCENARIOS.keys())}",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Run all scenarios in order",
    )

    parser.add_argument("--url", default="http://localhost:8000",
                        help="Backend base URL")
    parser.add_argument("--patient-id", type=int, default=None, metavar="ID",
                        help="Override patient ID (each scenario has its own default)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducible signal generation")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-sample logs")
    parser.add_argument("--output", metavar="FILE", default=None,
                        help="Save JSON result(s) to this file")
    return parser.parse_args(argv)


def _run_one(module, url: str, patient_id: int | None, seed: int, quiet: bool) -> dict:
    kwargs: dict = {"url": url, "seed": seed, "quiet": quiet}
    if patient_id is not None:
        kwargs["patient_id"] = patient_id
    return module.run(**kwargs)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    results: list[dict] = []

    if args.all:
        print(f"\nRunning all {len(SCENARIOS)} scenarios against {args.url}")
        for i, (name, module) in enumerate(SCENARIOS.items()):
            # Each scenario gets its own patient ID to prevent buffer/cooldown
            # state from one scenario bleeding into the next.
            if args.patient_id is not None:
                scenario_patient_id = args.patient_id * 1000 + i
            else:
                scenario_patient_id = None
            result = _run_one(module, args.url, scenario_patient_id, args.seed, args.quiet)
            results.append(result)
    else:
        module = SCENARIOS[args.scenario]
        result = _run_one(module, args.url, args.patient_id, args.seed, args.quiet)
        results.append(result)

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]

    print(f"\n{'='*55}")
    print(f"  FINAL SUMMARY: {len(passed)}/{len(results)} scenarios passed")
    print(f"{'='*55}")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  [{mark}] {r['name']}")

    if args.output:
        payload = results if len(results) > 1 else results[0]
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\nResults saved to {args.output}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
