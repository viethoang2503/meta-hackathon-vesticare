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

Each incident also gets a SAM analysis of the glasses frame at its start and
then a Muse Spark explanation (services/incident_agent.py), both in the
background so a slow or failing API never delays the countdown.
Ending a run settles unfinished analyses: Stop -> Cancelled, run timeout ->
TimedOut, restart -> Failed; a late API answer does not overwrite that.

Entering a zone with fall_exposure records an advisory (the HUD tip) and a
SAM confirmation of the frame at entry; re-entering within the cooldown is
logged but not shown. Once contact_1 is notified, an unacknowledged incident
escalates to contact_2 exactly once after the caregiver window.

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
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sqlalchemy.exc import IntegrityError

from datetime import timedelta

from app.config import (
    ADVISORY_COOLDOWN_SECONDS,
    AGENT_MAX_TOOL_CALLS,
    CAREGIVER_ACK_TIMEOUT_SECONDS,
    AGENT_MODEL,
    ANALYSIS_TIMEOUT_SECONDS,
    FIXES_LIBRARY_PATH,
    IMU_DETAIL_MAX_SECONDS,
    LIVE_DATA_DIR,
    LIVE_DEVICE_SILENT_SECONDS,
    LIVE_RUN_TIMEOUT_SECONDS,
    LIVE_WEARER_CANCEL_SECONDS,
    SAMPLING_RATE,
    SCENARIOS_DIR,
    SPATIAL_RUN_TIMEOUT_SECONDS,
    WEARER_CANCEL_WINDOW_SECONDS,
    WINDOW_SIZE,
)
from app import database
from app.models.schemas import AlertEvent, SpatialAction, SpatialAnalysis, SpatialSession
from app.services.broadcaster import broadcaster
from app.services.fall_detection import FallDetectionService, fall_detection_service
from app.services import muse_client, sam_client
from app.services.incident_agent import run_agent
from app.services.live_stream import MAX_SAMPLE_GAP_MS, BatchRejected, DeviceSample, LiveStream
from app.services.monitoring import StreamMonitor
from app.services.scene_context import frame_near, home_manifest, location_at

logger = logging.getLogger(__name__)

TICK_SECONDS = 0.1
SCENARIO_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ScenarioNotFound(Exception):
    pass


class RunAlreadyActive(Exception):
    def __init__(self, session_id: str):
        super().__init__(session_id)
        self.session_id = session_id


class LiveRunEnded(Exception):
    pass


# A live session has no measured position (MVP2.md, Mục tiêu và phạm vi).
# MVP2.md M6: data is labelled by where it came from, so a recording replayed through the
# fake device is never shown as this project's ESP32.
LIVE_SOURCE_LABELS = {"esp32": "ESP32 waist IMU", "fake_esp32": "Simulated device (recorded scenario)"}
UNKNOWN_LOCATION = {"source": "Unknown", "x": None, "z": None, "zone_id": None, "zone_label": None, "fall_exposure": False}
JOURNAL_COLUMNS = ["source_ms", "seq", "device_ms", "received_epoch_ms", "ax", "ay", "az", "gx", "gy", "gz", "svm"]


@dataclass
class LiveRun:
    """In-memory state of one live session. `ingest_lock` orders batches; the run lock guards DB writes."""

    patient_id: int
    device_id: str
    wearer_cancel: float
    monitor: StreamMonitor
    journal: Path
    stream: LiveStream | None = None
    incident_rows: dict = field(default_factory=dict)
    ingest_lock: threading.Lock = field(default_factory=threading.Lock)


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


