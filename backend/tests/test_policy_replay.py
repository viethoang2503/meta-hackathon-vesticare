"""M5b: the virtual-clock policy replay, alone and against the real run manager."""

import asyncio
import tempfile
import unittest

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import schemas  # noqa: F401
from app.models.schemas import AlertEvent
from app.services.policy_replay import PolicyConfig, chain, simulate
from app.services.s3_package import incident_record
from app.services.spatial_runs import SpatialRunManager, _utcnow, session_snapshot
from test_incident_lifecycle import RecordingNotifier

CONFIG = PolicyConfig(wearer_cancel_s=30, caregiver_ack_s=60, grace_s=15)


def reply(kind, at, capture=None):
    return {"kind": "reply", "reply": kind, "capture_end_ms": capture, "policy_ms": at}


def kinds(result):
    return [(a["t_ms"], a["kind"], a["target"]) for a in result["actions"]]


def test_silence_alerts_at_the_deadline_and_escalates_after_the_ack_window():
    result = simulate(CONFIG, [], {"contact_1": {"status": "sent", "done_ms": 30_400}})
    assert kinds(result) == [(30_000, "notify", "contact_1"), (90_000, "escalate", "contact_2")]
    assert result["final"] == {"state": "alerted", "alert_reason": "timeout", "acknowledged": False}


def test_a_reply_at_exactly_the_deadline_loses_to_the_timeout():
    result = simulate(CONFIG, [reply("ok", 30_000, capture=29_000)], {"contact_1": {"status": "sent", "done_ms": 30_100}})
    assert [a["kind"] for a in result["actions"]][:2] == ["notify", "self_report"]
    assert result["final"]["state"] == "alerted"
    assert result["actions"][1]["late_ok"] is True


def test_an_ack_at_exactly_the_caregiver_deadline_does_not_stop_the_escalation():
    events = [{"kind": "ack", "actor": "telegram:contact_1", "policy_ms": 90_000}]
    result = simulate(CONFIG, events, {"contact_1": {"status": "sent", "done_ms": 30_100}})
    assert [a["kind"] for a in result["actions"]] == ["notify", "escalate", "ack"]


def test_an_ack_before_the_caregiver_deadline_stops_it():
    events = [{"kind": "ack", "actor": "telegram:contact_1", "policy_ms": 40_000}]
    result = simulate(CONFIG, events, {"contact_1": {"status": "sent", "done_ms": 30_100}})
    assert [a["kind"] for a in result["actions"]] == ["notify", "ack"]


@pytest.mark.parametrize("policy_ms, capture, raised", [(35_000, 29_000, True), (45_000, 29_000, True), (45_001, 29_000, False),
                                                        (35_000, 30_500, False), (35_000, None, False)])
def test_help_after_a_cancel_needs_a_capture_before_d_and_a_result_by_d_plus_grace(policy_ms, capture, raised):
    events = [reply("ok", 5_000, capture=4_000), reply("help", policy_ms, capture=capture)]
    result = simulate(CONFIG, events, {"contact_1": {"status": "sent", "done_ms": policy_ms + 100}})
    assert (result["final"]["alert_reason"] == "wearer_help") is raised
    assert result["final"]["state"] == ("alerted" if raised else "cancelled_by_wearer")


def test_a_failed_first_send_escalates_when_the_failure_arrives_and_never_late_ok_to_a_failed_contact():
    events = [reply("ok", 31_000, capture=None)]
    result = simulate(CONFIG, events, {"contact_1": {"status": "failed", "done_ms": 30_300},
                                      "contact_2": {"status": "sent", "done_ms": 30_500}})
    assert kinds(result) == [(30_000, "notify", "contact_1"), (30_300, "escalate", "contact_2"),
                             (31_000, "self_report", "wearer"), (31_000, "late_ok", "contact_2")]


def test_a_late_ok_while_the_alert_is_still_sending_waits_for_the_result():
    events = [reply("ok", 30_200, capture=None)]
    result = simulate(CONFIG, events, {"contact_1": {"status": "sent", "done_ms": 30_600}})
    assert (30_600, "late_ok", "contact_1") in kinds(result)
    assert (30_200, "late_ok", "contact_1") not in kinds(result)


