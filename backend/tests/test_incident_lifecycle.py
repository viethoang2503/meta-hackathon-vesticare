"""MVP 2 policy through the real run manager: HELP, cancel then HELP, late OK, send failures.

Runs S2 through the real detector (non-realtime) with a recording notifier, so every
message text and status is the one a caregiver would get.
"""

import asyncio
import tempfile
import threading
import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import schemas  # noqa: F401
from app.services.spatial_runs import SpatialRunManager, RunAlreadyActive, _utcnow, session_snapshot


class RecordingNotifier:
    channel = "test"

    def __init__(self, outcomes=None, delay=0.0):
        self.sent, self.outcomes, self.delay = [], outcomes or {}, delay
        self.lock = threading.Lock()

    def configured_contacts(self):
        return {"contact_1", "contact_2"}

    def send(self, contact, text, incident_id, ack_button):
        import time
        time.sleep(self.delay)
        with self.lock:
            self.sent.append((contact, text, ack_button))
        return {"status": self.outcomes.get(contact, "sent")}


def actions(snap):
    return [(a["kind"], a["target"]) for a in snap["actions"] if a["incident_id"] is not None and a["kind"] != "self_report"]


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/l.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def tearDown(self):
        self.tmp.cleanup()

    def run_s2(self, script, notifier=None, **kw):
        """Start S2, wait for the incident, run script(m, sid, incident_id), let everything finish."""
        notifier = notifier or RecordingNotifier()

        async def go():
            m = SpatialRunManager(session_factory=self.db, notifier=notifier, **{
                "realtime": False, "run_timeout_seconds": 60, "wearer_cancel_seconds": 0.6,
                "caregiver_ack_seconds": 0.4, "late_help_grace_seconds": 0.6, **kw})
            sid = await m.start("S2")
            while not session_snapshot(sid, self.db)["incidents"]:
                await asyncio.sleep(0.02)
            incident_id = session_snapshot(sid, self.db)["incidents"][0]["id"]
            out = await script(m, sid, incident_id)
            await m.stop(sid)
            await m.wait(sid)
            return session_snapshot(sid, self.db), out
        snap, out = asyncio.run(go())
        return snap, out, notifier

    def test_help_inside_the_window_alerts_at_once_and_says_help_requested(self):
        async def say_help(m, sid, iid):
            result = await m.wearer_reply(iid, "help", _utcnow())
            await asyncio.sleep(0.2)
            await m.acknowledge(iid, "telegram:contact_1")
            return result
        snap, result, n = self.run_s2(say_help, wearer_cancel_seconds=5, caregiver_ack_seconds=5)
        self.assertEqual((result["applied"], result["incident_state"]), (True, "alerted"))
        self.assertEqual(actions(snap), [("notify", "contact_1"), ("ack", "telegram:contact_1")])
        contact, text, ack_button = n.sent[0]
        self.assertEqual((contact, ack_button), ("contact_1", True))
        self.assertIn("Wearer response: Help requested.", text)
        self.assertIn("Location: Unknown.", text)

    def test_help_captured_before_the_deadline_after_a_cancel_raises_the_alert(self):
        async def ok_then_help(m, sid, iid):
            ok = await m.wearer_reply(iid, "ok", _utcnow())
            captured = _utcnow()
            await asyncio.sleep(0.8)          # past the deadline, inside the grace
            help_ = await m.wearer_reply(iid, "help", captured)
            await m.acknowledge(iid, "telegram:contact_1")
            return ok, help_
        snap, (ok, help_), n = self.run_s2(ok_then_help, caregiver_ack_seconds=5)
        self.assertEqual(ok["incident_state"], "cancelled_by_wearer")
        self.assertEqual((help_["applied"], help_["incident_state"]), (True, "alerted"))
        self.assertEqual(actions(snap), [("notify", "contact_1"), ("ack", "telegram:contact_1")])

    def test_help_captured_after_the_deadline_does_not_undo_a_cancel(self):
        async def ok_then_late_help(m, sid, iid):
            await m.wearer_reply(iid, "ok", _utcnow())
            await asyncio.sleep(0.8)
            return await m.wearer_reply(iid, "help", _utcnow())
        snap, result, n = self.run_s2(ok_then_late_help)
        self.assertEqual((result["applied"], result["reason"]), (False, "captured_after_deadline"))
        self.assertEqual(actions(snap), [])
        self.assertEqual(n.sent, [])

    def test_a_cancelled_incident_holds_the_run_until_the_grace_ends(self):
        async def ok_then_start(m, sid, iid):
            await m.wearer_reply(iid, "ok", _utcnow())
            await m.stop(sid)
            with self.assertRaises(RunAlreadyActive):
                await m.start("S1")
            await m.wait(sid)
            nxt = await m.start("S1")
            await m.stop(nxt)
        snap, _, n = self.run_s2(ok_then_start)
        self.assertEqual(snap["incidents"][0]["state"], "cancelled_by_wearer")
        self.assertEqual(n.sent, [])

    def test_a_failed_first_send_escalates_at_once(self):
        async def wait(m, sid, iid):
            await asyncio.sleep(1.0)
        snap, _, n = self.run_s2(wait, notifier=RecordingNotifier({"contact_1": "failed"}), caregiver_ack_seconds=30)
        self.assertEqual(actions(snap), [("notify", "contact_1"), ("escalate", "contact_2")])
        self.assertIn("Reason: The first contact could not be notified.", n.sent[1][1])
        self.assertEqual(snap["incidents"][0]["notification_status"], "failed")

    def test_an_unknown_send_keeps_the_ack_timer_and_does_not_escalate_early(self):
        async def check_early(m, sid, iid):
            await asyncio.sleep(0.75)   # deadline 0.6 s, ack window 0.5 s: not yet
            return actions(session_snapshot(sid, self.db))
        snap, early, n = self.run_s2(check_early, notifier=RecordingNotifier({"contact_1": "unknown"}),
                                     caregiver_ack_seconds=0.5)
        self.assertEqual(early, [("notify", "contact_1")])
        self.assertEqual(actions(snap), [("notify", "contact_1"), ("escalate", "contact_2")])
        self.assertIn("Reason: No acknowledgement within the response window.", n.sent[1][1])

    def test_late_ok_before_escalation_is_carried_by_the_escalation_not_a_second_message(self):
        async def late_ok(m, sid, iid):
            while session_snapshot(sid, self.db)["incidents"][0]["state"] != "alerted":
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.1)
            return await m.wearer_reply(iid, "ok", _utcnow())
        snap, result, n = self.run_s2(late_ok, caregiver_ack_seconds=0.5)
        self.assertEqual(result["applied"], True)
        self.assertEqual(actions(snap), [("notify", "contact_1"), ("late_ok", "contact_1"),
                                         ("escalate", "contact_2"), ("late_ok", "contact_2")])
        texts = {contact: t for contact, t, _ in n.sent}
        self.assertEqual([c for c, _, _ in n.sent], ["contact_1", "contact_1", "contact_2"])
        self.assertIn("Wearer response: OK reported after alert.", texts["contact_2"])
        included = [a for a in snap["actions"] if a["kind"] == "late_ok" and a["target"] == "contact_2"][0]
        self.assertEqual(included["detail"]["status"], "included_in_alert")

    def test_a_late_ok_while_the_alert_is_still_sending_waits_for_its_result(self):
        async def ok_during_send(m, sid, iid):
            while session_snapshot(sid, self.db)["incidents"][0]["state"] != "alerted":
                await asyncio.sleep(0.01)
            result = await m.wearer_reply(iid, "ok", _utcnow())
            await asyncio.sleep(0.5)
            await m.acknowledge(iid, "telegram:contact_1")
            return result
        snap, result, n = self.run_s2(ok_during_send, notifier=RecordingNotifier(delay=0.3), caregiver_ack_seconds=5)
        self.assertTrue(result["applied"])
        self.assertEqual([c for c, _, _ in n.sent], ["contact_1", "contact_1"])
        self.assertTrue(n.sent[1][1].startswith("Update for fall alert"))