def _persist_events(db, session_id, patient_id, events, incident_rows, wearer_cancel) -> list[int]:
    """Stores what StreamMonitor reported; returns the ids of incidents opened now. Caller holds the run lock."""
    opened = []
    for event in events:
        if event is None:
            continue
        inc = event.incident
        if event.kind == "opened":
            row = AlertEvent(
                patient_id=patient_id,
                timestamp=_utcnow(),
                alert_type="FALL",
                confidence=inc.max_fall_probability,
                peak_svm=inc.peak_svm,
                is_acknowledged=False,
                notification_status="pending",
                spatial_state="awaiting_wearer",
                wearer_deadline_at=_utcnow() + timedelta(seconds=wearer_cancel),
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
    return opened


class SpatialRunManager:
    def __init__(
        self,
        *,
        realtime: bool = True,
        run_timeout_seconds: float = SPATIAL_RUN_TIMEOUT_SECONDS,
        wearer_cancel_seconds: float = WEARER_CANCEL_WINDOW_SECONDS,
        session_factory=None,
        detector: FallDetectionService = fall_detection_service,
        segmenter=None,
        chat=None,
        analysis_timeout_seconds: float = ANALYSIS_TIMEOUT_SECONDS,
        caregiver_ack_seconds: float = CAREGIVER_ACK_TIMEOUT_SECONDS,
        advisory_cooldown_seconds: float = ADVISORY_COOLDOWN_SECONDS,
        live_wearer_cancel_seconds: float = LIVE_WEARER_CANCEL_SECONDS,
        live_run_timeout_seconds: float = LIVE_RUN_TIMEOUT_SECONDS,
        live_data_dir: Path | None = None,
    ) -> None:
        self._realtime = realtime
        self._run_timeout = run_timeout_seconds
        self._wearer_cancel = wearer_cancel_seconds
        self._session_factory = session_factory
        self._detector = detector
        self._segment = segmenter or sam_client.segment
        self._chat = chat or muse_client.chat
        self._analysis_timeout = analysis_timeout_seconds
        self._caregiver_ack = caregiver_ack_seconds
        self._advisory_cooldown_ms = advisory_cooldown_seconds * 1000
        self._runs: dict[str, asyncio.Task] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._stop_requested: dict[str, asyncio.Event] = {}
        self._dispatches: set[asyncio.Task] = set()
        self._live_wearer_cancel = live_wearer_cancel_seconds
        self._live_run_timeout = live_run_timeout_seconds
        self._live_data_dir = live_data_dir
        self._live: dict[str, LiveRun] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def _db(self):
        # Looked up per call: tests swap database.SessionLocal after this module is imported.
        return (self._session_factory or database.SessionLocal)()

    def mark_interrupted_runs(self) -> int:
        """Startup: a Running row means its replay task died with the old process."""
        with self._db() as db:
            stale = db.query(SpatialSession).filter(SpatialSession.status == "Running").all()
            for s in stale:
                s.status, s.end_reason, s.ended_at = "Interrupted", "session_interrupted", _utcnow()
                _settle_analyses(db, s.id, "Failed", "session_interrupted")
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
        # Taken before the await: the replay may finish and clear its registry entries meanwhile.
        stop_requested, task = self._stop_requested.get(session_id), self._runs.get(session_id)
        await asyncio.to_thread(self._end_under, lock, session_id, "Stopped", "user_stop")
        if stop_requested is not None:
            stop_requested.set()
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)

    async def wait(self, session_id: str) -> None:
        """Wait for a run's replay task to finish (used by tests and shutdown)."""
        task = self._runs.get(session_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if self._dispatches:
            await asyncio.gather(*self._dispatches, return_exceptions=True)

    async def start_live(self, patient_id: int, device_id: str, source: str = "esp32") -> str:
        """Start a live session for one device. Samples arrive through ingest_live; the boot id is
        pinned by the first batch. One active run at a time, replay or live."""
        with self._db() as db:
            active = db.query(SpatialSession).filter(SpatialSession.status == "Running").first()
            if active is not None:
                raise RunAlreadyActive(active.id)
            session_id = uuid.uuid4().hex
            config = {
                "wearer_cancel_seconds": self._live_wearer_cancel,
                "caregiver_ack_seconds": self._caregiver_ack,
                "sampling_rate_hz": SAMPLING_RATE,
                "window_size": WINDOW_SIZE,
                "max_sample_gap_ms": MAX_SAMPLE_GAP_MS,
                "device_silent_seconds": LIVE_DEVICE_SILENT_SECONDS,
                "model_source": self._detector.model_source,
                "notification_mode": "simulated",
                "source": source,
                "source_label": LIVE_SOURCE_LABELS[source],
            }
            db.add(SpatialSession(
                id=session_id, kind="live", scenario_id=None, patient_id=patient_id, device_id=device_id,
                status="Running", started_at=_utcnow(), source_offset_ms=0, recording_ended=False,
                model_source=self._detector.model_source, config_snapshot=json.dumps(config),
                live_stats=json.dumps({"readiness": "waiting_for_device"}),
            ))
            db.commit()
        self._loop = asyncio.get_running_loop()
        self._locks[session_id] = threading.Lock()
        self._stop_requested[session_id] = asyncio.Event()
        self._live[session_id] = LiveRun(
            patient_id=patient_id, device_id=device_id, wearer_cancel=self._live_wearer_cancel,
            monitor=StreamMonitor(stream_key=f"session:{session_id}", detector=self._detector),
            journal=_journal_path(session_id, self._live_data_dir),
        )
        self._runs[session_id] = asyncio.create_task(self._live_watch(session_id))
        _hint(session_id, "started")
        return session_id

    async def _live_watch(self, session_id: str) -> None:
        """Holds a live session open until Stop or the run timeout; there is no replay to drive."""
        lock, stop_requested = self._locks[session_id], self._stop_requested[session_id]
        try:
            await asyncio.wait_for(stop_requested.wait(), self._live_run_timeout)
        except asyncio.TimeoutError:
            await asyncio.to_thread(self._end_under, lock, session_id, "Stopped", "timeout")
        finally:
            run = self._live.pop(session_id, None)
            if run is not None:
                with run.ingest_lock:
                    run.monitor.close()
            self._runs.pop(session_id, None)
            self._locks.pop(session_id, None)
            self._stop_requested.pop(session_id, None)

    def live_session_for(self, patient_id: int) -> str | None:
        return next((sid for sid, run in self._live.items() if run.patient_id == patient_id), None)

    def live_device(self, session_id: str) -> str | None:
        run = self._live.get(session_id)
        return run.device_id if run else None

    def ingest_live(self, session_id: str, boot_id: str, samples: list[DeviceSample],
                    dropped_at_source: int = 0, received_at: datetime | None = None) -> dict:
        """Called from the sensor route's worker thread. Feeds new samples to the session's detector,
        stores opened incidents, and hands their countdowns to the event loop after the commit."""
        run, lock = self._live.get(session_id), self._locks.get(session_id)
        if run is None or lock is None:
            raise LiveRunEnded(session_id)
        received_at = received_at or _utcnow()
        with run.ingest_lock:
            if run.stream is None:
                run.stream = LiveStream(boot_id)
            rejected = None
            try:
                steps = run.stream.accept(boot_id, samples, dropped_at_source)
            except BatchRejected as err:
                steps, rejected = [], err.reason
            stats_extra = {"last_rejection": rejected} if rejected else {}
            events = []
            for step in steps:
                if step.reset is not None:
                    run.monitor.reset_window()
                events.append(run.monitor.add_sample(step.source_ms, step.values))
            _append_journal(run.journal, steps, received_at)
            stats = {**run.stream.stats(), **stats_extra}
            with lock, self._db() as db:
                session = db.get(SpatialSession, session_id)
                if session is None or session.status != "Running":
                    raise LiveRunEnded(session_id)
                opened = _persist_events(db, session_id, run.patient_id, events, run.incident_rows, run.wearer_cancel)
                if stats["last_source_ms"] is not None:
                    session.source_offset_ms = stats["last_source_ms"]
                session.last_sample_at = received_at
                session.live_stats = json.dumps(stats)
                db.commit()
        _hint(session_id, "incident" if opened else "progress")
        for incident_id in opened:
            self._loop.call_soon_threadsafe(
                self._background, self._wearer_countdown(session_id, lock, incident_id, run.wearer_cancel))
        return {"session_id": session_id, "accepted": len(steps), "rejected": rejected,
                "readiness": stats["readiness"], "incidents_opened": opened}

    def _end_under(self, lock: threading.Lock, session_id: str, status: str, reason: str) -> bool:
        with lock:
            return self._end(session_id, status, reason)

    def _end(self, session_id: str, status: str, reason: str) -> bool:
        with self._db() as db:
            s = db.get(SpatialSession, session_id)
            if s is None or s.status != "Running":
                return False
            s.status, s.end_reason, s.ended_at = status, reason, _utcnow()
            settled = {"user_stop": "Cancelled", "timeout": "TimedOut"}.get(reason, "Failed")
            _settle_analyses(db, session_id, settled, reason)
            db.commit()
        _hint(session_id, status.lower())
        return True

    async def _replay(self, session_id: str, scenario: Scenario) -> None:
        lock, stop_requested = self._locks[session_id], self._stop_requested[session_id]
        monitor = StreamMonitor(stream_key=f"session:{session_id}", detector=self._detector)
        incident_rows: dict[int, int] = {}
        route_state = {"zone": None, "last_shown_ms": {}}
        loop = asyncio.get_running_loop()
        started = loop.time()
        # Latency is measured against time.monotonic(); uvloop's loop.time() uses a different epoch.
        started_mono = time.monotonic()
        cursor, total = 0, len(scenario.samples)
        try:
            while True:
                elapsed = loop.time() - started
                if elapsed >= self._run_timeout:
                    await asyncio.to_thread(self._end_under, lock, session_id, "Stopped", "timeout")
                    return
                due = min(total, int(elapsed * scenario.sample_rate_hz) + 1) if self._realtime else total
                if due > cursor:
                    ingested = await asyncio.to_thread(
                        self._ingest, lock, session_id, scenario, monitor, incident_rows, route_state, cursor, due,
                        started_mono if self._realtime else None,
                    )
                    if ingested is None:
                        return
                    opened, advisories = ingested
                    cursor = due
                    for advisory in advisories:
                        self._background(self._confirm_advisory(session_id, lock, scenario, *advisory))
                    for incident_id in opened:
                        self._background(self._wearer_countdown(session_id, lock, incident_id))
                        self._background(self._analyse(session_id, lock, scenario, incident_id))
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

    def _ingest(self, lock, session_id, scenario, monitor, incident_rows, route_state, start, end, started=None):
        """Runs in a worker thread. Returns (new incident ids, shown advisories), or None once the run has ended.

        With `started` (the run's monotonic start, realtime runs only), an opened incident's latency_ms is
        measured from when its opening sample was due at 1x to the commit that stores it.
        """
        events = [monitor.add_sample(int(scenario.source_ms[j]), scenario.samples[j]) for j in range(start, end)]
        entries = self._zone_entries(scenario, route_state, start, end)
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            if session is None or session.status != "Running":
                return None
            shown_advisories = []
            for source_ms, zone, shown in entries:
                action = SpatialAction(
                    session_id=session_id, kind="advisory", target=zone["id"], created_at=_utcnow(),
                    detail=json.dumps({"source_ms": source_ms, "shown": shown, "text": zone["advisory_text"],
                                       "source": "Authored location"}),
                )
                db.add(action)
                db.flush()
                if shown:
                    shown_advisories.append((action.id, source_ms, zone))
            opened = _persist_events(db, session_id, scenario.patient_id, events, incident_rows, self._wearer_cancel)
            session.source_offset_ms = int(scenario.source_ms[end - 1])
            session.recording_ended = end >= len(scenario.samples)
            if started is not None and opened:
                db.flush()
                committed_at = time.monotonic()
                for event in events:
                    if event is not None and event.kind == "opened":
                        due_at = started + event.incident.opened_at_ms / 1000
                        db.get(AlertEvent, incident_rows[event.incident.index]).latency_ms = round((committed_at - due_at) * 1000, 1)
            db.commit()
        _hint(session_id, "incident" if opened or entries else "progress")
        return opened, shown_advisories

    def _zone_entries(self, scenario, route_state, start, end) -> list[tuple[int, dict, bool]]:
        """Each sample's authored zone; an entry into a fall_exposure zone is shown unless that zone showed one within the cooldown."""
        entries = []
        for j in range(start, end):
            ms = int(scenario.source_ms[j])
            zone_id = location_at(scenario.meta, ms)["zone_id"]
            if zone_id == route_state["zone"]:
                continue
            route_state["zone"] = zone_id
            zone = next((z for z in home_manifest(scenario.meta["home"])["zones"] if z["id"] == zone_id), None)
            if not zone or not zone["fall_exposure"]:
                continue
            last = route_state["last_shown_ms"].get(zone_id)
            shown = last is None or ms - last >= self._advisory_cooldown_ms
            if shown:
                route_state["last_shown_ms"][zone_id] = ms
            entries.append((ms, zone, shown))
        return entries

    async def _confirm_advisory(self, session_id, lock, scenario, action_id, source_ms, zone) -> None:
        """SAM on the frame at zone entry. The HUD tip never waits for it."""
        try:
            analysis_id, request = await asyncio.to_thread(
                self._open_advisory_analysis, lock, session_id, scenario, action_id, source_ms, zone
            )
        except Exception:
            logger.exception("could not open the advisory SAM analysis for action %s", action_id)
            return
        if request is not None:
            await asyncio.to_thread(self._close_analysis, lock, session_id, analysis_id, await self._run_segment(request))

    def _open_advisory_analysis(self, lock, session_id, scenario, action_id, source_ms, zone):
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            if session is None or session.status != "Running":
                return None, None
            frame = frame_near(scenario.id, source_ms)
            analysis = SpatialAnalysis(session_id=session_id, advisory_id=action_id, kind="sam_advisory",
                                       status="Running", prompt=zone["hazard_prompt"], frame_id=frame.get("id"),
                                       created_at=_utcnow())
            request = self._segment_request(scenario, frame, analysis)
            db.add(analysis)
            db.commit()
            analysis_id = analysis.id
        _hint(session_id, "analysis")
        return analysis_id, request

    def _segment_request(self, scenario, frame, analysis):
        """(image, mime, prompt) for SAM, or None after marking the analysis Unavailable."""
        if frame["status"] != "Available":
            analysis.status, analysis.reason = "Unavailable", frame["reason"]
        elif not analysis.prompt:
            analysis.status, analysis.reason = "Unavailable", "the zone has no hazard prompt"
        else:
            path = SCENARIOS_DIR / scenario.id / frame["url"].split(f"/{scenario.id}/", 1)[1]
            try:
                return (_read_frame(path), "image/jpeg", analysis.prompt)
            except OSError as err:
                analysis.status, analysis.reason = "Unavailable", f"frame file unreadable: {type(err).__name__} {path.name}"
        analysis.finished_at = _utcnow()
        return None

    async def _run_segment(self, request) -> dict:
        try:
            # The budget covers the whole call; the worker thread may finish later, and
            # _close_analysis then finds the row TimedOut and drops the late answer.
            result = await asyncio.wait_for(asyncio.to_thread(self._segment, *request), self._analysis_timeout)
            return {
                "status": "Complete" if result.boxes else "NoMatch",
                "output_text": result.output_text,
                "boxes": json.dumps(result.boxes),
                "duration_ms": result.duration_ms,
                "model": result.model,
            }
        except asyncio.TimeoutError:
            return {"status": "TimedOut", "reason": f"no result within the {self._analysis_timeout} s analysis budget"}
        except sam_client.SamError as err:
            return {"status": err.status, "reason": err.reason}
        except Exception as err:
            logger.exception("SAM call failed")
            return {"status": "Failed", "reason": type(err).__name__}

    def _background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._dispatches.add(task)
        task.add_done_callback(self._dispatches.discard)

    async def _wearer_countdown(self, session_id: str, lock: threading.Lock, incident_id: int,
                                wearer_cancel: float | None = None) -> None:
        wearer_cancel = self._wearer_cancel if wearer_cancel is None else wearer_cancel
        await asyncio.sleep(wearer_cancel)
        if await asyncio.to_thread(self._alert_if_unanswered, lock, session_id, incident_id, wearer_cancel):
            await asyncio.sleep(self._caregiver_ack)
            await asyncio.to_thread(self._escalate_if_unacknowledged, lock, session_id, incident_id)

    def _escalate_if_unacknowledged(self, lock: threading.Lock, session_id: str, incident_id: int) -> None:
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            row = db.get(AlertEvent, incident_id)
            if session is None or session.status != "Running" or row.spatial_state != "alerted" or row.is_acknowledged:
                return
            db.add(SpatialAction(
                session_id=session_id, incident_id=incident_id, kind="escalate", target="contact_2",
                detail=json.dumps({"channel": "simulated", "after_caregiver_window_s": self._caregiver_ack}),
                created_at=_utcnow(),
            ))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return
        _hint(session_id, "action")

    async def acknowledge(self, incident_id: int, actor_id: str) -> dict | None:
        """The caregiver takes the incident. Returns None for an unknown incident."""
        with self._db() as db:
            row = db.get(AlertEvent, incident_id)
            if row is None or row.session_id is None:
                return None
            session_id, state = row.session_id, row.spatial_state
        lock = self._locks.get(session_id)
        if lock is None:
            return {"incident_id": incident_id, "applied": False, "incident_state": state, "reason": "run_ended"}
        return await asyncio.to_thread(self._record_ack, lock, session_id, incident_id, actor_id)

    def _record_ack(self, lock, session_id, incident_id, actor_id) -> dict:
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            row = db.get(AlertEvent, incident_id)
            result = {"incident_id": incident_id, "applied": False, "incident_state": row.spatial_state}
            if session is None or session.status != "Running":
                return {**result, "reason": "run_ended"}
            if row.spatial_state == "cancelled_by_wearer":
                return {**result, "reason": "cancelled_by_wearer"}
            if row.is_acknowledged:
                return {**result, "reason": "already_recorded"}
            row.is_acknowledged, row.acknowledged_at = True, _utcnow()
            db.add(SpatialAction(session_id=session_id, incident_id=incident_id, kind="ack", target=actor_id,
                                 detail=json.dumps({"by": actor_id}), created_at=_utcnow()))
            db.commit()
        _hint(session_id, "action")
        return {**result, "applied": True, "reason": None}

    def _alert_if_unanswered(self, lock: threading.Lock, session_id: str, incident_id: int, wearer_cancel: float) -> bool:
        """Simulated sink: the notification is the action-log row. Nothing leaves the process."""
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            row = db.get(AlertEvent, incident_id)
            if session is None or session.status != "Running" or row.spatial_state != "awaiting_wearer":
                return False
            row.spatial_state = "alerted"
            row.notification_status = "simulated"
            row.notification_sent_at = _utcnow()
            row.caregiver_deadline_at = _utcnow() + timedelta(seconds=self._caregiver_ack)
            db.add(SpatialAction(
                session_id=session_id,
                incident_id=incident_id,
                kind="notify",
                target="contact_1",
                detail=json.dumps({"channel": "simulated", "after_wearer_window_s": wearer_cancel}),
                created_at=_utcnow(),
            ))
            db.commit()
        _hint(session_id, "action")
        return True

    async def _analyse(self, session_id: str, lock: threading.Lock, scenario: Scenario, incident_id: int) -> None:
        try:
            analysis_id, request = await asyncio.to_thread(self._open_analysis, lock, session_id, scenario, incident_id)
        except Exception:
            logger.exception("could not open the SAM analysis for incident %s", incident_id)
            return
        if request is None:
            if analysis_id is not None:
                await self._explain(session_id, lock, scenario, incident_id)
            return
        outcome = await self._run_segment(request)
        await asyncio.to_thread(self._close_analysis, lock, session_id, analysis_id, outcome)
        await self._explain(session_id, lock, scenario, incident_id)

    async def _explain(self, session_id: str, lock: threading.Lock, scenario: Scenario, incident_id: int) -> None:
        try:
            analysis_id, bundle = await asyncio.to_thread(self._open_explanation, lock, session_id, scenario, incident_id)
        except Exception:
            logger.exception("could not open the agent analysis for incident %s", incident_id)
            return
        if bundle is None:
            return
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(run_agent, bundle, self._chat, max_tool_calls=AGENT_MAX_TOOL_CALLS,
                                  timeout_seconds=self._analysis_timeout),
                self._analysis_timeout,
            )
            outcome = {
                "status": result.status,
                "reason": result.reason,
                "output_text": json.dumps(result.result) if result.result is not None else None,
                "tool_log": json.dumps({"calls": result.tool_log, "usage": result.usage, "rejected_answer": result.raw_answer,
                                        "context": {"incident_state": bundle["incident"]["state"]}}),
            }
        except asyncio.TimeoutError:
            outcome = {"status": "TimedOut", "reason": f"no result within the {self._analysis_timeout} s analysis budget"}
        except Exception as err:
            logger.exception("agent analysis %s failed", analysis_id)
            outcome = {"status": "Failed", "reason": type(err).__name__}
        outcome["duration_ms"] = round((time.perf_counter() - started) * 1000)
        await asyncio.to_thread(self._close_analysis, lock, session_id, analysis_id, outcome)

    def _open_explanation(self, lock, session_id, scenario, incident_id):
        """Records the agent analysis and returns (id, evidence bundle), or (None, None) once the run has ended."""
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            if session is None or session.status != "Running":
                return None, None
            row = db.get(AlertEvent, incident_id)
            sam = (
                db.query(SpatialAnalysis)
                .filter(SpatialAnalysis.incident_id == incident_id, SpatialAnalysis.kind == "sam")
                .order_by(SpatialAnalysis.id.desc())
                .first()
            )
            others = (
                db.query(AlertEvent)
                .filter(AlertEvent.session_id == session_id, AlertEvent.id != incident_id)
                .all()
            )
            bundle = _evidence_bundle(scenario, row, sam, others)
            analysis = SpatialAnalysis(session_id=session_id, incident_id=incident_id, kind="agent",
                                       status="Running", model=AGENT_MODEL, created_at=_utcnow())
            db.add(analysis)
            db.commit()
            analysis_id = analysis.id
        _hint(session_id, "analysis")
        return analysis_id, bundle

    def _open_analysis(self, lock, session_id, scenario, incident_id):
        """Records the analysis and returns (id, segment args), or (id, None) when there is nothing to send."""
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            if session is None or session.status != "Running":
                return None, None
            row = db.get(AlertEvent, incident_id)
            location = location_at(scenario.meta, row.source_start_ms)
            zone = next((z for z in home_manifest(scenario.meta["home"])["zones"] if z["id"] == location["zone_id"]), None)
            frame = frame_near(scenario.id, row.source_start_ms)
            analysis = SpatialAnalysis(
                session_id=session_id, incident_id=incident_id, kind="sam", status="Running",
                prompt=(zone or {}).get("hazard_prompt"), frame_id=frame.get("id"), created_at=_utcnow(),
            )
            request = self._segment_request(scenario, frame, analysis)
            db.add(analysis)
            db.commit()
            analysis_id = analysis.id
        _hint(session_id, "analysis")
        return analysis_id, request

    def _close_analysis(self, lock, session_id, analysis_id, outcome) -> None:
        with lock, self._db() as db:
            analysis = db.get(SpatialAnalysis, analysis_id)
            if analysis.status != "Running":
                logger.info("SAM analysis %s answered after it was %s; result not stored", analysis_id, analysis.status)
                return
            for key, value in outcome.items():
                setattr(analysis, key, value)
            analysis.finished_at = _utcnow()
            db.commit()
        _hint(session_id, "analysis")

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


