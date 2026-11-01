"""G4 table (docs/spatial/MVP2.md, "G4 và bảng thử nghiệm") from one live session.

    .verify/venv/bin/python backend/tools/g4_report.py SESSION_ID --rest 5:35 \\
        --fall 60:70 --fall 90:100 --fall 120:130 --adl 140:200

Times are seconds of source time, read off the recording video or the laptop. Writes
docs/spatial/evidence/mvp2/g4/<session>.json and .md. A session streamed by the fake
device is reported but always marked "not G4 evidence".
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import SCALER_PARAMS_PATH  # noqa: E402
from app.services.spatial_runs import JOURNAL_COLUMNS  # noqa: E402

OUT_DIR = BACKEND.parent / "docs" / "spatial" / "evidence" / "mvp2" / "g4"
ACC_FULL_SCALE_G = 16.0
GYRO_FULL_SCALE_DPS = 2000.0
CLIP_FRACTION = 0.995  # a reading this close to full scale is treated as clipped
AXES = ("ax", "ay", "az")
COL = {name: i for i, name in enumerate(JOURNAL_COLUMNS)}


def _span(table: np.ndarray, start_s: float, end_s: float) -> np.ndarray:
    t = table[:, COL["source_ms"]]
    return table[(t >= start_s * 1000) & (t <= end_s * 1000)]


def _dominant_axis(vector) -> str:
    i = int(np.argmax(np.abs(vector)))
    return ("+" if vector[i] >= 0 else "-") + AXES[i][1]


def analyse(table: np.ndarray, stats: dict, incidents: list[dict], segments: dict, source: str,
            training_mean: list[float]) -> dict:
    """Pure: journal rows, the session's live_stats, its incidents and the labelled segments in, G4 rows out."""
    seq, device_ms = table[:, COL["seq"]], table[:, COL["device_ms"]]
    acc = table[:, [COL[a] for a in AXES]]
    gyro = table[:, [COL["gx"], COL["gy"], COL["gz"]]]
    svm = table[:, COL["svm"]]
    rows = {}

    if segments.get("rest"):
        rest = _span(table, *segments["rest"])
        mean_vec = rest[:, [COL[a] for a in AXES]].mean(axis=0) if len(rest) else np.zeros(3)
        mean_svm = float(rest[:, COL["svm"]].mean()) if len(rest) else float("nan")
        device_axis, train_axis = _dominant_axis(mean_vec), _dominant_axis(training_mean[:3])
        rows["rest"] = {
            "mean_svm_g": round(mean_svm, 3), "samples": int(len(rest)),
            "gravity_vector_g": [round(float(v), 3) for v in mean_vec],
            "gravity_axis": device_axis, "training_gravity_axis": train_axis,
            "pass": bool(abs(mean_svm - 1.0) <= 0.05),
            "note": None if device_axis == train_axis else
                    f"Gravity sits on {device_axis}, training data on {train_axis}: the model is orientation-sensitive (GATES.md G1)",
        }

    span_s = (device_ms[-1] - device_ms[0]) / 1000
    intervals = np.diff(device_ms)[np.diff(seq) == 1]
    acquired = int(seq[-1] - seq[0] + 1)
    rate = (seq[-1] - seq[0]) / span_s if span_s > 0 else float("nan")
    p95 = float(np.percentile(intervals, 95)) if len(intervals) else float("nan")
    rows["acquisition"] = {
        "rate_hz": round(float(rate), 2), "p95_interval_ms": round(p95, 1),
        "max_interval_ms": float(intervals.max()) if len(intervals) else None,
        "dropped_at_source": int(stats.get("dropped_at_source", 0)), "acquired": acquired,
        "pass": bool(49 <= rate <= 51 and p95 <= 30),
    }

    missing = acquired - len(table)
    transport_lost = max(0, missing - rows["acquisition"]["dropped_at_source"])
    kept = acquired - rows["acquisition"]["dropped_at_source"]
    rows["transport"] = {
        "lost": transport_lost, "of_acquired_kept": kept,
        "lost_fraction": round(transport_lost / kept, 4) if kept else None,
        "duplicates": int(stats.get("duplicates", 0)), "rejected_batches": int(stats.get("rejected_batches", 0)),
        "pass": bool(kept and transport_lost / kept < 0.01),
    }

    coverage = stats.get("coverage")
    resets: dict[str, int] = {}
    for r in stats.get("resets", []):
        resets[r["reason"]] = resets.get(r["reason"], 0) + 1
    rows["coverage"] = {
        "coverage": coverage, "unavailable_ms": stats.get("unavailable_ms"),
        "first_warmup_ms": stats.get("first_warmup_ms"), "reset_count": stats.get("reset_count", 0),
        "resets_by_reason": resets, "pass": bool(coverage is not None and coverage >= 0.95),
    }

    clipped = int(np.sum((np.abs(acc) >= CLIP_FRACTION * ACC_FULL_SCALE_G).any(axis=1)
                         | (np.abs(gyro) >= CLIP_FRACTION * GYRO_FULL_SCALE_DPS).any(axis=1)))
    fall_peaks = [round(float(_span(table, *f)[:, COL["svm"]].max()), 2) if len(_span(table, *f)) else None
                  for f in segments.get("falls", [])]
    rows["range"] = {"clipped_samples": clipped, "peak_svm_g": round(float(svm.max()), 2),
                     "fall_peak_svm_g": fall_peaks, "pass": clipped == 0}

    def hits(seg):
        return [i for i in incidents if seg[0] * 1000 <= i["source_start_ms"] <= seg[1] * 1000]

    falls = [{"segment_s": list(f), "incidents": [{"id": i["id"], "decision_source": i["decision_source"]} for i in hits(f)]}
             for f in segments.get("falls", [])]
    adl = [{"segment_s": list(a), "false_alarms": len(hits(a))} for a in segments.get("adl", [])]
    rows["detector"] = {
        "falls_detected": sum(1 for f in falls if f["incidents"]), "falls": len(falls), "fall_trials": falls,
        "adl_with_false_alarm": sum(1 for a in adl if a["false_alarms"]), "adl": len(adl), "adl_trials": adl,
    }

    simulated = source != "esp32"
    gate_rows = ("rest", "acquisition", "transport", "coverage", "range")
    return {
        "source": source,
        "status": "Simulated: not G4 evidence" if simulated else
                  ("Pass" if all(rows[k]["pass"] for k in gate_rows if k in rows) and "rest" in rows else "Fail"),
        "rows": rows,
    }


