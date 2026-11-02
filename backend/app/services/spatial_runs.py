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
import concurrent.futures
import hashlib
import json
import logging
import re
import threading
import time
import secrets
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
    LATE_HELP_GRACE_SECONDS,
    LIVE_DATA_DIR,
    LIVE_DEVICE_SILENT_SECONDS,
    LIVE_RUN_TIMEOUT_SECONDS,
    LIVE_WEARER_CANCEL_SECONDS,
    RESPONDER_MAX_TOOL_CALLS,
    RESPONDER_TIMEOUT_SECONDS,
    SAMPLING_RATE,
    SCENARIOS_DIR,
    SPATIAL_RUN_TIMEOUT_SECONDS,
    TELEGRAM_SEND_TIMEOUT_SECONDS,
    WEARER_CANCEL_WINDOW_SECONDS,
    WINDOW_SIZE,
)
from app import database
from app.models.schemas import AlertEvent, SpatialAction, SpatialAnalysis, SpatialSession
from app.services.broadcaster import broadcaster
from app.services.fall_detection import FallDetectionService, fall_detection_service
from app.services import muse_client, sam_client
from app.services.incident_agent import run_agent
from app.services.clock_sync import best_mapping
from app.services.incident_policy import ReplyDecision, ALERTED, CANCELLED, decide_deadline, decide_reply, late_ok_targets
from app.services.notifier import SimulatedNotifier
from app.services.notify_templates import render
from app.services.speech import SpeechJob, SpeechWorker, whisper_model
from app.services.responder import ResponderTools, check_say, check_send, run_responder
from app.services.live_frames import STAIRS_PROMPT, Frame, FrameStore, SamSlot, StairsGate, scene_status
from app.services.wearer_channel import GUARD_MS, TURN_MS, WearerChannel
from app.services.wearer_reply import classify_reply
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


