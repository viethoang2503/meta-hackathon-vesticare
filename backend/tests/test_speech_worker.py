import asyncio
import io
import math
import struct
import time
import wave

from app.services.speech import WHISPER_SETTINGS, SpeechJob, SpeechWorker


def wav(seconds=0.5, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(4000 * math.sin(i / 8))) for i in range(int(seconds * rate))))
    return buf.getvalue()


def test_whisper_settings_are_the_ones_u0_chose():
    assert WHISPER_SETTINGS == {"language": "en", "beam_size": 1, "vad_filter": True, "condition_on_previous_text": False}


def test_a_full_queue_drops_the_oldest_waiting_turn_and_keeps_the_newest():
    def slow_transcribe(pcm):
        time.sleep(0.3)
        return f"{len(pcm)} samples"

    async def go():
        worker, results = SpeechWorker(slow_transcribe, max_waiting=2), []

        async def on_done(job, result):
            results.append((job.meta["n"], result["status"]))
        for n in range(5):   # 1 runs, 2 and 3 wait, 4 pushes out 2, 5 pushes out 3
            worker.submit(SpeechJob(wav(), {"n": n + 1}, on_done))
            await asyncio.sleep(0.01)
        while len(results) < 5:
            await asyncio.sleep(0.05)
        return results, worker.overflows
    results, overflows = asyncio.run(go())
    assert overflows == 2
    assert sorted(results) == [(1, "ok"), (2, "dropped"), (3, "dropped"), (4, "ok"), (5, "ok")]


def test_undecodable_audio_is_a_failed_turn_not_silence():
    async def go():
        worker, out = SpeechWorker(lambda pcm: "never"), []

        async def on_done(job, result):
            out.append(result)
        worker.submit(SpeechJob(b"not audio", {}, on_done))
        while not out:
            await asyncio.sleep(0.02)
        return out[0]
    result = asyncio.run(go())
    assert result["status"] == "failed" and "transcript" not in result


def test_a_stuck_turn_is_failed_after_the_timeout_and_the_next_turn_still_runs():
    """Review finding 8: one inference that never returns must not block later turns."""
    import threading
    release = threading.Event()

    def transcribe(pcm):
        if not release.is_set() and transcribe.calls == 0:
            transcribe.calls += 1
            release.wait(5)        # the first call hangs
            return "too late"
        return "I need help"
    transcribe.calls = 0

    async def go():
        worker, results = SpeechWorker(transcribe, job_timeout=0.3), []

        async def on_done(job, result):
            results.append((job.meta["n"], result["status"], result.get("transcript")))
        worker.submit(SpeechJob(wav(), {"n": 1}, on_done))
        await asyncio.sleep(0.01)
        worker.submit(SpeechJob(wav(), {"n": 2}, on_done))
        while len(results) < 2:
            await asyncio.sleep(0.05)
        release.set()
        return results, worker.stuck
    results, stuck = asyncio.run(go())
    assert results == [(1, "failed", None), (2, "ok", "I need help")]
    assert stuck == 1