def test_two_runs_of_the_same_events_are_identical():
    events = [reply("ok", 5_000, capture=4_000), reply("help", 33_000, capture=29_500)]
    sends = {"contact_1": {"status": "sent", "done_ms": 33_200}}
    assert simulate(CONFIG, events, sends) == simulate(CONFIG, events, sends)


class AgainstTheRealManagerTest(unittest.TestCase):
    """The same recorded inputs through the simulator give the real manager's chain."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/m.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def tearDown(self):
        self.tmp.cleanup()

    def real_vs_replay(self, script, notifier=None, cancel=0.6, ack=0.4, grace=0.6):
        notifier = notifier or RecordingNotifier()

        async def go():
            m = SpatialRunManager(session_factory=self.db, notifier=notifier, realtime=False, run_timeout_seconds=60,
                                  wearer_cancel_seconds=cancel, caregiver_ack_seconds=ack, late_help_grace_seconds=grace)
            sid = await m.start("S2")
            while not session_snapshot(sid, self.db)["incidents"]:
                await asyncio.sleep(0.02)
            iid = session_snapshot(sid, self.db)["incidents"][0]["id"]
            await script(m, sid, iid)
            await m.stop(sid)
            await m.wait(sid)
            return iid
        iid = asyncio.run(go())
        with self.db() as db:
            record = incident_record(db, db.get(AlertEvent, iid), {"wearer_cancel_seconds": cancel})
        replayed = simulate(PolicyConfig(cancel, ack, grace), record["events"], record["sends"])
        return record, replayed

    def assert_same(self, record, replayed):
        self.assertEqual(chain(replayed), chain({"actions": record["actions"]}))
        self.assertEqual(replayed["final"], record["final"])

    def test_silence_then_escalation(self):
        async def script(m, sid, iid):
            await asyncio.sleep(1.8)
        record, replayed = self.real_vs_replay(script)
        self.assertEqual([a["kind"] for a in record["actions"]], ["notify", "escalate"])
        self.assert_same(record, replayed)

    def test_ok_then_help_inside_the_window(self):
        async def script(m, sid, iid):
            await m.wearer_reply(iid, "ok", _utcnow())
            await asyncio.sleep(0.2)
            await m.wearer_reply(iid, "help", _utcnow())
            await asyncio.sleep(0.3)
        record, replayed = self.real_vs_replay(script, cancel=3, ack=5)
        self.assertEqual(record["final"]["alert_reason"], "wearer_help")
        self.assert_same(record, replayed)

    def test_late_ok_then_ack(self):
        async def script(m, sid, iid):
            await asyncio.sleep(0.9)
            await m.wearer_reply(iid, "ok", _utcnow())
            await m.wearer_reply(iid, "ok", _utcnow())
            await asyncio.sleep(0.1)
            await m.acknowledge(iid, "telegram:contact_1")
            await asyncio.sleep(0.5)
        record, replayed = self.real_vs_replay(script, ack=2)
        self.assertEqual([a["kind"] for a in record["actions"]],
                         ["notify", "self_report", "late_ok", "self_report", "ack"])
        self.assert_same(record, replayed)

    def test_a_failed_first_send(self):
        async def script(m, sid, iid):
            await asyncio.sleep(1.2)
        record, replayed = self.real_vs_replay(script, notifier=RecordingNotifier(outcomes={"contact_1": "failed"}), ack=5)
        self.assertEqual([a["kind"] for a in record["actions"]], ["notify", "escalate"])
        self.assert_same(record, replayed)

    def test_help_captured_before_d_arrives_after_d_inside_the_grace(self):
        async def script(m, sid, iid):
            from datetime import timedelta
            await m.wearer_reply(iid, "ok", _utcnow())
            await asyncio.sleep(0.9)                                   # past D, inside D + grace
            await m.wearer_reply(iid, "help", _utcnow() - timedelta(seconds=0.5))
            await asyncio.sleep(0.2)
        record, replayed = self.real_vs_replay(script, ack=5, grace=1.5)
        self.assertEqual(record["final"]["alert_reason"], "wearer_help")
        self.assert_same(record, replayed)


if __name__ == "__main__":
    unittest.main()
