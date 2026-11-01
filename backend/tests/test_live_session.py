"""MVP 2 live session (U2): the fake ESP32 contract through the real detector and policy.

The samples are SisFall-based scenario recordings, fed in firmware-shaped batches; they
prove the software path, not the hardware (G4).
"""

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "tools"))

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import fake_esp32  # noqa: E402
from app import database  # noqa: E402
from app.database import Base  # noqa: E402
from app.api import sensor as sensor_api  # noqa: E402
from app.main import app  # noqa: E402
from app.models import schemas  # noqa: E402,F401
from app.services import spatial_runs as runs_module  # noqa: E402
from app.services.live_stream import DeviceSample  # noqa: E402
from app.services.spatial_runs import RunAlreadyActive, SpatialRunManager, incident_detail, load_scenario, session_snapshot  # noqa: E402

S2_FALL_START_MS = 23940


def device_batches(scenario_id, gap_at_ms=None, gap_ms=1000):
    table = load_scenario(scenario_id)
    full = np.column_stack([table.source_ms, table.samples])
    for chunk in fake_esp32.batches(full, gap_at_ms, gap_ms):
        yield [DeviceSample(int(i), fake_esp32.BOOT_DEVICE_MS + int(r[0]), r[1:].astype("float32")) for i, r in chunk]


class LiveSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{self.tmp.name}/live.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self._old_dir = runs_module.LIVE_DATA_DIR
        runs_module.LIVE_DATA_DIR = Path(self.tmp.name) / "live"

    def tearDown(self):
        runs_module.LIVE_DATA_DIR = self._old_dir
        self.tmp.cleanup()

    def stream(self, scenario_id, **gap):
        """Start a live session, send every batch from a worker thread like the HTTP route, Stop."""
        async def go():
            m = SpatialRunManager(session_factory=self.db, live_wearer_cancel_seconds=0.2, caregiver_ack_seconds=0.2)
            sid = await m.start_live(1, "fake-esp32", "fake_esp32")
            replies = []
            for batch in device_batches(scenario_id, **gap):
                replies.append(await asyncio.to_thread(m.ingest_live, sid, "boot-1", batch))
            await asyncio.sleep(0.8)
            await m.stop(sid)
            await m.wait(sid)
            return sid, replies
        sid, replies = asyncio.run(go())
        return session_snapshot(sid, self.db), replies

    def test_s2_fall_opens_one_incident_that_alerts_after_the_live_window(self):
        snap, replies = self.stream("S2")
        self.assertEqual((snap["kind"], snap["scenario_id"], snap["scenario"]), ("live", None, None))
        self.assertEqual(len(snap["incidents"]), 1)
        incident = snap["incidents"][0]
        self.assertEqual(incident["source_start_ms"], S2_FALL_START_MS)
        self.assertEqual(incident["location"]["source"], "Unknown")
        self.assertEqual(incident["state"], "alerted")
        notify = [a for a in snap["actions"] if a["kind"] == "notify"]
        self.assertEqual(notify[0]["detail"]["after_wearer_window_s"], 0.2)
        self.assertEqual(snap["live"]["config"]["wearer_cancel_seconds"], 0.2)
        self.assertEqual(snap["live"]["readiness"], "ready")
        self.assertEqual((snap["live"]["reset_count"], snap["live"]["duplicates"]), (0, 0))
        self.assertEqual(sum(len(r["incidents_opened"]) for r in replies), 1)

    def test_s1_walking_opens_no_incident(self):
        snap, _ = self.stream("S1")
        self.assertEqual(snap["incidents"], [])
        self.assertEqual(snap["live"]["coverage"], 1.0)

    def test_a_gap_resets_the_window_and_is_reported(self):
        snap, _ = self.stream("S1", gap_at_ms=10000, gap_ms=1000)
        live = snap["live"]
        self.assertEqual([r["reason"] for r in live["resets"]], ["sequence_gap"])
        self.assertEqual(live["resets"][0]["source_ms"], 11000)
        self.assertLess(live["coverage"], 1.0)
        self.assertEqual(live["unavailable_ms"], 12980 - 9980)  # last sample 9980, window full again 1980 ms after 11000

    def test_a_slow_batch_with_continuous_samples_does_not_reset(self):
        async def go():
            m = SpatialRunManager(session_factory=self.db)
            sid = await m.start_live(1, "fake-esp32", "fake_esp32")
            for n, batch in enumerate(device_batches("S1")):
                if n == 12:
                    await asyncio.sleep(1.5)  # the network stalls; the device clock and seq do not jump
                await asyncio.to_thread(m.ingest_live, sid, "boot-1", batch)
                if n == 20:
                    break
            snap = session_snapshot(sid, self.db)
            await m.stop(sid)
            return snap["live"]
        live = asyncio.run(go())
        self.assertEqual((live["reset_count"], live["readiness"], live["status_label"]), (0, "ready", "Detector ready"))

    def test_after_a_gap_the_laptop_says_detection_unavailable_until_the_window_refills(self):
        async def go():
            m = SpatialRunManager(session_factory=self.db)
            sid = await m.start_live(1, "fake-esp32", "fake_esp32")
            labels = []
            for batch in device_batches("S1", gap_at_ms=4000, gap_ms=500):
                await asyncio.to_thread(m.ingest_live, sid, "boot-1", batch)
                labels.append(session_snapshot(sid, self.db)["live"]["status_label"])
                if len(labels) == 80:
                    break
            await m.stop(sid)
            return labels
        labels = asyncio.run(go())
        order = [l for i, l in enumerate(labels) if i == 0 or labels[i - 1] != l]
        self.assertEqual(order, ["Warming up", "Detector ready", "Detection unavailable", "Detector ready"])

    def test_resent_batch_is_not_fed_twice_and_another_boot_is_rejected(self):
        async def go():
            m = SpatialRunManager(session_factory=self.db)
            sid = await m.start_live(1, "fake-esp32", "fake_esp32")
            first = next(device_batches("S1"))
            a = await asyncio.to_thread(m.ingest_live, sid, "boot-1", first)
            b = await asyncio.to_thread(m.ingest_live, sid, "boot-1", first)
            c = await asyncio.to_thread(m.ingest_live, sid, "boot-2", first)
            await m.stop(sid)
            return sid, a, b, c
        sid, a, b, c = asyncio.run(go())
        self.assertEqual((a["accepted"], b["accepted"], c["accepted"], c["rejected"]), (10, 0, 0, "boot_changed"))
        live = session_snapshot(sid, self.db)["live"]
        self.assertEqual((live["samples_accepted"], live["duplicates"], live["rejected_batches"]), (10, 10, 1))
        self.assertEqual(live["status_label"], "Device restarted: start a new live session")

    def test_one_run_at_a_time_across_replay_and_live(self):
        async def go():
            m = SpatialRunManager(session_factory=self.db, realtime=False, run_timeout_seconds=30)
            sid = await m.start_live(1, "fake-esp32", "fake_esp32")
            with self.assertRaises(RunAlreadyActive):
                await m.start("S1")
            await m.stop(sid)
        asyncio.run(go())

    def test_live_incident_detail_reads_the_journal_and_has_no_frame(self):
        snap, _ = self.stream("S2")
        detail = incident_detail(snap["incidents"][0]["id"], self.db)
        self.assertEqual(detail["kind"], "live")
        self.assertEqual(detail["imu"]["source"], "Simulated device (recorded scenario)")
        self.assertEqual(detail["frame"]["status"], "Unavailable")
        times = [row[0] for row in detail["imu"]["samples"]]
        self.assertLessEqual(times[0], S2_FALL_START_MS - 1000)
        self.assertGreaterEqual(times[-1], S2_FALL_START_MS)

    def test_snapshot_says_device_silent_before_any_sample(self):
        async def go():
            m = SpatialRunManager(session_factory=self.db)
            sid = await m.start_live(1, "fake-esp32", "fake_esp32")
            snap = session_snapshot(sid, self.db)
            await m.stop(sid)
            return snap
        live = asyncio.run(go())["live"]
        self.assertEqual((live["device_silent"], live["status_label"]), (True, "Waiting for device"))


