"""The phone link carries a single-use pairing code, never the wearer token."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database
from app.database import Base
from app.main import app
from app.services import spatial_runs as runs_module
from app.services.spatial_runs import spatial_runs
from app.services.wearer_channel import PAIRING_MAX_WRONG, PAIRING_TTL, WearerChannel

NOW = datetime(2026, 11, 2, 12, 0, 0)


def test_a_code_works_once_and_gives_the_token():
    channel = WearerChannel()
    code, _ = channel.new_pairing(NOW)
    assert channel.redeem(code, NOW + timedelta(seconds=5)) == channel.token
    assert channel.redeem(code, NOW + timedelta(seconds=6)) is None


def test_a_code_expires_and_a_new_one_replaces_the_old():
    channel = WearerChannel()
    old, _ = channel.new_pairing(NOW)
    assert channel.redeem(old, NOW + PAIRING_TTL) is None
    first, _ = channel.new_pairing(NOW)
    second, _ = channel.new_pairing(NOW)
    assert channel.redeem(first, NOW) is None
    assert channel.redeem(second, NOW) == channel.token


def test_wrong_guesses_lock_the_code_even_for_the_right_one():
    channel = WearerChannel()
    code, _ = channel.new_pairing(NOW)
    for _ in range(PAIRING_MAX_WRONG):
        assert channel.redeem("guess", NOW) is None
    assert channel.redeem(code, NOW) is None
    assert channel.redeem(None, NOW) is None


class PairingApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/p.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.saved = (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, spatial_runs._preload_whisper)
        spatial_runs._preload_whisper = False
        database.engine = engine
        database.SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        runs_module.LIVE_DATA_DIR = Path(self.tmp.name) / "live"

    def tearDown(self):
        database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, spatial_runs._preload_whisper = self.saved
        self.tmp.cleanup()

    def test_the_phone_pairs_through_the_tunnel_but_the_laptop_route_stays_closed(self):
        tunnel = {"Cf-Connecting-Ip": "203.0.113.9"}
        with TestClient(app) as client:
            sid = client.post("/api/spatial/live", json={"patient_id": 1, "device_id": "camera", "source": "camera_trial",
                                                         "notification_mode": "simulated"}).json()["id"]
            try:
                self.assertEqual(client.post(f"/api/spatial/sessions/{sid}/pairing", headers=tunnel).status_code, 403)
                code = client.post(f"/api/spatial/sessions/{sid}/pairing").json()["code"]
                self.assertEqual(client.post(f"/api/wearer/{sid}/pair", headers=tunnel, json={"code": "nope"}).status_code, 401)
                paired = client.post(f"/api/wearer/{sid}/pair", headers=tunnel, json={"code": code})
                self.assertEqual(paired.status_code, 200)
                token = paired.json()["token"]
                self.assertEqual(client.get(f"/api/wearer/{sid}/poll", headers={"X-Wearer-Token": token}).status_code, 200)
                self.assertEqual(client.post(f"/api/wearer/{sid}/pair", headers=tunnel, json={"code": code}).status_code, 401)
                self.assertEqual(client.post("/api/spatial/sessions/missing/pairing").status_code, 404)
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")


if __name__ == "__main__":
    unittest.main()
