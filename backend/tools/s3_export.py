"""Writes the S3 package of a finished live session.

    .verify/venv/bin/python backend/tools/s3_export.py SESSION_ID --participant "person A" \\
        --publication granted --recorded-on 2026-11-03 [--out assets/scenarios/S3] [--force]

Needs the person's consent fields: who (a label, not a name you do not have permission to publish),
whether they allow publication, and the recording date. The session must be stopped and should have
been started with record_frames on. Refuses, and writes nothing, if a token or key would be inside.
"""

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import LIVE_DATA_DIR  # noqa: E402
from app.services.s3_package import PackageRefused, build  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session")
    ap.add_argument("--participant", required=True)
    ap.add_argument("--publication", required=True, choices=("granted", "not_granted"))
    ap.add_argument("--recorded-on", required=True)
    ap.add_argument("--out", default=str(BACKEND.parent / "assets" / "scenarios" / "S3"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="export a session whose incident had not finished; the package says it is incomplete")
    args = ap.parse_args()
    try:
        manifest = build(args.session, Path(args.out), {"participant": args.participant, "publication": args.publication,
                                                          "recorded_on": args.recorded_on}, LIVE_DATA_DIR, force=args.force,
                         allow_incomplete=args.allow_incomplete)
    except PackageRefused as why:
        print(f"Not written: {why}")
        return 1
    print(f"Wrote {args.out}: {manifest['contents']}")
    if not manifest["contents"]["frames"]:
        print("No frames in this package: the session was not started with record_frames.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
