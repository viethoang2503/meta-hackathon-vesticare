"""
spatial.py

HTTP surface of the spatial replay workspace. Parses requests and returns
snapshots; the run lifecycle lives in services/spatial_runs.py.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.spatial_runs import (
    RunAlreadyActive,
    ScenarioNotFound,
    list_scenarios,
    session_snapshot,
    spatial_runs,
)

router = APIRouter(prefix="/api/spatial", tags=["Spatial"])


class StartRequest(BaseModel):
    scenario_id: str


class IncidentOut(BaseModel):
    id: int
    alert_type: str
    source_start_ms: int
    source_end_ms: int
    decision_source: str
    fall_probability: float
    peak_svm: float
    is_acknowledged: bool
    notification_status: str | None


class ActionOut(BaseModel):
    id: int
    incident_id: int | None
    kind: str
    target: str | None
    created_at: datetime


class SessionSnapshot(BaseModel):
    id: str
    scenario_id: str
    status: str
    end_reason: str | None
    started_at: datetime
    ended_at: datetime | None
    source_offset_ms: int
    recording_ended: bool
    model_source: str
    incidents: list[IncidentOut]
    actions: list[ActionOut]


def _snapshot_or_404(session_id: str) -> dict:
    snapshot = session_snapshot(session_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return snapshot


@router.get("/scenarios")
def get_scenarios() -> list[dict]:
    return list_scenarios()


@router.post("/sessions", response_model=SessionSnapshot, status_code=201)
async def start_session(body: StartRequest):
    try:
        session_id = await spatial_runs.start(body.scenario_id)
    except ScenarioNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown scenario {body.scenario_id!r}")
    except RunAlreadyActive as active:
        raise HTTPException(status_code=409, detail=f"Session {active.session_id} is still running; stop it first")
    return _snapshot_or_404(session_id)


@router.post("/sessions/{session_id}/stop", response_model=SessionSnapshot)
async def stop_session(session_id: str):
    await spatial_runs.stop(session_id)
    return _snapshot_or_404(session_id)


@router.get("/sessions/{session_id}", response_model=SessionSnapshot)
def get_session(session_id: str):
    return _snapshot_or_404(session_id)
