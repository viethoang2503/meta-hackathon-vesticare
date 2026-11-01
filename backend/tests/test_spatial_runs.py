"""Spatial replay runs: sessions, fall incidents and the simulated contact_1 notification.

Runs the real scenarios in assets/scenarios through the real detector. The
non-realtime manager replays a whole recording in one step so a run takes
seconds instead of the scenario's wall-clock length.
"""

import asyncio
import json
import sys
import tempfile
import threading
import time
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
from app.models.schemas import SpatialAnalysis, SpatialSession  # noqa: E402
from app.services.muse_client import AgentError  # noqa: E402
from app.services.sam_client import BOX, SamError, SamResult  # noqa: E402
from app.services.fall_detection import FallDetectionService, fall_detection_service  # noqa: E402
from app.services.monitoring import StreamMonitor  # noqa: E402
from app.services.spatial_runs import (  # noqa: E402
    RunAlreadyActive,
    SpatialRunManager,
    incident_detail,
    _utcnow,
    load_scenario,
    session_snapshot,
)

# The SisFall impact; the model also fires at 14720 ms while still walking, which the stillness rule drops.
S2_FALL_START_MS = 23940
S1_STAIRS_ENTRIES_MS = (16340, 35260)
S2_STAIRS_ENTRY_MS = 20180


def incident_actions(snap):
    return [(a["kind"], a["target"]) for a in snap["actions"] if a["incident_id"] is not None]
SAM_OUTPUT = (Path(__file__).parent / "fixtures" / "sam_stairs_output.txt").read_text()


def recorded_sam(image, mime, prompt):
    """A real SAM 3.1 answer recorded on day 3 (one stairs mask); no network in tests."""
    boxes = [dict(zip(("x1", "y1", "x2", "y2", "w", "h"), map(int, m))) for m in BOX.findall(SAM_OUTPUT)]
    return SamResult(SAM_OUTPUT, boxes, 2300, "sam-3.1")


