"""
[PROTOTYPE] Phase 8.1 — Forecast Risk API.

Endpoints:
  POST /api/daily-log/{patient_id}   — Submit today's daily log
  GET  /api/daily-log/{patient_id}   — Retrieve recent daily logs
  POST /api/forecast/{patient_id}    — Generate and store a risk forecast
  GET  /api/forecast/{patient_id}    — Retrieve stored forecasts

All forecast results carry an explicit prototype warning in the response.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.forecast_result import DailyLogCreate, DailyLogOut
from app.models.schemas import DailyLog, ForecastResult, Patient
from app.services.forecast_service import PROTOTYPE_WARNING, compute_risk

router = APIRouter(prefix="/api", tags=["Forecast"])


def _get_or_create_patient(db: Session, patient_id: int) -> Patient:
    """Return the patient row, creating a placeholder if it does not exist yet.

    There is no patient CRUD API (api/patient.py is still a placeholder), and the
    sensor pipeline accepts any positive patient_id without a patients row. A 404
    here therefore made daily logs and forecasts unusable for every patient.
    """
    if patient_id <= 0:
        raise HTTPException(status_code=422, detail="patient_id must be positive")
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if patient is None:
        patient = Patient(id=patient_id, name=f"Patient {patient_id}")
        db.add(patient)
        db.commit()
        db.refresh(patient)
    return patient


# ── Daily Log ──────────────────────────────────────────────────────────────

@router.post("/daily-log/{patient_id}", response_model=DailyLogOut, summary="Submit daily log")
def submit_daily_log(
    patient_id: int,
    payload: DailyLogCreate,
    db: Session = Depends(get_db),
):
    _get_or_create_patient(db, patient_id)

    from datetime import datetime
    log = DailyLog(
        patient_id=patient_id,
        date=datetime.utcnow(),
        sleep_hours=payload.sleep_hours,
        stress_level=payload.stress_level,
        medication_taken=payload.medication_taken,
        vertigo_symptoms=payload.vertigo_symptoms,
        notes=payload.notes,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@router.get("/daily-log/{patient_id}", response_model=list[DailyLogOut], summary="Get daily logs")
def get_daily_logs(
    patient_id: int,
    limit: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
):
    return (
        db.query(DailyLog)
        .filter(DailyLog.patient_id == patient_id)
        .order_by(DailyLog.date.desc())
        .limit(limit)
        .all()
    )


# ── Forecast ───────────────────────────────────────────────────────────────

def _serialize_forecast(r: ForecastResult) -> dict:
    return {
        "id": r.id,
        "patient_id": r.patient_id,
        "generated_at": r.generated_at.isoformat(),
        "forecast_horizon": r.forecast_horizon,
        "risk_score": r.risk_score,
        "risk_level": r.risk_level,
        "factors": json.loads(r.factors) if r.factors else [],
        "source": r.source,
    }


@router.post("/forecast/{patient_id}", summary="Generate risk forecast")
def generate_forecast(patient_id: int, db: Session = Depends(get_db)):
    _get_or_create_patient(db, patient_id)

    result = compute_risk(db, patient_id)
    return {**_serialize_forecast(result), "warning": PROTOTYPE_WARNING}


@router.get("/forecast/{patient_id}", summary="Get stored forecasts")
def get_forecasts(
    patient_id: int,
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    results = (
        db.query(ForecastResult)
        .filter(ForecastResult.patient_id == patient_id)
        .order_by(ForecastResult.generated_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "forecasts": [_serialize_forecast(r) for r in results],
        "warning": PROTOTYPE_WARNING,
    }
