"""S3 package: export a recorded live session, load it, replay it twice. Also the M4 table."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import pytest
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
from app.services.m4_report import summarize  # noqa: E402
from app.services.s3_package import PackageRefused, build, load, scan_for_secrets  # noqa: E402
from app.services.s3_replay import run_replay  # noqa: E402
from app.services.spatial_runs import load_scenario, spatial_runs  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + bytes(range(64)) + b"\xff\xd9"
CONSENT = {"participant": "person A", "publication": "granted", "recorded_on": "2026-11-03"}
PHONE_ZERO = time.monotonic() * 1000 - 5_000
phone_now = lambda: time.monotonic() * 1000 - PHONE_ZERO


def person(source="esp32", falls=3, detected=3, adl=10, false_alarm_in=()):
    return {"source": source,
            "falls": [{"segment_s": [i * 20, i * 20 + 10], "incidents": [i * 20000 + 1500] if i < detected else []} for i in range(falls)],
            "adl": [{"segment_s": [300 + i * 10, 308 + i * 10], "incidents": [300_000 + i * 10_000 + 100] if i in false_alarm_in else []}
                    for i in range(adl)]}


def test_m4_reports_misses_and_false_alarms_per_person_without_a_pass_mark():
    report = summarize({"ann": person(detected=2, false_alarm_in=(4,)), "bob": person()})
    assert report["status"] == "Reported"
    ann = report["people"]["ann"]
    assert (ann["falls_detected"], ann["falls_missed"], ann["adl_with_false_alarm"], ann["false_alarms"]) == (2, 1, 1, 1)
    assert ann["fall_trials"][0]["start_offset_s"] == 1.5 and ann["fall_trials"][2]["detected"] is False
    assert "not a clinical validation" in report["note"]


def test_m4_needs_two_real_people_with_enough_trials_and_ignores_stand_in_devices():
    assert summarize({"ann": person()})["status"] == "Incomplete"
    assert summarize({"ann": person(), "bob": person(falls=2)})["people"]["bob"]["missing"] == ["at least 3 controlled falls (have 2)"]
    fake = summarize({"ann": person(), "bob": person(source="fake_esp32")})
    assert fake["people"]["bob"]["status"] == "Simulated: not M4 evidence" and fake["status"] == "Incomplete"


def test_a_package_with_a_token_or_a_bot_token_shape_is_found(tmp_path):
    (tmp_path / "manifest.json").write_text('{"note": "clean"}')
    assert scan_for_secrets(tmp_path, ["device-secret-1"]) == []
    (tmp_path / "responses.json").write_text('{"x": "device-secret-1"}')
    (tmp_path / "expected.json").write_text('{"bot": "1234567890:AAH' + "a" * 32 + '"}')
    (tmp_path / "f.jpg").write_bytes(b"\xff\xd8 device-secret-1 \xff\xd9")
    assert scan_for_secrets(tmp_path, ["device-secret-1"]) == ["expected.json", "f.jpg", "responses.json"]


@pytest.mark.parametrize("consent", [{}, {**CONSENT, "publication": "maybe"}, {**CONSENT, "participant": ""}])
def test_no_package_without_the_persons_consent(consent, tmp_path):
    with pytest.raises(PackageRefused, match="consent"):
        build("anything", tmp_path / "S3", consent, tmp_path)


class ExportAndReplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/s.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.saved = (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, sensor_api.DEVICE_TOKEN,
                      spatial_runs._notifier_override, spatial_runs._live_wearer_cancel, spatial_runs._grace,
                      spatial_runs._caregiver_ack, spatial_runs._preload_whisper, spatial_runs._chat)
        spatial_runs._preload_whisper = False
        database.engine = engine
        database.SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        runs_module.LIVE_DATA_DIR = Path(self.tmp.name) / "live"
        sensor_api.DEVICE_TOKEN = "device-secret-value"
        spatial_runs._notifier_override = None
        spatial_runs._live_wearer_cancel, spatial_runs._grace, spatial_runs._caregiver_ack = 3, 1, 1.5

        def no_model(messages, tools, tool_choice, timeout):
            from app.services.muse_client import AgentError
            raise AgentError("Failed", "not used in this test")
        spatial_runs._chat = no_model

    def tearDown(self):
        (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, sensor_api.DEVICE_TOKEN,
         spatial_runs._notifier_override, spatial_runs._live_wearer_cancel, spatial_runs._grace,
         spatial_runs._caregiver_ack, spatial_runs._preload_whisper, spatial_runs._chat) = self.saved
        spatial_runs._clocks.clear()
        self.tmp.cleanup()

    def wait(self, client, sid, predicate, seconds=15):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            snap = client.get(f"/api/spatial/sessions/{sid}").json()
            if predicate(snap):
                return snap
            time.sleep(0.1)
        raise AssertionError("condition not reached")

    def record_a_session(self, client, record_frames=True):
        headers = {"Authorization": "Bearer device-secret-value"}
        sid = client.post("/api/spatial/live", json={"patient_id": 1, "device_id": "fake-esp32", "source": "fake_esp32",
                                                     "notification_mode": "simulated", "record_frames": record_frames}).json()["id"]
        wearer = {"X-Wearer-Token": client.get(f"/api/spatial/sessions/{sid}").json()["wearer"]["token"]}
        rounds = []
        for _ in range(5):
            t0 = phone_now()
            rounds.append({"t0": t0, **client.post(f"/api/wearer/{sid}/clock", headers=wearer).json(), "t3": phone_now()})
        client.post(f"/api/wearer/{sid}/clock/report", headers=wearer, json={"rounds": rounds})
        started = time.monotonic()
        fake_esp32.sync_clock(client, headers, "fake-esp32", "b1", lambda: fake_esp32.BOOT_DEVICE_MS + round((time.monotonic() - started) * 1000))
        table = load_scenario("S2")
        full = np.column_stack([table.source_ms, table.samples])
        incident = None
        for n, chunk in enumerate(fake_esp32.batches(full, None, 0)):
            r = client.post("/api/sensor/batch", headers=headers, json=fake_esp32.payload(chunk, 1, "fake-esp32", "b1", n, 0)).json()
            if n == 20:
                for _ in range(2):
                    client.post(f"/api/wearer/{sid}/frame?capture={phone_now()}", content=JPEG + bytes([n]),
                                headers={**wearer, "Content-Type": "image/jpeg"})
            incident = incident or (r["incidents_opened"] or [None])[0]
        self.wait(client, sid, lambda s: any(a["kind"] == "notify" and a["detail"]["status"] == "simulated" for a in s["actions"]))
        client.post(f"/api/spatial/incidents/{incident}/wearer-response", json={"response": "ok"})  # a late OK
        self.wait(client, sid, lambda s: any(a["kind"] == "escalate" and a["detail"]["status"] == "simulated" for a in s["actions"]))
        time.sleep(0.4)
        client.post(f"/api/spatial/incidents/{incident}/acknowledge", json={"actor_id": "telegram:contact_2"})
        client.post(f"/api/spatial/sessions/{sid}/stop")
        return sid

    def test_a_recorded_session_exports_loads_and_replays_identically_twice(self):
        with TestClient(app) as client:
            sid = self.record_a_session(client)
            out = Path(self.tmp.name) / "S3"
            manifest = build(sid, out, CONSENT, runs_module.LIVE_DATA_DIR, extra_secrets=["device-secret-value"])
            self.assertEqual(manifest["contents"], {"incidents": 1, "frames": 2, "raw_audio": "not stored"})
            self.assertEqual(manifest["consent"], CONSENT)
            self.assertEqual(manifest["clock"]["frame_uncertainty_ms"]["frames_with_clock"], 2)
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             ["expected.json", "frames", "frames.json", "imu.csv", "manifest.json", "responses.json"])

            package = load(out)
            report = run_replay(package)
            self.assertEqual((report["M5a"]["status"], report["M5a"]["incidents"], report["M5a"]["matches_recorded"]),
                             ("Pass", [1, 1], True))
            self.assertEqual((report["M5b"]["status"], report["M5b"]["matches_recorded_chain"]), ("Pass", True))
            kinds = [dict(step)["kind"] for step in report["M5b"]["chain"][0]]
            self.assertEqual(kinds, ["notify", "self_report", "late_ok", "escalate", "late_ok", "ack"])
            responses = json.loads((out / "responses.json").read_text())["incidents"][0]["events"]
            self.assertEqual([e["kind"] for e in responses], ["reply", "ack"])
            self.assertIsNotNone(responses[0]["capture_end_ms"])

    def test_a_session_whose_incident_has_not_finished_is_not_exported(self):
        """Review finding 6: Stop during the countdown left a package with a state the policy had not reached."""
        spatial_runs._live_wearer_cancel, spatial_runs._grace = 6, 3
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer device-secret-value"}
            sid = client.post("/api/spatial/live", json={"patient_id": 1, "device_id": "fake-esp32", "source": "fake_esp32",
                                                         "notification_mode": "simulated"}).json()["id"]
            table = load_scenario("S2")
            full = np.column_stack([table.source_ms, table.samples])
            for n, chunk in enumerate(fake_esp32.batches(full, None, 0)):
                r = client.post("/api/sensor/batch", headers=headers, json=fake_esp32.payload(chunk, 1, "fake-esp32", "b1", n, 0)).json()
                if r["incidents_opened"]:
                    break
            client.post(f"/api/spatial/sessions/{sid}/stop")
            out = Path(self.tmp.name) / "S3"
            with self.assertRaisesRegex(PackageRefused, "incident 0: it is still waiting for the wearer"):
                build(sid, out, CONSENT, runs_module.LIVE_DATA_DIR)
            self.assertFalse(out.exists())
            manifest = build(sid, out, CONSENT, runs_module.LIVE_DATA_DIR, allow_incomplete=True)
            self.assertEqual((manifest["complete"], len(manifest["incomplete_reasons"])), (False, 1))
            time.sleep(11)                                     # the incident runs out its window, grace and escalation after Stop
            self.assertTrue(build(sid, out, CONSENT, runs_module.LIVE_DATA_DIR, force=True)["complete"])

    def test_a_replay_under_another_detector_config_is_not_a_pass(self):
        """Review finding 7: stillness 2 s recorded, 5 s now. The incident count changed and the report still passed."""
        from app.services import s3_package
        with TestClient(app) as client:
            sid = self.record_a_session(client)
            out = Path(self.tmp.name) / "S3"
            build(sid, out, CONSENT, runs_module.LIVE_DATA_DIR)
            package = load(out)
            self.assertEqual(run_replay(package)["M5a"]["same_config"], True)
            saved = s3_package.FALL_STILLNESS_SECONDS
            s3_package.FALL_STILLNESS_SECONDS = 5.0
            try:
                a = run_replay(package)["M5a"]
            finally:
                s3_package.FALL_STILLNESS_SECONDS = saved
            self.assertEqual((a["status"], a["same_config"], a["config_differences"]),
                             ("Fail", False, ["stillness.seconds: recorded 2.0, now 5.0"]))
            del package["manifest"]["config"]["detector"]          # a package that does not say what it ran with
            self.assertEqual(run_replay(package)["M5a"]["status"], "Fail")

    def test_a_replay_with_another_model_than_the_recording_is_not_a_pass(self):
        with TestClient(app) as client:
            sid = self.record_a_session(client)
            out = Path(self.tmp.name) / "S3"
            build(sid, out, CONSENT, runs_module.LIVE_DATA_DIR)
            package = load(out)
            package["manifest"]["model"]["keras_sha256"] = "0" * 64
            report = run_replay(package)
            self.assertEqual((report["M5a"]["status"], report["M5a"]["same_model"], report["M5a"]["replays_identical"]),
                             ("Fail", False, True))

    def test_a_changed_frame_or_imu_file_is_refused_on_load(self):
        with TestClient(app) as client:
            sid = self.record_a_session(client)
            out = Path(self.tmp.name) / "S3"
            build(sid, out, CONSENT, runs_module.LIVE_DATA_DIR)
            (out / "frames" / "live-1.jpg").write_bytes(b"changed")
            with self.assertRaisesRegex(PackageRefused, "live-1.jpg does not match"):
                load(out)
            (out / "frames" / "live-1.jpg").write_bytes(JPEG + bytes([20]))
            (out / "imu.csv").write_text((out / "imu.csv").read_text() + "0,0,0,0,0,0,0,0,0,0,0,0\n")
            with self.assertRaisesRegex(PackageRefused, "imu.csv does not match"):
                load(out)

    def test_a_running_session_and_an_existing_folder_are_not_exported_over(self):
        with TestClient(app) as client:
            sid = client.post("/api/spatial/live", json={"patient_id": 1, "device_id": "fake-esp32", "source": "fake_esp32",
                                                         "notification_mode": "simulated"}).json()["id"]
            try:
                (runs_module.LIVE_DATA_DIR / sid).mkdir(parents=True)
                (runs_module.LIVE_DATA_DIR / sid / "imu.csv").write_text("source_ms\n")
                with self.assertRaisesRegex(PackageRefused, "stop the session first"):
                    build(sid, Path(self.tmp.name) / "S3", CONSENT, runs_module.LIVE_DATA_DIR)
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")
            out = Path(self.tmp.name) / "S3"
            out.mkdir()
            with self.assertRaisesRegex(PackageRefused, "exists"):
                build(sid, out, CONSENT, runs_module.LIVE_DATA_DIR)


if __name__ == "__main__":
    unittest.main()
