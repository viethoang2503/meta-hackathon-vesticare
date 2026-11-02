"""M2 table from the live sessions you ran.

    .verify/venv/bin/python backend/tools/m2_report.py SESSION_ID [SESSION_ID ...]

Tags every incident with the branch it exercised, breaks the timing of the spoken-answer path into its steps,
and counts each branch the spec lists as seen with real Telegram, seen with a stand-in, or not seen.
Writes docs/spatial/evidence/mvp2/m2/report.json and report.md. Raw audio is never stored; transcripts are.
"""

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.m2_report import m2_report  # noqa: E402

OUT = BACKEND.parent / "docs" / "spatial" / "evidence" / "mvp2" / "m2"


def to_markdown(report: dict) -> str:
    t, tg = report["schedule_timing"], report["telegram_send_ms"]
    lines = ["# M2 branches", "", f"{report['incidents']} incident(s). All branches seen with real Telegram: **{report['complete']}**.", "",
             "| Branch | What it covers | Seen | With real Telegram | Status |", "|---|---|---|---|---|"]
    lines += [f"| `{k}` | {b['text']} | {b['seen']} | {b['real_telegram']} | {b['status']} |" for k, b in report["branches"].items()]
    lines += ["", "## Timing", "",
              f"From classification (or the deadline, for silence) to the send being scheduled: {t['samples']} sample(s), "
              f"largest {t['max_ms']} ms, target {t['target_ms']} ms, within target: {t['within_target']}.",
              f"Telegram send, from scheduled to answered: {tg['n']} real send(s), {tg['min']} to {tg['max']} ms.",
              "Not measured: " + "; ".join(report["not_measured"]) + ".", "", "## Incidents", ""]
    for i in report["trials"]:
        lines.append(f"- Incident {i['incident_id']} ({i['channel']}, {i['source']}): {i['state']}, alert {i['alert_reason']}. "
                     f"Tags: {', '.join(i['tags']) or 'none'}.")
        for a in i["answers"]:
            lines.append(f"  - heard “{a['transcript']}” as {a['response']} (applied {a['applied']}, {a['reason']}); "
                         f"received {a['received_ms']} ms, queue {a['queue_ms']} ms, STT {a['stt_ms']} ms, classified {a['classified_ms']} ms"
                         + (f", scheduled {a['scheduled_ms']} ms" if "scheduled_ms" in a else ""))
        for s in i["sends"]:
            lines.append(f"  - send to {s['contact']}: {s['status']} via {s['channel']}, scheduled {s['scheduled_ms']} ms, done {s['done_ms']} ms")
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    report = m2_report(sys.argv[1:])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=1))
    markdown = to_markdown(report)
    (OUT / "report.md").write_text(markdown)
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
