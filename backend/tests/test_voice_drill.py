"""M7 voice drills: an incident opened by hand in a drill session, measured per person.

The drill is only a way to start the real wearer-window policy without a fall, so these
tests run it through the real manager with a scripted transcriber."""

import asyncio
import io
import math
import struct
import tempfile
import time
import unittest
import wave

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import schemas  # noqa: F401
from app.services.drill_report import summarize
from app.services.spatial_runs import DrillRefused, SpatialRunManager, session_snapshot


def wav(seconds):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 6))) for i in range(int(seconds * 16000))))
    return buf.getvalue()


def trial(person, expected, state, alert_reason=None, heard=()):
    return {"person": person, "expected": expected, "state": state, "alert_reason": alert_reason, "heard": list(heard)}


def full_set(person="ann", ok_cancelled=10, help_raised=10, false_cancel_in=None):
    trials = [trial(person, "ok", "cancelled_by_wearer" if i < ok_cancelled else "alerted",
                    None if i < ok_cancelled else "timeout") for i in range(10)]
    trials += [trial(person, "help", "alerted", "wearer_help" if i < help_raised else "timeout") for i in range(10)]
    trials += [trial(person, "ambiguous", "alerted", "timeout") for _ in range(10)]
    trials += [trial(person, "silence" if i < 5 else "loudspeaker", "alerted", "timeout") for i in range(10)]
    if false_cancel_in is not None:
        trials[false_cancel_in] = trial(person, trials[false_cancel_in]["expected"], "cancelled_by_wearer", None, ["I'm fine."])
    return trials


def test_a_full_clean_set_passes_and_groups_stay_separate():
    p = summarize(full_set(ok_cancelled=9, help_raised=9))["people"]["ann"]
    assert p["status"] == "Pass"
    assert p["groups"]["ok"] == {"n": 10, "correct": 9, "false_help": 0, "cancelled": 9}
    assert p["groups"]["silence_or_loudspeaker"]["n"] == 10
    assert p["false_cancel_count"] == 0


def test_one_false_cancel_in_a_negative_group_fails_the_person_whatever_else_is_good():
    for index in (20, 35):   # an ambiguous trial, a loudspeaker trial
        p = summarize(full_set(false_cancel_in=index))["people"]["ann"]
        assert p["status"] == "Fail" and p["false_cancel_count"] == 1
        assert p["false_cancels"][0]["heard"] == ["I'm fine."]


def test_below_target_or_too_few_trials():
    assert summarize(full_set(ok_cancelled=8))["people"]["ann"]["status"] == "Fail"
    assert summarize(full_set()[:25])["people"]["ann"]["status"] == "Incomplete"
    both = summarize(full_set("ann") + full_set("bob", help_raised=5))["people"]
    assert (both["ann"]["status"], both["bob"]["status"]) == ("Pass", "Fail")


class DrillSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/d.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_drills_run_the_real_policy_and_are_reported_per_person(self):
        heard = []

        async def go():
            m = SpatialRunManager(session_factory=self.db, transcribe=lambda pcm: heard.pop(0), caregiver_ack_seconds=0.3,
                                  late_help_grace_seconds=0.3)
            # Telegram is the default channel and none is configured: a drill must not need it.
            sid = await m.start_live(1, "drill", "drill", "telegram", wearer_cancel_seconds=3)
            assert m._session_notifiers[sid].channel == "simulated"
            m._phone_clocks[sid] = {"offset_ms": 0.0, "uncertainty_ms": 1.0}   # phone clock == backend monotonic
            token = m._channels[sid].token
            now_ms = lambda: time.monotonic() * 1000

            async def answer(text, expect):
                opened = await m.open_drill(sid, expect, "tester")
                await asyncio.sleep(1.2)                       # the grant opens with the incident
                heard.append(text)
                end = now_ms()
                reply = await m.wearer_audio(sid, token, opened["incident_id"], wav(1.0), end - 1000, end, "audio/wav")
                assert reply["capture_time"] == "confirmed"
                return opened

            first = await answer("I'm fine.", "ok")
            with self.assertRaises(DrillRefused) as busy:      # still inside the first incident's window
                await m.open_drill(sid, "help", "tester")
            assert busy.exception.reason == "busy"
            await asyncio.sleep(2.2)
            await answer("I need help", "help")
            await asyncio.sleep(3.3)
            await answer("Yes.", "ambiguous")
            await asyncio.sleep(3.5)
            await m.stop(sid)
            await m.wait(sid)
            return sid, first

        sid, first = asyncio.run(go())
        from app.services.drill_report import drill_report
        report = drill_report(sid, self.db)
        states = [(t["expected"], t["state"], t["alert_reason"]) for t in report["trials"]]
        self.assertEqual(states, [("ok", "cancelled_by_wearer", None), ("help", "alerted", "wearer_help"),
                                  ("ambiguous", "alerted", "timeout")])
        person = report["people"]["tester"]
        self.assertEqual((person["groups"]["ok"]["correct"], person["groups"]["help"]["correct"],
                          person["groups"]["ambiguous"]["correct"], person["false_cancel_count"]), (1, 1, 1, 0))
        self.assertEqual(person["status"], "Incomplete")
        snap = session_snapshot(sid, self.db)
        self.assertEqual({i["decision_source"] for i in snap["incidents"]}, {"voice_drill"})
        self.assertEqual(snap["live"]["config"]["wearer_cancel_seconds"], 3)

    def test_only_a_drill_session_can_open_a_drill(self):
        async def go():
            m = SpatialRunManager(session_factory=self.db)
            sid = await m.start_live(1, "fake-esp32", "fake_esp32", "simulated")
            try:
                await m.open_drill(sid, "ok", "tester")
            except DrillRefused as refused:
                return refused.reason
            finally:
                await m.stop(sid)
        self.assertEqual(asyncio.run(go()), "not_a_drill_session")
