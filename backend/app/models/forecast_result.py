"""Pydantic schemas for forecast and daily-log endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DailyLogCreate(BaseModel):
    sleep_hours: float = Field(..., ge=0, le=24, description="Hours of sleep last night")
    stress_level: int = Field(..., ge=1, le=10, description="Self-reported stress level 1-10")
    medication_taken: bool = Field(..., description="Was scheduled medication taken today?")
    vertigo_symptoms: int = Field(
        ..., ge=0, le=3,
        description="Vertigo severity: 0=none, 1=mild, 2=moderate, 3=severe",
    )
    notes: Optional[str] = None


class DailyLogOut(BaseModel):
    id: int
    patient_id: int
    date: datetime
    sleep_hours: float
    stress_level: int
    medication_taken: bool
    vertigo_symptoms: int
    notes: Optional[str]

    model_config = {"from_attributes": True}


class ForecastFactor(BaseModel):
    factor: str
    value: Any
    points: int
    description: str


class ForecastOut(BaseModel):
    id: int
    patient_id: int
    generated_at: datetime
    forecast_horizon: str
    risk_score: int
    risk_level: str
    factors: List[ForecastFactor]
    source: str
    warning: str

    model_config = {"from_attributes": True}