class LiveRouteTest(unittest.TestCase):
    """The HTTP path the ESP32 uses: /api/spatial/live then /api/sensor/batch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/route.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        # The app's lifespan runs init_db on database.engine; other test modules leave it on a deleted file.
        self._old = (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, sensor_api.DEVICE_TOKEN)
        database.engine = engine
        database.SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        runs_module.LIVE_DATA_DIR = Path(self.tmp.name) / "live"
        sensor_api.DEVICE_TOKEN = "device-secret"

    def tearDown(self):
        database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, sensor_api.DEVICE_TOKEN = self._old
        self.tmp.cleanup()

    def test_batches_route_to_the_live_session_and_are_validated(self):
        table = load_scenario("S1")
        full = np.column_stack([table.source_ms, table.samples])
        chunks = list(fake_esp32.batches(full, None, 0))
        auth = {"Authorization": "Bearer device-secret"}
        with TestClient(app, headers=auth) as client:
            sid = client.post("/api/spatial/live", json={"patient_id": 1, "device_id": "fake-esp32", "source": "fake_esp32"}).json()["id"]
            try:
                ok = client.post("/api/sensor/batch", json=fake_esp32.payload(chunks[0], 1, "fake-esp32", "b1", 0, 0))
                self.assertEqual(ok.status_code, 200)
                self.assertEqual((ok.json()["session_id"], ok.json()["accepted"]), (sid, 10))
                other = client.post("/api/sensor/batch", json=fake_esp32.payload(chunks[1], 1, "someone-else", "b1", 1, 0))
                self.assertEqual(other.status_code, 409)
                legacy = fake_esp32.payload(chunks[1], 1, "fake-esp32", "b1", 1, 0)
                del legacy["boot_id"]
                self.assertEqual(client.post("/api/sensor/batch", json=legacy).status_code, 422)
                no_token = client.post("/api/sensor/batch", headers={"Authorization": "Bearer wrong"},
                                       json=fake_esp32.payload(chunks[1], 1, "fake-esp32", "b1", 1, 0))
                self.assertEqual(no_token.status_code, 401)
                sensor_api.DEVICE_TOKEN = ""
                unset = client.post("/api/sensor/batch", json=fake_esp32.payload(chunks[1], 1, "fake-esp32", "b1", 1, 0))
                self.assertEqual(unset.status_code, 503)
                sensor_api.DEVICE_TOKEN = "device-secret"
                snap = client.get(f"/api/spatial/sessions/{sid}").json()
                self.assertEqual((snap["kind"], snap["live"]["samples_accepted"], snap["live"]["status_label"]),
                                 ("live", 10, "Warming up"))
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")


class MigrationTest(unittest.TestCase):
    def test_an_mvp1_database_keeps_its_rows_and_accepts_live_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/old.db"
            con = sqlite3.connect(path)
            con.executescript("""
                CREATE TABLE spatial_sessions (id VARCHAR PRIMARY KEY, scenario_id VARCHAR NOT NULL, status VARCHAR NOT NULL,
                  end_reason VARCHAR, started_at DATETIME NOT NULL, ended_at DATETIME, source_offset_ms INTEGER NOT NULL,
                  recording_ended BOOLEAN NOT NULL, model_source VARCHAR NOT NULL);
                INSERT INTO spatial_sessions VALUES ('old1', 'S2', 'Stopped', 'user_stop', '2026-10-29 10:00:00', NULL, 28400, 1, 'keras');
            """)
            con.commit()
            con.close()
            engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
            old_engine = database.engine
            database.engine = engine
            try:
                database.init_db()
            finally:
                database.engine = old_engine
            with engine.connect() as conn:
                cols = {c[1]: c for c in conn.execute(text("PRAGMA table_info(spatial_sessions)"))}
                row = conn.execute(text("SELECT id, kind, scenario_id, status FROM spatial_sessions")).one()
                conn.execute(text("INSERT INTO spatial_sessions (id, kind, scenario_id, status, started_at, source_offset_ms, "
                                  "recording_ended, model_source) VALUES ('live1', 'live', NULL, 'Stopped', '2026-10-31', 0, 0, 'keras')"))
            self.assertEqual(cols["scenario_id"][3], 0)
            self.assertEqual(tuple(row), ("old1", "replay", "S2", "Stopped"))
            self.assertIn("live_stats", cols)
