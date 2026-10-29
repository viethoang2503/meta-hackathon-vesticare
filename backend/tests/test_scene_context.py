"""Authored location and exported frames agree with each other and with the route."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import SCENARIOS_DIR  # noqa: E402
from app.services.scene_context import frame_near, frames_index, location_at, pose_at  # noqa: E402

S2 = json.loads((SCENARIOS_DIR / "S2" / "scenario.json").read_text())
S2_FALL_START_MS = 23940


def test_pose_interpolates_between_route_keypoints_and_clamps_at_the_ends():
    assert pose_at(S2["route"], 23900) == {"eye": [2.0, 1.6, -3.1], "look": [5.0, 1.2, -3.7]}
    assert pose_at(S2["route"], -5) == pose_at(S2["route"], 0)
    assert pose_at(S2["route"], 99_999) == pose_at(S2["route"], S2["route"][-1][0])
    mid = pose_at(S2["route"], 3000)
    assert mid["eye"] == pytest.approx([3.0, 1.6, (2.5 + -0.6) / 2])


def test_the_s2_fall_is_located_at_the_foot_of_the_stairs():
    loc = location_at(S2, S2_FALL_START_MS)
    assert (loc["zone_id"], loc["fall_exposure"], loc["source"]) == ("stairs_approach", True, "Authored location")


@pytest.mark.parametrize("scenario_id", ["S1", "S2"])
def test_every_exported_frame_matches_the_route_pose_and_its_hash(scenario_id):
    meta = json.loads((SCENARIOS_DIR / scenario_id / "scenario.json").read_text())
    index = frames_index(scenario_id)
    assert index["label"] == "Rendered frame"
    assert [f["source_ms"] for f in index["frames"]] == list(range(0, meta["duration_ms"] + 1, index["interval_ms"]))
    for frame in index["frames"]:
        expected = pose_at(meta["route"], frame["source_ms"])
        assert frame["pose"]["eye"] == pytest.approx(expected["eye"], abs=1e-4)
        assert frame["pose"]["look"] == pytest.approx(expected["look"], abs=1e-4)
        data = (SCENARIOS_DIR / scenario_id / frame["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest()[:16] == frame["sha256"]


def test_frame_lookup_aligns_within_tolerance_or_reports_unavailable():
    found = frame_near("S2", S2_FALL_START_MS)
    assert (found["status"], found["id"], found["alignment_error_ms"]) == ("Available", "S2-024000", 60)
    assert found["url"] == "/spatial-assets/scenarios/S2/frames/024000.jpg"
    past_last = frame_near("S2", S2["duration_ms"])
    assert past_last["status"] == "Unavailable"
