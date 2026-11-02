"""
s3_replay.py

M5a and M5b (docs/spatial/MVP2.md) from an S3 package. The detector reads the recorded IMU the way a live
stream does, so the same gaps reset it. The policy reads the recorded response events on a virtual
clock (policy_replay), so no speech model, agent, sleep or Telegram call is involved.

Pass means two replays of the package agree exactly. Agreement with what the live run recorded is
reported next to it and is not the pass condition: the journal holds samples at five decimals.
"""

from __future__ import annotations

import uuid

import numpy as np

from app.config import KERAS_MODEL_PATH, SCALER_PARAMS_PATH
from app.services.fall_detection import fall_detection_service
from app.services.live_stream import DeviceSample, LiveStream
from app.services.monitoring import StreamMonitor
from app.services.policy_replay import PolicyConfig, chain, simulate
from app.services.s3_package import JOURNAL_COLUMNS, _sha, config_differences, detector_config

COL = {name: i for i, name in enumerate(JOURNAL_COLUMNS)}
VALUES = [COL[c] for c in ("ax", "ay", "az", "gx", "gy", "gz", "svm")]


def replay_detector(package: dict) -> list[dict]:
    """The incidents the detector finds in the recorded IMU, one pass, fresh state."""
    table = package["table"]
    samples = [DeviceSample(int(r[COL["seq"]]), int(r[COL["device_ms"]]), r[VALUES].astype(np.float32)) for r in table]
    stream = LiveStream("s3")
    monitor = StreamMonitor(stream_key=f"s3:{uuid.uuid4().hex}", detector=fall_detection_service)
    for step in stream.accept("s3", samples):
        if step.reset is not None:
            monitor.reset_window()
        monitor.add_sample(step.source_ms, step.values)
    fall_detection_service.reset_patient(monitor.stream_key)
    return [{"index": i.index, "source_start_ms": i.source_start_ms, "source_end_ms": i.source_end_ms,
             "decision_source": i.decision_source} for i in monitor.incidents]


def replay_policy(package: dict) -> list[dict]:
    """The policy's actions for every incident, from the recorded events, on a virtual clock."""
    cfg = package["manifest"]["config"]
    config = PolicyConfig(cfg["wearer_cancel_seconds"], cfg["caregiver_ack_seconds"], cfg["late_help_grace_seconds"])
    out = []
    for incident in package["responses"]["incidents"]:
        events = [{k: e[k] for k in ("kind", "reply", "actor", "capture_end_ms", "policy_ms", "spoken") if k in e}
                  for e in incident["events"]]
        out.append({"index": incident["index"], **simulate(config, events, incident["sends"])})
    return out


def _times(result: dict) -> list:
    return [a["t_ms"] for a in result["actions"]]


def run_replay(package: dict) -> dict:
    """Both replays twice. Each row says what was compared and whether it held."""
    expected = package["expected"]
    detector = [replay_detector(package), replay_detector(package)]
    policy = [replay_policy(package), replay_policy(package)]
    recorded = expected["policy"]
    chain_matches = [chain(r) == chain({"actions": e["actions"]}) and r["final"] == e["final"]
                     for r, e in zip(policy[0], recorded)] if len(policy[0]) == len(recorded) else [False]
    deltas = [abs(a - b) for r, e in zip(policy[0], recorded) for a, b in zip(_times(r), _times(e))] or [0]
    recorded_model = package["manifest"]["model"]
    same_model = (_sha(KERAS_MODEL_PATH), _sha(SCALER_PARAMS_PATH)) == (recorded_model["keras_sha256"], recorded_model["scaler_sha256"])
    differences = config_differences(package["manifest"]["config"].get("detector"), detector_config())
    m5a_same = detector[0] == detector[1] and same_model and not differences
    m5b_same = policy[0] == policy[1]
    return {
        "M5a": {"status": "Pass" if m5a_same else "Fail", "replays_identical": detector[0] == detector[1], "same_model": same_model, "same_config": not differences, "config_differences": differences,
                "incidents": [len(d) for d in detector],
                "source_start_ms": [[i["source_start_ms"] for i in d] for d in detector],
                "matches_recorded": detector[0] == expected["incidents"], "recorded_incidents": len(expected["incidents"])},
        "M5b": {"status": "Pass" if m5b_same else "Fail", "replays_identical": m5b_same,
                "chain": [chain(r) for r in policy[0]],
                "matches_recorded_chain": all(chain_matches), "max_time_difference_ms": max(deltas),
                "events": sum(len(i["events"]) for i in package["responses"]["incidents"])},
    }