def scripted_agent(messages, tools, tool_choice, timeout):
    """Muse Spark stand-in: gathers evidence on the first turn, answers citing it on the second."""
    incident_id = int(messages[1]["content"].split("incident ")[1].split(" ")[0])
    if messages[-1]["role"] == "user":
        calls = [("get_event_context", {"incident_id": incident_id}), ("get_frame_analysis", {"incident_id": incident_id}),
                 ("get_environment_fixes", {"hazard": "stairs"})]
        return {"message": {"content": "", "tool_calls": [
            {"id": f"c{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for n, (name, args) in enumerate(calls)]}, "usage": {"total_tokens": 400}}
    answer = {
        "summary": "A fall was detected at the foot of the stairs in the simulated home.",
        "observations": [{"text": "High impact then stillness.", "evidence_ids": [f"imu:{incident_id}"]},
                         {"text": "Stairs are in the rendered frame.", "evidence_ids": ["frame:S2-024000"]}],
        "uncertainties": ["Why the person fell is not visible in this evidence."],
        "recommendation_ids": ["stairs_handrail_both_sides"],
    }
    return {"message": {"content": json.dumps(answer), "tool_calls": None}, "usage": {"total_tokens": 300}}


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
            **{"realtime": False, "run_timeout_seconds": 0.5, "wearer_cancel_seconds": 0.2, "segmenter": recorded_sam, "chat": scripted_agent, **kwargs},
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
            [(a["kind"], a["target"], a["incident_id"]) for a in snap["actions"] if a["incident_id"] is not None],
            [("notify", "contact_1", incident["id"])],
        )

    def test_incident_detail_brings_location_frame_and_imu_for_the_same_moment(self):
        snap = self.run_to_end("S2")
        detail = incident_detail(snap["incidents"][0]["id"], self.db)
        self.assertEqual(detail["session_id"], snap["id"])
        self.assertEqual((detail["location"]["zone_id"], detail["location"]["source"]), ("stairs_approach", "Authored location"))
        self.assertEqual((detail["frame"]["status"], detail["frame"]["id"]), ("Available", "S2-024000"))
        self.assertLessEqual(detail["frame"]["alignment_error_ms"], 250)
        times = [row[0] for row in detail["imu"]["samples"]]
        self.assertEqual((times[0], times[-1]), (S2_FALL_START_MS - 5000, 28400), "window is clipped at the recording's last sample")
        self.assertEqual(len(times), 474)
        self.assertEqual(detail["imu"]["source"], "Scenario-paired recording")
        self.assertEqual(snap["incidents"][0]["location"]["zone_label"], "Foot of the stairs")

    def analyses_after_run(self, segmenter, kind="sam", **kwargs):
        async def go():
            m = self.manager(run_timeout_seconds=60, segmenter=segmenter, **kwargs)
            sid = await m.start("S2")
            incident_id = await self.wait_for_incident(sid)
            while not [a for a in incident_detail(incident_id, self.db)["analyses"] if a["kind"] == kind and a["status"] != "Running"]:
                await asyncio.sleep(0.05)
            await m.stop(sid)
            return [a for a in incident_detail(incident_id, self.db)["analyses"] if a["kind"] == kind]
        return asyncio.run(go())

    def test_sam_analyses_the_frame_of_the_fall_with_the_zone_prompt(self):
        [a] = self.analyses_after_run(recorded_sam)
        self.assertEqual((a["kind"], a["status"], a["prompt"], a["frame_id"]), ("sam", "Complete", "stairs", "S2-024000"))
        self.assertEqual(a["boxes"], [{"x1": 77, "y1": 134, "x2": 251, "y2": 245, "w": 329, "h": 246}])
        self.assertEqual(a["output_text"], SAM_OUTPUT)

    def test_sam_finding_nothing_is_nomatch_not_an_invented_mask(self):
        [a] = self.analyses_after_run(lambda image, mime, prompt: SamResult("", [], 500, "sam-3.1"))
        self.assertEqual((a["status"], a["boxes"], a["output_text"]), ("NoMatch", [], ""))

    def test_sam_error_is_stored_as_failed_with_its_reason(self):
        def broken(image, mime, prompt):
            raise SamError("Failed", "HTTP 500: upstream")
        [a] = self.analyses_after_run(broken)
        self.assertEqual((a["status"], a["reason"], a["output_text"]), ("Failed", "HTTP 500: upstream", None))

    def test_stop_cancels_a_running_analysis_and_a_late_answer_is_not_stored(self):
        release = threading.Event()

        def slow(image, mime, prompt):
            release.wait(10)
            return recorded_sam(image, mime, prompt)

        async def go():
            m = self.manager(run_timeout_seconds=60, segmenter=slow)
            sid = await m.start("S2")
            incident_id = await self.wait_for_incident(sid)
            while not incident_detail(incident_id, self.db)["analyses"]:
                await asyncio.sleep(0.05)
            await m.stop(sid)
            release.set()
            await m.wait(sid)
            return incident_detail(incident_id, self.db)
        detail = asyncio.run(go())
        [a] = detail["analyses"]
        self.assertEqual((a["status"], a["reason"], a["output_text"]), ("Cancelled", "user_stop", None))

    def test_a_slow_analysis_does_not_delay_the_caregiver_alert(self):
        release = threading.Event()

        def slow(image, mime, prompt):
            release.wait(10)
            return recorded_sam(image, mime, prompt)

        async def go():
            m = self.manager(run_timeout_seconds=60, segmenter=slow)
            sid = await m.start("S2")
            incident_id = await self.wait_for_incident(sid)
            await asyncio.sleep(0.6)
            detail = incident_detail(incident_id, self.db)
            release.set()
            await m.stop(sid)
            return detail
        detail = asyncio.run(go())
        self.assertEqual(detail["state"], "alerted")
        self.assertEqual(detail["analyses"][0]["status"], "Running")

    def test_stop_returns_normally_when_the_replay_finishes_while_it_waits(self):
        class SlowStop(SpatialRunManager):
            def _end_under(self, lock, session_id, status, reason):
                ended = super()._end_under(lock, session_id, status, reason)
                if reason == "user_stop":
                    time.sleep(6.0)  # longer than replaying all of S1, so the replay cleans up first
                return ended

        async def go():
            m = SlowStop(session_factory=self.db, realtime=False, run_timeout_seconds=60, segmenter=recorded_sam, chat=scripted_agent)
            sid = await m.start("S1")
            await m.stop(sid)
            return sid
        snap = session_snapshot(asyncio.run(go()), self.db)
        self.assertEqual((snap["status"], snap["end_reason"]), ("Stopped", "user_stop"))

    def test_an_unreadable_frame_file_is_recorded_as_unavailable(self):
        def missing(path):
            raise FileNotFoundError(path)
        with patch("app.services.spatial_runs._read_frame", missing):
            [a] = self.analyses_after_run(recorded_sam)
        self.assertEqual(a["status"], "Unavailable")
        self.assertIn("frame file unreadable", a["reason"])

    def test_the_analysis_budget_bounds_the_whole_call_and_a_late_answer_is_dropped(self):
        def slow(image, mime, prompt):
            time.sleep(0.3)
            return recorded_sam(image, mime, prompt)
        [a] = self.analyses_after_run(slow, analysis_timeout_seconds=0.05)
        self.assertEqual((a["status"], a["output_text"]), ("TimedOut", None))
        self.assertIn("0.05 s", a["reason"])

    def test_the_agent_explains_after_sam_with_its_tool_log_and_library_fixes(self):
        [agent] = self.analyses_after_run(recorded_sam, kind="agent", wearer_cancel_seconds=5)
        self.assertEqual((agent["status"], agent["model"]), ("Complete", "muse-spark-1.3"))
        self.assertEqual([c["tool"] for c in agent["tool_log"]["calls"]], ["get_event_context", "get_frame_analysis", "get_environment_fixes"])
        self.assertEqual(agent["tool_log"]["usage"], {"total_tokens": 700})
        self.assertEqual(agent["tool_log"]["context"], {"incident_state": "awaiting_wearer"})
        self.assertEqual(json.loads(agent["output_text"])["recommendation_ids"], ["stairs_handrail_both_sides"])

    def test_incident_detail_resolves_the_chosen_fixes_from_the_library(self):
        async def go():
            m = self.manager(run_timeout_seconds=60)
            sid = await m.start("S2")
            incident_id = await self.wait_for_incident(sid)
            while not [a for a in incident_detail(incident_id, self.db)["analyses"] if a["kind"] == "agent" and a["status"] != "Running"]:
                await asyncio.sleep(0.05)
            await m.stop(sid)
            return incident_detail(incident_id, self.db)
        detail = asyncio.run(go())
        self.assertEqual([r["title"] for r in detail["recommendations"]], ["Handrails on both sides of the stairs"])

    def test_an_agent_failure_is_recorded_and_the_caregiver_is_still_alerted(self):
        def down(messages, tools, tool_choice, timeout):
            raise AgentError("Failed", "HTTP 503: unavailable")
        [agent] = self.analyses_after_run(recorded_sam, kind="agent", chat=down)
        self.assertEqual((agent["status"], agent["reason"], agent["output_text"]), ("Failed", "HTTP 503: unavailable", None))

    def test_stop_cancels_a_running_agent(self):
        release = threading.Event()

        def slow_chat(messages, tools, tool_choice, timeout):
            release.wait(10)
            return scripted_agent(messages, tools, tool_choice, timeout)

        async def go():
            m = self.manager(run_timeout_seconds=60, chat=slow_chat)
            sid = await m.start("S2")
            incident_id = await self.wait_for_incident(sid)
            while not [a for a in incident_detail(incident_id, self.db)["analyses"] if a["kind"] == "agent"]:
                await asyncio.sleep(0.05)
            await m.stop(sid)
            release.set()
            await m.wait(sid)
            return [a for a in incident_detail(incident_id, self.db)["analyses"] if a["kind"] == "agent"]
        [agent] = asyncio.run(go())
        self.assertEqual((agent["status"], agent["reason"]), ("Cancelled", "user_stop"))

    def test_s1_walk_produces_no_incident_and_one_shown_advisory(self):
        snap = self.run_to_end("S1")
        self.assertEqual(snap["incidents"], [])
        self.assertEqual(
            [(a["zone_id"], a["source_ms"], a["shown"]) for a in snap["advisories"]],
            [("stairs_approach", S1_STAIRS_ENTRIES_MS[0], True), ("stairs_approach", S1_STAIRS_ENTRIES_MS[1], False)],
        )
        self.assertEqual(snap["advisories"][0]["text"], "Stairs ahead. Use the handrail.")
        self.assertEqual([a["kind"] for a in snap["actions"]], ["advisory", "advisory"])

    def test_a_shown_advisory_is_confirmed_by_sam_on_the_entry_frame(self):
        snap = self.run_to_end("S1")
        shown = snap["advisories"][0]
        self.assertEqual((shown["sam_status"], shown["frame_id"]), ("Complete", "S1-016500"))
        self.assertIsNone(snap["advisories"][1]["sam_status"], "a suppressed entry does not call SAM")

    def test_the_advisory_comes_before_the_fall_and_notifies_nobody(self):
        snap = self.run_to_end("S2")
        [advisory] = snap["advisories"]
        self.assertEqual((advisory["source_ms"], advisory["shown"]), (S2_STAIRS_ENTRY_MS, True))
        self.assertLess(advisory["source_ms"], S2_FALL_START_MS)
        advisory_action = next(a for a in snap["actions"] if a["kind"] == "advisory")
        self.assertIsNone(advisory_action["incident_id"])

    def run_until_alerted(self, after, **kwargs):
        """Run S2, wait until contact_1 is notified, then call after(manager, session_id, incident_id)."""
        async def go():
            m = self.manager(run_timeout_seconds=60, **kwargs)
            sid = await m.start("S2")
            incident_id = await self.wait_for_incident(sid)
            while session_snapshot(sid, self.db)["incidents"][0]["state"] != "alerted":
                await asyncio.sleep(0.05)
            extra = await after(m, sid, incident_id)
            await m.stop(sid)
            return session_snapshot(sid, self.db), extra
        return asyncio.run(go())

    def test_caregiver_ack_before_the_deadline_means_contact_2_is_never_told(self):
        async def ack_then_wait(m, sid, incident_id):
            result = await m.acknowledge(incident_id, "caregiver_demo")
            await asyncio.sleep(0.8)
            return result
        snap, result = self.run_until_alerted(ack_then_wait, caregiver_ack_seconds=0.4)
        self.assertEqual((result["applied"], result["reason"]), (True, None))
        self.assertTrue(snap["incidents"][0]["is_acknowledged"])
        self.assertEqual(incident_actions(snap), [("notify", "contact_1"), ("ack", "caregiver_demo")])
        detail = incident_detail(snap["incidents"][0]["id"], self.db)
        self.assertIsNotNone(detail["caregiver_deadline_at"])
        self.assertIsNotNone(detail["acknowledged_at"])

    def test_no_ack_escalates_to_contact_2_exactly_once(self):
        async def wait_out(m, sid, incident_id):
            await asyncio.sleep(1.2)
            return await m.acknowledge(incident_id, "caregiver_demo")
        snap, late_ack = self.run_until_alerted(wait_out, caregiver_ack_seconds=0.4)
        self.assertEqual(incident_actions(snap), [("notify", "contact_1"), ("escalate", "contact_2"), ("ack", "caregiver_demo")])
        self.assertTrue(late_ack["applied"])

    def test_a_second_ack_is_not_recorded_twice(self):
        async def ack_twice(m, sid, incident_id):
            first = await m.acknowledge(incident_id, "caregiver_demo")
            return first, await m.acknowledge(incident_id, "caregiver_demo")
        snap, (first, second) = self.run_until_alerted(ack_twice, caregiver_ack_seconds=5)
        self.assertEqual((first["applied"], second["applied"], second["reason"]), (True, False, "already_recorded"))
        self.assertEqual(incident_actions(snap).count(("ack", "caregiver_demo")), 1)

    def test_an_alerted_incident_still_escalates_after_stop(self):
        # MVP2.md: Stop ends input, not an opened incident.
        async def stop_and_wait(m, sid, incident_id):
            await m.stop(sid)
            await asyncio.sleep(0.8)
        snap, _ = self.run_until_alerted(stop_and_wait, caregiver_ack_seconds=0.4)
        self.assertEqual(snap["status"], "Stopped")
        self.assertEqual(incident_actions(snap), [("notify", "contact_1"), ("escalate", "contact_2")])

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
        self.assertEqual(incident_actions(snap), [("self_report", "wearer")])

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
        self.assertEqual(incident_actions(snap), [("notify", "contact_1"), ("self_report", "wearer"),
                                                  ("late_ok", "contact_1"), ("self_report", "wearer")])
        late = next(a for a in snap["actions"] if a["kind"] == "late_ok")
        self.assertTrue(late["detail"]["text"].startswith("Update for fall alert"))

    def test_stop_during_the_countdown_still_alerts_at_the_deadline_and_blocks_a_new_start(self):
        async def go():
            m = self.manager(run_timeout_seconds=60, wearer_cancel_seconds=1, caregiver_ack_seconds=0.3)
            sid = await m.start("S2")
            await self.wait_for_incident(sid)
            await m.stop(sid)
            at_stop = session_snapshot(sid, self.db)
            with self.assertRaises(RunAlreadyActive):
                await m.start("S1")
            await m.wait(sid)
            later = session_snapshot(sid, self.db)
            next_sid = await m.start("S1")   # free once the incident has finished
            await m.stop(next_sid)
            return at_stop, later
        at_stop, later = asyncio.run(go())
        self.assertEqual(at_stop["incidents"][0]["state"], "awaiting_wearer")
        self.assertEqual(later["incidents"][0]["state"], "alerted")
        self.assertEqual(incident_actions(later), [("notify", "contact_1"), ("escalate", "contact_2")])

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
            m = SpatialRunManager(session_factory=db, realtime=False, run_timeout_seconds=60, segmenter=recorded_sam, chat=scripted_agent)
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
            db.add(SpatialAnalysis(session_id="orphan", incident_id=1, kind="sam", status="Running", created_at=_utcnow()))
            db.commit()
        self.assertEqual(self.manager().mark_interrupted_runs(), 1)
        with self.db() as db:
            analysis = db.query(SpatialAnalysis).one()
            self.assertEqual((analysis.status, analysis.reason), ("Failed", "session_interrupted"))
        snap = session_snapshot("orphan", self.db)
        self.assertEqual((snap["status"], snap["end_reason"]), ("Interrupted", "session_interrupted"))


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
