"""
test_http.py

HTTP-level tests using FastAPI TestClient.
These complement the function-level tests by exercising the full request/response
cycle: routing, middleware, serialisation, and HTTP status codes.

Covers:
- GET  /api/health
- POST /api/sensor/data   — happy path, validation errors
- POST /api/sensor/batch  — happy path, empty batch
- GET  /api/alerts/recent — filtering, limit
- POST /api/alerts/acknowledge/{id} — happy path, 404
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import database  # noqa: E402
from app.config import FALL_CONFIRM_WINDOWS, FALL_STILLNESS_SECONDS, SAMPLING_RATE, WINDOW_SIZE  # noqa: E402

# A fall alerts once the wearer is still for FALL_STILLNESS_SECONDS after it (services/monitoring.py).
STILLNESS_SAMPLES = int(FALL_STILLNESS_SECONDS * SAMPLING_RATE)
from app.models.schemas import AlertEvent  # noqa: E402
from app.services.fall_detection import fall_detection_service  # noqa: E402
from app.api.sensor import reset_stream  # noqa: E402

PATIENT_ID = 20  # Dedicated ID — does not collide with function-level tests

_NORMAL_PAYLOAD = dict(
    patient_id=PATIENT_ID,
    ax=0.0, ay=0.0, az=1.0,
    gx=0.0, gy=0.0, gz=0.0,
    svm=1.0,
)
_FALL_PAYLOAD = dict(
    patient_id=PATIENT_ID,
    ax=2.0, ay=2.0, az=3.0,
    gx=0.0, gy=0.0, gz=0.0,
    svm=3.5,
)


def _make_client(session_local) -> TestClient:
    """Return a TestClient wired to the given session factory."""
    from app.main import app
    from app.database import get_db

    def _override_get_db():
        db = session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


class HttpTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_http.db"
        cls.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        cls.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=cls.engine
        )
        database.engine = cls.engine
        database.SessionLocal = cls.SessionLocal
        database.Base.metadata.create_all(bind=cls.engine)

        cls.client = _make_client(cls.SessionLocal)

    @classmethod
    def tearDownClass(cls):
        from app.main import app
        from app.database import get_db
        app.dependency_overrides.pop(get_db, None)
        cls.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        database.Base.metadata.drop_all(bind=self.engine)
        database.Base.metadata.create_all(bind=self.engine)
        reset_stream(PATIENT_ID)

    def _confirm_fall(self):
        """Prime the window, then post enough FALL windows to clear confirmation.

        One high-SVM sample is one FALL window; `FALL_CONFIRM_WINDOWS` consecutive
        FALL windows are required before an alert is raised. These tests posted one
        and expected an immediate alert.
        """

        for _ in range(WINDOW_SIZE - 1):
            self.client.post("/api/sensor/data", json=_NORMAL_PAYLOAD)
        response = None
        for _ in range(FALL_CONFIRM_WINDOWS + STILLNESS_SAMPLES):
            response = self.client.post("/api/sensor/data", json=_FALL_PAYLOAD)
            if response.json()["alert_created"]:
                break
        return response

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def test_health_returns_200(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "online")

    def test_root_serves_live_dashboard(self):
        from app.main import app
        from starlette.routing import Mount

        dashboard_mounts = [
            route for route in app.routes
            if isinstance(route, Mount) and route.path == ""
        ]
        self.assertEqual(len(dashboard_mounts), 1)
        self.assertTrue(dashboard_mounts[0].app.html)

    # ------------------------------------------------------------------
    # POST /api/sensor/data
    # ------------------------------------------------------------------

    def test_sensor_data_happy_path(self):
        r = self.client.post("/api/sensor/data", json=_NORMAL_PAYLOAD)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("state", body)
        self.assertIn("samples_buffered", body)
        self.assertIn("alert_id", body)

    def test_sensor_data_missing_field_returns_422(self):
        payload = {k: v for k, v in _NORMAL_PAYLOAD.items() if k != "ax"}
        r = self.client.post("/api/sensor/data", json=payload)
        self.assertEqual(r.status_code, 422)

    def test_sensor_data_invalid_patient_id_returns_422(self):
        r = self.client.post("/api/sensor/data", json={**_NORMAL_PAYLOAD, "patient_id": 0})
        self.assertEqual(r.status_code, 422)

    def test_sensor_data_buffering_state_on_first_sample(self):
        r = self.client.post("/api/sensor/data", json=_NORMAL_PAYLOAD)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["state"], "BUFFERING")
        self.assertFalse(r.json()["window_ready"])

    def test_sensor_data_window_ready_after_100_samples(self):
        for _ in range(100):
            r = self.client.post("/api/sensor/data", json=_NORMAL_PAYLOAD)
            self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["window_ready"])
        self.assertIn(r.json()["state"], {"NORMAL", "SWAY", "FALL"})

    def test_sensor_data_fall_creates_alert(self):
        r = self._confirm_fall()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["state"], "FALL")
        self.assertTrue(body["alert_created"])
        self.assertIsNotNone(body["alert_id"])

    def test_sensor_data_fall_alert_is_in_db(self):
        r = self._confirm_fall()
        alert_id = r.json()["alert_id"]
        self.assertIsNotNone(alert_id)

        db = self.SessionLocal()
        try:
            alert = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
            self.assertIsNotNone(alert)
            self.assertEqual(alert.alert_type, "FALL")
        finally:
            db.close()

    def test_sensor_data_second_fall_suppressed_by_cooldown(self):
        r1 = self._confirm_fall()
        self.assertTrue(r1.json()["alert_created"])

        r2 = self.client.post("/api/sensor/data", json=_FALL_PAYLOAD)
        self.assertFalse(r2.json()["alert_created"])
        self.assertTrue(r2.json()["alert_suppressed"])

    # ------------------------------------------------------------------
    # POST /api/sensor/batch
    # ------------------------------------------------------------------

    def test_sensor_batch_happy_path(self):
        samples = [
            {"ax": 0.0, "ay": 0.0, "az": 1.0, "gx": 0.0, "gy": 0.0, "gz": 0.0, "svm": 1.0}
            for _ in range(10)
        ]
        r = self.client.post(
            "/api/sensor/batch",
            json={"patient_id": PATIENT_ID, "samples": samples},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["stored_samples"], 10)

    def test_sensor_batch_empty_returns_422(self):
        r = self.client.post(
            "/api/sensor/batch",
            json={"patient_id": PATIENT_ID, "samples": []},
        )
        self.assertEqual(r.status_code, 422)

    def test_sensor_batch_invalid_patient_id_returns_422(self):
        samples = [{"ax": 0.0, "ay": 0.0, "az": 1.0, "gx": 0.0, "gy": 0.0, "gz": 0.0}]
        r = self.client.post(
            "/api/sensor/batch",
            json={"patient_id": -1, "samples": samples},
        )
        self.assertEqual(r.status_code, 422)

    # ------------------------------------------------------------------
    # GET /api/alerts/recent
    # ------------------------------------------------------------------

    def _seed_alerts(self):
        now = datetime.now(timezone.utc)
        db = self.SessionLocal()
        try:
            db.add_all([
                AlertEvent(
                    patient_id=1, timestamp=now - timedelta(minutes=20),
                    alert_type="FALL", confidence=0.91, peak_svm=2.9, is_acknowledged=False,
                ),
                AlertEvent(
                    patient_id=1, timestamp=now - timedelta(minutes=10),
                    alert_type="FALL", confidence=0.82, peak_svm=2.7,
                    is_acknowledged=True, acknowledged_at=now - timedelta(minutes=5),
                ),
                AlertEvent(
                    patient_id=2, timestamp=now - timedelta(minutes=2),
                    alert_type="FALL", confidence=0.96, peak_svm=3.1, is_acknowledged=False,
                ),
            ])
            db.commit()
        finally:
            db.close()

    def test_recent_alerts_returns_200_and_list(self):
        self._seed_alerts()
        r = self.client.get("/api/alerts/recent")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("alerts", body)
        self.assertEqual(body["total"], 3)

    def test_recent_alerts_sorted_newest_first(self):
        self._seed_alerts()
        r = self.client.get("/api/alerts/recent")
        patient_ids = [a["patient_id"] for a in r.json()["alerts"]]
        self.assertEqual(patient_ids, [2, 1, 1])

    def test_recent_alerts_filter_by_patient(self):
        self._seed_alerts()
        r = self.client.get("/api/alerts/recent?patient_id=1")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(all(a["patient_id"] == 1 for a in r.json()["alerts"]))

    def test_recent_alerts_exclude_acknowledged(self):
        self._seed_alerts()
        r = self.client.get("/api/alerts/recent?include_acknowledged=false")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(all(not a["is_acknowledged"] for a in r.json()["alerts"]))

    def test_recent_alerts_limit(self):
        self._seed_alerts()
        r = self.client.get("/api/alerts/recent?limit=2")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["alerts"]), 2)

    # ------------------------------------------------------------------
    # POST /api/alerts/acknowledge/{id}
    # ------------------------------------------------------------------

    def test_acknowledge_alert_returns_200_and_updates(self):
        self._seed_alerts()
        db = self.SessionLocal()
        try:
            target = (
                db.query(AlertEvent)
                .filter(AlertEvent.is_acknowledged.is_(False))
                .order_by(AlertEvent.id.asc())
                .first()
            )
            alert_id = target.id
        finally:
            db.close()

        r = self.client.post(
            f"/api/alerts/acknowledge/{alert_id}",
            json={"notes": "Checked"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["is_acknowledged"])
        self.assertEqual(body["notes"], "Checked")
        self.assertIsNotNone(body["acknowledged_at"])

    def test_acknowledge_missing_alert_returns_404(self):
        r = self.client.post(
            "/api/alerts/acknowledge/999999",
            json={"notes": "Missing"},
        )
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