def to_markdown(session_id: str, report: dict) -> str:
    r = report["rows"]
    lines = [f"# G4 for session {session_id}", "", f"Source: {report['source']}. Status: **{report['status']}**.", "",
             "| Check | Measured | Target | Pass |", "|---|---|---|---|"]
    if "rest" in r:
        lines.append(f"| Rest at the waist | mean SVM {r['rest']['mean_svm_g']} g, gravity on {r['rest']['gravity_axis']} "
                     f"(training data {r['rest']['training_gravity_axis']}) | 1 ± 0.05 g | {r['rest']['pass']} |")
    a, t, c, g = r["acquisition"], r["transport"], r["coverage"], r["range"]
    lines += [
        f"| Acquisition | {a['rate_hz']} Hz, p95 {a['p95_interval_ms']} ms, max {a['max_interval_ms']} ms, "
        f"{a['dropped_at_source']} dropped at source | 49–51 Hz, p95 ≤ 30 ms | {a['pass']} |",
        f"| Transport | {t['lost']} of {t['of_acquired_kept']} lost, {t['duplicates']} duplicates, "
        f"{t['rejected_batches']} rejected batches | < 1% | {t['pass']} |",
        f"| Detector coverage | {c['coverage']}, {c['unavailable_ms']} ms unavailable, resets {c['resets_by_reason']} | ≥ 95% | {c['pass']} |",
        f"| Range | {g['clipped_samples']} clipped, peak {g['peak_svm_g']} g, falls {g['fall_peak_svm_g']} | no clipping | {g['pass']} |",
    ]
    d = r["detector"]
    lines += ["", f"Detector on the device: {d['falls_detected']}/{d['falls']} falls opened an incident; "
              f"{d['adl_with_false_alarm']}/{d['adl']} everyday trials raised one."]
    if r.get("rest", {}).get("note"):
        lines += ["", r["rest"]["note"] + "."]
    return "\n".join(lines) + "\n"


def _segment(text: str) -> tuple[float, float]:
    a, b = text.split(":")
    return float(a), float(b)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session")
    ap.add_argument("--rest", type=_segment)
    ap.add_argument("--fall", type=_segment, action="append", default=[])
    ap.add_argument("--adl", type=_segment, action="append", default=[])
    args = ap.parse_args()

    from app import database
    from app.models.schemas import AlertEvent, SpatialSession
    from app.services.spatial_runs import _journal_path, _read_journal

    with database.SessionLocal() as db:
        s = db.get(SpatialSession, args.session)
        if s is None or s.kind != "live":
            print(f"no live session {args.session}")
            return 1
        stats = json.loads(s.live_stats or "{}")
        source = json.loads(s.config_snapshot or "{}").get("source", "esp32")
        incidents = [{"id": i.id, "source_start_ms": i.source_start_ms, "decision_source": i.decision_source}
                     for i in db.query(AlertEvent).filter(AlertEvent.session_id == s.id)]
    table = _read_journal(_journal_path(args.session))
    training_mean = np.load(SCALER_PARAMS_PATH)["mean"].tolist()
    report = analyse(table, stats, incidents, {"rest": args.rest, "falls": args.fall, "adl": args.adl}, source, training_mean)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{args.session}.json").write_text(json.dumps(report, indent=1))
    markdown = to_markdown(args.session, report)
    (OUT_DIR / f"{args.session}.md").write_text(markdown)
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
