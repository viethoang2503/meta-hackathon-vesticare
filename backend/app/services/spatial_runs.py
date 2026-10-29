"""
spatial_runs.py

Lifecycle of spatial replay sessions: Start, the 1x replay task, Stop, the run
timeout, and Interrupted after a restart. Persists the incidents StreamMonitor
reports, then dispatches simulated notifications after the commit, so a slow
notification can never delay detection.

A fall incident starts as awaiting_wearer: the HUD asks "Are you OK?" for
WEARER_CANCEL_WINDOW_SECONDS. "I'm OK" in time makes it cancelled_by_wearer and
nobody is notified; silence makes it alerted and contact_1 is notified. "I'm OK"
after that is recorded but does not cancel anything.

Each run has one lock. Ending a run and every write the run makes (incident
rows, progress, notifications) check status and commit under that lock, so a
Stop can never land between a write's status check and its commit. Stop does
not cancel the replay task: it waits for it, because a worker thread started
with asyncio.to_thread keeps running after its coroutine is cancelled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from sqlalchemy.exc import IntegrityError

from app.config import (
    IMU_DETAIL_MAX_SECONDS,
    SCENARIOS_DIR,
    SPATIAL_RUN_TIMEOUT_SECONDS,
    WEARER_CANCEL_WINDOW_SECONDS,
)
from app import database
from app.models.schemas import AlertEvent, SpatialAction, SpatialSession
from app.services.broadcaster import broadcaster
from app.services.fall_detection import FallDetectionService, fall_detection_service
from app.services.monitoring import StreamMonitor
from app.services.scene_context import frame_near, location_at

logger = logging.getLogger(__name__)

TICK_SECONDS = 0.1
SCENARIO_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ScenarioNotFound(Exception):
    pass


class RunAlreadyActive(Exception):
    def __init__(self, session_id: str):
        super().__init__(session_id)
        self.session_id = session_id


@dataclass(frozen=True)
class Scenario:
    id: str
    meta: dict
    source_ms: np.ndarray
    samples: np.ndarray

    @property
    def patient_id(self) -> int:
        return int(self.meta["patient_id"])

    @property
    def sample_rate_hz(self) -> int:
        return int(self.meta["sample_rate_hz"])


def load_scenario(scenario_id: str) -> Scenario:
    folder = SCENARIOS_DIR / scenario_id
    if not SCENARIO_ID.match(scenario_id) or not (folder / "scenario.json").is_file():
        raise ScenarioNotFound(scenario_id)
    meta = json.loads((folder / "scenario.json").read_text())
    table = np.loadtxt(folder / meta["imu_file"], delimiter=",", skiprows=1, dtype=np.float64)
    return Scenario(scenario_id, meta, table[:, 0].astype(np.int64), table[:, 1:].astype(np.float32))


def list_scenarios() -> list[dict]:
    if not SCENARIOS_DIR.is_dir():
        return []
    return [
        json.loads((d / "scenario.json").read_text())
        for d in sorted(SCENARIOS_DIR.iterdir())
        if (d / "scenario.json").is_file()
    ]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hint(session_id: str, change: str) -> None:
    broadcaster.publish({"event_type": "spatial_session", "session_id": session_id, "change": change})


class SpatialRunManager:
    def __init__(
        self,
        *,
        realtime: bool = True,
        run_timeout_seconds: float = SPATIAL_RUN_TIMEOUT_SECONDS,
        wearer_cancel_seconds: float = WEARER_CANCEL_WINDOW_SECONDS,
        session_factory=None,
        detector: FallDetectionService = fall_detection_service,
    ) -> None:
        self._realtime = realtime
        self._run_timeout = run_timeout_seconds
        self._wearer_cancel = wearer_cancel_seconds
        self._session_factory = session_factory
        self._detector = detector
        self._runs: dict[str, asyncio.Task] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._stop_requested: dict[str, asyncio.Event] = {}
        self._dispatches: set[asyncio.Task] = set()

    def _db(self):
        # Looked up per call: tests swap database.SessionLocal after this module is imported.
        return (self._session_factory or database.SessionLocal)()

    def mark_interrupted_runs(self) -> int:
        """Startup: a Running row means its replay task died with the old process."""
        with self._db() as db:
            stale = db.query(SpatialSession).filter(SpatialSession.status == "Running").all()
            for s in stale:
                s.status, s.end_reason, s.ended_at = "Interrupted", "session_interrupted", _utcnow()
            db.commit()
            return len(stale)

    async def start(self, scenario_id: str) -> str:
        scenario = load_scenario(scenario_id)
        with self._db() as db:
            active = db.query(SpatialSession).filter(SpatialSession.status == "Running").first()
            if active is not None:
                raise RunAlreadyActive(active.id)
            session_id = uuid.uuid4().hex
            db.add(SpatialSession(
                id=session_id,
                scenario_id=scenario_id,
                status="Running",
                started_at=_utcnow(),
                source_offset_ms=0,
                recording_ended=False,
                model_source=self._detector.model_source,
            ))
            db.commit()
        self._locks[session_id] = threading.Lock()
        self._stop_requested[session_id] = asyncio.Event()
        self._runs[session_id] = asyncio.create_task(self._replay(session_id, scenario))
        _hint(session_id, "started")
        return session_id

    async def stop(self, session_id: str) -> None:
        """Returns once the run is Stopped and its replay task, worker thread included, has exited."""
        lock = self._locks.get(session_id)
        if lock is None:
            self._end(session_id, "Stopped", "user_stop")
            return
        await asyncio.to_thread(self._end_under, lock, session_id, "Stopped", "user_stop")
        self._stop_requested[session_id].set()
        task = self._runs.get(session_id)
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)

    async def wait(self, session_id: str) -> None:
        """Wait for a run's replay task to finish (used by tests and shutdown)."""
        task = self._runs.get(session_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if self._dispatches:
            await asyncio.gather(*self._dispatches, return_exceptions=True)

    def _end_under(self, lock: threading.Lock, session_id: str, status: str, reason: str) -> bool:
        with lock:
            return self._end(session_id, status, reason)

    def _end(self, session_id: str, status: str, reason: str) -> bool:
        with self._db() as db:
            s = db.get(SpatialSession, session_id)
            if s is None or s.status != "Running":
                return False
            s.status, s.end_reason, s.ended_at = status, reason, _utcnow()
            db.commit()
        _hint(session_id, status.lower())
        return True

    async def _replay(self, session_id: str, scenario: Scenario) -> None:
        lock, stop_requested = self._locks[session_id], self._stop_requested[session_id]
        monitor = StreamMonitor(stream_key=f"session:{session_id}", detector=self._detector)
        incident_rows: dict[int, int] = {}
        loop = asyncio.get_running_loop()
        started = loop.time()
        cursor, total = 0, len(scenario.samples)
        try:
            while True:
                elapsed = loop.time() - started
                if elapsed >= self._run_timeout:
                    await asyncio.to_thread(self._end_under, lock, session_id, "Stopped", "timeout")
                    return
                due = min(total, int(elapsed * scenario.sample_rate_hz) + 1) if self._realtime else total
                if due > cursor:
                    opened = await asyncio.to_thread(
                        self._ingest, lock, session_id, scenario, monitor, incident_rows, cursor, due
                    )
                    if opened is None:
                        return
                    cursor = due
                    for incident_id in opened:
                        self._background(self._wearer_countdown(session_id, lock, incident_id))
                try:
                    await asyncio.wait_for(stop_requested.wait(), TICK_SECONDS if self._realtime else 0.01)
                    return
                except asyncio.TimeoutError:
                    pass
        except Exception:
            logger.exception("spatial replay %s failed", session_id)
            try:
                self._end_under(lock, session_id, "Interrupted", "replay_error")
            except Exception:
                logger.exception("could not record Interrupted for %s", session_id)
        finally:
            monitor.close()
            self._runs.pop(session_id, None)
            self._locks.pop(session_id, None)
            self._stop_requested.pop(session_id, None)

    def _ingest(self, lock, session_id, scenario, monitor, incident_rows, start, end) -> list[int] | None:
        """Runs in a worker thread. Returns new incident ids, or None once the run has ended."""
        events = [monitor.add_sample(int(scenario.source_ms[j]), scenario.samples[j]) for j in range(start, end)]
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            if session is None or session.status != "Running":
                return None
            opened = []
            for event in events:
                if event is None:
                    continue
                inc = event.incident
                if event.kind == "opened":
                    row = AlertEvent(
                        patient_id=scenario.patient_id,
                        timestamp=_utcnow(),
                        alert_type="FALL",
                        confidence=inc.max_fall_probability,
                        peak_svm=inc.peak_svm,
                        is_acknowledged=False,
                        notification_status="pending",
                        spatial_state="awaiting_wearer",
                        session_id=session_id,
                        source_start_ms=inc.source_start_ms,
                        source_end_ms=inc.source_end_ms,
                        decision_source=inc.decision_source,
                    )
                    db.add(row)
                    db.flush()
                    incident_rows[inc.index] = row.id
                    opened.append(row.id)
                else:
                    row = db.get(AlertEvent, incident_rows[inc.index])
                    row.source_end_ms = inc.source_end_ms
                    row.confidence = inc.max_fall_probability
                    row.peak_svm = inc.peak_svm
            session.source_offset_ms = int(scenario.source_ms[end - 1])
            session.recording_ended = end >= len(scenario.samples)
            db.commit()
        _hint(session_id, "incident" if opened else "progress")
        return opened

    def _background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._dispatches.add(task)
        task.add_done_callback(self._dispatches.discard)

    async def _wearer_countdown(self, session_id: str, lock: threading.Lock, incident_id: int) -> None:
        await asyncio.sleep(self._wearer_cancel)
        await asyncio.to_thread(self._alert_if_unanswered, lock, session_id, incident_id)

    def _alert_if_unanswered(self, lock: threading.Lock, session_id: str, incident_id: int) -> None:
        """Simulated sink: the notification is the action-log row. Nothing leaves the process."""
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            row = db.get(AlertEvent, incident_id)
            if session is None or session.status != "Running" or row.spatial_state != "awaiting_wearer":
                return
            row.spatial_state = "alerted"
            row.notification_status = "simulated"
            row.notification_sent_at = _utcnow()
            db.add(SpatialAction(
                session_id=session_id,
                incident_id=incident_id,
                kind="notify",
                target="contact_1",
                detail=json.dumps({"channel": "simulated", "after_wearer_window_s": self._wearer_cancel}),
                created_at=_utcnow(),
            ))
            db.commit()
        _hint(session_id, "action")

    async def wearer_ok(self, incident_id: int) -> dict | None:
        """The wearer pressed "I'm OK". Returns None for an unknown incident."""
        with self._db() as db:
            row = db.get(AlertEvent, incident_id)
            if row is None or row.session_id is None:
                return None
            session_id = row.session_id
        lock = self._locks.get(session_id)
        if lock is None:
            return {"incident_id": incident_id, "applied": False, "incident_state": row.spatial_state, "reason": "run_ended"}
        return await asyncio.to_thread(self._record_wearer_ok, lock, session_id, incident_id)

    def _record_wearer_ok(self, lock: threading.Lock, session_id: str, incident_id: int) -> dict:
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            row = db.get(AlertEvent, incident_id)
            if session is None or session.status != "Running":
                return {"incident_id": incident_id, "applied": False, "incident_state": row.spatial_state, "reason": "run_ended"}
            cancels = row.spatial_state == "awaiting_wearer"
            if cancels:
                row.spatial_state = "cancelled_by_wearer"
            db.add(SpatialAction(
                session_id=session_id,
                incident_id=incident_id,
                kind="self_report",
                target="wearer",
                detail=json.dumps({"response": "ok", "cancelled_alert": cancels}),
                created_at=_utcnow(),
            ))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return {"incident_id": incident_id, "applied": False, "incident_state": row.spatial_state, "reason": "already_recorded"}
            state = row.spatial_state
        _hint(session_id, "action")
        return {"incident_id": incident_id, "applied": True, "incident_state": state, "reason": None}


def _actions_json(actions) -> list[dict]:
    return [
        {"id": a.id, "incident_id": a.incident_id, "kind": a.kind, "target": a.target,
         "detail": json.loads(a.detail) if a.detail else None, "created_at": a.created_at}
        for a in actions
    ]


def session_snapshot(session_id: str, session_factory=None) -> dict | None:
    with (session_factory or database.SessionLocal)() as db:
        s = db.get(SpatialSession, session_id)
        if s is None:
            return None
        scenario_meta = load_scenario(s.scenario_id).meta
        incidents = (
            db.query(AlertEvent)
            .filter(AlertEvent.session_id == session_id)
            .order_by(AlertEvent.source_start_ms)
            .all()
        )
        actions = (
            db.query(SpatialAction)
            .filter(SpatialAction.session_id == session_id)
            .order_by(SpatialAction.id)
            .all()
        )
        return {
            "id": s.id,
            "scenario_id": s.scenario_id,
            "status": s.status,
            "end_reason": s.end_reason,
            "started_at": s.started_at,
            "ended_at": s.ended_at,
            "source_offset_ms": s.source_offset_ms,
            "recording_ended": s.recording_ended,
            "model_source": s.model_source,
            "scenario": {k: scenario_meta[k] for k in ("id", "title", "duration_ms", "home", "location", "route")},
            "incidents": [
                {
                    "id": i.id,
                    "alert_type": i.alert_type,
                    "source_start_ms": i.source_start_ms,
                    "source_end_ms": i.source_end_ms,
                    "decision_source": i.decision_source,
                    "fall_probability": i.confidence,
                    "peak_svm": i.peak_svm,
                    "is_acknowledged": i.is_acknowledged,
                    "notification_status": i.notification_status,
                    "state": i.spatial_state,
                    "location": location_at(scenario_meta, i.source_start_ms),
                }
                for i in incidents
            ],
            "actions": _actions_json(actions),
        }


def incident_detail(incident_id: int, session_factory=None) -> dict | None:
    """Everything the evidence drawer shows for one incident, each part labelled with its source."""
    with (session_factory or database.SessionLocal)() as db:
        row = db.get(AlertEvent, incident_id)
        if row is None or row.session_id is None:
            return None
        session = db.get(SpatialSession, row.session_id)
        actions = (
            db.query(SpatialAction)
            .filter(SpatialAction.incident_id == incident_id)
            .order_by(SpatialAction.id)
            .all()
        )
        scenario = load_scenario(session.scenario_id)
        half = IMU_DETAIL_MAX_SECONDS * 1000 // 2
        lo, hi = row.source_start_ms - half, row.source_start_ms + half
        mask = (scenario.source_ms >= lo) & (scenario.source_ms <= hi)
        imu = [[int(ms), *[round(float(v), 4) for v in sample]] for ms, sample in zip(scenario.source_ms[mask], scenario.samples[mask])]
        return {
            "id": row.id,
            "session_id": row.session_id,
            "scenario_id": session.scenario_id,
            "alert_type": row.alert_type,
            "state": row.spatial_state,
            "source_start_ms": row.source_start_ms,
            "source_end_ms": row.source_end_ms,
            "decision_source": row.decision_source,
            "fall_probability": row.confidence,
            "peak_svm": row.peak_svm,
            "is_acknowledged": row.is_acknowledged,
            "location": location_at(scenario.meta, row.source_start_ms),
            "frame": frame_near(session.scenario_id, row.source_start_ms),
            "imu": {
                "source": "Scenario-paired recording",
                "columns": ["source_ms", "ax", "ay", "az", "gx", "gy", "gz", "svm"],
                "units": {"acc": "g", "gyro": "deg/s", "svm": "g"},
                "segments": scenario.meta["segments"],
                "samples": imu,
            },
            "actions": _actions_json(actions),
        }


spatial_runs = SpatialRunManager()
