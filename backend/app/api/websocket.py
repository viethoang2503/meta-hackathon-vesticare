"""
websocket.py

SSE endpoint — streams real-time detection state to dashboard clients.

    GET /api/stream

Response: text/event-stream
  - Each event: JSON-encoded state update (see sensor.py for payload shape)
  - Keepalive comment sent every 15 s to prevent proxy timeouts
  - Client disconnection is detected and cleans up the subscriber queue
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.broadcaster import broadcaster

router = APIRouter(prefix="/api", tags=["Stream"])


@router.get("/stream")
async def sse_stream(request: Request):
    q = broadcaster.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # keepalive — SSE comment, ignored by browser EventSource
                    yield ": keepalive\n\n"
        finally:
            broadcaster.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable Nginx buffering if behind proxy
        },
    )
