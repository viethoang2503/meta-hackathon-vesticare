"""M7 echo trials: does the phone's own voice cancel an alert or raise HELP? Scoring and the real flow."""

import asyncio
import io
import json
import math
import struct
import tempfile
import time
import unittest
import wave

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import schemas  # noqa: F401
from app.services import spatial_runs as runs_module
from app.services.drill_report import _echo_trial, drill_report, echo_summary, summarize
from app.services.spatial_runs import DrillRefused, SpatialRunManager


def wav(seconds):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 6))) for i in range(int(seconds * 16000))))
    return buf.getvalue()


def echo(volume, plays, result="clean", blamed=None, settled=True, room="kitchen", kind="echo_prompts"):
    return {"echo": True, "volume": volume, "room": room, "expected": kind, "settled": settled, "result": result,
            "plays": plays, "prompt_blamed": blamed, "heard": []}


PROMPTS = ["ask_ok", "ask_ok_10", "ask_ok_20"]


def full_table(extra=()):
    trials = []
    for volume in ("low", "mid", "high"):
        trials += [echo(volume, PROMPTS) for _ in range(3)]
        trials += [echo(volume, ["cancel_confirmed"], kind="echo_cancel") for _ in range(3)]
    return trials + list(extra)


def test_a_full_clean_table_passes_every_cell():
    summary = echo_summary(full_table())
    assert summary["status"] == "Pass"
    assert all(c["status"] == "Pass" and c["plays"] == 3 for v in summary["cells"].values() for c in v.values())
    assert summary["guard_ms"] == 500 and summary["rooms"]["low"] == ["kitchen"]


def test_one_false_cancel_fails_only_the_prompt_and_volume_that_caused_it():
    trials = full_table([echo("mid", ["ask_ok"], result="false_cancel", blamed="ask_ok")])
    summary = echo_summary(trials)
    assert summary["status"] == "Fail"
    assert summary["cells"]["mid"]["ask_ok"]["status"] == "Fail" and summary["cells"]["mid"]["ask_ok"]["false_cancel"] == 1
    assert summary["cells"]["mid"]["ask_ok_10"]["status"] == "Pass" and summary["cells"]["low"]["ask_ok"]["status"] == "Pass"


def test_fewer_than_three_plays_is_incomplete_and_a_running_trial_is_not_counted():
    trials = full_table()[:-1] + [echo("high", ["cancel_confirmed"], kind="echo_cancel", settled=False)]
    summary = echo_summary(trials)
    assert summary["cells"]["high"]["cancel_confirmed"] == {"plays": 2, "false_cancel": 0, "false_help": 0, "status": "Incomplete"}
    assert (summary["status"], summary["pending_trials"]) == ("Incomplete", 1)


def test_a_false_event_that_cannot_be_traced_to_a_prompt_still_fails_the_table():
    summary = echo_summary(full_table([echo("low", [], result="false_help", blamed=None)]))
    assert (summary["status"], summary["unattributed_false_events"]) == ("Fail", 1)


def test_echo_trials_are_not_mixed_into_a_persons_voice_table():
    assert summarize(full_table()) ["people"] == {}


def test_a_question_that_played_before_the_laptops_ok_does_not_count_as_an_echo_measurement():
    class Row:
        id, timestamp, alert_reason = 1, None, None

    class Action:
        def __init__(self, at, template):
            self.kind, self.created_at, self.detail = "spoken", at, json.dumps({"template_id": template})
    from datetime import datetime
    actions = [Action(datetime(2026, 1, 1, 0, 0, 1), "ask_ok"), Action(datetime(2026, 1, 1, 0, 0, 3), "cancel_confirmed")]
    trial = _echo_trial(Row, {"person": "p", "expected": "echo_cancel", "volume": "low", "room": "r"}, actions,
                        [{"cancelled_alert": True}], True)
    assert (trial["plays"], trial["result"]) == (["cancel_confirmed"], "clean")


class EchoTrialsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/e.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        self.saved_min = runs_module.ECHO_MIN_WINDOW_S

    def tearDown(self):
        runs_module.ECHO_MIN_WINDOW_S = self.saved_min
        self.tmp.cleanup()

    def test_refusals(self):
        async def go():
            m = SpatialRunManager(session_factory=self.db)
            sid = await m.start_live(1, "drill", "drill", "simulated", wearer_cancel_seconds=3)
            reasons = {}
            for name, args in {"no_volume": ("echo_cancel", "p", None, "kitchen"), "no_room": ("echo_cancel", "p", "low", " "),
                               "short": ("echo_prompts", "p", "low", "kitchen")}.items():
                with self.assertRaises(DrillRefused) as raised:
                    await m.open_drill(sid, *args)
                reasons[name] = raised.exception.reason
            await m.stop(sid)
            await m.wait(sid)
            return reasons
        self.assertEqual(asyncio.run(go()), {"no_volume": "volume_required", "no_room": "room_required",
                                              "short": "window_too_short"})

    def test_the_real_flow_records_prompts_played_and_blames_the_right_prompt(self):
        runs_module.ECHO_MIN_WINDOW_S = 3
        heard = []

        async def go():
            m = SpatialRunManager(session_factory=self.db, transcribe=lambda pcm: heard.pop(0), caregiver_ack_seconds=5,
                                  late_help_grace_seconds=0.3)
            sid = await m.start_live(1, "drill", "drill", "simulated", wearer_cancel_seconds=3)
            m._phone_clocks[sid] = {"offset_ms": 0.0, "uncertainty_ms": 1.0}
            token, now_ms, seen = m._channels[sid].token, (lambda: time.monotonic() * 1000), [0]

            def play_all():
                """The phone plays every queued line and reports it."""
                for line in m.wearer_poll(sid, token, seen[0])["utterances"]:
                    seen[0] = line["id"]
                    m.wearer_played(sid, token, line["id"])

            async def echo_the_prompt(text):
                await asyncio.sleep(1.2)                              # past the played report and its guard
                heard.append(text)
                end = now_ms()
                await m.wearer_audio(sid, token, opened["incident_id"], wav(0.4), end - 400, end, "audio/wav")

            for kind, volume, text in (("echo_prompts", "low", None), ("echo_prompts", "mid", "I'm fine."),
                                       ("echo_prompts", "high", "I need help"), ("echo_cancel", "low", None)):
                opened = await m.open_drill(sid, kind, "tester", volume, "kitchen")
                await asyncio.sleep(0.3)
                if kind == "echo_cancel":
                    await m.wearer_ok(opened["incident_id"])          # the laptop sends the OK
                    await asyncio.sleep(0.3)
                play_all()
                if text:
                    await echo_the_prompt(text)
                await asyncio.sleep(4.0 if kind == "echo_prompts" else 4.2)
            await m.stop(sid)
            await m.wait(sid)
            return sid
        sid = asyncio.run(go())
        trials = drill_report(sid, self.db)["trials"]
        self.assertEqual([(t["volume"], t["result"], t["plays"], t["prompt_blamed"]) for t in trials],
                         [("low", "clean", ["ask_ok"], None), ("mid", "false_cancel", ["ask_ok"], "ask_ok"),
                          ("high", "false_help", ["ask_ok"], "ask_ok"), ("low", "clean", ["cancel_confirmed"], None)])
        table = drill_report(sid, self.db)["echo"]
        self.assertEqual(table["cells"]["mid"]["ask_ok"]["false_cancel"], 1)
        self.assertEqual(table["cells"]["high"]["ask_ok"]["false_help"], 1)
        self.assertEqual(table["status"], "Fail")


if __name__ == "__main__":
    unittest.main()