def _fixes_library() -> list[dict]:
    return json.loads(FIXES_LIBRARY_PATH.read_text())["fixes"]


def _evidence_bundle(scenario: Scenario, row, sam, others) -> dict:
    """What the agent's tools may read: stored evidence only, nothing it has to trust from itself."""
    window = (scenario.source_ms >= row.source_start_ms - 1000) & (scenario.source_ms <= row.source_start_ms + 5000)
    samples = scenario.samples[window]
    tail = samples[-100:]
    sam_json = _analysis_json(sam) if sam else None
    if sam_json and sam_json["boxes"]:
        b = sam_json["boxes"][0]
        sam_json["box_area_fraction"] = round((b["x2"] - b["x1"]) * (b["y2"] - b["y1"]) / (b["w"] * b["h"]), 3)
    return {
        "incident": {"id": row.id, "source_start_ms": row.source_start_ms, "state": row.spatial_state,
                     "decision_source": row.decision_source, "fall_probability": row.confidence},
        "location": location_at(scenario.meta, row.source_start_ms),
        "imu_summary": {
            "window_s": [round((row.source_start_ms - 1000) / 1000, 2), round((row.source_start_ms + 5000) / 1000, 2)],
            "peak_svm_g": round(float(samples[:, 6].max()), 2),
            "peak_gyro_deg_s": round(float(np.linalg.norm(samples[:, 3:6], axis=1).max()), 1),
            "svm_std_last_2s_g": round(float(tail[:, 6].std()), 3),
        },
        "frame": frame_near(scenario.id, row.source_start_ms),
        "sam": sam_json,
        "recent": [{"id": o.id, "source_start_ms": o.source_start_ms,
                    "zone_id": location_at(scenario.meta, o.source_start_ms)["zone_id"]} for o in others],
        "fixes": _fixes_library(),
    }


