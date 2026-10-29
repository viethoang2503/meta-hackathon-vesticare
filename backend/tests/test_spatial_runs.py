"""Spatial replay runs: sessions, fall incidents and the simulated contact_1 notification.

Runs the real scenarios in assets/scenarios through the real detector. The
non-realtime manager replays a whole recording in one step so a run takes
seconds instead of the scenario's wall-clock length.
"""

import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session as OrmSession  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models import schemas  # noqa: E402,F401
from app.models.schemas import SpatialSession  # noqa: E402
from app.services.fall_detection import FallDetectionService, fall_detection_service  # noqa: E402
from app.services.monitoring import StreamMonitor  # noqa: E402
from app.services.spatial_runs import (  # noqa: E402
    RunAlreadyActive,
    SpatialRunManager,
    _utcnow,
    load_scenario,
    session_snapshot,
)

# The SisFall impact; the model also fires at 14720 ms while still walking, which the stillness rule drops.
S2_FALL_START_MS = 23940


class PausedAfterStatusRead(OrmSession):
    """Pauses the first worker-thread read of a session row until the test releases it."""

    gate: dict | None = None

    def get(self, entity, ident, **kw):
        row = super().get(entity, ident, **kw)
        gate = PausedAfterStatusRead.gate
        if gate and entity is SpatialSession and threading.current_thread() is not threading.main_thread() \
                and not gate["reached"].is_set():
            gate["reached"].set()
            gate["release"].wait(30)
        return row


class SpatialRunsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{self.tmp.name}/spatial.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def tearDown(self):
        PausedAfterStatusRead.gate = None
        self.tmp.cleanup()

    def manager(self, **kwargs):
        return SpatialRunManager(
            session_factory=self.db,
            **{"realtime": False, "run_timeout_seconds": 0.5, "wearer_cancel_seconds": 0.2, **kwargs},
        )

    async def wait_for_incident(self, sid):
        while not session_snapshot(sid, self.db)["incidents"]:
            await asyncio.sleep(0.05)
        return session_snapshot(sid, self.db)["incidents"][0]["id"]

    def run_to_end(self, scenario_id):
        """Replay the whole recording, let in-flight notifications land, then Stop."""
        async def go():
            m = self.manager(run_timeout_seconds=60)
            sid = await m.start(scenario_id)
            while not session_snapshot(sid, self.db)["recording_ended"]:
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.5)
            await m.stop(sid)
            return sid
        return session_snapshot(asyncio.run(go()), self.db)

    def test_s2_fall_alerts_contact_1_once_when_the_wearer_does_not_answer(self):
        snap = self.run_to_end("S2")
        self.assertEqual((snap["status"], snap["end_reason"]), ("Stopped", "user_stop"))
        self.assertTrue(snap["recording_ended"])
        self.assertEqual(len(snap["incidents"]), 1)
        incident = snap["incidents"][0]
        self.assertEqual(incident["alert_type"], "FALL")
        self.assertEqual(incident["source_start_ms"], S2_FALL_START_MS)
        self.assertEqual(incident["decision_source"], "keras")
        self.assertEqual(incident["notification_status"], "simulated")
        self.assertEqual(incident["state"], "alerted")
        self.assertEqual(
            [(a["kind"], a["target"], a["incident_id"]) for a in snap["actions"]],
            [("notify", "contact_1", incident["id"])],
        )

    def test_s1_walk_produces_no_incident(self):
        snap = self.run_to_end("S1")
        self.assertEqual(snap["incidents"], [])
        self.assertEqual(snap["actions"], [])

    def test_im_ok_inside_the_countdown_cancels_and_nobody_is_notified(self):
        async def go():
            m = self.manager(run_timeout_seconds=60, wearer_cancel_seconds=3)
            sid = await m.start("S2")
            incident_id = await self.wait_for_incident(sid)
            result = await m.wearer_ok(incident_id)
            await asyncio.sleep(3.5)
            await m.stop(sid)
            return result, session_snapshot(sid, self.db)
        result, snap = asyncio.run(go())
        self.assertEqual((result["applied"], result["incident_state"]), (True, "cancelled_by_wearer"))
        self.assertEqual(snap["incidents"][0]["state"], "cancelled_by_wearer")
        self.assertEqual([(a["kind"], a["target"]) for a in snap["actions"]], [("self_report", "wearer")])

    def test_im_ok_after_the_alert_is_recorded_but_does_not_cancel(self):
        async def go():
            m = self.manager(run_timeout_seconds=60)
            sid = await m.start("S2")
            incident_id = await self.wait_for_incident(sid)
            await asyncio.sleep(0.6)
            result = await m.wearer_ok(incident_id)
            again = await m.wearer_ok(incident_id)
            await m.stop(sid)
            return result, again, session_snapshot(sid, self.db)
        result, again, snap = asyncio.run(go())
        self.assertEqual((result["applied"], result["incident_state"]), (True, "alerted"))
        self.assertEqual((again["applied"], again["reason"]), (False, "already_recorded"))
        self.assertEqual(
            [(a["kind"], a["target"]) for a in snap["actions"]],
            [("notify", "contact_1"), ("self_report", "wearer")],
        )

    def test_stop_during_the_countdown_means_no_later_notification(self):
        async def go():
            m = self.manager(run_timeout_seconds=60, wearer_cancel_seconds=2)
            sid = await m.start("S2")
            await self.wait_for_incident(sid)
            await m.stop(sid)
            at_stop = session_snapshot(sid, self.db)
            await asyncio.sleep(2.5)
            return at_stop, session_snapshot(sid, self.db)
        at_stop, later = asyncio.run(go())
        self.assertEqual(later, at_stop)
        self.assertEqual(later["incidents"][0]["state"], "awaiting_wearer")
        self.assertEqual(later["actions"], [])

    def test_run_timeout_stops_the_run(self):
        async def go():
            m = self.manager(run_timeout_seconds=0.5)
            sid = await m.start("S1")
            await m.wait(sid)
            return sid
        snap = session_snapshot(asyncio.run(go()), self.db)
        self.assertEqual((snap["status"], snap["end_reason"]), ("Stopped", "timeout"))

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

    def test_nothing_changes_after_stop_returns_even_with_a_write_in_flight(self):
        gate = {"reached": threading.Event(), "release": threading.Event()}
        PausedAfterStatusRead.gate = gate
        db = sessionmaker(bind=self.engine, class_=PausedAfterStatusRead, autocommit=False, autoflush=False)

        async def go():
            m = SpatialRunManager(session_factory=db, realtime=False, run_timeout_seconds=60)
            sid = await m.start("S2")
            self.assertTrue(await asyncio.to_thread(gate["reached"].wait, 60), "ingest never read the session")
            stopping = asyncio.create_task(m.stop(sid))
            await asyncio.sleep(0.5)
            gate["release"].set()
            await stopping
            at_return = session_snapshot(sid, self.db)
            await asyncio.sleep(2)
            return at_return, session_snapshot(sid, self.db)

        at_return, later = asyncio.run(go())
        self.assertEqual((at_return["status"], at_return["end_reason"]), ("Stopped", "user_stop"))
        self.assertEqual(later, at_return)

    def test_stop_while_the_detector_is_still_working_leaves_no_trace(self):
        async def go():
            m = self.manager(run_timeout_seconds=60)
            sid = await m.start("S2")
            await asyncio.sleep(0.3)
            await m.stop(sid)
            after_stop = session_snapshot(sid, self.db)
            await asyncio.sleep(5)
            return after_stop, session_snapshot(sid, self.db)
        after_stop, later = asyncio.run(go())
        for snap in (after_stop, later):
            self.assertEqual((snap["status"], snap["end_reason"]), ("Stopped", "user_stop"))
            self.assertEqual(snap["incidents"], [])
            self.assertEqual(snap["actions"], [])
            self.assertEqual(snap["source_offset_ms"], 0)

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
        at_impact = None
        try:
            for ms, sample in zip(scenario.source_ms, scenario.samples):
                legacy = fall_detection_service.add_sample(legacy_key, sample)
                monitor.add_sample(int(ms), sample)
                if int(ms) == S2_FALL_START_MS:
                    at_impact = legacy
        finally:
            fall_detection_service.reset_patient(legacy_key)
            monitor.close()
        self.assertEqual(at_impact.state, "FALL")
        self.assertTrue(at_impact.alert_suppressed, "the legacy cooldown, consumed by earlier windows, hides the impact")
        self.assertEqual([i.source_start_ms for i in monitor.incidents], [S2_FALL_START_MS])


class DecisionSourceTest(unittest.TestCase):
    def test_incident_names_the_heuristic_when_the_model_file_is_missing(self):
        with patch("app.services.fall_detection.KERAS_MODEL_PATH", Path("/nonexistent.keras")):
            detector = FallDetectionService()
        self.assertEqual(detector.model_source, "heuristic")
        monitor = StreamMonitor("test:heuristic", detector)
        scenario = load_scenario("S2")
        for ms, sample in zip(scenario.source_ms, scenario.samples):
            monitor.add_sample(int(ms), sample)
        monitor.close()
        self.assertEqual([(i.source_start_ms, i.decision_source) for i in monitor.incidents], [(22660, "heuristic")])


class SpatialApiTest(unittest.TestCase):
    def test_unknown_or_unsafe_scenario_ids_are_404(self):
        client = TestClient(app)
        for scenario_id in ("S9", "../ml", ""):
            response = client.post("/api/spatial/sessions", json={"scenario_id": scenario_id})
            self.assertEqual(response.status_code, 404, scenario_id)


if __name__ == "__main__":
    unittest.main()
