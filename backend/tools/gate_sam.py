"""Gate G2: run SAM 3.1 on demo frames and record what it finds.

    python backend/tools/gate_sam.py --prompt stairs --out <dir> frame1.png frame2.png ...

Reads MODEL_API_KEY from backend/.env. For each image it saves the raw
response JSON and prints latency, object count and boxes, so the gate result
can be checked against the images by eye.
"""

import argparse
import base64
import json
import re
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

API_URL = "https://api.meta.ai/v1/responses"
BOX = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)\|>")


def collect_text(node) -> str:
    """The segmentation lines live in output_text parts; gather every string that carries them."""
    if isinstance(node, str):
        return node if "<|box" in node or "<|mask" in node else ""
    if isinstance(node, dict):
        return "\n".join(filter(None, (collect_text(v) for v in node.values())))
    if isinstance(node, list):
        return "\n".join(filter(None, (collect_text(v) for v in node)))
    return ""


def segment(client: httpx.Client, key: str, image: Path, prompt: str) -> tuple[int, float, dict]:
    data_url = "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode()
    body = {
        "model": "sam-3.1",
        "input": [{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": data_url},
            ],
        }],
    }
    started = time.perf_counter()
    resp = client.post(API_URL, json=body, headers={"Authorization": f"Bearer {key}"})
    elapsed = time.perf_counter() - started
    try:
        payload = resp.json()
    except ValueError:
        payload = {"non_json_body": resp.text[:2000]}
    return resp.status_code, elapsed, payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args()

    key = dotenv_values(Path(__file__).resolve().parents[1] / ".env").get("MODEL_API_KEY", "")
    if not key:
        raise SystemExit("MODEL_API_KEY is empty in backend/.env")
    args.out.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=120) as client:
        for image in args.images:
            status, elapsed, payload = segment(client, key, image, args.prompt)
            (args.out / f"{image.stem}.{args.prompt.replace(' ', '_')}.json").write_text(json.dumps(payload, indent=2))
            boxes = BOX.findall(collect_text(payload))
            print(f"{image.name}  prompt={args.prompt!r}  HTTP {status}  {elapsed:.2f}s  objects={len(boxes)}")
            for x1, y1, x2, y2, w, h in boxes:
                print(f"    box=({x1},{y1})-({x2},{y2}) on {w}x{h}")
            if status != 200:
                print("    error:", json.dumps(payload)[:300])


if __name__ == "__main__":
    main()
