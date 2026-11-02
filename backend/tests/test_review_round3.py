"""Review round 3: the legacy sensor route during a live session, spoken results after the grace, the
grace holding the session, finding a session again after a refresh, a cut-off speech line, and SAM."""

import asyncio
import http.server
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database
from app.database import Base
from app.main import app
from app.models import schemas  # noqa: F401
from app.models.schemas import SpatialAction
from app.services import live_frames, sam_client
from app.services import spatial_runs as runs_module
from app.services.live_stream import DeviceSample
from app.services.sam_client import SamError, SamResult
from app.services.spatial_runs import RunAlreadyActive, SpatialRunManager, _utcnow, load_scenario, session_snapshot, spatial_runs
from app.services.wearer_channel import GUARD_MS, WearerChannel
from test_incident_lifecycle import RecordingNotifier


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/r.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def tearDown(self):
        self.tmp.cleanup()

    def manager(self, notifier, cancel, ack, grace, **kw):
        return SpatialRunManager(session_factory=self.db, notifier=notifier, live_wearer_cancel_seconds=cancel,
                                 caregiver_ack_seconds=ack, late_help_grace_seconds=grace, live_data_dir=Path(self.tmp.name), **kw)

    async def open_incident(self, m):
        table = load_scenario("S2")
        sid = await m.start_live(1, "fake-esp32", "fake_esp32", "simulated")
        samples = [DeviceSample(i + 1, 12_345 + int(ms), v.astype(np.float32)) for i, (ms, v) in enumerate(zip(table.source_ms, table.samples))]
        for start in range(0, len(samples), 10):
            await asyncio.to_thread(m.ingest_live, sid, "b1", samples[start:start + 10])
            if session_snapshot(sid, self.db)["incidents"]:
                break
        return sid, session_snapshot(sid, self.db)["incidents"][0]["id"]

    def test_a_spoken_result_that_arrives_after_the_grace_is_only_logged(self):
        """Finding 3: audio sent before D, STT took longer than D + grace, and it still sent a late-OK update."""
        notifier = RecordingNotifier()

        async def go():
            m = self.manager(notifier, cancel=0.6, ack=5, grace=0.4)
            sid, iid = await self.open_incident(m)
            await asyncio.sleep(1.4)                                           # past D + grace (1.0 s); the ack timer keeps the run
            spoken = await m.wearer_reply(iid, "ok", _utcnow(), {"transcript": "I'm fine"})
            sent_after_speech = len(notifier.sent)
            button = await m.wearer_reply(iid, "ok", _utcnow())                 # the laptop button is not a speech result
            await asyncio.sleep(0.3)
            await m.stop(sid)
            return sid, spoken, sent_after_speech, button
        sid, spoken, sent_after_speech, button = asyncio.run(go())
        self.assertEqual((spoken["applied"], spoken["reason"]), (False, "processed_after_grace"))
        self.assertEqual(sent_after_speech, 1)                                  # only the alert itself
        self.assertEqual((button["applied"], len(notifier.sent)), (True, 2))
        snap = session_snapshot(sid, self.db)
        self.assertEqual([a["detail"]["late_ok"] for a in snap["actions"] if a["kind"] == "self_report"], [False, True])

    def test_an_early_alert_keeps_the_token_and_blocks_a_new_session_until_the_grace_ends(self):
        """Finding 4: HELP early, then Stop. The token died at D and a new session started inside D + grace."""
        notifier = RecordingNotifier()

        async def go():
            m = self.manager(notifier, cancel=1.0, ack=0.3, grace=1.5)
            sid, iid = await self.open_incident(m)
            token = m._channels[sid].token
            await m.wearer_reply(iid, "help", _utcnow())
            await m.stop(sid)
            await asyncio.sleep(1.3)                                            # after D (1.0 s), before D + grace (2.5 s)
            inside = (m._channel(sid, token) is not None)
            with self.assertRaises(RunAlreadyActive):
                await m.start_live(1, "fake-esp32", "fake_esp32", "simulated")
            await asyncio.sleep(1.8)                                            # past D + grace and the escalation
            after = (m._channel(sid, token) is None)
            again = await m.start_live(1, "fake-esp32", "fake_esp32", "simulated")
            await m.stop(again)
            return inside, after
        self.assertEqual(asyncio.run(go()), (True, True))

    def test_the_live_sam_path_refuses_a_new_request_while_a_timed_out_one_still_runs(self):
        """Finding 10: after the analysis timeout the worker kept running and the next request overlapped it."""
        release, running, peak, calls = threading.Event(), [0], [0], []
        lock = threading.Lock()

        def segmenter(image, mime, prompt):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
                calls.append(prompt)
            try:
                if len(calls) == 1:
                    release.wait(5)
                return SamResult("", [], 5, "sam-3.1")
            finally:
                with lock:
                    running[0] -= 1

        async def go():
            saved = live_frames.SAM_MIN_START_GAP_S
            live_frames.SAM_MIN_START_GAP_S = 0.05
            try:
                m = self.manager(RecordingNotifier(), 1, 1, 1, segmenter=segmenter, analysis_timeout_seconds=0.3)
                request = (b"x", "image/jpeg", "stairs")
                first = await m._live_segment("s", request)
                second = await m._live_segment("s", request)
                release.set()
                await asyncio.sleep(0.2)
                third = await m._live_segment("s", request)
                return first, second, third
            finally:
                live_frames.SAM_MIN_START_GAP_S = saved
        first, second, third = asyncio.run(go())
        self.assertEqual(first["status"], "TimedOut")
        self.assertEqual((second["status"], second["reason"]), ("Failed", "an earlier SAM request is still running"))
        self.assertEqual(third["status"], "NoMatch")
        self.assertEqual((peak[0], len(calls)), (1, 2))                          # never two at once; the refused one never ran


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/x.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.saved = (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, spatial_runs._preload_whisper)
        spatial_runs._preload_whisper = False
        database.engine = engine
        database.SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        runs_module.LIVE_DATA_DIR = Path(self.tmp.name) / "live"

    def tearDown(self):
        database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, spatial_runs._preload_whisper = self.saved
        self.tmp.cleanup()

    def test_the_old_sensor_route_does_not_run_beside_a_live_session(self):
        """Finding 1: it kept its own detector and its own alert path."""
        sample = {"patient_id": 1, "ax": 0.0, "ay": -1.0, "az": 0.0, "gx": 0.0, "gy": 0.0, "gz": 0.0}
        with TestClient(app) as client:
            sid = client.post("/api/spatial/live", json={"patient_id": 1, "device_id": "d", "source": "fake_esp32",
                                                         "notification_mode": "simulated"}).json()["id"]
            refused = client.post("/api/sensor/data", json=sample)
            client.post(f"/api/spatial/sessions/{sid}/stop")
            self.assertEqual((refused.status_code, "/api/sensor/batch" in refused.json()["detail"]), (409, True))
            self.assertEqual(client.post("/api/sensor/data", json=sample).status_code, 200)

    def test_a_refreshed_page_finds_the_running_live_session_again(self):
        """Finding 5: the page forgot its session, so Start answered 409 and Stop was out of reach."""
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/spatial/live/active").status_code, 404)
            sid = client.post("/api/spatial/live", json={"patient_id": 1, "device_id": "d", "source": "camera_trial",
                                                         "notification_mode": "simulated"}).json()["id"]
            found = client.get("/api/spatial/live/active")
            self.assertEqual((found.status_code, found.json()["id"], found.json()["live"]["config"]["source"]),
                             (200, sid, "camera_trial"))
            client.post(f"/api/spatial/sessions/{sid}/stop")
            self.assertEqual(client.get("/api/spatial/live/active").status_code, 404)


