"""
speech.py

Turns one recorded listening turn into text (docs/spatial/MVP2.md, "Kênh tiếng nói").
Whisper small, int8 on CPU, with Whisper's own VAD filter and no carried context: without
those, three of three silent iPhone turns produced text in U0
(docs/spatial/evidence/mvp2/u0/README.md).

One worker. At most MAX_WAITING turns wait behind the one being transcribed; a new turn
pushes out the oldest waiting one, because nobody knows yet which turn holds HELP and the
newest is the most current. Every drop is reported.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import io
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import av
import numpy as np

logger = logging.getLogger(__name__)

MAX_WAITING = 2
# A turn takes about 1 s (U0). Past this the turn is failed and the worker moves on: a stuck
# inference must not silence every later turn. The stuck thread cannot be killed; it is counted.
JOB_TIMEOUT_SECONDS = 20
WHISPER_SETTINGS = {"language": "en", "beam_size": 1, "vad_filter": True, "condition_on_previous_text": False}

_model = None
_model_lock = threading.Lock()


def whisper_model():
    """Loaded once, on first use (about 0.5 GB)."""
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel("small", device="cpu", compute_type="int8")
        return _model


def decode_audio(data: bytes) -> dict:
    """Container facts plus 16 kHz mono PCM under "pcm", or decodable=False and the decoder's error."""
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


def transcribe(pcm: np.ndarray) -> str:
    segments, _ = whisper_model().transcribe(pcm, **WHISPER_SETTINGS)
    return " ".join(s.text.strip() for s in segments)


@dataclass
class SpeechJob:
    data: bytes
    meta: dict
    on_done: Callable[["SpeechJob", dict], Awaitable[None]]
    submitted: float = field(default_factory=time.monotonic)


class SpeechWorker:
    def __init__(self, transcribe_fn: Callable[[np.ndarray], str] = transcribe, max_waiting: int = MAX_WAITING,
                 job_timeout: float = JOB_TIMEOUT_SECONDS):
        self._transcribe = transcribe_fn
        self._max_waiting = max_waiting
        self._job_timeout = job_timeout
        self.stuck = 0
        # One inference thread. A job that times out cannot be killed, so it keeps this thread; new turns
        # are refused until it ends instead of starting a second inference beside it.
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
        self._abandoned: concurrent.futures.Future | None = None
        self._waiting: deque[SpeechJob] = deque()
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self.overflows = 0

    def submit(self, job: SpeechJob) -> SpeechJob | None:
        """Queues a turn; returns the waiting turn it pushed out, if any. Call on the event loop."""
        if self._task is None or self._task.done():
            self._wake = asyncio.Event()
            self._task = asyncio.create_task(self._run())
        dropped = None
        if len(self._waiting) >= self._max_waiting:
            dropped = self._waiting.popleft()
            self.overflows += 1
            asyncio.create_task(dropped.on_done(dropped, {"status": "dropped", "reason": "queue_full",
                                                          "queue_depth": len(self._waiting) + 1}))
        self._waiting.append(job)
        self._wake.set()
        return dropped

    @property
    def depth(self) -> int:
        return len(self._waiting)

    async def _run(self) -> None:
        while True:
            if not self._waiting:
                self._wake.clear()
                await self._wake.wait()
                continue
            job = self._waiting.popleft()
            started = time.monotonic()
            if self._abandoned is not None and not self._abandoned.done():
                result = {"status": "failed", "error": "an earlier transcription is still running"}
            else:
                self._abandoned = None
                work = self._executor.submit(self._process, job)
                try:
                    result = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(work)), self._job_timeout)
                except asyncio.TimeoutError:
                    self.stuck += 1
                    self._abandoned = work
                    logger.error("speech job exceeded %s s; its thread is kept until it ends (%s stuck so far)",
                                 self._job_timeout, self.stuck)
                    result = {"status": "failed", "error": f"transcription took longer than {self._job_timeout} s"}
                except Exception as err:
                    logger.exception("speech job failed")
                    result = {"status": "failed", "error": type(err).__name__}
            result |= {"queue_ms": round((started - job.submitted) * 1000),
                       "stt_ms": round((time.monotonic() - started) * 1000)}
            try:
                await job.on_done(job, result)
            except Exception:
                logger.exception("speech result handler failed")

    def _process(self, job: SpeechJob) -> dict:
        decoded = decode_audio(job.data)
        pcm = decoded.pop("pcm", None)
        if not decoded["decodable"]:
            return {"status": "failed", "error": decoded["error"]}
        return {"status": "ok", "transcript": self._transcribe(pcm), **decoded}
