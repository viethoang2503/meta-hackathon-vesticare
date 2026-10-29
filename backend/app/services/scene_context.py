"""
scene_context.py

Where the wearer is and what the glasses saw at a scenario's source time. Both
come from authored data: the route in scenario.json and the frames exported
along it by backend/tools/export_frames.py. Nothing here is measured from the
IMU, and every value returned says so in its `source` field.
"""

from __future__ import annotations

import json
from bisect import bisect_left
from functools import lru_cache

from app.config import FRAME_ALIGNMENT_TOLERANCE_MS, SCENARIOS_DIR

HOMES_DIR = SCENARIOS_DIR.parent / "homes"


@lru_cache(maxsize=4)
def home_manifest(home_id: str) -> dict:
    return json.loads((HOMES_DIR / home_id / "manifest.json").read_text())


def pose_at(route: list[list[float]], source_ms: float) -> dict:
    """Linear interpolation between route keypoints [t_ms, x, y, z, look x, look y, look z]."""
    if source_ms <= route[0][0]:
        point = route[0][1:]
    elif source_ms >= route[-1][0]:
        point = route[-1][1:]
    else:
        i = bisect_left([p[0] for p in route], source_ms)
        a, b = route[i - 1], route[i]
        f = (source_ms - a[0]) / (b[0] - a[0])
        point = [a[k] + f * (b[k] - a[k]) for k in range(1, 7)]
    return {"eye": point[:3], "look": point[3:]}


def zone_at(manifest: dict, x: float, z: float) -> dict | None:
    """Mirrors zoneAt in dashboard/spatial/scene.js: a hazard zone wins where zones overlap."""
    hits = [
        zone for zone in manifest["zones"]
        if zone["rect"]["x"][0] <= x <= zone["rect"]["x"][1] and zone["rect"]["z"][0] <= z <= zone["rect"]["z"][1]
    ]
    return next((zone for zone in hits if zone["fall_exposure"]), hits[0] if hits else None)


def location_at(scenario_meta: dict, source_ms: float) -> dict:
    pose = pose_at(scenario_meta["route"], source_ms)
    x, _, z = pose["eye"]
    zone = zone_at(home_manifest(scenario_meta["home"]), x, z)
    return {
        "source": "Authored location",
        "x": round(x, 3),
        "z": round(z, 3),
        "zone_id": zone["id"] if zone else None,
        "zone_label": zone["label"] if zone else None,
        "fall_exposure": bool(zone and zone["fall_exposure"]),
    }


@lru_cache(maxsize=8)
def frames_index(scenario_id: str) -> dict | None:
    path = SCENARIOS_DIR / scenario_id / "frames.json"
    return json.loads(path.read_text()) if path.is_file() else None


def frame_near(scenario_id: str, source_ms: float) -> dict:
    """The exported frame closest to source_ms, or Unavailable when none is within tolerance."""
    index = frames_index(scenario_id)
    if index is None:
        return {"status": "Unavailable", "reason": "no frames exported for this scenario"}
    frame = min(index["frames"], key=lambda f: abs(f["source_ms"] - source_ms))
    error = abs(frame["source_ms"] - source_ms)
    if error > FRAME_ALIGNMENT_TOLERANCE_MS:
        return {"status": "Unavailable", "reason": f"nearest frame is {error:.0f} ms away"}
    return {
        "status": "Available",
        "id": frame["id"],
        "url": f"/spatial-assets/scenarios/{scenario_id}/{frame['file']}",
        "source_ms": frame["source_ms"],
        "alignment_error_ms": round(error),
        "label": index["label"],
        "scene": f"{index['home']} v{index['home_version']}",
        "pose": frame["pose"],
    }