def test_a_line_cut_off_after_a_speech_timeout_keeps_a_quiet_span_that_rejects_an_overlapping_turn():
    """Finding 9: after the timeout the phone listened while the speaker could still be talking."""
    now = datetime(2026, 11, 2, 12, 0, 20)
    channel = WearerChannel()
    line = channel.say("ask_ok", 7, now - timedelta(seconds=20))
    channel.grant(7, "awaiting", now + timedelta(seconds=30), now - timedelta(seconds=20))
    start, end = now - timedelta(seconds=20), now - timedelta(seconds=5)       # the phone's bound on when it was talking
    assert channel.played(line.id, (start, end), now, "speech_timeout")
    assert next(u for u in channel.utterances if u.id == line.id).reason == "speech_timeout"
    inside = channel.check_turn(7, now - timedelta(seconds=5), now - timedelta(seconds=1), timedelta(milliseconds=1))
    after = channel.check_turn(7, end + timedelta(milliseconds=GUARD_MS + 5), now, timedelta(milliseconds=1))
    assert inside == "overlaps the phone's own speech or guard" and after is None


class Trickle(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            for _ in range(40):                       # a byte every 0.2 s: each gap is far under the read timeout
                self.wfile.write(b" ")
                self.wfile.flush()
                time.sleep(0.2)
        except OSError:
            pass

    def log_message(self, *args):
        pass


def test_a_server_that_trickles_bytes_cannot_keep_a_sam_call_past_its_budget(monkeypatch):
    """Finding 10: the per-read timeout restarted at every byte, so one slow response held a thread for a minute."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(sam_client, "MODEL_API_BASE", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setattr(sam_client, "MODEL_API_KEY", "test-key-not-real")
    monkeypatch.setattr(sam_client, "ANALYSIS_TIMEOUT_SECONDS", 1.0)
    started = time.monotonic()
    try:
        with pytest.raises(SamError) as raised:
            sam_client.segment(b"x", "image/jpeg", "stairs")
    finally:
        server.shutdown()
    assert raised.value.status == "TimedOut"
    assert time.monotonic() - started < 2.5
