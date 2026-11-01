from app.services.clock_sync import best_mapping


def round_(t0, offset, out_ms, back_ms, server_ms=1.0):
    t1 = t0 + out_ms + offset
    t2 = t1 + server_ms
    return {"t0": t0, "t1": t1, "t2": t2, "t3": t2 - offset + back_ms}


def test_the_lowest_rtt_round_sets_the_offset():
    rounds = [round_(1000 + 100 * i, 50_000, out, back) for i, (out, back) in
              enumerate([(40, 60), (8, 12), (30, 5), (90, 90), (20, 25)])]
    m = best_mapping(rounds)
    assert m["rtt_ms"] == 20.0
    assert m["uncertainty_ms"] == 10.5   # half the rtt plus half a device millisecond
    assert m["offset_ms"] == 50_000 + (8 - 12) / 2   # asymmetry inside the uncertainty
    assert abs(m["offset_ms"] - 50_000) <= m["uncertainty_ms"]


def test_too_few_or_impossible_rounds_give_no_mapping():
    good = [round_(1000 * i, 7, 5, 5) for i in range(4)]
    assert best_mapping(good) is None
    broken = good + [{"t0": 10, "t1": 5, "t2": 4, "t3": 20}]
    assert best_mapping(broken) is None
    assert best_mapping(good + [round_(9000, 7, 5, 5)])["offset_ms"] == 7.0


def test_a_round_faster_than_the_device_clock_tick_still_counts():
    rounds = [{"t0": 100 + i, "t1": 5000.2 + i, "t2": 5000.5 + i, "t3": 100 + i} for i in range(5)]
    m = best_mapping(rounds)
    assert (m["rtt_ms"], m["uncertainty_ms"]) == (0.0, 0.5)
