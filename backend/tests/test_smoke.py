"""
test_smoke.py — Phase 6 smoke tests.

Verifies the full end-to-end pipeline on every run:
  data in -> inference -> alert created -> Telegram dispatched -> DB persisted

These are NOT unit tests. They exercise the real HTTP layer (TestClient),
real DB (in-memory SQLite), real inference service, and a mocked Telegram call.
If any of these fail the system cannot be considered operational.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app import database as db_module  # noqa: E402
from app.config import FALL_CONFIRM_WINDOWS, WINDOW_SIZE  # noqa: E402
from app.services.fall_detection import fall_detection_service  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SMOKE_PATIENT_ID = 99  # isolated patient ID for smoke tests


@pytest.fixture(scope="module")
def test_db():
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "smoke.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_module.engine = engine
    db_module.SessionLocal = SessionLocal
    db_module.Base.metadata.create_all(bind=engine)
    yield engine, SessionLocal
    engine.dispose()
    tmp.cleanup()


@pytest.fixture()
def client(test_db):
    engine, SessionLocal = test_db
    db_module.Base.metadata.drop_all(bind=engine)
    db_module.Base.metadata.create_all(bind=engine)
    fall_detection_service.reset_patient(SMOKE_PATIENT_ID)

    from app.main import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NORMAL_SAMPLE = dict(
    patient_id=SMOKE_PATIENT_ID,
    ax=0.0, ay=0.0, az=1.0,
    gx=0.0, gy=0.0, gz=0.0,
    svm=1.0,
)

_FALL_SAMPLE = dict(
    patient_id=SMOKE_PATIENT_ID,
    ax=2.0, ay=2.0, az=3.0,
    gx=0.0, gy=0.0, gz=0.0,
    svm=3.5,  # > SVM_FALL_THRESHOLD (2.5)
)


def _fill(client, n: int, sample: dict | None = None) -> dict:
    """POST n samples, return last response JSON."""
    payload = sample or _NORMAL_SAMPLE
    resp = None
    for _ in range(n):
        resp = client.post("/api/sensor/data", json=payload)
        assert resp.status_code == 200, f"sensor/data failed: {resp.text}"
    return resp.json()


def _post_confirmed_fall(client):
    """Fill the window, then post enough FALL windows to clear confirmation.

    A single high-SVM window is deliberately *not* a FALL any more:
    `FALL_CONFIRM_WINDOWS` consecutive FALL predictions are required, which costs
    about a second of latency and cuts false positives. These tests used to post
    one fall sample and assert `state == "FALL"`, so they were asserting the
    pre-confirmation behaviour. Returns the response for the window that confirms.
    """

    _fill(client, WINDOW_SIZE - 1)
    resp = None
    for _ in range(FALL_CONFIRM_WINDOWS):
        resp = client.post("/api/sensor/data", json=_FALL_SAMPLE)
        assert resp.status_code == 200, f"sensor/data failed: {resp.text}"
    return resp


# ---------------------------------------------------------------------------
# Smoke test 1 — health check
# ---------------------------------------------------------------------------

def test_health_check(client):
    """Backend starts and responds to health check."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "online"


# ---------------------------------------------------------------------------
# Smoke test 2 — data ingestion reaches inference
# ---------------------------------------------------------------------------

def test_ingest_single_sample_returns_ok(client):
    """Single sample is accepted and returns status ok."""
    resp = client.post("/api/sensor/data", json=_NORMAL_SAMPLE)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["samples_buffered"] == 1
    assert data["state"] == "BUFFERING"


def test_window_fills_and_produces_prediction(client):
    """After 100 samples the window is ready and a real state is returned."""
    result = _fill(client, 100)
    assert result["window_ready"] is True
    assert result["state"] in {"NORMAL", "SWAY", "FALL"}


# ---------------------------------------------------------------------------
# Smoke test 3 — FALL detection creates an alert
# ---------------------------------------------------------------------------

def test_fall_detection_creates_alert(client):
    """99 normal samples + 1 high-SVM sample must create a FALL alert."""
    mock_tg = MagicMock(return_value=(True, None))
    with patch("app.api.sensor.send_fall_alert", mock_tg):
        resp = _post_confirmed_fall(client)

    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "FALL", f"Expected FALL, got {data['state']}"
    assert data["alert_created"] is True
    assert data["alert_id"] is not None


# ---------------------------------------------------------------------------
# Smoke test 4 — alert is persisted in DB and queryable via API
# ---------------------------------------------------------------------------

def test_alert_is_returned_by_api(client):
    """An alert created by FALL detection must appear in GET /api/alerts/recent."""
    mock_tg = MagicMock(return_value=(True, None))
    with patch("app.api.sensor.send_fall_alert", mock_tg):
        ingest_resp = _post_confirmed_fall(client)

    alert_id = ingest_resp.json()["alert_id"]
    assert alert_id is not None

    list_resp = client.get(f"/api/alerts/recent?patient_id={SMOKE_PATIENT_ID}")
    assert list_resp.status_code == 200
    alerts = list_resp.json()["alerts"]
    assert any(a["id"] == alert_id for a in alerts), "Created alert not found in /api/alerts/recent"


# ---------------------------------------------------------------------------
# Smoke test 5 — Telegram is dispatched on FALL
# ---------------------------------------------------------------------------