def _append_journal(path: Path, steps, received_at: datetime) -> None:
    """The live session's own IMU record; incident_detail reads it back."""
    if not steps:
        return
    received = round(received_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        if new:
            f.write(",".join(JOURNAL_COLUMNS) + "\n")
        for st in steps:
            f.write(f"{st.source_ms},{st.seq},{st.device_ms},{received}," + ",".join(f"{float(v):.5f}" for v in st.values) + "\n")


def _journal_path(session_id: str, live_data_dir: Path | None = None) -> Path:
    # Looked up per call so tests can point LIVE_DATA_DIR at a temporary folder.
    return (live_data_dir or LIVE_DATA_DIR) / session_id / "imu.csv"


def _live_json(s) -> dict:
    stats = json.loads(s.live_stats or "{}")
    received_any = s.last_sample_at is not None
    silent = s.status == "Running" and (
        not received_any or (_utcnow() - s.last_sample_at).total_seconds() > LIVE_DEVICE_SILENT_SECONDS)
    if stats.get("last_rejection") == "boot_changed":
        # The device restarted. MVP2_ARCHITECTURE.md: reconnect before accepting again; for now
        # that means Stop and a new live session.
        label = "Device restarted: start a new live session"
    elif silent:
        label = "Device silent" if received_any else "Waiting for device"
    else:
        label = {"warming_up": "Warming up", "detection_unavailable": "Detection unavailable",
                 "ready": "Detector ready"}.get(stats.get("readiness"), "Waiting for device")
    return {"patient_id": s.patient_id, "device_id": s.device_id, "last_sample_at": s.last_sample_at,
            "device_silent": silent, "status_label": label,
            "config": json.loads(s.config_snapshot or "{}"), **stats}


def _read_journal(path: Path) -> np.ndarray:
    if not path.exists():
        return np.zeros((0, len(JOURNAL_COLUMNS)))
    return np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)


