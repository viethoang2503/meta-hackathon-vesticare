"""M4 table from the people's live sessions.

    .verify/venv/bin/python backend/tools/m4_report.py trials.json

trials.json: {"people": {"person A": {"session": "<id>", "falls": [[60, 70], ...], "adl": [[140, 150], ...]}}}
Times are seconds of source time, read off the recording. Writes docs/spatial/evidence/mvp2/m4/report.json and .md.
"""

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.m4_report import m4_report  # noqa: E402

OUT = BACKEND.parent / "docs" / "spatial" / "evidence" / "mvp2" / "m4"


def to_markdown(report: dict) -> str:
    lines = [f"# M4 per person", "", f"Status: **{report['status']}**. {report['note']}", ""]
    lines += [f"Still needed: {m}" for m in report["missing"]]
    lines += ["| Person | Source | Falls detected | Missed | Everyday trials with a false alarm | Status |", "|---|---|---|---|---|---|"]
    for name, p in report["people"].items():
        lines.append(f"| {name} | {p['source']} | {p['falls_detected']} of {p['falls']} | {p['falls_missed']} | "
                     f"{p['adl_with_false_alarm']} of {p['adl']} ({p['false_alarms']} alarms) | {p['status']} |")
        lines += [f"|  | needs {m} | | | | |" for m in p["missing"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    report = m4_report(json.loads(Path(sys.argv[1]).read_text()))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=1))
    markdown = to_markdown(report)
    (OUT / "report.md").write_text(markdown)
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
