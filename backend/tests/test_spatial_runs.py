"""Spatial replay runs: sessions, fall incidents and the simulated contact_1 notification.

Runs the real scenarios in assets/scenarios through the real detector. The
non-realtime manager replays a whole recording in one step so a run takes
seconds instead of the scenario's wall-clock length.
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models import schemas  # noqa: E402,F401
from app.models.schemas import SpatialSession  # noqa: E402
from app.services.fall_detection import fall_detection_service  # noqa: E402
from app.services.monitoring import StreamMonitor  # noqa: E402
from app.services.spatial_runs import (  # noqa: E402
    RunAlreadyActive,
    SpatialRunManager,
    _utcnow,
    load_scenario,
    session_snapshot,
)

S2_FALL_START_MS = 14720


class SpatialRunsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/spatial.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def tearDown(self):
        self.tmp.cleanup()

    def manager(self, **kwargs):
        return SpatialRunManager(session_factory=self.db, **{"realtime": False, "run_timeout_seconds": 0.5, **kwargs})

    def run_to_end(self, scenario_id):
        async def go():
            m = self.manager()
            sid = await m.start(scenario_id)
            await m.wait(sid)
            return sid
        return session_snapshot(asyncio.run(go()), self.db)

    def test_s2_opens_one_fall_incident_and_notifies_contact_1_once(self):
        snap = self.run_to_end("S2")
        self.assertEqual(snap["status"], "Stopped")
        self.assertEqual(snap["end_reason"], "timeout")
        self.assertTrue(snap["recording_ended"])
        self.assertEqual(len(snap["incidents"]), 1)
        incident = snap["incidents"][0]
        self.assertEqual(incident["alert_type"], "FALL")
        self.assertEqual(incident["source_start_ms"], S2_FALL_START_MS)
        self.assertEqual(incident["decision_source"], "model")
        self.assertEqual(incident["notification_status"], "simulated")
        self.assertEqual(
            [(a["kind"], a["target"], a["incident_id"]) for a in snap["actions"]],
            [("notify", "contact_1", incident["id"])],
        )

    def test_s1_walk_produces_no_incident(self):
        snap = self.run_to_end("S1")
        self.assertEqual(snap["incidents"], [])
        self.assertEqual(snap["actions"], [])

    def test_two_runs_keep_separate_detector_state(self):
        first = self.run_to_end("S2")
        second = self.run_to_end("S2")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual([i["source_start_ms"] for i in first["incidents"]], [S2_FALL_START_MS])
        self.assertEqual([i["source_start_ms"] for i in second["incidents"]], [S2_FALL_START_MS])
        self.assertNotEqual(first["incidents"][0]["id"], second["incidents"][0]["id"])

    def test_second_start_is_refused_until_the_first_run_is_stopped(self):
        async def go():
            m = self.manager(realtime=True, run_timeout_seconds=60)
            first = await m.start("S2")
            with self.assertRaises(RunAlreadyActive):
                await m.start("S1")
            await m.stop(first)
            second = await m.start("S1")
            await m.stop(second)
            await m.wait(second)
            return first, second
        first, second = asyncio.run(go())
        self.assertEqual(session_snapshot(first, self.db)["end_reason"], "user_stop")
        self.assertEqual(session_snapshot(second, self.db)["status"], "Stopped")

    def test_stop_before_the_fall_leaves_no_incident(self):
        async def go():
            m = self.manager(realtime=True, run_timeout_seconds=60)
            sid = await m.start("S2")
            await asyncio.sleep(1.0)
            await m.stop(sid)
            await m.wait(sid)
            return sid
        snap = session_snapshot(asyncio.run(go()), self.db)
        self.assertEqual(snap["status"], "Stopped")
        self.assertEqual(snap["end_reason"], "user_stop")
        self.assertFalse(snap["recording_ended"])
        self.assertLess(snap["source_offset_ms"], S2_FALL_START_MS)
        self.assertEqual(snap["incidents"], [])

    def test_restart_marks_a_running_session_interrupted(self):
        with self.db() as db:
            db.add(SpatialSession(id="orphan", scenario_id="S2", status="Running", started_at=_utcnow(),
                                  source_offset_ms=3000, recording_ended=False, model_source="keras"))
            db.commit()
        self.assertEqual(self.manager().mark_interrupted_runs(), 1)
        snap = session_snapshot("orphan", self.db)
        self.assertEqual((snap["status"], snap["end_reason"]), ("Interrupted", "session_interrupted"))

    def test_fall_opens_an_incident_even_when_the_legacy_cooldown_suppresses_it(self):
        scenario = load_scenario("S2")
        legacy_key, monitor = "test:legacy-cooldown", StreamMonitor("test:spatial", fall_detection_service)
        first_fall = None
        try:
            for ms, sample in zip(scenario.source_ms, scenario.samples):
                legacy = fall_detection_service.add_sample(legacy_key, sample)
                event = monitor.add_sample(int(ms), sample)
                if legacy.state == "FALL" and first_fall is None:
                    first_fall = (int(ms), legacy.alert_suppressed, event)
        finally:
            fall_detection_service.reset_patient(legacy_key)
            monitor.close()
        ms, suppressed, event = first_fall
        self.assertEqual(ms, S2_FALL_START_MS)
        self.assertTrue(suppressed, "legacy cooldown, consumed by earlier INSTABILITY windows, hides this fall")
        self.assertEqual(event.kind, "opened")


class SpatialApiTest(unittest.TestCase):
    def test_unknown_or_unsafe_scenario_ids_are_404(self):
        client = TestClient(app)
        for scenario_id in ("S9", "../ml", ""):
            response = client.post("/api/spatial/sessions", json={"scenario_id": scenario_id})
            self.assertEqual(response.status_code, 404, scenario_id)


if __name__ == "__main__":
    unittest.main()
