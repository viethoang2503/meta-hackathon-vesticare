"""
broadcaster.py

Thread-safe SSE broadcaster.

Sync routes (sensor ingest) call publish() from a threadpool thread.
Async SSE clients subscribe via asyncio.Queue and get events pushed to them.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Set


def _safe_put(q: asyncio.Queue, event: dict) -> None:
    """Scheduled inside the event loop by publish(). Catches QueueFull silently."""
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        pass  # slow client — drop rather than propagate an unhandled callback error


class SSEBroadcaster:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: Set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at startup from the async lifespan context."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE client. Returns its dedicated queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a client queue (called when client disconnects)."""
        with self._lock:
            self._queues.discard(q)

    def publish(self, event: dict) -> None:
        """
        Publish an event to all connected clients.
        Safe to call from a sync thread (e.g. FastAPI threadpool route).
        Drops events for slow clients instead of blocking.
        """
        if self._loop is None or not self._loop.is_running():
            return
        with self._lock:
            queues = list(self._queues)
        for q in queues:
            self._loop.call_soon_threadsafe(_safe_put, q, event)


broadcaster = SSEBroadcaster()
