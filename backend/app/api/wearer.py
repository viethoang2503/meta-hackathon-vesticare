"""
wearer.py

HTTP surface for iPhone 1 in a live session (docs/spatial/MVP2_ARCHITECTURE.md,
"WearerChannel và STT"). Every route needs the session's wearer token in X-Wearer-Token;
the token opens nothing else (no Start, Stop, Ack or evidence).
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.spatial_runs import spatial_runs

router = APIRouter(prefix="/api/wearer/{session_id}", tags=["Wearer"])
MAX_AUDIO_BYTES = 2 * 1024 * 1024
MAX_FRAME_BYTES = 1024 * 1024


class Played(BaseModel):
    utterance_id: int = Field(ge=1)
    speech_start: float | None = None  # phone performance.now() ms
    speech_end: float | None = None


class Status(BaseModel):
    microphone_error: str | None = Field(default=None, max_length=200)


class ClockRound(BaseModel):
    t0: float
    t1: float
    t2: float
    t3: float


class ClockReport(BaseModel):
    rounds: list[ClockRound] = Field(min_length=1, max_length=20)


def _ok(value):
    if value is None:
        raise HTTPException(status_code=401, detail="Unknown live session or wrong wearer token")
    return value


@router.get("/poll")
def poll(session_id: str, after: int = 0, x_wearer_token: str | None = Header(default=None)):
    return _ok(spatial_runs.wearer_poll(session_id, x_wearer_token, after))


@router.post("/played")
def played(session_id: str, body: Played, x_wearer_token: str | None = Header(default=None)):
    return {"recorded": _ok(spatial_runs.wearer_played(session_id, x_wearer_token, body.utterance_id,
                                                       body.speech_start, body.speech_end))}


@router.post("/status")
def status(session_id: str, body: Status, x_wearer_token: str | None = Header(default=None)):
    return {"recorded": _ok(spatial_runs.wearer_status(session_id, x_wearer_token, body.microphone_error))}


@router.post("/clock")
def clock(session_id: str, x_wearer_token: str | None = Header(default=None)):
    t1 = time.monotonic() * 1000
    _ok(spatial_runs._channel(session_id, x_wearer_token))
    return {"t1": t1, "t2": time.monotonic() * 1000}


@router.post("/clock/report")
def clock_report(session_id: str, body: ClockReport, x_wearer_token: str | None = Header(default=None)):
    mapping = _ok(spatial_runs.wearer_clock_report(session_id, x_wearer_token, [r.model_dump() for r in body.rounds]))
    if not mapping:
        raise HTTPException(status_code=422, detail="Need at least 5 usable rounds")
    return mapping


@router.post("/audio", status_code=202)
async def audio(session_id: str, request: Request, incident_id: int, capture_start: float | None = None,
                capture_end: float | None = None, x_wearer_token: str | None = Header(default=None)):
    """One finalized listening turn; capture_start/capture_end are the phone's performance.now() ms."""
    data = await request.body()
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413 if data else 422, detail="Audio missing or larger than 2 MB")
    return _ok(await spatial_runs.wearer_audio(session_id, x_wearer_token, incident_id, data, capture_start,
                                               capture_end, request.headers.get("content-type", "")))


@router.post("/frame", status_code=202)
async def frame(session_id: str, request: Request, capture: float | None = None,
                x_wearer_token: str | None = Header(default=None)):
    """One JPEG from the forehead camera; `capture` is the phone's performance.now() ms at capture."""
    data = await request.body()
    if not data or len(data) > MAX_FRAME_BYTES:
        raise HTTPException(status_code=413 if data else 422, detail="Frame missing or larger than 1 MB")
    if data[:2] != b"\xff\xd8":
        raise HTTPException(status_code=422, detail="Frame is not a JPEG")
    return _ok(await spatial_runs.wearer_frame(session_id, x_wearer_token, data, capture))
