from datetime import datetime

import pytest

from app.services.live_frames import (ADVISORY_COOLDOWN_S, FrameStore, Frame, StairsGate, scene_status)

T0 = datetime(2026, 11, 2, 10, 0, 0)


def frame(seq, source_ms=None, unc=20.0, mono=100.0):
    return Frame(seq, T0, mono, source_ms, unc if source_ms is not None else None, b"\xff\xd8")


def test_match_picks_nearest_frame_within_tolerance():
    store = FrameStore()
    for seq, ms in enumerate([1000, 2000, 3000], start=1):
        store.add(frame(seq, ms))
    m = store.match(2300)
    assert (m.status, m.frame.seq, m.delta_ms, m.reason) == ("Available", 2, -300, None)


def test_match_counts_clock_uncertainty_against_the_tolerance():
    store = FrameStore()
    store.add(frame(1, 1000, unc=400))
    m = store.match(1500)  # 500 ms away + 400 ms clock = 900 > 750
    assert m.status == "Unavailable" and m.frame.seq == 1 and "500 ms away" in m.reason


def test_match_without_clock_is_unsynchronized_but_keeps_the_latest_frame_to_view():
    store = FrameStore()
    store.add(frame(1)); store.add(frame(2))
    m = store.match(1000)
    assert (m.status, m.frame.seq) == ("Unsynchronized", 2)


def test_match_with_no_frames_is_unavailable():
    assert FrameStore().match(0).status == "Unavailable"


def test_store_keeps_only_the_newest_thirty():
    store = FrameStore()
    for i in range(1, 41):
        store.add(frame(i, i * 1000))
    assert (len(store.frames), store.received, store.get("live-10"), store.get("live-11").seq) == (30, 40, None, 11)


def test_gate_runs_one_request_and_keeps_only_the_newest_waiting_frame():
    gate = StairsGate()
    gate.offer(frame(1))
    assert gate.take(0).seq == 1
    gate.offer(frame(2)); gate.offer(frame(3))
    assert gate.take(10) is None          # still busy
    gate.done()
    assert gate.take(10).seq == 3         # frame 2 was never sent


def test_gate_starts_requests_at_least_three_seconds_apart():
    gate = StairsGate()
    gate.offer(frame(1)); gate.take(0); gate.done()
    gate.offer(frame(2))
    assert gate.take(2.9) is None
    assert gate.take(3.0).seq == 2


@pytest.mark.parametrize("age, incident_open, expected", [
    (1, False, True), (5, False, True), (5.1, False, False), (1, True, False)])
def test_announce_needs_a_fresh_answer_and_no_open_incident(age, incident_open, expected):
    gate = StairsGate()
    assert gate.may_announce(frame(1, mono=100), 100 + age, incident_open) is expected


def test_announce_waits_out_the_cooldown_and_a_refusal_does_not_start_it():
    gate = StairsGate()
    f = frame(1, mono=100)
    assert not gate.may_announce(f, 101, incident_open=True)
    assert gate.may_announce(f, 101, incident_open=False)
    g = frame(2, mono=120)
    assert not gate.may_announce(g, 121, incident_open=False)
    h = frame(3, mono=101 + ADVISORY_COOLDOWN_S)
    assert gate.may_announce(h, 102 + ADVISORY_COOLDOWN_S, incident_open=False)


@pytest.mark.parametrize("status, word", [("Complete", "Stairs visible"), ("NoMatch", "No stairs identified"),
                                          ("Running", "Pending"), ("Failed", "Failed"), ("TimedOut", "Timed out"),
                                          ("Unavailable", "Unavailable"), (None, "Unavailable")])
def test_scene_status_uses_the_spec_words(status, word):
    assert scene_status(status) == word
