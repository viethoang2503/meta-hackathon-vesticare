"""U0 device probe (MVP2_PLAN.md): can this iPhone play a line, record a decodable
turn and upload a frame? Off unless VC_PROBE_TOKEN is set, because the tunnel puts
these routes on the internet.
"""

from __future__ import annotations

import asyncio
import hmac
import io
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import av
import numpy as np
from fastapi import APIRouter, Header, HTTPException, Request

from app.config import PROBE_TOKEN

router = APIRouter(prefix="/api/probe", tags=["Probe"])

REPO = Path(__file__).resolve().parents[3]
# Raw audio and room photos stay out of git; only the measured report is evidence.
DATA_DIR = REPO / "backend" / "probe_data"
REPORT_PATH = REPO / "docs" / "spatial" / "evidence" / "mvp2" / "u0" / "trials.jsonl"
MAX_BODY_BYTES = 3 * 1024 * 1024
TRIAL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_whisper = None
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


def decode_audio(data: bytes) -> dict:
    """Container facts plus 16 kHz mono PCM, or the decoder's error."""
    try:
        with av.open(io.BytesIO(data)) as container:
            stream = container.streams.audio[0]
            facts = {"container": container.format.name, "codec": stream.codec_context.name,
                     "source_rate": stream.rate, "channels": stream.codec_context.channels}
            resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
            chunks = []
            for frame in container.decode(stream):
                chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
            chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]
    except (av.FFmpegError, IndexError, ValueError) as exc:
        return {"decodable": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    pcm = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, np.float32)
    level = lambda x: round(20 * float(np.log10(x + 1e-12)), 1)
    return {"decodable": True, **facts, "duration_s": round(len(pcm) / 16000, 2),
            "rms_dbfs": level(np.sqrt(np.mean(pcm ** 2))) if len(pcm) else None,
            "peak_dbfs": level(np.max(np.abs(pcm))) if len(pcm) else None, "pcm": pcm}


def transcribe(pcm: np.ndarray) -> dict:
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel("small", device="cpu", compute_type="int8")
        started = time.perf_counter()
        segments, _ = _whisper.transcribe(pcm, language="en", beam_size=1, vad_filter=False)
        text = " ".join(s.text.strip() for s in segments)
    return {"transcript": text, "stt_ms": round((time.perf_counter() - started) * 1000)}


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
