"""
spatial_runs.py

Lifecycle of spatial replay sessions: Start, the 1x replay task, Stop, the run
timeout, and Interrupted after a restart. Persists the incidents StreamMonitor
reports, then dispatches simulated notifications after the commit, so a slow
notification can never delay detection.

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

from app.config import SCENARIOS_DIR, SPATIAL_RUN_TIMEOUT_SECONDS
from app import database
from app.models.schemas import AlertEvent, SpatialAction, SpatialSession
from app.services.broadcaster import broadcaster
from app.services.fall_detection import FallDetectionService, fall_detection_service
from app.services.monitoring import StreamMonitor

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
        session_factory=None,
        detector: FallDetectionService = fall_detection_service,
    ) -> None:
        self._realtime = realtime
        self._run_timeout = run_timeout_seconds
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
                        self._dispatch(session_id, lock, incident_id, "contact_1")
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

    def _dispatch(self, session_id: str, lock: threading.Lock, incident_id: int, target: str) -> None:
        task = asyncio.create_task(asyncio.to_thread(self._notify, lock, session_id, incident_id, target))
        self._dispatches.add(task)
        task.add_done_callback(self._dispatches.discard)

    def _notify(self, lock: threading.Lock, session_id: str, incident_id: int, target: str) -> None:
        """Simulated sink: the notification is the action-log row. Nothing leaves the process."""
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            if session is None or session.status != "Running":
                return
            db.add(SpatialAction(
                session_id=session_id,
                incident_id=incident_id,
                kind="notify",
                target=target,
                detail=json.dumps({"channel": "simulated"}),
                created_at=_utcnow(),
            ))
            row = db.get(AlertEvent, incident_id)
            row.notification_status = "simulated"
            row.notification_sent_at = _utcnow()
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return
        _hint(session_id, "action")


def session_snapshot(session_id: str, session_factory=None) -> dict | None:
    with (session_factory or database.SessionLocal)() as db:
        s = db.get(SpatialSession, session_id)
        if s is None:
            return None
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
                }
                for i in incidents
            ],
            "actions": [
                {"id": a.id, "incident_id": a.incident_id, "kind": a.kind, "target": a.target, "created_at": a.created_at}
                for a in actions
            ],
        }


spatial_runs = SpatialRunManager()
