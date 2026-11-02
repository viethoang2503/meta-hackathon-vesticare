"""The forehead camera over HTTP: frames in, a spoken stairs advisory out, and the frame
nearest a fall attached to the incident. SAM is a stand-in; the clocks are real."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fake_esp32  # noqa: E402
from app import database  # noqa: E402
from app.api import sensor as sensor_api  # noqa: E402
from app.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models.schemas import SpatialAnalysis  # noqa: E402
from app.services import spatial_runs as runs_module  # noqa: E402
from app.services.sam_client import SamResult  # noqa: E402
from app.services.spatial_runs import load_scenario, spatial_runs  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
BOX = {"x1": 10, "y1": 10, "x2": 200, "y2": 300, "w": 640, "h": 480}
PHONE_ZERO = time.monotonic() * 1000 - 41_000
phone_now = lambda: time.monotonic() * 1000 - PHONE_ZERO


class LiveCameraTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/c.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.saved = (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, sensor_api.DEVICE_TOKEN,
                      spatial_runs._segment, spatial_runs._live_wearer_cancel, spatial_runs._grace, spatial_runs._caregiver_ack,
                      spatial_runs._preload_whisper)
        spatial_runs._preload_whisper = False
        database.engine = engine
        database.SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        runs_module.LIVE_DATA_DIR = Path(self.tmp.name) / "live"
        sensor_api.DEVICE_TOKEN = "device-secret"
        self.boxes, self.seen = [BOX], []

        def sam(image, mime, prompt):
            self.seen.append(prompt)
            return SamResult("<out>", self.boxes, 120, "sam-3.1")
        spatial_runs._segment = sam
        spatial_runs._live_wearer_cancel, spatial_runs._grace, spatial_runs._caregiver_ack = 8, 2, 30

    def tearDown(self):
        (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, sensor_api.DEVICE_TOKEN, spatial_runs._segment,
         spatial_runs._live_wearer_cancel, spatial_runs._grace, spatial_runs._caregiver_ack,
         spatial_runs._preload_whisper) = self.saved
        spatial_runs._clocks.clear()  # the manager is a singleton; later tests expect an unsynchronized device
        self.tmp.cleanup()

    def start(self, client):
        sid = client.post("/api/spatial/live", json={"patient_id": 1, "device_id": "fake-esp32", "source": "fake_esp32",
                                                     "notification_mode": "simulated"}).json()["id"]
        return sid, {"X-Wearer-Token": client.get(f"/api/spatial/sessions/{sid}").json()["wearer"]["token"]}

    def frame(self, client, sid, auth, capture=None, body=JPEG):
        query = "" if capture is None else f"?capture={capture}"
        return client.post(f"/api/wearer/{sid}/frame{query}", content=body, headers={**auth, "Content-Type": "image/jpeg"})

    def sync_phone(self, client, sid, auth):
        rounds = []
        for _ in range(5):
            t0 = phone_now()
            rounds.append({"t0": t0, **client.post(f"/api/wearer/{sid}/clock", headers=auth).json(), "t3": phone_now()})
        client.post(f"/api/wearer/{sid}/clock/report", headers=auth, json={"rounds": rounds})

    def spoken(self, client, sid, auth, wait=2.0):
        end = time.monotonic() + wait
        while time.monotonic() < end:
            lines = client.get(f"/api/wearer/{sid}/poll", headers=auth).json()["utterances"]
            if lines:
                return [u["text"] for u in lines]
            time.sleep(0.05)
        return []

    def test_frame_route_checks_the_token_and_the_body(self):
        with TestClient(app) as client:
            sid, auth = self.start(client)
            try:
                self.assertEqual(self.frame(client, sid, {"X-Wearer-Token": "nope"}).status_code, 401)
                self.assertEqual(self.frame(client, sid, auth, body=b"not a jpeg").status_code, 422)
                self.assertEqual(self.frame(client, sid, auth, body=b"").status_code, 422)
                self.assertEqual(self.frame(client, sid, auth, body=JPEG + b"\x00" * 1_100_000).status_code, 413)
                ok = self.frame(client, sid, auth, capture=phone_now())
                self.assertEqual((ok.status_code, ok.json()), (202, {"frame_id": "live-1", "clock": "unsynchronized"}))
                camera = client.get(f"/api/spatial/sessions/{sid}").json()["camera"]
                self.assertEqual((camera["frames_received"], camera["latest_frame_id"], camera["clock"]),
                                 (1, "live-1", "unsynchronized"))
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")

    def test_stairs_in_view_is_spoken_once_and_stored(self):
        with TestClient(app) as client:
            sid, auth = self.start(client)
            try:
                self.frame(client, sid, auth, capture=phone_now())
                self.assertEqual(self.spoken(client, sid, auth), ["Stairs visible ahead."])
                self.assertEqual(self.seen, ["stairs"])
                with database.SessionLocal() as db:
                    row = db.query(SpatialAnalysis).filter_by(session_id=sid, kind="sam_advisory").one()
                    self.assertEqual((row.status, row.prompt, row.frame_id), ("Complete", "stairs", "live-1"))
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")

    def test_a_second_sighting_inside_the_cooldown_is_stored_but_not_spoken(self):
        with TestClient(app) as client:
            sid, auth = self.start(client)
            try:
                self.frame(client, sid, auth, capture=phone_now())
                self.assertEqual(self.spoken(client, sid, auth), ["Stairs visible ahead."])
                time.sleep(3.1)  # SAM may start again, but the 30 s cooldown has not passed
                self.frame(client, sid, auth, capture=phone_now())
                time.sleep(1.0)
                queued = client.get(f"/api/wearer/{sid}/poll", headers=auth).json()["utterances"]
                self.assertEqual(len(queued), 1)
                self.assertEqual(self.seen, ["stairs", "stairs"])
                with database.SessionLocal() as db:
                    self.assertEqual(db.query(SpatialAnalysis).filter_by(session_id=sid, status="Complete").count(), 2)
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")

    def test_no_stairs_found_says_nothing(self):
        self.boxes = []
        with TestClient(app) as client:
            sid, auth = self.start(client)
            try:
                self.frame(client, sid, auth, capture=phone_now())
                self.assertEqual(self.spoken(client, sid, auth, wait=1.0), [])
                with database.SessionLocal() as db:
                    self.assertEqual(db.query(SpatialAnalysis).filter_by(session_id=sid).one().status, "NoMatch")
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")

    def test_source_time_of_a_frame_follows_both_clocks(self):
        with TestClient(app) as client:
            sid, auth = self.start(client)
            try:
                run = spatial_runs._live[sid]
                run.stream = type("S", (), {"origin_ms": 12_345, "boot_id": "b1"})()
                spatial_runs._phone_clocks[sid] = {"offset_ms": 1000.0, "uncertainty_ms": 5.0}
                spatial_runs._clocks[("fake-esp32", "b1")] = {"offset_ms": 400.0, "uncertainty_ms": 3.0}
                self.assertEqual(spatial_runs._frame_source_ms(sid, 20_000), (20_000 + 1000 - 400 - 12_345, 8.0))
                run.stream = None  # no sample yet, so no source clock
                self.assertEqual(spatial_runs._frame_source_ms(sid, 20_000), (None, None))
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")

    def open_incident(self, client, sid):
        headers = {"Authorization": "Bearer device-secret"}
        table = load_scenario("S2")
        full = np.column_stack([table.source_ms, table.samples])
        started = time.monotonic()
        device_now = lambda: fake_esp32.BOOT_DEVICE_MS + round((time.monotonic() - started) * 1000)
        fake_esp32.sync_clock(client, headers, "fake-esp32", "b1", device_now)
        for n, chunk in enumerate(fake_esp32.batches(full, None, 0)):
            r = client.post("/api/sensor/batch", headers=headers,
                            json=fake_esp32.payload(chunk, 1, "fake-esp32", "b1", n, 0)).json()
            if r["incidents_opened"]:
                return r["incidents_opened"][0]
        raise AssertionError("S2 opened no incident")

    def scene_rows(self, sid):
        with database.SessionLocal() as db:
            return [(a.camera_status, a.status, a.frame_id) for a in
                    db.query(SpatialAnalysis).filter_by(session_id=sid, kind="sam").order_by(SpatialAnalysis.id)]

    def test_an_incident_without_frames_records_the_camera_as_unavailable(self):
        with TestClient(app) as client:
            sid, auth = self.start(client)
            try:
                self.open_incident(client, sid)
                time.sleep(0.3)
                self.assertEqual(self.scene_rows(sid), [("Unavailable", "Unavailable", None)])
                self.assertEqual(self.seen, [])  # nothing to send to SAM
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")

    def test_frame_near_the_fall_becomes_evidence_and_a_far_one_does_not(self):
        with TestClient(app) as client:
            sid, auth = self.start(client)
            try:
                iid = self.open_incident(client, sid)
                self.sync_phone(client, sid, auth)
                fall_ms = next(i for i in client.get(f"/api/spatial/sessions/{sid}").json()["incidents"] if i["id"] == iid)["source_start_ms"]
                at_zero, _ = spatial_runs._frame_source_ms(sid, 0)
                self.frame(client, sid, auth, capture=fall_ms - at_zero + 5000)           # 5 s after: too far
                analysis_id, request = spatial_runs._open_scene_analysis(spatial_runs._locks[sid], sid, iid)
                self.assertIsNone(request)
                self.assertEqual(self.scene_rows(sid)[-1], ("Unavailable", "Unavailable", "live-1"))

                self.frame(client, sid, auth, capture=fall_ms - at_zero + 200)            # 200 ms after: matches
                analysis_id, request = spatial_runs._open_scene_analysis(spatial_runs._locks[sid], sid, iid)
                self.assertEqual((request[1], request[2]), ("image/jpeg", "stairs"))
                self.assertEqual(self.scene_rows(sid)[-1], ("Available", "Running", "live-2"))
                got = client.get(f"/api/spatial/sessions/{sid}/frames/live-2")
                self.assertEqual((got.status_code, got.headers["content-type"], got.content), (200, "image/jpeg", JPEG))
                self.assertEqual(client.get(f"/api/spatial/sessions/{sid}/frames/live-99").status_code, 404)
                self.assertEqual(client.get(f"/api/spatial/sessions/{sid}/frames/..%2Fimu.csv").status_code, 404)
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")
                client.post(f"/api/spatial/incidents/{iid}/acknowledge", json={"actor_id": "cleanup"})

    def test_no_advisory_plays_while_an_incident_waits_for_the_wearer(self):
        with TestClient(app) as client:
            sid, auth = self.start(client)
            try:
                iid = self.open_incident(client, sid)
                spatial_runs._channels[sid].say("stairs_visible", None, runs_module._utcnow())
                lines = client.get(f"/api/wearer/{sid}/poll", headers=auth).json()["utterances"]
                self.assertEqual([u["text"] for u in lines], ["Are you all right? Please respond."])
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")
                client.post(f"/api/spatial/incidents/{iid}/acknowledge", json={"actor_id": "cleanup"})


if __name__ == "__main__":
    unittest.main()