def _read_frame(path) -> bytes:
    return path.read_bytes()


def _settle_analyses(db, session_id: str, status: str, reason: str) -> None:
    for analysis in db.query(SpatialAnalysis).filter(
        SpatialAnalysis.session_id == session_id, SpatialAnalysis.status.in_(("Pending", "Running"))
    ):
        analysis.status, analysis.reason, analysis.finished_at = status, reason, _utcnow()


def _analysis_json(a) -> dict:
    return {
        "id": a.id, "kind": a.kind, "status": a.status, "reason": a.reason, "prompt": a.prompt,
        "frame_id": a.frame_id, "model": a.model, "duration_ms": a.duration_ms,
        "boxes": json.loads(a.boxes) if a.boxes else [], "output_text": a.output_text,
        "tool_log": json.loads(a.tool_log) if a.tool_log else None,
        "created_at": a.created_at, "finished_at": a.finished_at,
    }


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
        live = s.kind == "live"
        scenario_meta = None if live else load_scenario(s.scenario_id).meta
        where = (lambda ms: UNKNOWN_LOCATION) if live else (lambda ms: location_at(scenario_meta, ms))
        analysis_statuses: dict[int, list[str]] = {}
        advisory_sam: dict[int, SpatialAnalysis] = {}
        for a in db.query(SpatialAnalysis).filter(SpatialAnalysis.session_id == session_id).order_by(SpatialAnalysis.id):
            if a.advisory_id is not None:
                advisory_sam[a.advisory_id] = a
            else:
                analysis_statuses.setdefault(a.incident_id, []).append(a.status)
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
            "kind": s.kind,
            "scenario_id": s.scenario_id,
            "status": s.status,
            "end_reason": s.end_reason,
            "started_at": s.started_at,
            "ended_at": s.ended_at,
            "source_offset_ms": s.source_offset_ms,
            "recording_ended": s.recording_ended,
            "model_source": s.model_source,
            "scenario": None if live else {k: scenario_meta[k] for k in ("id", "title", "duration_ms", "home", "location", "route")},
            "live": _live_json(s) if live else None,
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
                    "location": where(i.source_start_ms),
                    "analysis_status": ",".join(analysis_statuses.get(i.id, [])) or None,
                    "latency_ms": i.latency_ms,
                    "acknowledged_at": i.acknowledged_at,
                    "wearer_deadline_at": i.wearer_deadline_at,
                    "caregiver_deadline_at": i.caregiver_deadline_at,
                }
                for i in incidents
            ],
            "actions": _actions_json(actions),
            "advisories": [] if live else [_advisory_json(a, advisory_sam.get(a.id), scenario_meta) for a in actions if a.kind == "advisory"],
        }


