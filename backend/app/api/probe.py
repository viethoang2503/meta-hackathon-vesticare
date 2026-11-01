"""U0 device probe (MVP2_PLAN.md): can this iPhone play a line, record a decodable
turn and upload a frame? Off unless VC_PROBE_TOKEN is set, because the tunnel puts
these routes on the internet.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Header, HTTPException, Request

from app.config import PROBE_TOKEN
from app.services.speech import decode_audio, whisper_model

router = APIRouter(prefix="/api/probe", tags=["Probe"])

REPO = Path(__file__).resolve().parents[3]
# Raw audio and room photos stay out of git; only the measured report is evidence.
DATA_DIR = REPO / "backend" / "probe_data"
REPORT_PATH = REPO / "docs" / "spatial" / "evidence" / "mvp2" / "u0" / "trials.jsonl"
MAX_BODY_BYTES = 3 * 1024 * 1024
TRIAL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_whisper_lock = threading.Lock()


def _authorize(token: str | None) -> None:
    if not PROBE_TOKEN:
        raise HTTPException(404)
    if not token or not hmac.compare_digest(token, PROBE_TOKEN):
        raise HTTPException(401, "bad probe token")


def _trial(trial_id: str) -> str:
    if not TRIAL_ID.match(trial_id):
        raise HTTPException(422, "bad trial id")
    return trial_id


async def _body(request: Request) -> bytes:
    data = await request.body()
    if not data:
        raise HTTPException(422, "empty body")
    if len(data) > MAX_BODY_BYTES:
        raise HTTPException(413, "body too large")
    return data


def transcribe(pcm: np.ndarray) -> dict:
    model = whisper_model()
    with _whisper_lock:
        out = {}
        # The first iPhone run heard "Thank you." in silent turns; run both so the
        # report shows whether Silero VAD removes that without losing real speech.
        for key, vad in (("raw", False), ("vad", True)):
            started = time.perf_counter()
            segments = list(model.transcribe(pcm, language="en", beam_size=1, vad_filter=vad,
                                                condition_on_previous_text=False)[0])
            out[f"transcript_{key}"] = " ".join(s.text.strip() for s in segments)
            out[f"stt_{key}_ms"] = round((time.perf_counter() - started) * 1000)
            out[f"no_speech_{key}"] = round(max((s.no_speech_prob for s in segments), default=1.0), 2)
    return out


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the first SOF marker, or None when it is not a JPEG."""
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + length
    return None


@router.post("/audio")
async def probe_audio(request: Request, trial: str, stt: bool = False, keep: bool = False,
                      x_probe_token: str | None = Header(default=None)):
    _authorize(x_probe_token)
    trial = _trial(trial)
    data = await _body(request)
    mime = request.headers.get("content-type", "")
    result = await asyncio.to_thread(decode_audio, data)
    pcm = result.pop("pcm", None)
    if stt and pcm is not None and len(pcm):
        result |= await asyncio.to_thread(transcribe, pcm)
    if keep:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / f"{trial}.audio").write_bytes(data)
    return {"trial": trial, "mime": mime, "bytes": len(data), "kept": keep, **result}


@router.post("/frame")
async def probe_frame(request: Request, trial: str, x_probe_token: str | None = Header(default=None)):
    _authorize(x_probe_token)
    trial = _trial(trial)
    data = await _body(request)
    size = jpeg_size(data)
    if size is None:
        raise HTTPException(422, "not a JPEG")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{trial}.jpg").write_bytes(data)
    return {"trial": trial, "bytes": len(data), "width": size[0], "height": size[1]}


@router.post("/report")
async def probe_report(request: Request, x_probe_token: str | None = Header(default=None)):
    _authorize(x_probe_token)
    try:
        report = json.loads(await _body(request))
    except json.JSONDecodeError:
        raise HTTPException(422, "not JSON")
    if not isinstance(report, dict):
        raise HTTPException(422, "report must be an object")
    _trial(str(report.get("trial", "")))
    row = {"received_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **report}
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"recorded": True}
