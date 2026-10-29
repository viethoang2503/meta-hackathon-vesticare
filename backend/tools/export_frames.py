"""Export the glasses camera along a scenario's authored route as evidence frames.

    .verify/venv/bin/python backend/tools/export_frames.py --base http://127.0.0.1:<port> S1 S2

Needs a running backend (vc up) because the page renders the home. Poses come
from scene_context.pose_at, the same function the backend uses for locations,
so a frame and the location reported for its time always agree. Writes
assets/scenarios/<id>/frames/<ms>.jpg and frames.json (pose, sha256 per frame).
"""

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import SCENARIOS_DIR  # noqa: E402
from app.services.scene_context import home_manifest, pose_at  # noqa: E402

INTERVAL_MS = 500
WIDTH, HEIGHT = 640, 480


def export(page, scenario_id: str) -> int:
    folder = SCENARIOS_DIR / scenario_id
    meta = json.loads((folder / "scenario.json").read_text())
    out = folder / "frames"
    out.mkdir(exist_ok=True)
    for old in out.glob("*.jpg"):
        old.unlink()
    frames = []
    for ms in range(0, meta["duration_ms"] + 1, INTERVAL_MS):
        pose = pose_at(meta["route"], ms)
        data_url = page.evaluate("([p, w, h]) => window.__spatial.renderPose(p, w, h)", [pose, WIDTH, HEIGHT])
        jpeg = base64.b64decode(data_url.split(",", 1)[1])
        name = f"frames/{ms:06d}.jpg"
        (folder / name).write_bytes(jpeg)
        frames.append({
            "id": f"{scenario_id}-{ms:06d}",
            "source_ms": ms,
            "file": name,
            "pose": {k: [round(v, 4) for v in pose[k]] for k in ("eye", "look")},
            "sha256": hashlib.sha256(jpeg).hexdigest()[:16],
        })
    manifest = home_manifest(meta["home"])
    (folder / "frames.json").write_text(json.dumps({
        "scenario": scenario_id,
        "home": meta["home"],
        "home_version": manifest["version"],
        "label": "Rendered frame",
        "width": WIDTH,
        "height": HEIGHT,
        "interval_ms": INTERVAL_MS,
        "frames": frames,
    }, indent=1))
    return len(frames)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("scenarios", nargs="+")
    args = parser.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(f"{args.base}/spatial/", wait_until="load")
        page.wait_for_function("window.__spatial && window.__spatial.ready", timeout=30000)
        for sid in args.scenarios:
            print(f"{sid}: {export(page, sid)} frames")
        browser.close()


if __name__ == "__main__":
    main()
