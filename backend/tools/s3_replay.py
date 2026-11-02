"""Replays an S3 package twice (M5a detector, M5b policy) and writes the evidence.

    .verify/venv/bin/python backend/tools/s3_replay.py [assets/scenarios/S3]

Writes docs/spatial/evidence/mvp2/s3/replay.json and replay.md. Nothing is sent and no model is asked:
the policy runs on a virtual clock from the recorded response events.
"""

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.s3_package import PackageRefused, load  # noqa: E402
from app.services.s3_replay import run_replay  # noqa: E402

OUT = BACKEND.parent / "docs" / "spatial" / "evidence" / "mvp2" / "s3"


def to_markdown(manifest: dict, report: dict) -> str:
    a, b = report["M5a"], report["M5b"]
    unfinished = [] if manifest.get("complete", True) else [
        f"**Incomplete recording.** {'; '.join(manifest['incomplete_reasons'])}.", ""]
    stand_in = [] if manifest["source"] == "esp32" else [f"**Simulated: not S3 evidence.** This package came from: {manifest['source_label']}.", ""]
    return "\n".join([
        "# S3 replay", "", *stand_in, *unfinished,
        f"Package: {manifest['source_label']}, recorded {manifest['recorded_on']}, "
        f"{manifest['contents']['incidents']} incident(s), {manifest['contents']['frames']} frames. Participant consent: "
        f"publication {manifest['consent']['publication']}.", "",
        "| Check | Result | Detail |", "|---|---|---|",
        f"| M5a detector, two replays | **{a['status']}** | incidents {a['incidents']}, source start ms {a['source_start_ms']}; "
        f"same model: {a['same_model']}, same detector config: {a['same_config']}"
        f"{'' if a['same_config'] else ' (' + '; '.join(a['config_differences']) + ')'}; "
        f"matches the live run: {a['matches_recorded']} ({a['recorded_incidents']} recorded) |",
        f"| M5b policy, two replays | **{b['status']}** | {b['events']} recorded response events; "
        f"matches the live chain: {b['matches_recorded_chain']}; largest time difference {b['max_time_difference_ms']} ms |", "",
        f"Replay uses the config and model hashes in the package: model {manifest['model']['keras_sha256'][:12]}…, "
        f"scaler {manifest['model']['scaler_sha256'][:12]}…. No speech model, agent or Telegram call runs.", ""])


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else BACKEND.parent / "assets" / "scenarios" / "S3"
    try:
        package = load(path)
    except (PackageRefused, FileNotFoundError) as why:
        print(f"Cannot replay: {why}")
        return 1
    report = run_replay(package)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "replay.json").write_text(json.dumps(report, indent=1))
    markdown = to_markdown(package["manifest"], report)
    (OUT / "replay.md").write_text(markdown)
    print(markdown)
    return 0 if report["M5a"]["status"] == "Pass" and report["M5b"]["status"] == "Pass" else 1


if __name__ == "__main__":
    sys.exit(main())
