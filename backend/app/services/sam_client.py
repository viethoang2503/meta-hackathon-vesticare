"""
sam_client.py

One SAM 3.1 image segmentation call through the Meta Model API. Returns the
raw output_text (the browser decodes masks from it with @meta-sam/parser) and
the boxes parsed from it. Raises SamError with a status the analysis record can
store; never invents a result.
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass

import httpx

from app.config import ANALYSIS_TIMEOUT_SECONDS, MODEL_API_BASE, MODEL_API_KEY, SAM_MODEL

BOX = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)\|>")


@dataclass(frozen=True)
class SamResult:
    output_text: str
    boxes: list[dict]
    duration_ms: int
    model: str


class SamError(Exception):
    def __init__(self, status: str, reason: str):
        super().__init__(f"{status}: {reason}")
        self.status = status  # Failed | TimedOut
        self.reason = reason


def _output_text(payload: dict) -> str:
    return "".join(
        part.get("text", "")
        for item in payload.get("output", [])
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )


def segment(image: bytes, mime: str, prompt: str) -> SamResult:
    if not MODEL_API_KEY:
        raise SamError("Failed", "MODEL_API_KEY is not set")
    body = {
        "model": SAM_MODEL,
        "input": [{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:{mime};base64,{base64.b64encode(image).decode()}"},
            ],
        }],
    }
    started = time.perf_counter()
    try:
        resp = httpx.post(
            f"{MODEL_API_BASE}/responses",
            json=body,
            headers={"Authorization": f"Bearer {MODEL_API_KEY}"},
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        raise SamError("TimedOut", f"no response within {ANALYSIS_TIMEOUT_SECONDS} s")
    except httpx.HTTPError as exc:
        raise SamError("Failed", f"request error: {type(exc).__name__}")
    if resp.status_code != 200:
        raise SamError("Failed", f"HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        payload = resp.json()
    except ValueError:
        raise SamError("Failed", "response was not JSON")
    if payload.get("status") not in (None, "completed"):
        raise SamError("Failed", f"response status {payload.get('status')}")
    text = _output_text(payload)
    boxes = [
        {"x1": int(a), "y1": int(b), "x2": int(c), "y2": int(d), "w": int(w), "h": int(h)}
        for a, b, c, d, w, h in BOX.findall(text)
    ]
    return SamResult(text, boxes, round((time.perf_counter() - started) * 1000), payload.get("model", SAM_MODEL))
