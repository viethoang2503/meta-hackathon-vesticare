"""Stop and restart on a live session (docs/spatial/MVP2.md, M2): an opened incident finishes its own
response window after Stop, and a restart sends nothing by itself."""

import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import schemas  # noqa: F401
from app.services.live_stream import DeviceSample
from app.services.spatial_runs import SpatialRunManager, _utcnow, load_scenario, session_snapshot
from test_incident_lifecycle import RecordingNotifier


class LiveStopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/s.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def tearDown(self):
        self.tmp.cleanup()

    def manager(self, notifier, cancel=0.8, ack=5, grace=0.6):
        return SpatialRunManager(session_factory=self.db, notifier=notifier, live_wearer_cancel_seconds=cancel,
                                 caregiver_ack_seconds=ack, late_help_grace_seconds=grace, live_data_dir=Path(self.tmp.name))

    async def open_incident(self, m):
        table = load_scenario("S2")
        sid = await m.start_live(1, "fake-esp32", "fake_esp32", "simulated")
        samples = [DeviceSample(i + 1, 12_345 + int(ms), v.astype(np.float32)) for i, (ms, v) in enumerate(zip(table.source_ms, table.samples))]
        for start in range(0, len(samples), 10):
            await asyncio.to_thread(m.ingest_live, sid, "b1", samples[start:start + 10])
            if session_snapshot(sid, self.db)["incidents"]:
                break
        return sid, session_snapshot(sid, self.db)["incidents"][0]["id"]

    def test_stop_while_the_wearer_is_still_being_asked_does_not_cancel_the_alert(self):
        notifier = RecordingNotifier()

        async def go():
            m = self.manager(notifier)
            sid, iid = await self.open_incident(m)
            await m.stop(sid)
            during = session_snapshot(sid, self.db)["incidents"][0]["state"]
            await asyncio.sleep(1.6)                   # past the 0.8 s window
            return sid, during
        sid, during = asyncio.run(go())
        snap = session_snapshot(sid, self.db)
        self.assertEqual((snap["status"], during, snap["incidents"][0]["state"]), ("Stopped", "awaiting_wearer", "alerted"))
        self.assertEqual([(c, a) for c, _, a in notifier.sent][0][0], "contact_1")

    def test_stop_after_a_cancel_still_hears_help_until_the_window_and_grace_close(self):
        notifier = RecordingNotifier()

        async def go():
            m = self.manager(notifier, cancel=3)
            sid, iid = await self.open_incident(m)
            await m.wearer_reply(iid, "ok", _utcnow())
            await m.stop(sid)
            result = await m.wearer_reply(iid, "help", _utcnow() - timedelta(seconds=0.1))
            await asyncio.sleep(0.3)
            return sid, result
        sid, result = asyncio.run(go())
        self.assertEqual((result["applied"], result["incident_state"]), (True, "alerted"))
        self.assertEqual(session_snapshot(sid, self.db)["status"], "Stopped")
        self.assertEqual(len(notifier.sent), 1)

    def test_a_restart_marks_the_session_interrupted_and_sends_nothing_by_itself(self):
        first = RecordingNotifier()

        async def before_the_crash():
            m = self.manager(first, cancel=0.8)
            return await self.open_incident(m)        # the loop ends here: its tasks die like a killed process
        sid, iid = asyncio.run(before_the_crash())

        second = RecordingNotifier()

        async def after_the_restart():
            m = self.manager(second, cancel=0.8)
            marked = m.mark_interrupted_runs()
            await asyncio.sleep(1.5)                  # longer than the window the old process was counting down
            return marked
        marked = asyncio.run(after_the_restart())
        snap = session_snapshot(sid, self.db)
        self.assertEqual((marked, snap["status"], snap["end_reason"]), (1, "Interrupted", "session_interrupted"))
        self.assertEqual((first.sent, second.sent), ([], []))
        self.assertEqual(snap["incidents"][0]["state"], "awaiting_wearer")


if __name__ == "__main__":
    unittest.main()
