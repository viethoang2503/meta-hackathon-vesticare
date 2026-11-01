"""The voice path over HTTP: fall -> spoken question -> recorded turns -> decision.

The transcriber is a stand-in that returns scripted text, so the test covers everything
around Whisper: tokens, clock mapping, capture checks, the classifier and the policy."""

import io
import math
import struct
import sys
import tempfile
import time
import unittest
import wave
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
from app.services import spatial_runs as runs_module  # noqa: E402
from app.services.spatial_runs import load_scenario, spatial_runs  # noqa: E402
from app.services.speech import SpeechWorker  # noqa: E402

PHONE_ZERO = time.monotonic() * 1000 - 77_000   # the phone's performance.now() started 77 s ago
phone_now = lambda: time.monotonic() * 1000 - PHONE_ZERO


def wav(seconds):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 6))) for i in range(int(seconds * 16000))))
    return buf.getvalue()


class WearerApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/w.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.saved = (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, sensor_api.DEVICE_TOKEN,
                      spatial_runs._speech, spatial_runs._live_wearer_cancel, spatial_runs._grace, spatial_runs._caregiver_ack,
                      spatial_runs._preload_whisper)
        spatial_runs._preload_whisper = False
        database.engine = engine
        database.SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        runs_module.LIVE_DATA_DIR = Path(self.tmp.name) / "live"
        sensor_api.DEVICE_TOKEN = "device-secret"
        self.heard = []
        spatial_runs._speech = SpeechWorker(lambda pcm: self.heard.pop(0))
        spatial_runs._live_wearer_cancel, spatial_runs._grace, spatial_runs._caregiver_ack = 4, 2, 30

    def tearDown(self):
        (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, sensor_api.DEVICE_TOKEN, spatial_runs._speech,
         spatial_runs._live_wearer_cancel, spatial_runs._grace, spatial_runs._caregiver_ack,
         spatial_runs._preload_whisper) = self.saved
        self.tmp.cleanup()

    def open_incident(self, client):
        sid = client.post("/api/spatial/live", json={"patient_id": 1, "device_id": "fake-esp32", "source": "fake_esp32",
                                                     "notification_mode": "simulated"}).json()["id"]
        table = load_scenario("S2")
        full = np.column_stack([table.source_ms, table.samples])
        for n, chunk in enumerate(fake_esp32.batches(full, None, 0)):
            r = client.post("/api/sensor/batch", headers={"Authorization": "Bearer device-secret"},
                            json=fake_esp32.payload(chunk, 1, "fake-esp32", "b1", n, 0)).json()
            if r["incidents_opened"]:
                return sid, r["incidents_opened"][0]
        raise AssertionError("S2 opened no incident")

    def snapshot(self, client, sid):
        return client.get(f"/api/spatial/sessions/{sid}").json()

    def wait_state(self, client, sid, state, seconds=6):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.snapshot(client, sid)["incidents"][0]["state"] == state:
                return
            time.sleep(0.05)
        raise AssertionError(f"never reached {state}: {self.snapshot(client, sid)['incidents'][0]['state']}")

    def sync_phone_clock(self, client, sid, auth):
        rounds = []
        for _ in range(5):
            t0 = phone_now()
            rounds.append({"t0": t0, **client.post(f"/api/wearer/{sid}/clock", headers=auth).json(), "t3": phone_now()})
        client.post(f"/api/wearer/{sid}/clock/report", headers=auth, json={"rounds": rounds})

    def test_an_ok_from_outside_this_incidents_listening_cannot_cancel(self):
        """Review finding 1: audio stamped an hour before the incident used to cancel it."""
        with TestClient(app) as client:
            sid, iid = self.open_incident(client)
            try:
                token = self.snapshot(client, sid)["wearer"]["token"]
                auth = {"X-Wearer-Token": token}
                self.sync_phone_clock(client, sid, auth)
                question = client.get(f"/api/wearer/{sid}/poll", headers=auth).json()["utterances"][0]
                spoke_from = phone_now()
                time.sleep(0.6)
                client.post(f"/api/wearer/{sid}/played", headers=auth,
                            json={"utterance_id": question["id"], "speech_start": spoke_from, "speech_end": phone_now()})
                self.heard += ["I'm fine.", "I'm fine."]
                old = phone_now() - 3_600_000
                r = client.post(f"/api/wearer/{sid}/audio?incident_id={iid}&capture_start={old - 1000}&capture_end={old}",
                                content=wav(1.0), headers={**auth, "Content-Type": "audio/wav"}).json()
                self.assertEqual(r["capture_time"], "captured before listening opened")
                over = client.post(f"/api/wearer/{sid}/audio?incident_id={iid}&capture_start={spoke_from + 100}&capture_end={spoke_from + 500}",
                                   content=wav(0.4), headers={**auth, "Content-Type": "audio/wav"}).json()
                self.assertEqual(over["capture_time"], "overlaps the phone's own speech or guard")
                self.wait_state(client, sid, "alerted", seconds=6)   # neither OK cancelled; the deadline alerted
                reports = [a["detail"] for a in self.snapshot(client, sid)["actions"] if a["kind"] == "self_report"]
                self.assertEqual([(r["response"], r["speech"]["not_counted_as_ok"]) for r in reports[:2]],
                                 [("unclear", "captured before listening opened"),
                                  ("unclear", "overlaps the phone's own speech or guard")])
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")
                client.post(f"/api/spatial/incidents/{iid}/acknowledge", json={"actor_id": "cleanup"})

    def send_turn(self, client, sid, token, iid, seconds=1.0):
        end = phone_now()
        return client.post(f"/api/wearer/{sid}/audio?incident_id={iid}&capture_start={end - seconds * 1000}&capture_end={end}",
                           content=wav(seconds), headers={"X-Wearer-Token": token, "Content-Type": "audio/wav"})

    def test_ok_cancels_then_help_inside_the_window_raises_the_alert(self):
        with TestClient(app) as client:
            sid, iid = self.open_incident(client)
            try:
                token = self.snapshot(client, sid)["wearer"]["token"]
                auth = {"X-Wearer-Token": token}
                self.assertEqual(client.get(f"/api/wearer/{sid}/poll", headers={"X-Wearer-Token": "nope"}).status_code, 401)
                time.sleep(0.2)
                first = client.get(f"/api/wearer/{sid}/poll", headers=auth).json()
                self.assertEqual([u["text"] for u in first["utterances"]], ["Are you all right? Please respond."])
                self.assertEqual(first["listen"]["mode"], "awaiting")
                client.post(f"/api/wearer/{sid}/played", headers=auth, json={"utterance_id": first["utterances"][0]["id"]})
                rounds = []
                for _ in range(5):
                    t0 = phone_now()
                    rounds.append({"t0": t0, **client.post(f"/api/wearer/{sid}/clock", headers=auth).json(), "t3": phone_now()})
                self.assertIn("offset_ms", client.post(f"/api/wearer/{sid}/clock/report", headers=auth, json={"rounds": rounds}).json())
                time.sleep(1.6)  # the phone records only after the question and its 500 ms guard

                self.heard.append("I'm fine. I'm fine.")
                self.assertEqual(self.send_turn(client, sid, token, iid).json()["capture_time"], "confirmed")
                self.wait_state(client, sid, "cancelled_by_wearer")
                after_ok = client.get(f"/api/wearer/{sid}/poll?after={first['utterances'][0]['id']}", headers=auth).json()
                self.assertEqual([u["template_id"] for u in after_ok["utterances"]], ["cancel_confirmed"])
                self.assertEqual(after_ok["listen"]["mode"], "help_only")

                self.heard.append("Actually I need help, I can't get up.")
                self.send_turn(client, sid, token, iid)
                self.wait_state(client, sid, "alerted")
                snap = self.snapshot(client, sid)
                reports = [a["detail"] for a in snap["actions"] if a["kind"] == "self_report"]
                self.assertEqual([(r["response"], r["applied"]) for r in reports], [("ok", True), ("help", True)])
                self.assertEqual(reports[1]["speech"]["transcript"], "Actually I need help, I can't get up.")
                self.assertIsNone(client.get(f"/api/wearer/{sid}/poll", headers=auth).json()["listen"])
                notify = next(a for a in snap["actions"] if a["kind"] == "notify")
                self.assertIn("Wearer response: Help requested.", notify["detail"]["text"])
                self.assertIn("Wearer channel: Ready.", notify["detail"]["text"])
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")
                client.post(f"/api/spatial/incidents/{iid}/acknowledge", json={"actor_id": "cleanup"})

    def test_without_a_phone_clock_ok_does_not_cancel_but_help_still_alerts(self):
        with TestClient(app) as client:
            sid, iid = self.open_incident(client)
            try:
                token = self.snapshot(client, sid)["wearer"]["token"]
                self.heard += ["I'm OK.", "Help!"]
                reply = self.send_turn(client, sid, token, iid).json()
                self.assertEqual(reply["capture_time"], "phone clock unsynchronized")
                time.sleep(0.5)
                self.assertEqual(self.snapshot(client, sid)["incidents"][0]["state"], "awaiting_wearer")
                self.send_turn(client, sid, token, iid)               # HELP can only strengthen
                self.wait_state(client, sid, "alerted")
                snap = self.snapshot(client, sid)
                self.assertEqual(snap["incidents"][0]["state"], "alerted")
                first = [a["detail"] for a in snap["actions"] if a["kind"] == "self_report"][0]
                self.assertEqual(first["speech"]["not_counted_as_ok"], "phone clock unsynchronized")
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")
                client.post(f"/api/spatial/incidents/{iid}/acknowledge", json={"actor_id": "cleanup"})

    def test_through_the_tunnel_only_the_phone_routes_answer(self):
        tunnel = {"Cf-Connecting-Ip": "203.0.113.9"}
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/spatial/live", headers=tunnel, json={"patient_id": 1, "device_id": "x"}).status_code, 403)
            self.assertEqual(client.get("/api/spatial/scenarios", headers=tunnel).status_code, 403)
            self.assertEqual(client.get("/index.html", headers=tunnel).status_code, 403)
            self.assertEqual(client.get("/glasses/index.html", headers=tunnel).status_code, 200)
            self.assertEqual(client.get("/api/wearer/none/poll", headers={**tunnel, "X-Wearer-Token": "x"}).status_code, 401)


    def test_the_deadline_alerts_even_if_the_phone_never_played_the_question(self):
        with TestClient(app) as client:
            sid, iid = self.open_incident(client)
            try:
                self.wait_state(client, sid, "alerted", seconds=6)   # nobody polled, nothing was played
                snap = self.snapshot(client, sid)
                self.assertEqual([u["status"] for u in snap["wearer"]["spoken"]], ["queued"])
                self.assertEqual(snap["wearer"]["status"], "Unknown")
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")
                client.post(f"/api/spatial/incidents/{iid}/acknowledge", json={"actor_id": "cleanup"})
