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
