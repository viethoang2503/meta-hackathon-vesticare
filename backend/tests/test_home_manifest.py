"""The authored home matches the SPEC inventory and the renderer can build every object (AC01)."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "assets" / "homes" / "home1" / "manifest.json").read_text())
SCENE_JS = (ROOT / "dashboard" / "spatial" / "scene.js").read_text()

# docs/spatial/SPEC.md, "Ngôi nhà và giao diện"
REQUIRED = {
    "living_room": {"sofa", "coffee_table", "tv_stand", "rug", "floor_lamp", "plant"},
    "kitchen": {"counter", "sink", "stove", "fridge", "dining_table", "chair"},
    "bedroom": {"bed", "wardrobe", "nightstand", "table_lamp"},
    "bathroom": {"toilet", "bath_sink", "mirror", "shower"},
}


def kinds_in(zone):
    return {o["kind"] for o in MANIFEST["objects"] if o["zone"] == zone}


def test_every_room_has_its_required_furniture():
    missing = {zone: sorted(req - kinds_in(zone)) for zone, req in REQUIRED.items() if req - kinds_in(zone)}
    assert missing == {}


def test_living_room_has_a_window_and_the_hall_has_the_front_door():
    openings = [o for w in MANIFEST["walls"] for o in w.get("openings", [])]
    assert any(o["kind"] == "window" for o in openings)
    assert any(o.get("id") == "front_door" for o in openings)


def test_every_object_kind_has_a_builder_in_scene_js():
    builders = set(re.findall(r"^  (\w+)\(g(?:, o)?\) \{", SCENE_JS, flags=re.M))
    assert {o["kind"] for o in MANIFEST["objects"]} - builders == set()


def test_hazard_zones_carry_the_advisory_and_sam_prompt():
    hazards = [z for z in MANIFEST["zones"] if z["fall_exposure"]]
    assert [z["id"] for z in hazards] == ["stairs_approach", "stairs_flight", "stairs_landing"]
    assert all(z["advisory_text"] and z["hazard_prompt"] == "stairs" for z in hazards)


def test_stair_flight_footprint_sits_inside_its_zone():
    s = MANIFEST["stairs"]
    x0, z0 = s["start"]
    x1, z1 = x0 + s["steps"] * s["tread"], z0 + s["width"]
    zone = next(z for z in MANIFEST["zones"] if z["id"] == s["zone"])
    assert zone["rect"]["x"] == pytest.approx([x0, x1]) and zone["rect"]["z"] == pytest.approx([z0, z1])


def test_selectable_objects_are_the_stairs_rug_and_chairs():
    assert sorted(o["id"] for o in MANIFEST["objects"] if o.get("selectable")) == [
        "chair_1", "chair_2", "chair_3", "chair_4", "living_rug",
    ]