class DrillRefused(Exception):
    """Why a voice drill cannot open now: not_a_drill_session, busy or run_ended."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class LiveNotReady(Exception):
    """A live session that sends for real needs both Telegram contacts (MVP2.md, Live ready)."""


# A live session has no measured position (MVP2.md, Mục tiêu và phạm vi).
# MVP2.md M6: data is labelled by where it came from, so a recording replayed through the
# fake device is never shown as this project's ESP32.
LIVE_SOURCE_LABELS = {"esp32": "ESP32 waist IMU", "fake_esp32": "Simulated device (recorded scenario)",
                      "drill": "Voice drill (no device, no fall detected)",
                      "camera_trial": "Camera trial (no device, no fall detected)"}
# echo_prompts: the phone asks and nobody answers; echo_cancel: the laptop sends the OK and nobody answers
# the help-only turns (MVP2.md, M7). They measure whether the phone hears its own voice.
ECHO_KINDS = ("echo_prompts", "echo_cancel")
ECHO_VOLUMES = ("low", "mid", "high")
ECHO_MIN_WINDOW_S = 30  # the 20 s reminder needs time left to be heard
DRILL_EXPECTED = ("ok", "help", "ambiguous", "silence", "loudspeaker", *ECHO_KINDS)
UNKNOWN_LOCATION = {"source": "Unknown", "x": None, "z": None, "zone_id": None, "zone_label": None, "fall_exposure": False}
# session_ms is device time mapped onto the session clock, nan while the device is unsynchronized.
JOURNAL_COLUMNS = ["source_ms", "seq", "device_ms", "received_epoch_ms", "session_ms", "ax", "ay", "az", "gx", "gy", "gz", "svm"]


@dataclass(frozen=True)
class IncidentConfig:
    """Pinned per session at Start (MVP2.md, Tham số P0)."""

    wearer_cancel: float
    caregiver_ack: float
    grace: float


@dataclass
class LiveRun:
    """In-memory state of one live session. `ingest_lock` orders batches; the run lock guards DB writes."""

    patient_id: int
    device_id: str
    wearer_cancel: float
    monitor: StreamMonitor
    journal: Path
    started_mono_ms: float
    drill: bool = False
    record_frames: bool = False  # keep every camera frame on disk (S3 recording, with the person's consent)
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
        late_help_grace_seconds: float = LATE_HELP_GRACE_SECONDS,
        notifier=None,
        send_timeout_seconds: float = TELEGRAM_SEND_TIMEOUT_SECONDS,
        transcribe=None,
        preload_whisper: bool = False,
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
        self._clocks: dict[tuple[str, str], dict] = {}  # (device_id, boot_id) -> latest mapping
        self._loop: asyncio.AbstractEventLoop | None = None
        self._grace = late_help_grace_seconds
        self._notifier_override = notifier  # tests: one notifier for every session
        self._telegram = None               # set by use_telegram() when the bot is configured
        self._session_notifiers: dict[str, object] = {}
        self._send_timeout = send_timeout_seconds
        self._incident_tasks: dict[str, set[asyncio.Task]] = {}
        self._incident_cfg: dict[str, IncidentConfig] = {}
        self._channels: dict[str, WearerChannel] = {}
        self._frames: dict[str, FrameStore] = {}
        self._sam_slots: dict[str, SamSlot] = {}
        # Live SAM calls run on one thread. A call that timed out cannot be stopped, so it keeps the thread and
        # new requests are refused until it ends, instead of running beside it.
        self._sam_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="sam")
        self._sam_work: concurrent.futures.Future | None = None
        self._stairs: dict[str, StairsGate] = {}
        # Acks outlive the run's registry: a contact may tap long after the last task ended.
        self._idle_lock = threading.Lock()
        self._phone_clocks: dict[str, dict] = {}
        self._speech = SpeechWorker(transcribe) if transcribe else SpeechWorker()
        self._preload_whisper = preload_whisper and transcribe is None

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

    def use_telegram(self, notifier) -> None:
        self._telegram = notifier

    def _notifier_for(self, session_id: str):
        return self._session_notifiers.get(session_id) or SimulatedNotifier()

    def _pick_notifier(self, kind: str, notification_mode: str):
        """Replay never sends for real. A live session sends through Telegram unless simulated was
        chosen explicitly, and then only with both contacts configured."""
        if self._notifier_override is not None:
            return self._notifier_override
        if kind == "replay" or notification_mode == "simulated":
            return SimulatedNotifier()
        if self._telegram is None or not {"contact_1", "contact_2"} <= self._telegram.configured_contacts():
            raise LiveNotReady("contact setup")
        return self._telegram

    async def telegram_ack(self, incident_id: int, action_id: int, contact: str, message_id) -> dict | None:
        """A tap on Acknowledge counts when its button belongs to an alert sent to this contact about
        this incident. The button names that send, so a tap still counts when Telegram delivered the
        message but its reply (with the message id) was lost; when the id is known it must match."""
        with self._db() as db:
            action = db.get(SpatialAction, action_id)
            ours = (action is not None and action.incident_id == incident_id and action.target == contact
                    and action.kind in ("notify", "escalate"))
            known = json.loads(action.detail).get("message_id") if ours else None
        if not ours or (known is not None and message_id != known):
            return {"incident_id": incident_id, "applied": False, "incident_state": None, "reason": "not_this_message"}
        return await self.acknowledge(incident_id, f"telegram:{contact}")

    def _refuse_if_busy(self, db) -> None:
        """One run at a time, and no new run while the last one still has an incident in progress."""
        active = db.query(SpatialSession).filter(SpatialSession.status == "Running").first()
        if active is not None:
            raise RunAlreadyActive(active.id)
        finishing = next((sid for sid, tasks in self._incident_tasks.items() if tasks), None)
        if finishing is not None:
            raise RunAlreadyActive(finishing)

    def active_live_session(self) -> str | None:
        """The running live session, if any: a page that was refreshed finds its session again here."""
        with self._db() as db:
            row = db.query(SpatialSession).filter(SpatialSession.status == "Running", SpatialSession.kind == "live").first()
            return None if row is None else row.id

    async def start(self, scenario_id: str) -> str:
        scenario = load_scenario(scenario_id)
        notifier = self._pick_notifier("replay", "simulated")
        with self._db() as db:
            self._refuse_if_busy(db)
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
        self._incident_cfg[session_id] = IncidentConfig(self._wearer_cancel, self._caregiver_ack, self._grace)
        self._session_notifiers[session_id] = notifier
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
        """Wait for a run's replay task and its incidents to finish (used by tests and shutdown)."""
        task = self._runs.get(session_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        while self._incident_tasks.get(session_id):
            await asyncio.gather(*list(self._incident_tasks[session_id]), return_exceptions=True)
        if self._dispatches:
            await asyncio.gather(*self._dispatches, return_exceptions=True)

    async def start_live(self, patient_id: int, device_id: str, source: str = "esp32",
                         notification_mode: str = "telegram", wearer_cancel_seconds: float | None = None,
                         record_frames: bool = False) -> str:
        """Start a live session for one device. Samples arrive through ingest_live; the boot id is
        pinned by the first batch. One active run at a time, replay or live.

        source "drill" is a voice drill for the M7 trials: no device, incidents opened by hand with
        open_drill, never sent anywhere (simulated is forced), and the window may be shortened.
        source "camera_trial" is the same without incidents: only the camera runs, for the M3 trials."""
        drill = source == "drill"
        if source in ("drill", "camera_trial"):
            notification_mode = "simulated"
        wearer_cancel = (wearer_cancel_seconds if drill and wearer_cancel_seconds else self._live_wearer_cancel)
        notifier = self._pick_notifier("live", notification_mode)
        with self._db() as db:
            self._refuse_if_busy(db)
            session_id = uuid.uuid4().hex
            config = {
                "wearer_cancel_seconds": wearer_cancel,
                "caregiver_ack_seconds": self._caregiver_ack,
                "late_help_grace_seconds": self._grace,
                "sampling_rate_hz": SAMPLING_RATE,
                "window_size": WINDOW_SIZE,
                "max_sample_gap_ms": MAX_SAMPLE_GAP_MS,
                "device_silent_seconds": LIVE_DEVICE_SILENT_SECONDS,
                "model_source": self._detector.model_source,
                "notification_mode": notifier.channel,
                "source": source,
                "source_label": LIVE_SOURCE_LABELS[source],
                "record_frames": record_frames,
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
        self._incident_cfg[session_id] = IncidentConfig(wearer_cancel, self._caregiver_ack, self._grace)
        self._session_notifiers[session_id] = notifier
        self._channels[session_id] = WearerChannel()
        self._frames[session_id], self._stairs[session_id] = FrameStore(), StairsGate()
        self._live[session_id] = LiveRun(
            patient_id=patient_id, device_id=device_id, wearer_cancel=wearer_cancel, drill=drill,
            record_frames=record_frames,
            monitor=StreamMonitor(stream_key=f"session:{session_id}", detector=self._detector),
            journal=_journal_path(session_id, self._live_data_dir),
            started_mono_ms=time.monotonic() * 1000,
        )
        self._runs[session_id] = asyncio.create_task(self._live_watch(session_id))
        if self._preload_whisper:
            # The first turn after a fall should not wait for the model to load (2.5 s in the first live run).
            self._background(asyncio.to_thread(whisper_model))
        _hint(session_id, "started")
        return session_id

    async def open_drill(self, session_id: str, expected: str, person: str, volume: str | None = None,
                         room: str | None = None) -> dict:
        """Opens an incident by hand in a drill session so the voice path can be measured without a
        fall. The incident is labelled voice_drill and carries what the person was told to do; the
        wearer window, listening, cancel, HELP and grace all run through the same policy as a real
        incident. One wearer-facing incident at a time: the phone listens for one."""
        run, lock, cfg = self._live.get(session_id), self._locks.get(session_id), self._incident_cfg.get(session_id)
        if run is None or lock is None or cfg is None:
            raise DrillRefused("run_ended")
        if not run.drill:
            raise DrillRefused("not_a_drill_session")
        if expected not in DRILL_EXPECTED:
            raise DrillRefused("unknown_expected")
        if expected in ECHO_KINDS:
            if volume not in ECHO_VOLUMES:
                raise DrillRefused("volume_required")
            if not room or not room.strip():
                raise DrillRefused("room_required")
            if expected == "echo_prompts" and cfg.wearer_cancel < ECHO_MIN_WINDOW_S:
                raise DrillRefused("window_too_short")

        def create():
            with lock, self._db() as db:
                session = db.get(SpatialSession, session_id)
                if session is None or session.status != "Running":
                    raise DrillRefused("run_ended")
                now = _utcnow()
                busy = db.query(AlertEvent).filter(
                    AlertEvent.session_id == session_id,
                    AlertEvent.spatial_state.in_(("awaiting_wearer", "cancelled_by_wearer")),
                    AlertEvent.wearer_deadline_at > now).first()
                if busy is not None:
                    raise DrillRefused("busy")
                row = AlertEvent(
                    patient_id=run.patient_id, timestamp=now, alert_type="FALL", confidence=0.0, peak_svm=0.0,
                    is_acknowledged=False, notification_status="pending", spatial_state="awaiting_wearer",
                    wearer_deadline_at=now + timedelta(seconds=cfg.wearer_cancel), session_id=session_id,
                    source_start_ms=session.source_offset_ms, source_end_ms=session.source_offset_ms,
                    decision_source="voice_drill")
                db.add(row)
                db.flush()
                db.add(SpatialAction(session_id=session_id, incident_id=row.id, kind="drill", target=person,
                                     created_at=now, detail=json.dumps({
                                         "expected": expected, "person": person,
                                         **({"volume": volume, "room": room.strip(), "guard_ms": GUARD_MS}
                                            if expected in ECHO_KINDS else {})})))
                db.commit()
                return row.id, row.wearer_deadline_at
        incident_id, deadline = await asyncio.to_thread(create)
        self._track(session_id, self._incident_timeline(session_id, lock, incident_id))
        _hint(session_id, "incident")
        return {"incident_id": incident_id, "deadline": deadline.isoformat(), "window_s": cfg.wearer_cancel}

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
            self._release_if_idle(session_id)

    def record_clock(self, device_id: str, boot_id: str, rounds: list[dict]) -> dict | None:
        """Stores the device's latest clock mapping. A new mapping applies to samples received
        from now on; samples already journalled keep the mapping they were stored with."""
        mapping = best_mapping(rounds)
        if mapping is not None:
            self._clocks[(device_id, boot_id)] = {**mapping, "synced_at": _utcnow().isoformat(timespec="seconds")}
        return mapping

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
            clock = self._clocks.get((run.device_id, boot_id))
            session_offset = clock["offset_ms"] - run.started_mono_ms if clock else None
            _append_journal(run.journal, steps, received_at, session_offset)
            stats = {**run.stream.stats(), **stats_extra, "clock": clock or {"status": "Unsynchronized"}}
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
            self._loop.call_soon_threadsafe(self._track, session_id, self._incident_timeline(session_id, lock, incident_id))
            self._loop.call_soon_threadsafe(self._track, session_id, self._live_scene(session_id, lock, incident_id))
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
                        self._track(session_id, self._incident_timeline(session_id, lock, incident_id))
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
            self._release_if_idle(session_id)

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

    async def _live_call(self, request):
        if self._sam_work is not None and not self._sam_work.done():
            raise sam_client.SamError("Failed", "an earlier SAM request is still running")
        work = self._sam_work = self._sam_executor.submit(self._segment, *request)
        work.add_done_callback(lambda f: f.cancelled() or f.exception())  # an answer nobody waits for is not an error
        return await asyncio.shield(asyncio.wrap_future(work))

    async def _run_segment(self, request, call=None) -> dict:
        try:
            # The budget covers the whole call; the worker thread may finish later, and
            # _close_analysis then finds the row TimedOut and drops the late answer.
            result = await asyncio.wait_for(call(request) if call else asyncio.to_thread(self._segment, *request),
                                            self._analysis_timeout)
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

    # --- Incident lifecycle (docs/spatial/MVP2.md, "Policy do code quyết định") ---------------
    # Every decision is taken under the run lock and committed before any message goes out.
    # These tasks outlive Stop: an opened incident finishes its window, sends and escalation.

    def _track(self, session_id: str, coro) -> None:
        task = asyncio.create_task(coro)
        tasks = self._incident_tasks.setdefault(session_id, set())
        tasks.add(task)

        def done(t):
            tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.error("incident task for %s failed", session_id, exc_info=t.exception())
            self._release_if_idle(session_id)
        task.add_done_callback(done)

    def _release_if_idle(self, session_id: str) -> None:
        if session_id in self._runs or self._incident_tasks.get(session_id):
            return
        for registry in (self._incident_tasks, self._locks, self._stop_requested, self._incident_cfg,
                         self._session_notifiers, self._channels, self._phone_clocks, self._frames, self._stairs,
                         self._sam_slots):
            registry.pop(session_id, None)

    def _dispatch(self, session_id: str, lock: threading.Lock, follow: dict) -> None:
        for action_id in follow.get("deliver", []):
            self._track(session_id, self._deliver(session_id, lock, action_id))
        if follow.get("caregiver_timer"):
            self._track(session_id, self._caregiver_timer(session_id, lock, follow["caregiver_timer"]))
        if follow.get("responder"):
            self._track(session_id, self._respond(session_id, lock, follow["responder"]))

    async def _incident_timeline(self, session_id: str, lock: threading.Lock, incident_id: int) -> None:
        """Wakes at the wearer deadline D, and stays open until D + grace in every state: a HELP captured
        before D can still raise a cancelled incident, and audio captured before an early alert may still be
        processed. The token and the run lock are kept, and no new session starts, until then."""
        cfg = self._incident_cfg[session_id]
        deadline = await asyncio.to_thread(self._deadline_of, incident_id)
        opened = deadline - timedelta(seconds=cfg.wearer_cancel)
        await asyncio.to_thread(self._voice, lock, session_id, incident_id, "ask_ok", "awaiting", deadline)
        # Reminders become due at 10 s and 20 s; the phone plays the newest between its turns.
        for template_id, at in (("ask_ok_10", 10), ("ask_ok_20", 20)):
            due = opened + timedelta(seconds=at)
            if due >= deadline:
                break
            while (remaining := (due - _utcnow()).total_seconds()) > 0:
                await asyncio.sleep(remaining)
            await asyncio.to_thread(self._voice, lock, session_id, incident_id, template_id, None, None)
        while (remaining := (deadline - _utcnow()).total_seconds()) > 0:
            await asyncio.sleep(remaining)
        follow = await asyncio.to_thread(self._on_deadline, lock, session_id, incident_id)
        self._dispatch(session_id, lock, follow)
        while (remaining := (deadline + timedelta(seconds=cfg.grace) - _utcnow()).total_seconds()) > 0:
            await asyncio.sleep(remaining)

    def _voice(self, lock, session_id, incident_id, template_id, listen_mode, until) -> None:
        """Queue a line for iPhone 1 if the incident is still waiting; optionally (re)open listening."""
        channel = self._channels.get(session_id)
        if channel is None:
            return
        with lock, self._db() as db:
            if db.get(AlertEvent, incident_id).spatial_state != "awaiting_wearer":
                return
            channel.say(template_id, incident_id, _utcnow())
            if listen_mode:
                channel.grant(incident_id, listen_mode, until, _utcnow())
        _hint(session_id, "wearer")

    def _deadline_of(self, incident_id: int) -> datetime:
        with self._db() as db:
            return db.get(AlertEvent, incident_id).wearer_deadline_at

    def _on_deadline(self, lock, session_id, incident_id) -> dict:
        with lock, self._db() as db:
            session, row = db.get(SpatialSession, session_id), db.get(AlertEvent, incident_id)
            if not _incident_session_ok(session):
                return {}
            follow = {"state": row.spatial_state}
            reason = decide_deadline(row.spatial_state)
            if reason is not None:
                follow = self._alert_locked(db, session_id, row, reason)
            db.commit()
        _hint(session_id, "action")
        return follow

    def _alert_locked(self, db, session_id: str, row, reason: str) -> dict:
        """awaiting/cancelled -> alerted, plus the initial send to contact_1. Caller commits."""
        cfg = self._incident_cfg[session_id]
        now = _utcnow()
        channel = self._channels.get(session_id)
        if channel is not None and channel.listen is not None and channel.listen.incident_id == row.id:
            channel.listen = None  # MVP2.md: once alerted, listening ends; no new conversation in P0
        row.spatial_state, row.alert_reason, row.first_notify_at = ALERTED, reason, now
        row.caregiver_deadline_at = now + timedelta(seconds=cfg.caregiver_ack)
        row.notification_status = "scheduled"
        action = SpatialAction(
            session_id=session_id, incident_id=row.id, kind="notify", target="contact_1",
            action_key=f"initial:{row.id}:contact_1", created_at=now,
            detail=json.dumps({"template": "notify_initial", "status": "scheduled", "alert_reason": reason,
                               "channel": self._notifier_for(session_id).channel, "after_wearer_window_s": cfg.wearer_cancel}),
        )
        db.add(action)
        db.flush()
        return {"state": ALERTED, "deliver": [action.id], "caregiver_timer": row.id, "responder": row.id}

    def _escalate_locked(self, db, session_id: str, row, reason: str) -> dict:
        if row.spatial_state != ALERTED or row.is_acknowledged or _action(db, f"escalate:{row.id}:contact_2"):
            return {}
        now = _utcnow()
        action = SpatialAction(
            session_id=session_id, incident_id=row.id, kind="escalate", target="contact_2",
            action_key=f"escalate:{row.id}:contact_2", created_at=now,
            detail=json.dumps({"template": "notify_escalation", "status": "scheduled", "escalation_reason": reason,
                               "channel": self._notifier_for(session_id).channel,
                               "after_caregiver_window_s": self._incident_cfg[session_id].caregiver_ack}),
        )
        db.add(action)
        if _late_ok_reported(db, row.id):
            # The escalation already says the wearer reported OK; no separate update follows.
            db.add(SpatialAction(session_id=session_id, incident_id=row.id, kind="late_ok", target="contact_2",
                                 action_key=f"late_ok:{row.id}:contact_2", created_at=now,
                                 detail=json.dumps({"status": "included_in_alert"})))
        db.flush()
        return {"deliver": [action.id]}

    async def _caregiver_timer(self, session_id: str, lock: threading.Lock, incident_id: int) -> None:
        await asyncio.sleep(self._incident_cfg[session_id].caregiver_ack)
        follow = await asyncio.to_thread(self._escalate_if_unacknowledged, lock, session_id, incident_id, "no_ack")
        self._dispatch(session_id, lock, follow)

    def _escalate_if_unacknowledged(self, lock, session_id, incident_id, reason) -> dict:
        with lock, self._db() as db:
            session, row = db.get(SpatialSession, session_id), db.get(AlertEvent, incident_id)
            if not _incident_session_ok(session):
                return {}
            follow = self._escalate_locked(db, session_id, row, reason)
            db.commit()
        if follow:
            _hint(session_id, "action")
        return follow

    async def _deliver(self, session_id: str, lock: threading.Lock, action_id: int) -> None:
        prepared = await asyncio.to_thread(self._prepare_send, lock, session_id, action_id)
        if prepared is None:
            return
        try:
            outcome = await asyncio.wait_for(asyncio.to_thread(self._notifier_for(session_id).send, *prepared), self._send_timeout)
        except asyncio.TimeoutError:
            outcome = {"status": "unknown", "error": f"no answer within {self._send_timeout} s"}
        except Exception as err:
            logger.exception("notifier failed for action %s", action_id)
            outcome = {"status": "failed", "error": type(err).__name__}
        follow = await asyncio.to_thread(self._record_send, lock, session_id, action_id, outcome)
        self._dispatch(session_id, lock, follow)

    def _prepare_send(self, lock, session_id, action_id):
        """(contact, text, incident_id, ack_data) for a scheduled send, rendered from the spec templates.
        ack_data names the incident and this send, so the button can be checked without the message id."""
        with lock, self._db() as db:
            action = db.get(SpatialAction, action_id)
            row = db.get(AlertEvent, action.incident_id)
            detail = json.loads(action.detail)
            channel = self._channels.get(session_id)
            values = {"incident_id": row.id,
                      "wearer_channel_status": channel.status(_utcnow()) if channel else "Unknown"}
            if detail["template"] in ("notify_initial", "notify_escalation"):
                values |= {"event_time": _local_time(row.timestamp), "location_label": "Unknown",
                           "response_status": _response_status(db, row)}
            if detail["template"] == "notify_escalation":
                values["escalation_reason"] = ESCALATION_REASONS[detail["escalation_reason"]]
            if detail["template"] == "notify_evidence":
                camera, scene, _ = _scene_values(db, row.id)
                values |= {"response_status": _response_status(db, row), "camera_status": camera, "scene_status": scene,
                           "location_label": "Unknown"}
            text = render(detail["template"], **values)
            detail["text"] = text
            action.detail = json.dumps(detail)
            ack_data = f"ack:{row.id}:{action.id}" if action.kind in ("notify", "escalate") else None
            prepared = (action.target, text, row.id, ack_data)
            db.commit()
        return prepared

    def _record_send(self, lock, session_id, action_id, outcome) -> dict:
        with lock, self._db() as db:
            action = db.get(SpatialAction, action_id)
            row = db.get(AlertEvent, action.incident_id)
            detail = {**json.loads(action.detail), **outcome, "done_at": _utcnow().isoformat()}
            action.detail = json.dumps(detail)
            follow: dict = {"deliver": []}
            if action.kind == "notify":
                row.notification_status = outcome["status"]
                row.notification_sent_at = _utcnow()
                row.notification_error = outcome.get("error")
                session = db.get(SpatialSession, session_id)
                if outcome["status"] == "failed" and _incident_session_ok(session):
                    follow |= self._escalate_locked(db, session_id, row, "first_contact_failed")
            if action.kind in ("notify", "escalate") and _late_ok_reported(db, row.id):
                follow["deliver"] += self._late_ok_locked(db, session_id, row)
            db.commit()
        _hint(session_id, "action")
        return follow

    def _late_ok_locked(self, db, session_id, row) -> list[int]:
        sends = {a.target: json.loads(a.detail)["status"] for a in db.query(SpatialAction).filter(
            SpatialAction.incident_id == row.id, SpatialAction.kind.in_(("notify", "escalate")))}
        done = {a.target for a in db.query(SpatialAction).filter(
            SpatialAction.incident_id == row.id, SpatialAction.kind == "late_ok")}
        ids = []
        for contact in late_ok_targets(sends, done):
            action = SpatialAction(session_id=session_id, incident_id=row.id, kind="late_ok", target=contact,
                                   action_key=f"late_ok:{row.id}:{contact}", created_at=_utcnow(),
                                   detail=json.dumps({"template": "notify_late_ok", "status": "scheduled",
                                                      "channel": self._notifier_for(session_id).channel}))
            db.add(action)
            db.flush()
            ids.append(action.id)
        return ids

    async def acknowledge(self, incident_id: int, actor_id: str) -> dict | None:
        """The caregiver takes the incident. Returns None for an unknown incident."""
        with self._db() as db:
            row = db.get(AlertEvent, incident_id)
            if row is None or row.session_id is None:
                return None
            session_id = row.session_id
        lock = self._locks.get(session_id) or self._idle_lock
        return await asyncio.to_thread(self._record_ack, lock, session_id, incident_id, actor_id)

    def _record_ack(self, lock, session_id, incident_id, actor_id) -> dict:
        with lock, self._db() as db:
            session, row = db.get(SpatialSession, session_id), db.get(AlertEvent, incident_id)
            result = {"incident_id": incident_id, "applied": False, "incident_state": row.spatial_state}
            if not _incident_session_ok(session):
                return {**result, "reason": "run_ended"}
            if row.spatial_state == CANCELLED:
                return {**result, "reason": "cancelled_by_wearer"}
            if row.is_acknowledged:
                return {**result, "reason": "already_recorded"}
            row.is_acknowledged, row.acknowledged_at = True, _utcnow()
            db.add(SpatialAction(session_id=session_id, incident_id=incident_id, kind="ack", target=actor_id,
                                 action_key=f"ack:{incident_id}:{actor_id}", detail=json.dumps({"by": actor_id}),
                                 created_at=_utcnow()))
            db.commit()
        _hint(session_id, "action")
        return {**result, "applied": True, "reason": None}

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
        """The MVP 1 HUD button: a clear OK, captured now."""
        return await self.wearer_reply(incident_id, "ok", _utcnow())

    async def wearer_reply(self, incident_id: int, reply: str, capture_end: datetime | None,
                           speech: dict | None = None) -> dict | None:
        """A classified answer from the wearer (U4 feeds STT results here). Returns None for an
        unknown incident. `capture_end` is when the audio ended, already checked against the clock."""
        with self._db() as db:
            row = db.get(AlertEvent, incident_id)
            if row is None or row.session_id is None:
                return None
            session_id, state = row.session_id, row.spatial_state
        lock = self._locks.get(session_id)
        if lock is None:
            return {"incident_id": incident_id, "applied": False, "incident_state": state, "reason": "run_ended"}
        result, follow = await asyncio.to_thread(self._record_reply, lock, session_id, incident_id, reply, capture_end, speech)
        self._dispatch(session_id, lock, follow)
        return result

    def _record_reply(self, lock, session_id, incident_id, reply, capture_end, speech=None):
        with lock, self._db() as db:
            session, row = db.get(SpatialSession, session_id), db.get(AlertEvent, incident_id)
            result = {"incident_id": incident_id, "applied": False, "incident_state": row.spatial_state}
            if not _incident_session_ok(session):
                return {**result, "reason": "run_ended"}, {}
            grace = timedelta(seconds=self._incident_cfg[session_id].grace)
            if speech is not None and _utcnow() > row.wearer_deadline_at + grace:
                # MVP2.md: when the grace ends, speech results still pending are blocked. They are logged with
                # the reason and change nothing: no late OK, no update to a contact, no alert.
                decision = ReplyDecision(reason="processed_after_grace")
            else:
                decision = decide_reply(row.spatial_state, reply, row.wearer_deadline_at, grace, _utcnow(), capture_end)
            follow: dict = {"deliver": []}
            if decision.alert:
                follow |= self._alert_locked(db, session_id, row, decision.alert)
            channel = self._channels.get(session_id)
            if decision.cancel:
                row.spatial_state = CANCELLED
                if channel is not None:
                    channel.say("cancel_confirmed", row.id, _utcnow())
                    channel.grant(row.id, "help_only", row.wearer_deadline_at, _utcnow())
            applied, reason = decision.applied, decision.reason
            if decision.late_ok and _late_ok_reported(db, row.id):
                applied, reason = False, "already_recorded"  # a repeated OK is logged, nothing more is sent
            db.add(SpatialAction(
                session_id=session_id, incident_id=incident_id, kind="self_report", target="wearer",
                created_at=_utcnow(),
                detail=json.dumps({"response": reply, "applied": applied, "reason": reason,
                                   "cancelled_alert": decision.cancel, "raised_alert": decision.alert,
                                   "late_ok": decision.late_ok and applied,
                                   "capture_end": capture_end.isoformat() if capture_end else None,
                                   **({"speech": speech} if speech else {})}),
            ))
            db.flush()
            if decision.late_ok and applied:
                follow["deliver"] = follow.get("deliver", []) + self._late_ok_locked(db, session_id, row)
                if channel is not None:
                    channel.say("late_ok_recorded", row.id, _utcnow())
            db.commit()
            result = {**result, "applied": applied, "incident_state": row.spatial_state, "reason": reason}
        _hint(session_id, "action")
        return result, follow

    # --- iPhone 1 (docs/spatial/MVP2_ARCHITECTURE.md, "WearerChannel và STT") ----------------

    def _channel(self, session_id: str, token: str | None) -> WearerChannel | None:
        channel = self._channels.get(session_id)
        if channel is None or not token or not secrets.compare_digest(token, channel.token):
            return None
        channel.last_seen = _utcnow()
        return channel

    def new_pairing(self, session_id: str) -> dict | None:
        """A fresh pairing code for the phone, replacing any earlier one. Laptop only (not through the tunnel)."""
        channel, lock = self._channels.get(session_id), self._locks.get(session_id)
        if channel is None or lock is None:
            return None
        with lock:
            code, expires = channel.new_pairing(_utcnow())
        return {"code": code, "expires_at": expires}

    def wearer_pair(self, session_id: str, code: str | None) -> str | None:
        channel, lock = self._channels.get(session_id), self._locks.get(session_id)
        if channel is None or lock is None:
            return None
        with lock:
            token = channel.redeem(code, _utcnow())
        if token is not None:
            channel.last_seen = _utcnow()
        return token

    def wearer_poll(self, session_id: str, token: str | None, after: int) -> dict | None:
        channel, lock = self._channel(session_id, token), self._locks.get(session_id)
        if channel is None or lock is None:
            return None
        now = _utcnow()
        with lock, self._db() as db:
            rows = {}

            def row(incident_id):
                if incident_id not in rows:
                    rows[incident_id] = db.get(AlertEvent, incident_id)
                return rows[incident_id]
            if self._wearer_window_open(db, session_id):
                channel.drop_advisories("incident open")
            pending = channel.pending(after, lambda i: row(i).spatial_state, lambda i: row(i).wearer_deadline_at, now)
            listen = channel.listening_for(now)
            until_mono_ms = None if listen is None else \
                time.monotonic() * 1000 + (listen.until - now).total_seconds() * 1000
        return {
            "utterances": [{"id": u.id, "template_id": u.template_id, "text": u.text, "incident_id": u.incident_id}
                           for u in pending],
            "listen": None if listen is None else {
                "incident_id": listen.incident_id, "mode": listen.mode, "turn_ms": TURN_MS,
                "remaining_ms": max(0, round((listen.until - now).total_seconds() * 1000)),
                # The deadline on the backend clock; the phone converts it with its own clock mapping
                # instead of trusting a remaining time that has already aged on the network.
                "until_backend_ms": round(until_mono_ms, 1)},
        }

    def wearer_played(self, session_id: str, token: str | None, utterance_id: int,
                      speech_start_ms: float | None = None, speech_end_ms: float | None = None,
                      timed_out: bool = False) -> bool | None:
        channel, lock = self._channel(session_id, token), self._locks.get(session_id)
        if channel is None or lock is None:
            return None
        now = _utcnow()
        span = None
        if speech_start_ms is not None and speech_end_ms is not None and speech_end_ms >= speech_start_ms:
            start, end = self._phone_to_utc(session_id, speech_start_ms, now), self._phone_to_utc(session_id, speech_end_ms, now)
            span = (start, end) if start and end else None
        with lock, self._db() as db:
            line = next((u for u in channel.utterances if u.id == utterance_id), None)
            if not channel.played(utterance_id, span, now, "speech_timeout" if timed_out else None):
                return False
            # What the phone really said is kept as a record: M7 echo trials need to know which prompts played.
            db.add(SpatialAction(session_id=session_id, incident_id=line.incident_id, kind="spoken", target="wearer",
                                 created_at=now, detail=json.dumps({"template_id": line.template_id, "utterance_id": line.id,
                                                                   "source": line.source, "timed_out": timed_out})))
            db.commit()
            return True

    def _phone_to_utc(self, session_id: str, phone_ms: float, now: datetime) -> datetime | None:
        clock = self._phone_clocks.get(session_id)
        if clock is None:
            return None
        return now - timedelta(milliseconds=time.monotonic() * 1000 - (phone_ms + clock["offset_ms"]))

    def wearer_status(self, session_id: str, token: str | None, microphone_error: str | None) -> bool | None:
        channel, lock = self._channel(session_id, token), self._locks.get(session_id)
        if channel is None or lock is None:
            return None
        with lock:
            channel.microphone_error = microphone_error
        _hint(session_id, "wearer")
        return True

    def wearer_clock_report(self, session_id: str, token: str | None, rounds: list[dict]) -> dict | None:
        if self._channel(session_id, token) is None:
            return None
        mapping = best_mapping(rounds)
        if mapping is not None:
            self._phone_clocks[session_id] = mapping
        return mapping or {}

    def _capture_end(self, session_id: str, incident_id: int, start_ms: float | None, end_ms: float | None,
                     received: datetime) -> tuple:
        """The turn's end on the backend clock, or (None, reason) when it cannot be confirmed:
        MVP2.md does not let an unchecked phone timestamp decide anything. A confirmed turn lies
        inside a listening grant for this incident and outside the phone's own speech."""
        clock, channel = self._phone_clocks.get(session_id), self._channels.get(session_id)
        if clock is None or channel is None:
            return None, "phone clock unsynchronized"
        if start_ms is None or end_ms is None or not 0 < end_ms - start_ms <= TURN_MS + 500:
            return None, "capture interval missing or impossible"
        start_utc, end_utc = self._phone_to_utc(session_id, start_ms, received), self._phone_to_utc(session_id, end_ms, received)
        slack = timedelta(milliseconds=clock["uncertainty_ms"])
        if end_utc > received + slack:
            return None, "capture ends after it was received"
        problem = channel.check_turn(incident_id, start_utc, end_utc, slack)
        return (None, problem) if problem else (end_utc, None)

    async def wearer_audio(self, session_id: str, token: str | None, incident_id: int, data: bytes,
                           capture_start_ms: float | None, capture_end_ms: float | None, mime: str) -> dict | None:
        """One finalized turn from iPhone 1. Decoded and transcribed in memory, never stored."""
        channel = self._channel(session_id, token)
        if channel is None:
            return None
        received = _utcnow()
        capture_end, capture_problem = self._capture_end(session_id, incident_id, capture_start_ms, capture_end_ms, received)
        channel.turns += 1
        capture_start = None if capture_start_ms is None else self._phone_to_utc(session_id, capture_start_ms, received)
        meta = {"session_id": session_id, "incident_id": incident_id, "mime": mime, "bytes": len(data),
                "received_at": received.isoformat(), "capture_start": capture_start.isoformat() if capture_start else None,
                "capture_end": capture_end, "capture_problem": capture_problem,
                "capture_ms": None if capture_start_ms is None or capture_end_ms is None else capture_end_ms - capture_start_ms}
        dropped = self._speech.submit(SpeechJob(data, meta, self._speech_done))
        return {"queued": True, "pushed_out_older_turn": dropped is not None, "queue_depth": self._speech.depth,
                "capture_time": "confirmed" if capture_end else capture_problem}

    # --- Camera (docs/spatial/MVP2.md, "Camera và SAM") --------------------------------------

    def _frame_source_ms(self, session_id: str, phone_ms: float) -> tuple[float | None, float | None]:
        """A phone capture time on the IMU's source clock with its uncertainty, or (None, None) while
        the phone or the device clock is unsynchronized."""
        phone, run = self._phone_clocks.get(session_id), self._live.get(session_id)
        if phone is None or run is None or run.stream is None or run.stream.origin_ms is None:
            return None, None
        device = self._clocks.get((run.device_id, run.stream.boot_id))
        if device is None:
            return None, None
        source = phone_ms + phone["offset_ms"] - device["offset_ms"] - run.stream.origin_ms
        return source, phone["uncertainty_ms"] + device["uncertainty_ms"]

    async def wearer_frame(self, session_id: str, token: str | None, data: bytes, capture_ms: float | None) -> dict | None:
        channel = self._channel(session_id, token)
        store, gate, lock = self._frames.get(session_id), self._stairs.get(session_id), self._locks.get(session_id)
        if channel is None or store is None or gate is None or lock is None:
            return None
        if await asyncio.to_thread(self._session_status, session_id) != "Running":
            raise LiveRunEnded(session_id)  # the token outlives Stop for the incident's voice, not for new frames
        source_ms, uncertainty = (None, None) if capture_ms is None else self._frame_source_ms(session_id, capture_ms)
        received_mono = time.monotonic()
        phone = self._phone_clocks.get(session_id)
        captured_mono = None
        if capture_ms is not None and phone is not None:
            captured_mono = (capture_ms + phone["offset_ms"]) / 1000
            if captured_mono > received_mono + phone["uncertainty_ms"] / 1000:
                captured_mono = None  # claims to be from the future: the mapping cannot be trusted for this frame
        frame = Frame(store.received + 1, _utcnow(), received_mono, source_ms, uncertainty, data, captured_mono,
                      None if captured_mono is None else phone["uncertainty_ms"])
        store.add(frame)
        run = self._live.get(session_id)
        if run is not None and run.record_frames:
            await asyncio.to_thread(_record_frame, _journal_path(session_id, self._live_data_dir).parent, frame)
        gate.offer(frame)
        job = gate.take(frame.received_mono)
        if job is not None:
            self._background(self._stairs_job(session_id, lock, job))
        return {"frame_id": frame.id, "clock": "synchronized" if source_ms is not None else "unsynchronized"}

    def _session_status(self, session_id: str) -> str | None:
        with self._db() as db:
            session = db.get(SpatialSession, session_id)
            return session.status if session else None

    def _wearer_window_open(self, db, session_id: str) -> bool:
        """An incident is still inside the wearer's response window: waiting for the answer, or
        cancelled and not yet past its deadline plus grace (a HELP can still raise it)."""
        grace = timedelta(seconds=self._incident_cfg[session_id].grace) if session_id in self._incident_cfg else timedelta(0)
        now = _utcnow()
        for row in db.query(AlertEvent).filter(AlertEvent.session_id == session_id, AlertEvent.spatial_state.in_(
                ("awaiting_wearer", "cancelled_by_wearer"))):
            if row.spatial_state == "awaiting_wearer" or now < row.wearer_deadline_at + grace:
                return True
        return False

    async def _stairs_job(self, session_id: str, lock: threading.Lock, frame: Frame) -> None:
        """SAM on one frame for the spoken advisory. The result is stored whatever it is."""
        gate = self._stairs.get(session_id)
        try:
            analysis_id = await asyncio.to_thread(self._open_stairs_analysis, lock, session_id, frame)
            if analysis_id is None:
                return
            outcome = await self._live_segment(session_id, (frame.data, "image/jpeg", STAIRS_PROMPT))
            await asyncio.to_thread(self._close_analysis, lock, session_id, analysis_id, outcome)
            if outcome["status"] == "Complete":
                await asyncio.to_thread(self._announce_stairs, lock, session_id, frame)
        except Exception:
            logger.exception("stairs analysis failed for session %s", session_id)
        finally:
            if gate is not None:
                gate.done()

    def _open_stairs_analysis(self, lock, session_id: str, frame: Frame) -> int | None:
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            if session is None or session.status != "Running":
                return None
            analysis = SpatialAnalysis(session_id=session_id, kind="sam_advisory", status="Running", prompt=STAIRS_PROMPT,
                                       frame_id=frame.id, created_at=_utcnow())
            db.add(analysis)
            db.commit()
            return analysis.id

    def _announce_stairs(self, lock, session_id: str, frame: Frame) -> None:
        channel, gate = self._channels.get(session_id), self._stairs.get(session_id)
        if channel is None or gate is None:
            return
        with lock, self._db() as db:
            session = db.get(SpatialSession, session_id)
            if session is None or session.status != "Running":
                return
            if not gate.may_announce(frame, time.monotonic(), self._wearer_window_open(db, session_id)):
                return
            channel.say("stairs_visible", None, _utcnow())
        _hint(session_id, "wearer")

    async def _live_scene(self, session_id: str, lock: threading.Lock, incident_id: int) -> None:
        """The frame nearest the fall and SAM's reading of it, stored as the incident's scene evidence."""
        analysis_id, request = await asyncio.to_thread(self._open_scene_analysis, lock, session_id, incident_id)
        if request is not None:
            outcome = await self._live_segment(session_id, request)
            await asyncio.to_thread(self._close_analysis, lock, session_id, analysis_id, outcome)

    async def _live_segment(self, session_id: str, request) -> dict:
        """Every live SAM call, advisory or incident scene, goes through one slot per session: one
        request at a time, starts at least 3 s apart. Sending the alert never waits here."""
        slot = self._sam_slots.setdefault(session_id, SamSlot())
        return await slot.run(lambda: self._run_segment(request, call=self._live_call))

    def _open_scene_analysis(self, lock, session_id: str, incident_id: int):
        store = self._frames.get(session_id)
        if store is None:
            return None, None
        with lock, self._db() as db:
            row = db.get(AlertEvent, incident_id)
            match = store.match(row.source_start_ms)
            analysis = SpatialAnalysis(session_id=session_id, incident_id=incident_id, kind="sam", status="Running",
                                       prompt=STAIRS_PROMPT, camera_status=match.status,
                                       frame_id=match.frame.id if match.frame else None, created_at=_utcnow(),
                                       frame_info=None if match.frame is None else json.dumps(
                                           {"source_ms": match.frame.source_ms, "delta_ms": match.delta_ms,
                                            "uncertainty_ms": match.frame.uncertainty_ms}))
            request = None
            if match.frame is not None:
                folder = _journal_path(session_id, self._live_data_dir).parent / "frames"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{match.frame.id}.jpg").write_bytes(match.frame.data)
            if match.status == "Available":
                request = (match.frame.data, "image/jpeg", STAIRS_PROMPT)
            else:
                analysis.status, analysis.reason, analysis.finished_at = "Unavailable", match.reason, _utcnow()
            db.add(analysis)
            db.commit()
            analysis_id = analysis.id
        _hint(session_id, "analysis")
        return analysis_id, request

    def live_frame_jpeg(self, session_id: str, frame_id: str) -> bytes | None:
        """A frame still in memory, else one saved as an incident's evidence."""
        store = self._frames.get(session_id)
        frame = store.get(frame_id) if store else None
        if frame is not None:
            return frame.data
        if not re.fullmatch(r"live-\d+", frame_id):
            return None
        path = _journal_path(session_id, self._live_data_dir).parent / "frames" / f"{frame_id}.jpg"
        return path.read_bytes() if path.is_file() else None

    # --- Responder (docs/spatial/MVP2.md, "Agent P0") --------------------------------------------
    # Starts after the first alert action is committed and never gates it: sends and escalation
    # run on their own tasks. Whatever the agent asks for is checked again under the run lock.

    async def _respond(self, session_id: str, lock: threading.Lock, incident_id: int) -> None:
        opened = await asyncio.to_thread(self._open_response, lock, session_id, incident_id)
        if opened is None:
            return
        analysis_id, imu = opened
        tools = ResponderTools(
            read_facts=lambda: self._responder_facts(lock, session_id, incident_id, imu),
            send=lambda contact, ids, seen: self._agent_send(lock, session_id, incident_id, imu, contact, ids, seen),
            say=lambda template_id: self._agent_say(lock, session_id, incident_id, imu, template_id))
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(run_responder, tools, incident_id, self._chat,
                                  max_tool_calls=RESPONDER_MAX_TOOL_CALLS, timeout_seconds=RESPONDER_TIMEOUT_SECONDS),
                RESPONDER_TIMEOUT_SECONDS + 5)
            outcome = {"status": result.status, "reason": result.reason,
                       "output_text": json.dumps(result.result) if result.result is not None else None,
                       "tool_log": json.dumps({"calls": result.tool_log, "usage": result.usage,
                                               "rejected_answer": result.raw_answer})}
        except asyncio.TimeoutError:
            outcome = {"status": "TimedOut", "reason": f"no result within {RESPONDER_TIMEOUT_SECONDS} s"}
        except Exception as err:
            logger.exception("responder for incident %s failed", incident_id)
            outcome = {"status": "Failed", "reason": type(err).__name__}
        outcome["duration_ms"] = round((time.perf_counter() - started) * 1000)
        await asyncio.to_thread(self._close_analysis, lock, session_id, analysis_id, outcome)

    def _open_response(self, lock, session_id: str, incident_id: int):
        """(analysis id, IMU summary) for a live incident that a person will read about, else None."""
        with lock, self._db() as db:
            session, row = db.get(SpatialSession, session_id), db.get(AlertEvent, incident_id)
            if session is None or session.kind != "live" or row.decision_source == "voice_drill":
                return None
            analysis = SpatialAnalysis(session_id=session_id, incident_id=incident_id, kind="agent", status="Running",
                                       model=AGENT_MODEL, created_at=_utcnow())
            db.add(analysis)
            db.commit()
            analysis_id, start = analysis.id, row.source_start_ms
        _hint(session_id, "analysis")
        return analysis_id, _live_imu_summary(_journal_path(session_id, self._live_data_dir), start)

    def _responder_facts(self, lock, session_id: str, incident_id: int, imu: dict) -> dict:
        with lock, self._db() as db:
            return self._facts_locked(db, session_id, db.get(AlertEvent, incident_id), imu)

    def _facts_locked(self, db, session_id: str, row, imu: dict) -> dict:
        """What the responder may know and what the checks need, read together under the run lock."""
        actions = db.query(SpatialAction).filter(SpatialAction.incident_id == row.id).order_by(SpatialAction.id).all()
        sends: dict[str, str] = {}
        delivery = []
        for a in actions:
            if a.kind in ("notify", "escalate"):
                sends.pop(a.target, None)  # newest send last, so the dict order is the send order
                sends[a.target] = json.loads(a.detail)["status"]
                delivery.append({"evidence_id": f"delivery:{row.id}:{a.id}", "source": "Notifier result",
                                 "contact": a.target, "template": json.loads(a.detail)["template"],
                                 "status": sends[a.target], "error": json.loads(a.detail).get("error")})
        if row.is_acknowledged:
            ack = next(a for a in actions if a.kind == "ack")
            delivery.append({"evidence_id": f"ack:{row.id}", "source": "Caregiver Acknowledge button", "by": ack.target})
        channel = self._channels.get(session_id)
        said = [u.template_id for u in channel.utterances if u.incident_id == row.id and u.status != "dropped"] if channel else []
        camera, scene, scene_row = _scene_values(db, row.id)
        response = _response_status(db, row)
        event = [
            {"evidence_id": f"imu:{row.id}", "source": "ESP32 waist IMU or its stand-in; see decision_source",
             **imu, "decision_source": row.decision_source, "fall_probability": round(row.confidence or 0, 3)},
            {"evidence_id": f"wearer:{row.id}", "source": "Phone microphone, classified by code", "response_status": response,
             "alert_reason": row.alert_reason},
        ]
        scene_items = []
        if scene_row is not None:
            info = json.loads(scene_row.frame_info) if scene_row.frame_info else {}
            if camera == "Available":
                scene_items.append({"evidence_id": f"frame:{scene_row.frame_id}", "source": "Phone camera at eye height",
                                    "camera_status": camera, "alignment_error_ms": round(abs(info.get("delta_ms") or 0))})
            else:
                scene_items.append({"evidence_id": f"camera:{row.id}", "source": "Phone camera", "camera_status": camera,
                                    "reason": scene_row.reason})
            scene_items.append({"evidence_id": f"sam:{scene_row.id}", "source": "SAM", "prompt": scene_row.prompt,
                                "status": scene_row.status, "scene_status": scene, "objects": len(json.loads(scene_row.boxes or "[]")),
                                "reason": scene_row.reason})
        return {"incident_id": row.id, "state": row.spatial_state, "acknowledged": bool(row.is_acknowledged),
                "sends": sends, "agent_updates": [a.target for a in actions if a.kind == "agent_update"], "said": said,
                "evidence": {"event": event, "scene": scene_items, "delivery": delivery}}

    def _agent_send(self, lock, session_id: str, incident_id: int, imu: dict, contact, evidence_ids, seen) -> dict:
        with lock, self._db() as db:
            session, row = db.get(SpatialSession, session_id), db.get(AlertEvent, incident_id)
            if not _incident_session_ok(session):
                return {"refused": "the live session has ended"}
            reason = check_send(self._facts_locked(db, session_id, row, imu), contact, evidence_ids, seen)
            if reason:
                return {"refused": reason}
            action = SpatialAction(session_id=session_id, incident_id=incident_id, kind="agent_update", target=contact,
                                   action_key=f"agent_update:{incident_id}:{contact}", created_at=_utcnow(),
                                   detail=json.dumps({"template": "notify_evidence", "status": "scheduled",
                                                      "evidence_ids": evidence_ids,
                                                      "channel": self._notifier_for(session_id).channel}))
            db.add(action)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return {"refused": "this contact already has the agent's update"}
            action_id = action.id
        _hint(session_id, "action")
        self._loop.call_soon_threadsafe(self._dispatch, session_id, lock, {"deliver": [action_id]})
        return {"scheduled": True, "contact": contact}

    def _agent_say(self, lock, session_id: str, incident_id: int, imu: dict, template_id) -> dict:
        channel = self._channels.get(session_id)
        if channel is None:
            return {"refused": "no wearer phone is attached"}
        with lock, self._db() as db:
            row = db.get(AlertEvent, incident_id)
            reason = check_say(self._facts_locked(db, session_id, row, imu), template_id)
            if reason:
                return {"refused": reason}
            channel.say(template_id, incident_id, _utcnow(), source="agent")
        _hint(session_id, "wearer")
        return {"said": template_id}

    async def _speech_done(self, job: SpeechJob, result: dict) -> None:
        meta, session_id = job.meta, job.meta["session_id"]
        channel = self._channels.get(session_id)
        if channel is not None:
            if result["status"] == "dropped":
                channel.speech_dropped(_utcnow())
            channel.stt_failed = result["status"] == "failed"
        capture_end, problem = meta["capture_end"], meta["capture_problem"]
        if result["status"] == "ok" and capture_end is not None and meta["capture_ms"] is not None \
                and abs(result["duration_s"] * 1000 - meta["capture_ms"]) > 500:
            capture_end, problem = None, "decoded length does not match the capture interval"
        speech = {"status": result["status"], "transcript": result.get("transcript"), "queue_ms": result.get("queue_ms"),
                  "stt_ms": result.get("stt_ms"), "error": result.get("error") or result.get("reason"),
                  "capture_problem": problem, "mime": meta["mime"], "bytes": meta["bytes"],
                  "received_at": meta["received_at"], "capture_start": meta["capture_start"]}
        reply = classify_reply(result.get("transcript")) if result["status"] == "ok" else "unclear"
        if reply == "ok" and capture_end is None:
            # Only an answer heard inside this incident's listening can cancel it. HELP still
            # goes through: it can only make the alert stronger.
            reply, speech["not_counted_as_ok"] = "unclear", problem
        await self.wearer_reply(meta["incident_id"], reply, capture_end, speech)


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


def _record_frame(folder: Path, frame: Frame) -> None:
    """One frame and its index line, for the S3 package: id, capture source time, clock uncertainty, receive time, hash."""
    frames = folder / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / f"{frame.id}.jpg").write_bytes(frame.data)
    line = {"id": frame.id, "file": f"frames/{frame.id}.jpg", "source_ms": frame.source_ms,
            "uncertainty_ms": frame.uncertainty_ms, "received_at": frame.received_at.isoformat(),
            "bytes": len(frame.data), "sha256": hashlib.sha256(frame.data).hexdigest()}
    with (folder / "frames.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def _append_journal(path: Path, steps, received_at: datetime, session_offset: float | None) -> None:
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
            session_ms = f"{st.device_ms + session_offset:.1f}" if session_offset is not None else "nan"
            f.write(f"{st.source_ms},{st.seq},{st.device_ms},{received},{session_ms},"
                    + ",".join(f"{float(v):.5f}" for v in st.values) + "\n")


def _journal_path(session_id: str, live_data_dir: Path | None = None) -> Path:
    # Looked up per call so tests can point LIVE_DATA_DIR at a temporary folder.
    return (live_data_dir or LIVE_DATA_DIR) / session_id / "imu.csv"


def _channel_snapshot(manager, session_id: str) -> dict | None:
    """The wearer channel as the laptop shows it, including the link token: the snapshot route is
    not reachable through the tunnel (main.py), so only the laptop sees it."""
    channel = manager._channels.get(session_id)
    if channel is None:
        return None
    return {**channel.snapshot(_utcnow()), "token": channel.token}


def _camera_snapshot(manager, session_id: str) -> dict | None:
    store = manager._frames.get(session_id)
    if store is None:
        return None
    latest = store.latest()
    return {"frames_received": store.received, "latest_frame_id": latest.id if latest else None,
            "last_frame_age_s": None if latest is None else round(time.monotonic() - latest.received_mono, 1),
            "clock": None if latest is None else "synchronized" if latest.source_ms is not None else "unsynchronized"}


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


ESCALATION_REASONS = {"no_ack": "No acknowledgement within the response window",
                      "first_contact_failed": "The first contact could not be notified"}
VN_TZ = timezone(timedelta(hours=7))


def _incident_session_ok(session) -> bool:
    """An opened incident keeps running after Stop; only a broken run (Interrupted) ends it."""
    return session is not None and session.status in ("Running", "Stopped")


def _action(db, key: str):
    return db.query(SpatialAction).filter(SpatialAction.action_key == key).first()


def _late_ok_reported(db, incident_id: int) -> bool:
    return any(json.loads(a.detail).get("late_ok") for a in db.query(SpatialAction).filter(
        SpatialAction.incident_id == incident_id, SpatialAction.kind == "self_report"))


def _response_status(db, row) -> str:
    if _late_ok_reported(db, row.id):
        return "OK reported after alert"
    return "Help requested" if row.alert_reason == "wearer_help" else "No clear response"


def _scene_values(db, incident_id: int):
    """(camera_status, scene_status, analysis row) of the incident's newest scene analysis, in the
    spec's words. With none, both are Unavailable: nothing is invented."""
    row = (db.query(SpatialAnalysis).filter(SpatialAnalysis.incident_id == incident_id, SpatialAnalysis.kind == "sam",
                                            SpatialAnalysis.camera_status.isnot(None))
           .order_by(SpatialAnalysis.id.desc()).first())
    if row is None:
        return "Unavailable", "Unavailable", None
    return row.camera_status, scene_status(row.status) if row.camera_status == "Available" else "Unavailable", row


def _live_imu_summary(journal: Path, source_start_ms: int) -> dict:
    """Peak acceleration and rotation around the fall and how still the last 2 s were, from the journal."""
    table = _read_journal(journal)
    if not len(table):
        return {"note": "no IMU samples were journalled"}
    window = table[(table[:, 0] >= source_start_ms - 1000) & (table[:, 0] <= source_start_ms + 5000)]
    if not len(window):
        return {"note": "no IMU samples near the fall"}
    gyro = window[:, [JOURNAL_COLUMNS.index(c) for c in ("gx", "gy", "gz")]]
    tail = window[window[:, 0] >= window[-1, 0] - 2000]
    return {"window_s": [round((source_start_ms - 1000) / 1000, 2), round((source_start_ms + 5000) / 1000, 2)],
            "peak_svm_g": round(float(window[:, JOURNAL_COLUMNS.index("svm")].max()), 2),
            "peak_gyro_deg_s": round(float(np.linalg.norm(gyro, axis=1).max()), 1),
            "svm_std_last_2s_g": round(float(tail[:, JOURNAL_COLUMNS.index("svm")].std()), 3)}


def _local_time(utc_naive: datetime) -> str:
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(VN_TZ).strftime("%Y-%m-%d %H:%M:%S %z")


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


def session_snapshot(session_id: str, session_factory=None, manager=None) -> dict | None:
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
            "wearer": _channel_snapshot(manager or spatial_runs, s.id),
            "camera": _camera_snapshot(manager or spatial_runs, s.id),
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


def _live_frame(session_id: str, analyses) -> dict:
    """The incident's camera evidence from its stored scene analysis: the frame only counts as
    evidence when it was matched to the fall, otherwise the reason is shown instead."""
    source = "Phone camera at eye height (glasses proxy)"
    scene = next((a for a in reversed(analyses) if a.kind == "sam" and a.camera_status), None)
    if scene is None:
        return {"status": "Unavailable", "reason": "No scene analysis was recorded for this incident", "source": source}
    info = json.loads(scene.frame_info) if scene.frame_info else {}
    url = f"/api/spatial/sessions/{session_id}/frames/{scene.frame_id}" if scene.frame_id else None
    if scene.camera_status != "Available":
        return {"status": scene.camera_status, "reason": scene.reason, "source": source, "nearest_url": url}
    return {"status": "Available", "id": scene.frame_id, "url": url, "label": "Phone camera frame (glasses proxy)",
            "scene": f"Scene analysis: {scene_status(scene.status)}", "source_ms": info.get("source_ms"),
            "alignment_error_ms": round(abs(info.get("delta_ms") or 0)), "source": source}


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
            source_ms, samples = table[:, 0].astype(np.int64), table[:, JOURNAL_COLUMNS.index("ax"):]
            source = json.loads(session.config_snapshot or "{}").get("source", "esp32")
            location, imu_source, segments = UNKNOWN_LOCATION, LIVE_SOURCE_LABELS[source], []
            frame = _live_frame(session.id, analyses)
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


spatial_runs = SpatialRunManager(preload_whisper=True)
