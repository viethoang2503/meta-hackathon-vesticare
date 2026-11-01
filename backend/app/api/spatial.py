"""
spatial.py

HTTP surface of the spatial replay workspace. Parses requests and returns
snapshots; the run lifecycle lives in services/spatial_runs.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.spatial_runs import (
    LiveNotReady,
    RunAlreadyActive,
    ScenarioNotFound,
    incident_detail,
    list_scenarios,
    session_snapshot,
    spatial_runs,
)

router = APIRouter(prefix="/api/spatial", tags=["Spatial"])


class StartRequest(BaseModel):
    scenario_id: str


class StartLiveRequest(BaseModel):
    patient_id: int = Field(gt=0)
    device_id: str = Field(min_length=1, max_length=64)
    source: Literal["esp32", "fake_esp32"] = "esp32"
    # Simulated must be chosen explicitly and never counts as a real M2 send (MVP2.md).
    notification_mode: Literal["telegram", "simulated"] = "telegram"


class LocationOut(BaseModel):
    source: str
    x: float | None
    z: float | None
    zone_id: str | None
    zone_label: str | None
    fall_exposure: bool


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
    state: str | None
    location: LocationOut
    analysis_status: str | None
    latency_ms: float | None
    acknowledged_at: datetime | None
    wearer_deadline_at: datetime | None
    caregiver_deadline_at: datetime | None


class ActionOut(BaseModel):
    id: int
    incident_id: int | None
    kind: str
    target: str | None
    detail: dict | None
    created_at: datetime


class SessionSnapshot(BaseModel):
    id: str
    kind: str = "replay"
    scenario_id: str | None
    status: str
    end_reason: str | None
    started_at: datetime
    ended_at: datetime | None
    source_offset_ms: int
    recording_ended: bool
    model_source: str
    scenario: dict | None
    live: dict | None = None
    incidents: list[IncidentOut]
    actions: list[ActionOut]
    advisories: list[dict]


class WearerResponse(BaseModel):
    response: Literal["ok"]


class AckRequest(BaseModel):
    actor_id: str


class WearerResult(BaseModel):
    incident_id: int
    applied: bool
    incident_state: str | None
    reason: str | None


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


@router.post("/live", response_model=SessionSnapshot, status_code=201)
async def start_live(body: StartLiveRequest):
    try:
        session_id = await spatial_runs.start_live(body.patient_id, body.device_id, body.source, body.notification_mode)
    except LiveNotReady:
        raise HTTPException(status_code=409, detail="Blocked: contact setup. Configure TELEGRAM_BOT_TOKEN, "
                                                    "TELEGRAM_CHAT_ID_1 and TELEGRAM_CHAT_ID_2, or choose simulated")
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


@router.post("/incidents/{incident_id}/wearer-response", response_model=WearerResult)
async def wearer_response(incident_id: int, body: WearerResponse):
    result = await spatial_runs.wearer_ok(incident_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return result


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: int) -> dict:
    detail = incident_detail(incident_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return detail


@router.post("/incidents/{incident_id}/acknowledge", response_model=WearerResult)
async def acknowledge(incident_id: int, body: AckRequest):
    result = await spatial_runs.acknowledge(incident_id, body.actor_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return result