def _advisory_json(action, sam, scenario_meta) -> dict:
    detail = json.loads(action.detail)
    zone = next(z for z in home_manifest(scenario_meta["home"])["zones"] if z["id"] == action.target)
    return {
        "action_id": action.id,
        "zone_id": action.target,
        "zone_label": zone["label"],
        "source_ms": detail["source_ms"],
        "shown": detail["shown"],
        "text": detail["text"],
        "location_source": detail["source"],
        "frame_id": sam.frame_id if sam else None,
        "sam_status": sam.status if sam else None,
        "sam_objects": len(json.loads(sam.boxes)) if sam and sam.boxes else 0,
        "sam_reason": sam.reason if sam else None,
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
        analyses = (
            db.query(SpatialAnalysis)
            .filter(SpatialAnalysis.incident_id == incident_id)
            .order_by(SpatialAnalysis.id)
            .all()
        )
        live = session.kind == "live"
        if live:
            table = _read_journal(_journal_path(session.id))
            source_ms, samples = table[:, 0].astype(np.int64), table[:, 4:]
            source = json.loads(session.config_snapshot or "{}").get("source", "esp32")
            location, imu_source, segments = UNKNOWN_LOCATION, LIVE_SOURCE_LABELS[source], []
            frame = {"status": "Unavailable", "reason": "No camera frames in a live session yet",
                     "source": "Phone camera at eye height (glasses proxy)"}
        else:
            scenario = load_scenario(session.scenario_id)
            source_ms, samples = scenario.source_ms, scenario.samples
            location, imu_source = location_at(scenario.meta, row.source_start_ms), "Scenario-paired recording"
            segments, frame = scenario.meta["segments"], frame_near(session.scenario_id, row.source_start_ms)
        half = IMU_DETAIL_MAX_SECONDS * 1000 // 2
        lo, hi = row.source_start_ms - half, row.source_start_ms + half
        mask = (source_ms >= lo) & (source_ms <= hi)
        imu = [[int(ms), *[round(float(v), 4) for v in sample]] for ms, sample in zip(source_ms[mask], samples[mask])]
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
            "acknowledged_at": row.acknowledged_at,
            "wearer_deadline_at": row.wearer_deadline_at,
            "caregiver_deadline_at": row.caregiver_deadline_at,
            "kind": session.kind,
            "location": location,
            "frame": frame,
            "imu": {
                "source": imu_source,
                "columns": ["source_ms", "ax", "ay", "az", "gx", "gy", "gz", "svm"],
                "units": {"acc": "g", "gyro": "deg/s", "svm": "g"},
                "segments": segments,
                "samples": imu,
            },
            "actions": _actions_json(actions),
            "analyses": [_analysis_json(a) for a in analyses],
            "recommendations": _recommendations(analyses),
        }


def _recommendations(analyses) -> list[dict]:
    """Library entries for the fixes a Complete agent analysis chose, in its order."""
    agent = next((a for a in reversed(analyses) if a.kind == "agent" and a.status == "Complete"), None)
    if agent is None or not agent.output_text:
        return []
    library = {f["id"]: f for f in _fixes_library()}
    return [library[r] for r in json.loads(agent.output_text).get("recommendation_ids", []) if r in library]


spatial_runs = SpatialRunManager()
