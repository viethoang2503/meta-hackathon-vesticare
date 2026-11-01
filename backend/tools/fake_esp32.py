"""Stand-in for the ESP32 in a live session: streams a recorded scenario to /api/sensor/batch
in the MVP 2 batch contract (boot_id, batch_id, dropped_at_source, sample_seq, device_ms).

    .verify/venv/bin/python backend/tools/fake_esp32.py --base http://127.0.0.1:8010 --start S2

--start opens the live session first. Without --fast it sends 10 samples every 200 ms like
the firmware. --gap-at drops samples at a source time to show a detector reset. The
samples are recordings, not this project's device, so the result is never G4 evidence.
"""

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import SCENARIOS_DIR  # noqa: E402

BATCH = 10
BOOT_DEVICE_MS = 12_345  # where the device clock stands at the first sample


def batches(table: np.ndarray, gap_at_ms: int | None, gap_ms: int):
    """Yields (first index, rows) in firmware order; a gap drops samples but keeps seq counting."""
    rows = [(i, r) for i, r in enumerate(table)
            if gap_at_ms is None or not gap_at_ms <= r[0] < gap_at_ms + gap_ms]
    for k in range(0, len(rows), BATCH):
        yield rows[k:k + BATCH]


def payload(chunk, patient_id: int, device_id: str, boot_id: str, batch_id: int, dropped: int) -> dict:
    return {
        "patient_id": patient_id, "device_id": device_id, "firmware_version": "fake-esp32",
        "transport": "wifi", "boot_id": boot_id, "batch_id": batch_id, "dropped_at_source": dropped,
        "samples": [
            {"sample_seq": int(i), "device_ms": BOOT_DEVICE_MS + int(r[0]),
             **dict(zip(("ax", "ay", "az", "gx", "gy", "gz", "svm"), map(float, r[1:])))}
            for i, r in chunk
        ],
    }


def sync_clock(client, headers, device_id: str, boot_id: str, device_now) -> dict:
    """The firmware's five clock rounds and report (vesticare-sensor/src/main.cpp, syncClock)."""
    ident = {"device_id": device_id, "boot_id": boot_id}
    rounds = []
    for _ in range(5):
        t0 = device_now()
        r = client.post("/api/sensor/clock", headers=headers, json=ident)
        r.raise_for_status()
        rounds.append({"t0": t0, **r.json(), "t3": device_now()})
    r = client.post("/api/sensor/clock/report", headers=headers, json={**ident, "rounds": rounds})
    r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario")
    ap.add_argument("--base", default="http://127.0.0.1:8010")
    ap.add_argument("--patient", type=int, default=1)
    ap.add_argument("--device", default="fake-esp32")
    ap.add_argument("--start", action="store_true", help="open the live session before streaming")
    ap.add_argument("--fast", action="store_true", help="send without waiting 200 ms between batches")
    ap.add_argument("--gap-at", type=int, default=None, help="source ms where samples go missing")
    ap.add_argument("--notify", choices=("simulated", "telegram"), default="simulated",
                    help="caregiver channel for the session --start opens")
    ap.add_argument("--gap-ms", type=int, default=1000)
    args = ap.parse_args()

    table = np.loadtxt(SCENARIOS_DIR / args.scenario / "imu.csv", delimiter=",", skiprows=1)
    headers = {"Authorization": f"Bearer {os.environ['DEVICE_TOKEN']}"} if os.environ.get("DEVICE_TOKEN") else {}
    with httpx.Client(base_url=args.base, timeout=10) as client:
        if args.start:
            r = client.post("/api/spatial/live", json={"patient_id": args.patient, "device_id": args.device,
                                                       "source": "fake_esp32", "notification_mode": args.notify})
            r.raise_for_status()
            print(f"live session {r.json()['id']}")
        boot_id = uuid.uuid4().hex[:12]
        sent, last = 0, {}
        started = time.monotonic()
        # The fake device clock runs from boot like millis(); in real time it matches device_ms below.
        device_now = lambda: BOOT_DEVICE_MS + round((time.monotonic() - started) * 1000)
        print(f"clock {sync_clock(client, headers, args.device, boot_id, device_now)}")
        for batch_id, chunk in enumerate(batches(table, args.gap_at, args.gap_ms)):
            dropped = chunk[-1][0] + 1 - sent - len(chunk)  # samples the "device" skipped so far
            sent += len(chunk)
            r = client.post("/api/sensor/batch", headers=headers,
                            json=payload(chunk, args.patient, args.device, boot_id, batch_id, dropped))
            if r.status_code != 200:
                print(f"batch {batch_id}: HTTP {r.status_code} {r.text[:200]}")
                return 1
            last = r.json()
            if last.get("incidents_opened"):
                print(f"batch {batch_id}: incident {last['incidents_opened']} at source ~{int(chunk[-1][1][0])} ms")
            if not args.fast:
                # Pace by schedule like the firmware's sampling clock, not by request round trips.
                time.sleep(max(0.0, started + (batch_id + 1) * BATCH / 50 - time.monotonic()))
        print(f"sent {sent} of {len(table)} rows, last readiness {last.get('readiness')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
