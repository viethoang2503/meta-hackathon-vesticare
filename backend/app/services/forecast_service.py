"""
[PROTOTYPE] Phase 8.1 — Fall Risk Forecast Service.

Scoring method: rule-based v1.
Input:  AlertEvent history (last 7 days) + most recent DailyLog.
Output: ForecastResult stored in DB, returned to caller.

Limitations:
- Not clinically validated.
- Based on synthetic / simulator data only.
- Rules and weights are heuristic — not derived from a labelled dataset.
- Results must not be used for medical decision-making.
"""

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.schemas import AlertEvent, DailyLog, ForecastResult

logger = logging.getLogger("vesti_care.forecast")

PROTOTYPE_SOURCE = "v1_prototype_rules"
PROTOTYPE_WARNING = (
    "[PROTOTYPE] Rule-based scoring only. "
    "Not clinically validated. "
    "Based on synthetic/simulator data. "
    "Do not use for medical decision-making."
)

# Risk level thresholds
_THRESHOLD_HIGH = 60
_THRESHOLD_MEDIUM = 30


def _classify_level(score: int) -> str:
    if score >= _THRESHOLD_HIGH:
        return "HIGH"
    if score >= _THRESHOLD_MEDIUM:
        return "MEDIUM"
    return "LOW"


def compute_risk(db: Session, patient_id: int) -> ForecastResult:
    """
    Run rule-based risk scoring for a patient and persist the result.

    Rules applied (total possible: ~120 pts, clamped to 100):
      Recent alerts (last 7 days):
        +20 per confirmed FALL, max 40 pts
        +5  per confirmed VESTIBULAR_INSTABILITY, max 20 pts
      Latest daily log:
        Sleep < 6h  → +15 pts
        Sleep < 7h  → +5 pts
        Stress >= 8 → +15 pts
        Stress >= 6 → +5 pts
        Medication not taken → +15 pts
        Vertigo mild (1)     → +10 pts
        Vertigo moderate (2) → +20 pts
        Vertigo severe (3)   → +30 pts
    """
    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=7)

    factors = []
    risk_score = 0

    # ── Factor 1: recent alert history ────────────────────────────────────
    recent_alerts = (
        db.query(AlertEvent)
        .filter(
            AlertEvent.patient_id == patient_id,
            AlertEvent.timestamp >= seven_days_ago,
            # Exclude confirmed false alerts. In SQL `NULL != 'false'` is NULL,
            # so alerts without feedback must be kept explicitly.
            or_(AlertEvent.feedback.is_(None), AlertEvent.feedback != "false"),
        )
        .all()
    )
    falls = [a for a in recent_alerts if a.alert_type == "FALL"]
    # "SWAY" is the legacy name; the detector now emits VESTIBULAR_INSTABILITY.
    sways = [
        a for a in recent_alerts
        if a.alert_type in ("VESTIBULAR_INSTABILITY", "SWAY")
    ]

    fall_pts = min(len(falls) * 20, 40)
    sway_pts = min(len(sways) * 5, 20)
    risk_score += fall_pts + sway_pts

    if fall_pts > 0:
        factors.append({
            "factor": "recent_falls",
            "value": len(falls),
            "points": fall_pts,
            "description": f"{len(falls)} fall event(s) in the last 7 days",
        })
    if sway_pts > 0:
        factors.append({
            "factor": "recent_sways",
            "value": len(sways),
            "points": sway_pts,
            "description": f"{len(sways)} vestibular instability event(s) in the last 7 days",
        })

    # ── Factor 2: most recent daily log ───────────────────────────────────
    latest_log = (
        db.query(DailyLog)
        .filter(DailyLog.patient_id == patient_id)
        .order_by(DailyLog.date.desc())
        .first()
    )

    if latest_log:
        # Sleep quality
        if latest_log.sleep_hours < 6:
            pts = 15
            risk_score += pts
            factors.append({
                "factor": "poor_sleep",
                "value": latest_log.sleep_hours,
                "points": pts,
                "description": f"Sleep {latest_log.sleep_hours:.1f}h (under 6h threshold)",
            })
        elif latest_log.sleep_hours < 7:
            pts = 5
            risk_score += pts
            factors.append({
                "factor": "short_sleep",
                "value": latest_log.sleep_hours,
                "points": pts,
                "description": f"Sleep {latest_log.sleep_hours:.1f}h (under 7h threshold)",
            })

        # Stress level
        if latest_log.stress_level >= 8:
            pts = 15
            risk_score += pts
            factors.append({
                "factor": "high_stress",
                "value": latest_log.stress_level,
                "points": pts,
                "description": f"Stress level {latest_log.stress_level}/10 (high)",
            })
        elif latest_log.stress_level >= 6:
            pts = 5
            risk_score += pts
            factors.append({
                "factor": "moderate_stress",
                "value": latest_log.stress_level,
                "points": pts,
                "description": f"Stress level {latest_log.stress_level}/10 (moderate)",
            })

        # Medication adherence
        if not latest_log.medication_taken:
            pts = 15
            risk_score += pts
            factors.append({
                "factor": "missed_medication",
                "value": False,
                "points": pts,
                "description": "Scheduled medication was not taken",
            })

        # Vertigo symptom severity
        vertigo_pts_map = {1: 10, 2: 20, 3: 30}
        pts = vertigo_pts_map.get(latest_log.vertigo_symptoms, 0)
        if pts > 0:
            severity_label = {1: "mild", 2: "moderate", 3: "severe"}[latest_log.vertigo_symptoms]
            risk_score += pts
            factors.append({
                "factor": "vertigo_symptoms",
                "value": latest_log.vertigo_symptoms,
                "points": pts,
                "description": f"Vertigo symptoms reported: {severity_label}",
            })
    else:
        factors.append({
            "factor": "no_daily_log",
            "value": None,
            "points": 0,
            "description": "No daily log submitted — daily risk factors not evaluated",
        })

    # ── Clamp and classify ─────────────────────────────────────────────────
    risk_score = max(0, min(100, risk_score))
    risk_level = _classify_level(risk_score)

    logger.info(
        "Forecast generated: patient=%d score=%d level=%s factors=%d source=%s",
        patient_id, risk_score, risk_level, len(factors), PROTOTYPE_SOURCE,
    )

    result = ForecastResult(
        patient_id=patient_id,
        generated_at=now,
        forecast_horizon="next_24h",
        risk_score=risk_score,
        risk_level=risk_level,
        factors=json.dumps(factors),
        source=PROTOTYPE_SOURCE,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result
