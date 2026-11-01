"""
clock_sync.py

Maps a device's monotonic clock onto the backend's (MVP2_ARCHITECTURE.md, "Firmware và
đồng hồ"). Each round is NTP-style: the device notes t0 when it sends and t3 when the
reply arrives; the backend notes t1 on receipt and t2 just before replying. The round
with the smallest network time gives the offset; half of that time bounds the error.
Pure: callers pass the timestamps.
"""

from __future__ import annotations

MIN_ROUNDS = 5
# Device timestamps are whole milliseconds (millis()), so on a fast link a round can
# look up to 1 ms shorter than the backend's own handling time.
DEVICE_RESOLUTION_MS = 1.0


def best_mapping(rounds: list[dict]) -> dict | None:
    """`rounds` hold t0..t3 in ms. Returns offset (backend = device + offset), rtt and
    uncertainty from the lowest-rtt round, or None with fewer than MIN_ROUNDS usable rounds."""
    usable = []
    for r in rounds:
        rtt = (r["t3"] - r["t0"]) - (r["t2"] - r["t1"])
        if r["t3"] >= r["t0"] and r["t2"] >= r["t1"] and rtt >= -DEVICE_RESOLUTION_MS:
            usable.append((max(rtt, 0.0), r))
    if len(usable) < MIN_ROUNDS:
        return None
    rtt, r = min(usable, key=lambda item: item[0])
    offset = ((r["t1"] - r["t0"]) + (r["t2"] - r["t3"])) / 2
    uncertainty = rtt / 2 + DEVICE_RESOLUTION_MS / 2
    return {"offset_ms": round(offset, 1), "rtt_ms": round(rtt, 1), "uncertainty_ms": round(uncertainty, 1),
            "rounds": len(rounds), "usable_rounds": len(usable)}