def test_telegram_is_called_on_fall(client):
    """Telegram send_fall_alert must be called exactly once per FALL alert."""
    mock_tg = MagicMock(return_value=(True, None))
    with patch("app.api.sensor.send_fall_alert", mock_tg):
        _post_confirmed_fall(client)

    mock_tg.assert_called_once()
    kwargs = mock_tg.call_args.kwargs
    assert kwargs["patient_id"] == SMOKE_PATIENT_ID
    assert kwargs["peak_svm"] > 0


# ---------------------------------------------------------------------------
# Smoke test 6 — notification status persisted after Telegram success
# ---------------------------------------------------------------------------

def test_notification_status_persisted(client, test_db):
    """After a successful Telegram send, notification_status must be 'sent'."""
    _, SessionLocal = test_db
    mock_tg = MagicMock(return_value=(True, None))
    with patch("app.api.sensor.send_fall_alert", mock_tg):
        resp = _post_confirmed_fall(client)

    alert_id = resp.json()["alert_id"]
    from app.models.schemas import AlertEvent
    with SessionLocal() as session:
        alert = session.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
        assert alert is not None
        assert alert.notification_status == "sent"


# ---------------------------------------------------------------------------
# Smoke test 7 — acknowledge and feedback round-trip
# ---------------------------------------------------------------------------

def test_acknowledge_and_feedback_round_trip(client):
    """Alert can be acknowledged and marked as valid/false via the API."""
    mock_tg = MagicMock(return_value=(True, None))
    with patch("app.api.sensor.send_fall_alert", mock_tg):
        ingest_resp = _post_confirmed_fall(client)

    alert_id = ingest_resp.json()["alert_id"]

    # Acknowledge
    ack_resp = client.post(f"/api/alerts/acknowledge/{alert_id}", json={"notes": "smoke test"})
    assert ack_resp.status_code == 200
    assert ack_resp.json()["is_acknowledged"] is True

    # Feedback
    fb_resp = client.post(f"/api/alerts/feedback/{alert_id}", json={"label": "valid"})
    assert fb_resp.status_code == 200
    assert fb_resp.json()["feedback"] == "valid"


# ---------------------------------------------------------------------------
# Smoke test 8 — batch ingest works end-to-end
# ---------------------------------------------------------------------------

def test_batch_ingest_creates_alert(client):
    """Batch ingest with a FALL sample after buffer primed must create an alert."""
    mock_tg = MagicMock(return_value=(True, None))
    with patch("app.api.sensor.send_fall_alert", mock_tg):
        # Prime buffer via single samples
        _fill(client, 99)

        # One fall sample is one FALL window, which sustained confirmation
        # deliberately downgrades. This test passed before only because
        # `_fall_streak` leaked in from earlier tests in the module.
        batch_payload = {
            "patient_id": SMOKE_PATIENT_ID,
            "samples": [
                dict(ax=2.0, ay=2.0, az=3.0, gx=0.0, gy=0.0, gz=0.0, svm=3.5)
                for _ in range(FALL_CONFIRM_WINDOWS)
            ],
        }
        resp = client.post("/api/sensor/batch", json=batch_payload)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["created_alert_ids"]) >= 1
    mock_tg.assert_called_once()


# ---------------------------------------------------------------------------
# Smoke test 9 — sustained confirmation is what it claims to be
# ---------------------------------------------------------------------------
def test_single_fall_window_does_not_alert(client):
    """One high-SVM window must not raise an alert on its own.

    This is the contract `FALL_CONFIRM_WINDOWS` exists for, and nothing asserted
    it: every other test posted one fall sample and expected an immediate FALL,
    so the suite described the behaviour the feature replaced.
    """
    mock_tg = MagicMock(return_value=(True, None))
    with patch("app.api.sensor.send_fall_alert", mock_tg):
        _fill(client, WINDOW_SIZE - 1)
        resp = client.post("/api/sensor/data", json=_FALL_SAMPLE)

    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "NORMAL", f"one FALL window must not alert, got {data['state']}"
    assert data["alert_created"] is False
    assert data["alert_id"] is None
    mock_tg.assert_not_called()


def test_sustained_confirmation_only_delays_a_one_sample_spike(client):
    """Pins what sustained confirmation actually does, which is less than claimed.

    `FALL_CONFIRM_WINDOWS` is derived from `FALL_CONFIRM_SECONDS`. Measured here: a
    single one-sample spike, followed only by normal samples, still alerts
    `FALL_CONFIRM_WINDOWS - 1` samples later -- just under FALL_CONFIRM_SECONDS --
    and keeps reading FALL after that.

    The reason is that the gate counts consecutive *windows*, while `peak_svm` is
    the max over a 100-sample sliding buffer. One spike stays inside that buffer
    for 100 windows, so three consecutive FALL windows follow automatically. The
    gate delays the alert; it does not filter a spike.

    Kept as an assertion rather than a comment so this cannot change silently. It
    is not a claim that the current behaviour is the desired one.
    """
    mock_tg = MagicMock(return_value=(True, None))
    with patch("app.api.sensor.send_fall_alert", mock_tg):
        _fill(client, WINDOW_SIZE - 1)
        states = [client.post("/api/sensor/data", json=_FALL_SAMPLE).json()["state"]]
        for _ in range(FALL_CONFIRM_WINDOWS):
            states.append(client.post("/api/sensor/data", json=_NORMAL_SAMPLE).json()["state"])

    assert states[: FALL_CONFIRM_WINDOWS - 1] == ["NORMAL"] * (FALL_CONFIRM_WINDOWS - 1)
    assert states[FALL_CONFIRM_WINDOWS - 1] == "FALL"
    mock_tg.assert_called_once()
