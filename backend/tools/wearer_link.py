"""QR code that opens the wearer page of a live session on iPhone 1.

    .verify/venv/bin/python backend/tools/wearer_link.py --tunnel https://<name>.trycloudflare.com [SESSION_ID]

Without SESSION_ID it uses the running live session. Writes the PNG to the scratch path it
prints and opens it. The link carries a single-use pairing code that expires after 10 minutes: do not share it.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import qrcode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", nargs="?")
    ap.add_argument("--tunnel", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8010")
    args = ap.parse_args()
    session = args.session
    if session is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app import database
        from app.models.schemas import SpatialSession
        with database.SessionLocal() as db:
            live = db.query(SpatialSession).filter(SpatialSession.kind == "live", SpatialSession.status == "Running").first()
        if live is None:
            print("No running live session")
            return 1
        session = live.id
    reply = httpx.post(f"{args.base}/api/spatial/sessions/{session}/pairing")
    if reply.status_code != 200:
        print(f"Session {session} has no wearer channel (not a running live session)")
        return 1
    url = f"{args.tunnel.rstrip('/')}/glasses/index.html#s={session}&p={reply.json()['code']}"
    out = Path(tempfile.gettempdir()) / f"wearer_{session[:8]}.png"
    qrcode.make(url, box_size=12).save(out)
    subprocess.run(["open", str(out)], check=False)
    print(f"QR for session {session} written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
