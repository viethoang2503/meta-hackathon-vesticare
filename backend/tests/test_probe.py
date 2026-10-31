import io
import json
import math
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from app.api import probe
from app.main import app

TOKEN = "t" * 32


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "PROBE_TOKEN", TOKEN)
    monkeypatch.setattr(probe, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(probe, "REPORT_PATH", tmp_path / "trials.jsonl")
    return TestClient(app)


def _tone_wav(seconds=1.0, rate=48000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
                               for i in range(int(seconds * rate))))
    return buf.getvalue()


def _jpeg(width, height):
    sof = b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof + b"\xff\xd9"


def test_routes_answer_404_while_the_probe_is_off(monkeypatch):
    monkeypatch.setattr(probe, "PROBE_TOKEN", "")
    res = TestClient(app).post("/api/probe/audio?trial=a", content=b"x", headers={"X-Probe-Token": "anything"})
    assert res.status_code == 404


def test_a_wrong_token_is_refused_and_nothing_is_written(client, tmp_path):
    res = client.post("/api/probe/frame?trial=a", content=_jpeg(640, 480), headers={"X-Probe-Token": "wrong"})
    assert res.status_code == 401
    assert not (tmp_path / "data").exists()


def test_audio_is_decoded_to_16k_mono_and_not_kept_by_default(client, tmp_path):
    res = client.post("/api/probe/audio?trial=speak-1", content=_tone_wav(1.0),
                      headers={"X-Probe-Token": TOKEN, "Content-Type": "audio/wav"})
    body = res.json()
    assert res.status_code == 200
    assert (body["decodable"], body["codec"], body["source_rate"], body["duration_s"], body["kept"]) == (True, "pcm_s16le", 48000, 1.0, False)
    assert -21 < body["rms_dbfs"] < -15
    assert not (tmp_path / "data").exists()


def test_undecodable_audio_reports_the_error_instead_of_failing(client):
    body = client.post("/api/probe/audio?trial=bad", content=b"not audio at all",
                       headers={"X-Probe-Token": TOKEN, "Content-Type": "audio/mp4"}).json()
    assert body["decodable"] is False
    assert body["error"]


def test_frame_size_is_read_from_the_jpeg(client):
    body = client.post("/api/probe/frame?trial=speak-1", content=_jpeg(640, 480), headers={"X-Probe-Token": TOKEN}).json()
    assert (body["width"], body["height"]) == (640, 480)
    assert client.post("/api/probe/frame?trial=x", content=b"png?", headers={"X-Probe-Token": TOKEN}).status_code == 422


def test_report_is_appended_as_one_json_line(client, tmp_path):
    for n in (1, 2):
        assert client.post("/api/probe/report", content=json.dumps({"trial": f"speak-{n}", "mode": "speak"}),
                           headers={"X-Probe-Token": TOKEN}).json() == {"recorded": True}
    rows = [json.loads(line) for line in (tmp_path / "trials.jsonl").read_text().splitlines()]
    assert [r["trial"] for r in rows] == ["speak-1", "speak-2"]
    assert all("received_at" in r for r in rows)
    assert client.post("/api/probe/report", content=json.dumps({"trial": "../etc"}),
                       headers={"X-Probe-Token": TOKEN}).status_code == 422
