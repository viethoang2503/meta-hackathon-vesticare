"""
m4_report.py

M4 table (docs/spatial/MVP2.md): per person, how many controlled falls opened an incident, how many were
missed, and how many everyday trials raised a false alarm. At least two people, and for each at least
3 falls and 10 everyday trials. There is no pass threshold in the spec: the table reports, and it
is not a clinical validation. A session from a stand-in device is shown but never counts.
"""

from __future__ import annotations

import json

from app import database
from app.models.schemas import AlertEvent, SpatialSession

MIN_PEOPLE, MIN_FALLS, MIN_ADL = 2, 3, 10
NOT_CLINICAL = "Controlled trials with healthy adults. This is not a clinical validation."


def summarize(people: dict[str, dict]) -> dict:
    """`people`: name -> {"source": "esp32" | other, "falls": [{"segment_s", "incidents": [source_start_ms]}],
    "adl": [{"segment_s", "incidents": [source_start_ms]}]}. A trial counts incidents that began inside its span."""
    rows = {}
    for name, p in people.items():
        falls = [{"segment_s": f["segment_s"], "detected": bool(f["incidents"]),
                  "start_offset_s": round(f["incidents"][0] / 1000 - f["segment_s"][0], 2) if f["incidents"] else None}
                 for f in p["falls"]]
        adl = [{"segment_s": a["segment_s"], "false_alarms": len(a["incidents"])} for a in p["adl"]]
        short = [text for lacking, text in (
            (len(falls) < MIN_FALLS, f"at least {MIN_FALLS} controlled falls (have {len(falls)})"),
            (len(adl) < MIN_ADL, f"at least {MIN_ADL} everyday trials (have {len(adl)})")) if lacking]
        counts = p["source"] == "esp32"
        rows[name] = {
            "source": p["source"], "counts": counts,
            "falls": len(falls), "falls_detected": sum(f["detected"] for f in falls),
            "falls_missed": sum(not f["detected"] for f in falls),
            "adl": len(adl), "adl_with_false_alarm": sum(a["false_alarms"] > 0 for a in adl),
            "false_alarms": sum(a["false_alarms"] for a in adl),
            "fall_trials": falls, "adl_trials": adl, "missing": short,
            "status": "Simulated: not M4 evidence" if not counts else "Incomplete" if short else "Reported"}
    counted = [r for r in rows.values() if r["counts"]]
    missing = [] if len(counted) >= MIN_PEOPLE else [f"at least {MIN_PEOPLE} people with real device data (have {len(counted)})"]
    done = len(counted) >= MIN_PEOPLE and all(r["status"] == "Reported" for r in counted)
    return {"status": "Reported" if done else "Incomplete", "missing": missing, "note": NOT_CLINICAL, "people": rows}


def collect(spec: dict, session_factory=None) -> dict:
    """`spec`: {"people": {name: {"session": id, "falls": [[s, e]], "adl": [[s, e]]}}}, times in source seconds."""
    people = {}
    with (session_factory or database.SessionLocal)() as db:
        for name, entry in spec["people"].items():
            session = db.get(SpatialSession, entry["session"])
            if session is None or session.kind != "live":
                raise ValueError(f"{name}: no live session {entry['session']}")
            starts = sorted(r.source_start_ms for r in db.query(AlertEvent).filter(AlertEvent.session_id == session.id))
            inside = lambda seg: [t for t in starts if seg[0] * 1000 <= t <= seg[1] * 1000]
            people[name] = {"source": json.loads(session.config_snapshot or "{}").get("source", "esp32"),
                            "falls": [{"segment_s": seg, "incidents": inside(seg)} for seg in entry["falls"]],
                            "adl": [{"segment_s": seg, "incidents": inside(seg)} for seg in entry["adl"]]}
    return people


def m4_report(spec: dict, session_factory=None) -> dict:
    return summarize(collect(spec, session_factory))
