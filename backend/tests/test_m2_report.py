"""M2 report: tags and timings read from sessions run through the real run manager."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import schemas  # noqa: F401
from app.services.m2_report import BRANCHES, SCHEDULE_TARGET_MS, m2_report
import numpy as np

from app.services.live_stream import DeviceSample
from app.services.spatial_runs import SpatialRunManager, _utcnow, load_scenario, session_snapshot
from test_incident_lifecycle import RecordingNotifier


class TelegramLike(RecordingNotifier):
    channel = "telegram"


class M2ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/m.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        self.sessions = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_s2(self, script, notifier=None, stop=True, cancel=0.6, ack=0.4, grace=0.6):
        """A live session fed S2 through the same ingest path as the device, then `script`."""
        table = load_scenario("S2")

        async def go():
            m = SpatialRunManager(session_factory=self.db, notifier=notifier or RecordingNotifier(), live_wearer_cancel_seconds=cancel,
                                  caregiver_ack_seconds=ack, late_help_grace_seconds=grace, live_data_dir=Path(self.tmp.name))
            sid = await m.start_live(1, "fake-esp32", "fake_esp32", "simulated")
            samples = [DeviceSample(i + 1, 12_345 + int(ms), values.astype(np.float32))
                       for i, (ms, values) in enumerate(zip(table.source_ms, table.samples))]
            for start in range(0, len(samples), 10):
                await asyncio.to_thread(m.ingest_live, sid, "b1", samples[start:start + 10])
                if session_snapshot(sid, self.db)["incidents"]:
                    break
            iid = session_snapshot(sid, self.db)["incidents"][0]["id"]
            await script(m, sid, iid)
            if stop:
                await m.stop(sid)
            await m.wait(sid)
            await asyncio.sleep(0.1)
            return sid
        sid = asyncio.run(go())
        self.sessions.append(sid)
        return sid

    def test_branches_are_tagged_and_counted_as_real_or_stand_in(self):
        async def silence(m, sid, iid):
            await asyncio.sleep(1.8)

        async def ok_then_help(m, sid, iid):
            await m.wearer_reply(iid, "ok", _utcnow())
            await asyncio.sleep(0.2)
            await m.wearer_reply(iid, "help", _utcnow())
            await asyncio.sleep(0.3)

        async def late_ok_then_ack(m, sid, iid):
            await asyncio.sleep(0.9)
            await m.wearer_reply(iid, "ok", _utcnow())
            await m.wearer_reply(iid, "ok", _utcnow())
            await asyncio.sleep(0.1)
            await m.acknowledge(iid, "telegram:contact_1")
            await asyncio.sleep(0.4)

        async def stop_at_once(m, sid, iid):
            await asyncio.sleep(0.1)

        self.run_s2(silence, TelegramLike())
        self.run_s2(ok_then_help, cancel=3, ack=5)
        self.run_s2(late_ok_then_ack, ack=2)
        self.run_s2(stop_at_once, cancel=3, ack=5)
        self.run_s2(silence, TelegramLike(outcomes={"contact_1": "failed"}), ack=5)

        report = m2_report(self.sessions, self.db)
        status = {k: b["status"] for k, b in report["branches"].items()}
        real, stand_in, absent = "Seen with real Telegram", "Seen with a stand-in", "Not seen"
        self.assertEqual(status["timeout_alert"], real)
        self.assertEqual(status["escalation_no_ack"], real)
        self.assertEqual(status["send_failed"], real)
        self.assertEqual({status[k] for k in ("help_alert", "ok_cancel", "ok_then_help_before_deadline")}, {stand_in})
        self.assertEqual({status[k] for k in ("ok_late", "ok_repeated", "ack", "stop_during_window")}, {stand_in})
        self.assertEqual({status[k] for k in ("stt_dropped", "stt_failed", "interrupted", "send_unknown", "help_in_grace")}, {absent})
        self.assertFalse(report["complete"])
        self.assertEqual(set(report["branches"]), set(BRANCHES))

    def test_silence_and_help_are_scheduled_within_two_seconds_and_the_steps_are_kept(self):
        async def help_now(m, sid, iid):
            await m.wearer_reply(iid, "help", _utcnow(), {"transcript": "I need help", "queue_ms": 5, "stt_ms": 180,
                                                         "received_at": _utcnow().isoformat()})
            await asyncio.sleep(0.2)
        sid = self.run_s2(help_now, cancel=3, ack=5)
        report = m2_report([sid], self.db)
        answer = report["trials"][0]["answers"][0]
        self.assertEqual((answer["response"], answer["transcript"], answer["stt_ms"], answer["queue_ms"]), ("help", "I need help", 180, 5))
        self.assertLessEqual(answer["classified_to_scheduled_ms"], SCHEDULE_TARGET_MS)
        self.assertTrue(report["schedule_timing"]["within_target"])

        silent = m2_report([self.run_s2(self.silence_script(), TelegramLike())], self.db)
        self.assertEqual(silent["schedule_timing"]["samples"], 1)
        self.assertTrue(silent["schedule_timing"]["within_target"])
        self.assertEqual(silent["telegram_send_ms"]["n"], 2)   # the first alert and the escalation both went out

    def silence_script(self):
        async def script(m, sid, iid):
            await asyncio.sleep(1.8)
        return script

    def test_an_unknown_session_is_an_error_not_an_empty_table(self):
        with self.assertRaises(ValueError):
            m2_report(["missing"], self.db)


if __name__ == "__main__":
    unittest.main()
