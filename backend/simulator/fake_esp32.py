"""
fake_esp32.py

Simulator loi loi du lieu cam bien IMU gia lap ESP32.

Su dung:
    python fake_esp32.py --mode NORMAL --duration 10
    python fake_esp32.py --mode FALL   --patient-id 2 --seed 42
    python fake_esp32.py --mode SWAY   --rate 25 --duration 30
    python fake_esp32.py --mode INSTABILITY --duration 20
    python fake_esp32.py --mode VESTIBULAR_EPISODE --duration 30

Modes:
    NORMAL              - SVM ~1.0g, khong co su kien nga
    SWAY                - SVM ~1.5-2.0g, lao dao nhe (alias cu)
    INSTABILITY         - SVM dao dong 1.3-2.0g tan so thap (0.5-2 Hz),
                          gyro cao, mô phong mat tham bang tien dinh
    FALL                - SVM binh thuong -> dot bien > 2.5g -> tro lai binh thuong
    VESTIBULAR_EPISODE  - Chuoi day du: NORMAL -> INSTABILITY -> FALL

Tham so:
    --mode              NORMAL | SWAY | INSTABILITY | FALL | VESTIBULAR_EPISODE
    --patient-id        ID benh nhan (mac dinh: 1)
    --duration          So giay chay (mac dinh: 30)
    --rate              So mau / giay (mac dinh: 50, khop SAMPLING_RATE backend)
    --url               Backend URL (mac dinh: http://localhost:8000)
    --seed              Random seed de tai tao ket qua (mac dinh: None)
    --instability-at    Giay bat dau INSTABILITY (chi VESTIBULAR_EPISODE, mac dinh: 5)
    --fall-at           Giay bat dau FALL (mac dinh: 15)
    --fall-duration     So giay FALL keo dai (mac dinh: 1)
    --sway-freq         Tan so dao dong INSTABILITY Hz (mac dinh: 1.0)
    --quiet             Tat log tung mau, chi in tom tat cuoi
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

import httpx


# ---------------------------------------------------------------------------
# Signal constants — mirrors backend/app/config.py thresholds
# ---------------------------------------------------------------------------

SVM_NORMAL_CENTER      = 1.0   # g
SVM_SWAY_CENTER        = 1.8   # g
SVM_INSTABILITY_CENTER = 1.6   # g  — oscillates ±0.35g at vestibular sway freq
SVM_FALL_PEAK          = 3.5   # g  (well above SVM_FALL_THRESHOLD = 2.5)

NOISE_NORMAL      = 0.05
NOISE_SWAY        = 0.15
NOISE_INSTABILITY = 0.12
NOISE_FALL        = 0.10


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SensorSample:
    patient_id: int
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    svm: float
    timestamp: str

    def to_payload(self) -> dict:
        return {
            "patient_id": self.patient_id,
            "ax": round(self.ax, 4),
            "ay": round(self.ay, 4),
            "az": round(self.az, 4),
            "gx": round(self.gx, 4),
            "gy": round(self.gy, 4),
            "gz": round(self.gz, 4),
            "svm": round(self.svm, 4),
            "timestamp": self.timestamp,
        }


@dataclass
class SimulationResult:
    mode: str
    patient_id: int
    duration_s: float
    rate: int
    samples_sent: int = 0
    samples_failed: int = 0
    alerts_created: int = 0
    alert_ids: list[int] = field(default_factory=list)
    alert_latencies_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def summary(self) -> dict:
        avg_latency = (
            sum(self.alert_latencies_ms) / len(self.alert_latencies_ms)
            if self.alert_latencies_ms else None
        )
        return {
            "mode": self.mode,
            "patient_id": self.patient_id,
            "duration_s": round(self.duration_s, 2),
            "rate_hz": self.rate,
            "samples_sent": self.samples_sent,
            "samples_failed": self.samples_failed,
            "alerts_created": self.alerts_created,
            "alert_ids": self.alert_ids,
            "avg_alert_latency_ms": round(avg_latency, 2) if avg_latency is not None else None,
            "errors": self.errors[-10:],  # cap at last 10
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ---------------------------------------------------------------------------
# Signal generators
# ---------------------------------------------------------------------------

def _accel_from_svm(svm: float, rng: random.Random) -> tuple[float, float, float]:
    """
    Decompose target SVM into (ax, ay, az) using a random unit vector.
    az gets most of the energy (gravity-dominant axis), ax/ay get the rest.
    """
    # Gravity component on z axis, rest as noise
    az = svm * 0.85 + rng.uniform(-0.05, 0.05)
    remainder = max(0.0, svm ** 2 - az ** 2)
    ax = math.sqrt(remainder / 2) * rng.choice([-1, 1]) + rng.uniform(-0.02, 0.02)
    ay = math.sqrt(remainder / 2) * rng.choice([-1, 1]) + rng.uniform(-0.02, 0.02)
    return ax, ay, az


def _gyro_from_mode(mode: str, rng: random.Random, phase: float = 0.0) -> tuple[float, float, float]:
    if mode == "NORMAL":
        gx = rng.uniform(-5.0, 5.0)
        gy = rng.uniform(-5.0, 5.0)
        gz = rng.uniform(-2.0, 2.0)
    elif mode in ("SWAY", "INSTABILITY"):
        # Vestibular instability: rhythmic lateral sway dominant on gx/gy
        sway_amp = 45.0
        gx = sway_amp * math.sin(phase) + rng.gauss(0, 8.0)
        gy = sway_amp * math.cos(phase * 0.7) + rng.gauss(0, 8.0)
        gz = rng.uniform(-15.0, 15.0)
    else:  # FALL
        gx = rng.uniform(-80.0, 80.0)
        gy = rng.uniform(-80.0, 80.0)
        gz = rng.uniform(-25.0, 25.0)
    return gx, gy, gz


def normal_sample(patient_id: int, rng: random.Random) -> SensorSample:
    svm = SVM_NORMAL_CENTER + rng.gauss(0, NOISE_NORMAL)
    svm = max(0.8, svm)
    ax, ay, az = _accel_from_svm(svm, rng)
    gx, gy, gz = _gyro_from_mode("NORMAL", rng)
    return SensorSample(
        patient_id=patient_id,
        ax=ax, ay=ay, az=az,
        gx=gx, gy=gy, gz=gz,
        svm=svm,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def sway_sample(patient_id: int, rng: random.Random) -> SensorSample:
    svm = SVM_SWAY_CENTER + rng.gauss(0, NOISE_SWAY)
    svm = max(1.3, svm)
    ax, ay, az = _accel_from_svm(svm, rng)
    gx, gy, gz = _gyro_from_mode("SWAY", rng)
    return SensorSample(
        patient_id=patient_id,
        ax=ax, ay=ay, az=az,
        gx=gx, gy=gy, gz=gz,
        svm=svm,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def instability_sample(patient_id: int, rng: random.Random, phase: float = 0.0) -> SensorSample:
    """
    Vestibular instability sample: low-frequency oscillatory sway (0.5–2 Hz).
    SVM oscillates between ~1.2–2.0g (not a spike), gyro rhythmically elevated.
    Models the characteristic motion pattern of vestibular disorder patients.
    """
    sway_amplitude = 0.35
    svm = SVM_INSTABILITY_CENTER + sway_amplitude * math.sin(phase) + rng.gauss(0, NOISE_INSTABILITY)
    svm = max(1.2, min(2.2, svm))
    ax, ay, az = _accel_from_svm(svm, rng)
    gx, gy, gz = _gyro_from_mode("INSTABILITY", rng, phase)
    return SensorSample(
        patient_id=patient_id,
        ax=ax, ay=ay, az=az,
        gx=gx, gy=gy, gz=gz,
        svm=svm,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def fall_sample(patient_id: int, rng: random.Random) -> SensorSample:
    svm = SVM_FALL_PEAK + rng.gauss(0, NOISE_FALL)
    svm = max(2.6, svm)
    ax, ay, az = _accel_from_svm(svm, rng)
    gx, gy, gz = _gyro_from_mode("FALL", rng)
    return SensorSample(
        patient_id=patient_id,
        ax=ax, ay=ay, az=az,
        gx=gx, gy=gy, gz=gz,
        svm=svm,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Sample stream generator
# ---------------------------------------------------------------------------

def sample_stream(
    *,
    mode: str,
    patient_id: int,
    rate: int,
    duration_s: float,
    instability_at_s: float = 5.0,
    fall_at_s: float = 15.0,
    fall_duration_s: float = 1.0,
    sway_freq: float = 1.0,
    rng: random.Random,
) -> Iterator[tuple[SensorSample, bool]]:
    """
    Yield (sample, is_fall_moment) tuples for the full simulation duration.

    Modes:
      NORMAL              — constant normal walking signal
      SWAY                — constant elevated sway (legacy alias)
      INSTABILITY         — sustained vestibular oscillatory sway
      FALL                — normal → fall spike → normal
      VESTIBULAR_EPISODE  — normal → instability → fall → normal
    """
    interval = 1.0 / rate
    total_samples = int(duration_s * rate)
    instability_start_idx = int(instability_at_s * rate)
    fall_start_idx        = int(fall_at_s * rate)
    fall_end_idx          = int((fall_at_s + fall_duration_s) * rate)

    for i in range(total_samples):
        iter_start = time.monotonic()
        is_fall_moment = False
        t = i / rate  # elapsed time in seconds
        phase = 2 * math.pi * sway_freq * t

        if mode == "NORMAL":
            sample = normal_sample(patient_id, rng)

        elif mode == "SWAY":
            sample = sway_sample(patient_id, rng)

        elif mode == "INSTABILITY":
            sample = instability_sample(patient_id, rng, phase)

        elif mode == "FALL":
            if fall_start_idx <= i < fall_end_idx:
                sample = fall_sample(patient_id, rng)
                is_fall_moment = True
            else:
                sample = normal_sample(patient_id, rng)

        else:  # VESTIBULAR_EPISODE: NORMAL → INSTABILITY → FALL → NORMAL
            if i < instability_start_idx:
                sample = normal_sample(patient_id, rng)
            elif i < fall_start_idx:
                sample = instability_sample(patient_id, rng, phase)
            elif fall_start_idx <= i < fall_end_idx:
                sample = fall_sample(patient_id, rng)
                is_fall_moment = True
            else:
                sample = normal_sample(patient_id, rng)

        yield sample, is_fall_moment

        elapsed = time.monotonic() - iter_start
        sleep_time = max(0.0, interval - elapsed)
        time.sleep(sleep_time)


# ---------------------------------------------------------------------------
# HTTP sender
# ---------------------------------------------------------------------------

def send_sample(
    client: httpx.Client,
    url: str,
    sample: SensorSample,
) -> dict | None:
    """POST one sample. Returns parsed JSON body or None on failure."""
    try:
        response = client.post(
            f"{url}/api/sensor/data",
            json=sample.to_payload(),
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        return {"_error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
    except httpx.RequestError as exc:
        return {"_error": f"Request error: {exc}"}


# ---------------------------------------------------------------------------
# Main simulation runner
# ---------------------------------------------------------------------------

def run_simulation(
    *,
    mode: str,
    patient_id: int,
    duration_s: float,
    rate: int,
    url: str,
    seed: int | None,
    instability_at_s: float = 0.0,
    fall_at_s: float = 0.0,
    fall_duration_s: float = 1.0,
    sway_freq: float = 0.5,
    quiet: bool = False,
) -> SimulationResult:
    rng = random.Random(seed)
    result = SimulationResult(
        mode=mode,
        patient_id=patient_id,
        duration_s=duration_s,
        rate=rate,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    fall_injected_at: float | None = None

    print(f"[simulator] mode={mode} patient_id={patient_id} duration={duration_s}s "
          f"rate={rate}Hz seed={seed} url={url}")
    if mode == "FALL":
        print(f"[simulator] Fall will be injected at t={fall_at_s}s for {fall_duration_s}s")
    elif mode == "INSTABILITY":
        print(f"[simulator] Vestibular instability sway at {sway_freq:.1f} Hz")
    elif mode == "VESTIBULAR_EPISODE":
        print(f"[simulator] Episode: NORMAL(0–{instability_at_s}s) → "
              f"INSTABILITY({instability_at_s}–{fall_at_s}s) → "
              f"FALL({fall_at_s}–{fall_at_s+fall_duration_s}s) → NORMAL")
    print("[simulator] Starting stream — press Ctrl+C to stop early\n")

    with httpx.Client() as client:
        stream = sample_stream(
            mode=mode,
            patient_id=patient_id,
            rate=rate,
            duration_s=duration_s,
            instability_at_s=instability_at_s,
            fall_at_s=fall_at_s,
            fall_duration_s=fall_duration_s,
            sway_freq=sway_freq,
            rng=rng,
        )

        for sample, is_fall_moment in stream:
            send_ts = time.monotonic()
            body = send_sample(client, url, sample)

            if body is None or "_error" in (body or {}):
                result.samples_failed += 1
                err_msg = (body or {}).get("_error", "unknown error")
                result.errors.append(err_msg)
                if not quiet:
                    print(f"  [ERR] {err_msg}")
                continue

            result.samples_sent += 1

            if is_fall_moment and fall_injected_at is None:
                fall_injected_at = send_ts

            alert_created = body.get("alert_created", False)
            alert_id = body.get("alert_id")

            if alert_created and alert_id is not None:
                result.alerts_created += 1
                result.alert_ids.append(alert_id)
                if fall_injected_at is not None:
                    latency_ms = (time.monotonic() - fall_injected_at) * 1000
                    result.alert_latencies_ms.append(latency_ms)
                    if not quiet:
                        print(f"  [ALERT] id={alert_id} latency={latency_ms:.1f}ms "
                              f"fall_prob={body.get('fall_probability', '?'):.3f} "
                              f"peak_svm={body.get('peak_svm', '?'):.3f}")
                else:
                    if not quiet:
                        print(f"  [ALERT] id={alert_id} (no fall injection recorded)")
            elif not quiet:
                state = body.get("state", "?")
                buffered = body.get("samples_buffered", "?")
                suppressed = body.get("alert_suppressed", False)
                tag = "[SUPP]" if suppressed else f"[{state}]"
                print(f"  {tag} buffered={buffered} svm={sample.svm:.3f}")

    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vesti-Care IMU Simulator — streams fake sensor data to the backend",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["NORMAL", "SWAY", "INSTABILITY", "FALL", "VESTIBULAR_EPISODE"],
        default="NORMAL",
        help="Signal mode to simulate",
    )
    parser.add_argument("--patient-id", type=int, default=1, metavar="ID",
                        help="Patient ID to use in every payload")
    parser.add_argument("--duration", type=float, default=30.0, metavar="SECONDS",
                        help="How long to stream (seconds)")
    parser.add_argument("--rate", type=int, default=50, metavar="HZ",
                        help="Samples per second (should match backend SAMPLING_RATE)")
    parser.add_argument("--url", default="http://localhost:8000",
                        help="Backend base URL")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducible signal generation")
    parser.add_argument("--instability-at", type=float, default=5.0, metavar="SECONDS",
                        help="Time (s) to start INSTABILITY phase (VESTIBULAR_EPISODE only)")
    parser.add_argument("--fall-at", type=float, default=15.0, metavar="SECONDS",
                        help="Time (s) to inject fall event (FALL / VESTIBULAR_EPISODE)")
    parser.add_argument("--fall-duration", type=float, default=1.0, metavar="SECONDS",
                        help="Duration (s) of the fall spike")
    parser.add_argument("--sway-freq", type=float, default=1.0, metavar="HZ",
                        help="Oscillation frequency Hz for INSTABILITY/VESTIBULAR_EPISODE (0.5–2.0)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-sample logs; only print summary")
    parser.add_argument("--output", metavar="FILE", default=None,
                        help="Save JSON result summary to this file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        result = run_simulation(
            mode=args.mode,
            patient_id=args.patient_id,
            duration_s=args.duration,
            rate=args.rate,
            url=args.url,
            seed=args.seed,
            instability_at_s=args.instability_at,
            fall_at_s=args.fall_at,
            fall_duration_s=args.fall_duration,
            sway_freq=args.sway_freq,
            quiet=args.quiet,
        )
    except KeyboardInterrupt:
        print("\n[simulator] Stopped by user.")
        return 0

    summary = result.summary()

    print("\n" + "=" * 50)
    print("  SIMULATION COMPLETE")
    print("=" * 50)
    print(json.dumps(summary, indent=2))

    if args.output:
        out_path = args.output
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"\n[simulator] Result saved to {out_path}")

    # Exit code 1 on any failed sample (partial failure is still a failure)
    return 1 if result.samples_failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
