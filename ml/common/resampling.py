"""Exact anti-aliased rational timebase conversion for legacy public IMU.

The legacy vestibular preprocessor decimated 128 Hz data with an integer stride
of ``128 // 50 == 2``, which produces 64 Hz and makes a nominal 100-sample
window span 1.5625 s instead of 2 s. This module replaces only that defect with
deterministic anti-aliased rational polyphase resampling: reduce the rate ratio
by its GCD, then apply ``scipy.signal.resample_poly`` with a fixed Kaiser window
and line padding. 128 Hz to 50 Hz uses exactly ``up=25`` and ``down=64``.

Output sample count is ``ceil(n * up / down)``. Timestamps sit on an exact
integer-nanosecond grid at the target rate and never reach the end of the source
span. Input dtype (float32 or float64) is preserved; non-finite, boolean,
object, and one-dimensional inputs are rejected.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

import numpy as np
from scipy.signal import resample_poly

RESAMPLING_SCHEMA_VERSION = 1

_KAISER_WINDOW = ("kaiser", 5.0)


class ResamplingError(ValueError):
    """Raised with one stable machine-readable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.code


@dataclass(frozen=True)
class RationalRate:
    source_hz: int
    target_hz: int
    up: int
    down: int


@dataclass(frozen=True)
class ResampledSeries:
    values: np.ndarray
    timestamp_ns: Tuple[int, ...]
    rate: RationalRate
    source_sample_count: int


def _check_positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ResamplingError(code)
    number = int(value)
    if number <= 0:
        raise ResamplingError(code)
    return number


def _validate_input(values: np.ndarray) -> np.ndarray:
    if not isinstance(values, np.ndarray):
        raise ResamplingError("resample_input_not_ndarray")
    if values.dtype == np.bool_ or values.dtype.kind not in ("f",):
        raise ResamplingError("resample_input_dtype_invalid")
    if values.ndim != 2:
        raise ResamplingError("resample_input_rank_invalid")
    if values.shape[0] <= 0 or values.shape[1] <= 0:
        raise ResamplingError("resample_input_empty")
    if not np.all(np.isfinite(values)):
        raise ResamplingError("resample_input_not_finite")
    return values


def _rational_rate(source_hz: int, target_hz: int) -> RationalRate:
    divisor = math.gcd(source_hz, target_hz)
    return RationalRate(
        source_hz=source_hz,
        target_hz=target_hz,
        up=target_hz // divisor,
        down=source_hz // divisor,
    )


def _target_period_ns(target_hz: int) -> int:
    if 1_000_000_000 % target_hz != 0:
        raise ResamplingError("resample_target_rate_not_integer_ns")
    return 1_000_000_000 // target_hz


def resample_polyphase(
    values: np.ndarray,
    *,
    source_hz: int,
    target_hz: int,
    start_timestamp_ns: int,
) -> ResampledSeries:
    """Apply deterministic anti-aliased rational polyphase resampling."""

    source_hz = _check_positive_int(source_hz, "resample_source_rate_invalid")
    target_hz = _check_positive_int(target_hz, "resample_target_rate_invalid")
    if isinstance(start_timestamp_ns, bool) or not isinstance(start_timestamp_ns, int):
        raise ResamplingError("resample_start_timestamp_invalid")
    if start_timestamp_ns < 0:
        raise ResamplingError("resample_start_timestamp_invalid")

    array = _validate_input(values)
    in_dtype = array.dtype
    n = int(array.shape[0])

    rate = _rational_rate(source_hz, target_hz)
    period_ns = _target_period_ns(target_hz)

    expected_count = math.ceil(n * rate.up / rate.down)
    work = np.ascontiguousarray(array, dtype=np.float64)
    resampled = resample_poly(
        work, up=rate.up, down=rate.down, axis=0, window=_KAISER_WINDOW, padtype="line"
    )
    if resampled.shape[0] != expected_count:  # pragma: no cover - guarded by scipy contract
        raise ResamplingError("resample_output_count_unexpected")

    resampled = np.ascontiguousarray(resampled, dtype=in_dtype)
    if not np.all(np.isfinite(resampled)):
        raise ResamplingError("resample_output_not_finite")

    timestamps = tuple(
        int(start_timestamp_ns + index * period_ns) for index in range(expected_count)
    )
    source_span_ns = start_timestamp_ns + math.ceil(n * 1_000_000_000 / source_hz)
    if timestamps and timestamps[-1] >= source_span_ns:
        raise ResamplingError("resample_timestamp_exceeds_source_span")

    return ResampledSeries(
        values=resampled,
        timestamp_ns=timestamps,
        rate=rate,
        source_sample_count=n,
    )


def resample_128_hz_to_50_hz(
    values: np.ndarray,
    *,
    start_timestamp_ns: int,
) -> ResampledSeries:
    """Resample 128 Hz IMU to 50 Hz using exactly ``up=25`` and ``down=64``."""

    series = resample_polyphase(
        values, source_hz=128, target_hz=50, start_timestamp_ns=start_timestamp_ns
    )
    # Raised, not asserted: `python -O` strips `assert`, and this is the one
    # statement binding this function's name to the 25/64 factor it promises.
    if series.rate.up != 25 or series.rate.down != 64:
        raise ResamplingError("resampling_rate_contract_violated")
    return series


__all__ = [
    "RESAMPLING_SCHEMA_VERSION",
    "RationalRate",
    "ResampledSeries",
    "ResamplingError",
    "resample_polyphase",
    "resample_128_hz_to_50_hz",
]
