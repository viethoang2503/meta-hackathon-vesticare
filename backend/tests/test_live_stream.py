import numpy as np
import pytest

from app.services.live_stream import BatchRejected, DeviceSample, LiveStream

V = np.zeros(7, dtype=np.float32)


def samples(first_seq, n, first_ms, step_ms=20):
    return [DeviceSample(first_seq + i, first_ms + i * step_ms, V) for i in range(n)]


def test_continuous_stream_warms_up_after_100_samples_then_is_ready():
    s = LiveStream("boot-a")
    steps = s.accept("boot-a", samples(0, 99, 5000))
    assert [st.source_ms for st in steps[:3]] == [0, 20, 40]
    assert all(st.reset is None for st in steps)
    assert s.readiness == "warming_up"
    s.accept("boot-a", samples(99, 1, 5000 + 99 * 20))
    assert s.readiness == "ready"
    assert s.stats()["first_warmup_ms"] == 1980


def test_a_resent_batch_is_counted_as_duplicates_and_not_fed_twice():
    s = LiveStream("boot-a")
    s.accept("boot-a", samples(0, 10, 0))
    again = s.accept("boot-a", samples(5, 10, 100))
    assert [st.source_ms for st in again] == [200, 220, 240, 260, 280]
    assert (s.duplicates, s.samples_accepted) == (5, 15)


def test_late_samples_that_were_never_seen_are_rejected_not_reordered():
    s = LiveStream("boot-a")
    s.accept("boot-a", samples(0, 10, 0))
    s.accept("boot-a", samples(20, 10, 400))
    with pytest.raises(BatchRejected) as err:
        s.accept("boot-a", samples(10, 10, 200))
    assert err.value.reason == "arrived_after_newer_samples"
    with pytest.raises(BatchRejected):
        s.accept("boot-a", list(reversed(samples(30, 3, 600))))
    assert (s.rejected_batches, s.samples_accepted) == (2, 20)


def test_another_boot_is_rejected():
    s = LiveStream("boot-a")
    with pytest.raises(BatchRejected) as err:
        s.accept("boot-b", samples(0, 5, 0))
    assert err.value.reason == "boot_changed"


@pytest.mark.parametrize("second, reason", [
    (samples(150, 10, 3000), "sequence_gap"),          # seq jumps, device time continuous
    (samples(100, 10, 2041), "time_gap"),              # seq continuous, 41 ms between samples
    (samples(100, 10, 1000), "clock_back"),            # device clock runs backwards
])
def test_a_gap_resets_the_window_and_counts_against_coverage(second, reason):
    s = LiveStream("boot-a")
    s.accept("boot-a", samples(0, 100, 0))
    assert s.readiness == "ready"
    steps = s.accept("boot-a", second)
    assert steps[0].reset == reason and all(st.reset is None for st in steps[1:])
    assert s.readiness == "detection_unavailable"
    assert steps[0].source_ms > 1980
    assert s.resets[0]["reason"] == reason


def test_40_ms_between_samples_is_still_continuous():
    s = LiveStream("boot-a")
    steps = s.accept("boot-a", [DeviceSample(0, 0, V), DeviceSample(1, 40, V)])
    assert steps[1].reset is None


def test_coverage_follows_the_spec_formula():
    s = LiveStream("boot-a")
    s.accept("boot-a", samples(0, 500, 0))          # 0..9980 ms, ready from 1980
    assert s.coverage()["coverage"] == 1.0          # first nominal 2 s excluded; ready from 1980
    s.accept("boot-a", samples(600, 500, 12000))    # seq gap at 12000: 2 s gap + 1980 ms refill
    c = s.coverage()
    assert c["denominator_ms"] == 21980 - 2000
    assert c["unavailable_ms"] == (13980 - 9980)
    assert c["coverage"] == round((19980 - 4000) / 19980, 4)
