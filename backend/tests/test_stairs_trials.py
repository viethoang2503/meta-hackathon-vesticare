"""M3: the stairs trial table and the camera-trial session that feeds it."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database
from app.database import Base
from app.main import app
from app.services import spatial_runs as runs_module
from app.services.sam_client import SamResult
from app.services.spatial_runs import spatial_runs
from app.services.stairs_trials import summarize

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
BOX = {"x1": 10, "y1": 10, "x2": 200, "y2": 300, "w": 640, "h": 480}
PHONE_ZERO = time.monotonic() * 1000 - 9_000
phone_now = lambda: time.monotonic() * 1000 - PHONE_ZERO


def trial(condition, outcome, ended=True, ms=1800):
    status = {"found": "Complete", "not_found": "NoMatch", "failed": "Failed", "running": "Running"}[outcome]
    calls = [] if outcome == "none" else [{"status": status, "duration_ms": None if status == "Running" else ms}]
    return {"condition": condition, "ended": ended, "calls": calls}


def full_set(lit_found=10, none_found=0, dim_found=5):
    return ([trial("lit", "found" if i < lit_found else "not_found") for i in range(10)]
            + [trial("dim", "found" if i < dim_found else "not_found") for i in range(10)]
            + [trial("none", "found" if i < none_found else "not_found") for i in range(10)])


def test_targets_are_rates_per_condition_and_dim_is_only_reported():
    report = summarize(full_set(lit_found=8, none_found=1, dim_found=0))
    assert report["status"] == "Pass"
    assert {k: v["status"] for k, v in report["conditions"].items()} == {"lit": "Pass", "dim": "Reported", "none": "Pass"}
    assert report["conditions"]["lit"]["rate"] == 0.8
    assert summarize(full_set(lit_found=7))["conditions"]["lit"]["status"] == "Fail"
    assert summarize(full_set(none_found=2))["conditions"]["none"]["status"] == "Fail"
    assert summarize(full_set(lit_found=7))["status"] == "Fail"


def test_a_trial_without_a_completed_reading_is_not_counted_as_not_found():
    trials = full_set()
    for i in range(3):
        trials[i] = trial("lit", "failed")
    lit = summarize(trials)["conditions"]["lit"]
    assert (lit["found"], lit["not_found"], lit["no_result"], lit["valid"], lit["status"]) == (7, 0, 3, 7, "Incomplete")


def test_unfinished_trials_and_running_readings_keep_the_condition_incomplete():
    pending = full_set()
    pending[0] = trial("lit", "found", ended=False)
    assert summarize(pending)["conditions"]["lit"]["pending"] == 1
    assert summarize(pending)["status"] == "Incomplete"
    running = full_set()
    running[10] = trial("dim", "running")
    assert summarize(running)["conditions"]["dim"]["status"] == "Incomplete"


def test_too_few_trials_are_incomplete_and_latency_covers_completed_readings_only():
    few = [trial("lit", "found", ms=1000), trial("lit", "found", ms=3000), trial("lit", "failed", ms=99)]
    lit = summarize(few)["conditions"]["lit"]
    assert lit["status"] == "Incomplete"
    assert lit["latency"] == {"n": 2, "p50_ms": 2000, "p95_ms": 2900}


class CameraTrialApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/s.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.saved = (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, spatial_runs._segment,
                      spatial_runs._preload_whisper)
        spatial_runs._preload_whisper = False
        database.engine = engine
        database.SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        runs_module.LIVE_DATA_DIR = Path(self.tmp.name) / "live"
        self.boxes = [BOX]
        spatial_runs._segment = lambda image, mime, prompt: SamResult("<out>", self.boxes, 150, "sam-3.1")

    def tearDown(self):
        (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, spatial_runs._segment,
         spatial_runs._preload_whisper) = self.saved
        spatial_runs._clocks.clear()
        self.tmp.cleanup()

    def start(self, client, source="camera_trial"):
        sid = client.post("/api/spatial/live", json={"patient_id": 1, "device_id": "camera", "source": source,
                                                     "notification_mode": "simulated"}).json()["id"]
        return sid, {"X-Wearer-Token": client.get(f"/api/spatial/sessions/{sid}").json()["wearer"]["token"]}

    def see(self, client, sid, auth):
        client.post(f"/api/wearer/{sid}/frame?capture={phone_now()}", content=JPEG,
                    headers={**auth, "Content-Type": "image/jpeg"})
        time.sleep(0.5)  # the stand-in SAM answers at once

    def test_a_trial_collects_the_readings_made_while_it_is_open(self):
        with TestClient(app) as client:
            sid, auth = self.start(client)
            try:
                snap = client.get(f"/api/spatial/sessions/{sid}").json()
                self.assertEqual(snap["live"]["config"]["source_label"], "Camera trial (no device, no fall detected)")
                self.see(client, sid, auth)                                   # before any trial: not counted
                time.sleep(3.1)
                started = client.post(f"/api/spatial/sessions/{sid}/stairs-trials", json={"condition": "lit"})
                self.assertEqual((started.status_code, started.json()["condition"]), (201, "lit"))
                self.assertEqual(client.post(f"/api/spatial/sessions/{sid}/stairs-trials", json={"condition": "dim"}).status_code, 409)
                self.see(client, sid, auth)
                self.assertEqual(client.post(f"/api/spatial/sessions/{sid}/stairs-trials/end").status_code, 200)
                self.assertEqual(client.post(f"/api/spatial/sessions/{sid}/stairs-trials/end").status_code, 409)
                time.sleep(3.1)
                self.see(client, sid, auth)                                   # after the trial: not counted

                report = client.get(f"/api/spatial/sessions/{sid}/stairs-report").json()
                self.assertEqual([(t["condition"], t["result"], len(t["calls"])) for t in report["trials"]],
                                 [("lit", "found", 1)])
                self.assertEqual(report["conditions"]["lit"]["latency"]["p50_ms"], 150)
                self.assertEqual(report["status"], "Incomplete")
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")

    def test_a_scene_without_stairs_reads_as_not_found(self):
        self.boxes = []
        with TestClient(app) as client:
            sid, auth = self.start(client)
            try:
                client.post(f"/api/spatial/sessions/{sid}/stairs-trials", json={"condition": "none"})
                self.see(client, sid, auth)
                client.post(f"/api/spatial/sessions/{sid}/stairs-trials/end")
                report = client.get(f"/api/spatial/sessions/{sid}/stairs-report").json()
                self.assertEqual(report["trials"][0]["result"], "not_found")
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")

    def test_only_a_running_camera_trial_session_takes_trials(self):
        with TestClient(app) as client:
            sid, _ = self.start(client, source="drill")
            try:
                refused = client.post(f"/api/spatial/sessions/{sid}/stairs-trials", json={"condition": "lit"})
                self.assertEqual((refused.status_code, refused.json()["detail"]), (422, "not_a_camera_trial"))
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")
            sid, _ = self.start(client)
            client.post(f"/api/spatial/sessions/{sid}/stop")
            self.assertEqual(client.post(f"/api/spatial/sessions/{sid}/stairs-trials", json={"condition": "lit"}).status_code, 409)
            self.assertEqual(client.post("/api/spatial/sessions/nope/stairs-trials", json={"condition": "lit"}).status_code, 404)
            self.assertEqual(client.post(f"/api/spatial/sessions/{sid}/stairs-trials", json={"condition": "x"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
