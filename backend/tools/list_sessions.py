"""Lists the most recent live sessions, so you can copy an id into g4_report, m2_report, m4_report or s3_export.

    .verify/venv/bin/python backend/tools/list_sessions.py [N]

Shows the id, when it started (your local time), its state, the source, the alert channel, the number of incidents
and whether camera frames were recorded. Nothing secret is printed.
"""

import json
import sys
from datetime import timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import database  # noqa: E402
from app.models.schemas import AlertEvent, SpatialSession  # noqa: E402


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    with database.SessionLocal() as db:
        sessions = (db.query(SpatialSession).filter(SpatialSession.kind == "live")
                    .order_by(SpatialSession.started_at.desc()).limit(limit).all())
        if not sessions:
            print("No live sessions yet.")
        for s in sessions:
            config = json.loads(s.config_snapshot or "{}")
            incidents = db.query(AlertEvent).filter(AlertEvent.session_id == s.id).count()
            started = s.started_at.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            print(f"{s.id}  {started}  {s.status:<11} {config.get('source', '?'):<12} alerts:{config.get('notification_mode', '?'):<9} "
                  f"incidents:{incidents}  frames_recorded:{config.get('record_frames', False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
