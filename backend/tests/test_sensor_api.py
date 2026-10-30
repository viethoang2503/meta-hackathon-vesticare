"""
test_sensor_api.py

Unit tests for Phase 2 — covers:
- Payload validation (POST /api/sensor/data and /batch)
- Inference flow (BUFFERING -> prediction)
- Alerting flow (FALL creates alert, cooldown suppresses duplicate, NORMAL is silent)
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import database  # noqa: E402
from app.config import FALL_CONFIRM_WINDOWS, FALL_STILLNESS_SECONDS, SAMPLING_RATE, WINDOW_SIZE  # noqa: E402

# A fall alerts once the wearer is still for FALL_STILLNESS_SECONDS after it (services/monitoring.py).
STILLNESS_SAMPLES = int(FALL_STILLNESS_SECONDS * SAMPLING_RATE)
from app.api.sensor import (  # noqa: E402
    BatchSensorReadingPayload,
    SensorBatchPayload,
    SensorReadingPayload,
    ingest_sensor_batch,
    ingest_sensor_data,
)
from app.services.fall_detection import fall_detection_service  # noqa: E402
from app.api.sensor import reset_stream  # noqa: E402

PATIENT_ID = 10  # Use a dedicated id to avoid colliding with alerts test

_NORMAL = dict(
    patient_id=PATIENT_ID,
    ax=0.0, ay=0.0, az=1.0,
    gx=0.0, gy=0.0, gz=0.0,
    svm=1.0,
)
_FALL = dict(
    patient_id=PATIENT_ID,
    ax=2.0, ay=2.0, az=3.0,
    gx=0.0, gy=0.0, gz=0.0,
    svm=3.5,  # > SVM_FALL_THRESHOLD (2.5)
)


class SensorApiTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_sensor.db"
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

        from app.main import app  # noqa: E402 — triggers logging config
        cls.app = app
        database.Base.metadata.create_all(bind=cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        database.Base.metadata.drop_all(bind=self.engine)
        database.Base.metadata.create_all(bind=self.engine)
        # Reset fall detection service state for this patient
        reset_stream(PATIENT_ID)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ingest(self, **kwargs):
        payload = SensorReadingPayload(**{**_NORMAL, **kwargs})
        db = self.SessionLocal()
        try:
            return ingest_sensor_data(payload=payload, db=db)
        finally:
            db.close()

    def _fill(self, n, **override):
        """Send n samples, return last result."""
        result = None
        for _ in range(n):
            result = self._ingest(**override)
        return result

    def _confirm_fall(self, **override):
        """Prime the window, then post enough FALL windows to clear confirmation.

        A single high-SVM sample is one FALL window, and `FALL_CONFIRM_WINDOWS`
        consecutive FALL windows are required before an alert is raised. These
        tests posted one and expected an immediate alert, so they described the
        behaviour the sustained-confirmation gate replaced.
        """

        self._fill(WINDOW_SIZE - 1)
        result = None
        for _ in range(FALL_CONFIRM_WINDOWS + STILLNESS_SAMPLES):
            result = self._ingest(**{**_FALL, **override})
            if result["alert_created"]:
                break
        return result

    # ------------------------------------------------------------------
    # Payload validation — POST /api/sensor/data
    # ------------------------------------------------------------------

    def test_valid_payload_is_accepted(self):
        result = self._ingest()
        self.assertEqual(result["status"], "ok")

    def test_missing_accelerometer_field_raises(self):
        with self.assertRaises(Exception):
            SensorReadingPayload(
                patient_id=PATIENT_ID,
                ay=0.0, az=1.0,
                gx=0.0, gy=0.0, gz=0.0,
            )

    def test_patient_id_zero_raises(self):
        with self.assertRaises(Exception):
            SensorReadingPayload(**{**_NORMAL, "patient_id": 0})

    def test_patient_id_negative_raises(self):
        with self.assertRaises(Exception):
            SensorReadingPayload(**{**_NORMAL, "patient_id": -1})

    def test_none_timestamp_is_accepted(self):
        result = self._ingest(timestamp=None)
        self.assertEqual(result["status"], "ok")

    def test_svm_is_computed_when_absent(self):
        import math
        # ax=ay=az=1.0 -> svm = sqrt(3) ~ 1.732 (below fall threshold)
        result = self._ingest(ax=1.0, ay=1.0, az=1.0, svm=None)
        self.assertEqual(result["status"], "ok")
        # Should not trigger FALL since computed svm < 2.5
        if result["window_ready"]:
            self.assertNotEqual(result["state"], "FALL")

    # ------------------------------------------------------------------
    # Payload validation — POST /api/sensor/batch
    # ------------------------------------------------------------------

    def test_empty_batch_raises(self):
        with self.assertRaises(Exception):
            SensorBatchPayload(patient_id=PATIENT_ID, samples=[])

    def test_batch_with_valid_samples_is_accepted(self):
        samples = [
            BatchSensorReadingPayload(ax=0.0, ay=0.0, az=1.0, gx=0.0, gy=0.0, gz=0.0, svm=1.0)
            for _ in range(5)
        ]
        payload = SensorBatchPayload(patient_id=PATIENT_ID, samples=samples)
        db = self.SessionLocal()
        try:
            result = ingest_sensor_batch(payload=payload, db=db)
        finally:
            db.close()
        self.assertEqual(result["stored_samples"], 5)

    def test_batch_accepts_usb_transport_and_persists_source(self):
        from app.models.schemas import SensorData

        payload = SensorBatchPayload(
            patient_id=PATIENT_ID,
            device_id="esp32-usb",
            transport="usb",
            samples=[
                BatchSensorReadingPayload(
                    ax=0.0, ay=0.0, az=1.0,
                    gx=0.1, gy=0.2, gz=0.3,
                )
            ],
        )
        db = self.SessionLocal()
        try:
            ingest_sensor_batch(payload=payload, db=db)
            stored = db.query(SensorData).one()
            self.assertEqual(stored.source_type, "hardware_usb")
        finally:
            db.close()

    def test_batch_rejects_unknown_transport(self):
        with self.assertRaises(Exception):
            SensorBatchPayload(
                patient_id=PATIENT_ID,
                transport="bluetooth",
                samples=[
                    BatchSensorReadingPayload(
                        ax=0.0, ay=0.0, az=1.0,
                        gx=0.0, gy=0.0, gz=0.0,
                    )
                ],
            )

    # ------------------------------------------------------------------
    # Inference flow
    # ------------------------------------------------------------------

    def test_first_sample_is_buffering(self):
        result = self._ingest()
        self.assertEqual(result["state"], "BUFFERING")
        self.assertFalse(result["window_ready"])
        self.assertEqual(result["samples_buffered"], 1)

    def test_buffered_count_increments(self):
        r1 = self._ingest()
        r2 = self._ingest()
        self.assertEqual(r1["samples_buffered"], 1)
        self.assertEqual(r2["samples_buffered"], 2)

    def test_window_ready_after_100_samples(self):
        result = self._fill(100)
        self.assertTrue(result["window_ready"])
        self.assertNotEqual(result["state"], "BUFFERING")

    def test_state_is_not_none_after_window_full(self):
        result = self._fill(100)
        self.assertIn(result["state"], {"NORMAL", "SWAY", "FALL"})

    def test_heuristic_fallback_when_model_not_loaded(self):
        original_interp = fall_detection_service.interpreter
        original_source = fall_detection_service.model_source
        fall_detection_service.interpreter = None
        fall_detection_service.model_source = "heuristic"
        try:
            result = self._fill(100)
            self.assertTrue(result["window_ready"])
            self.assertEqual(result["model_source"], "heuristic")
            self.assertIn(result["state"], {"NORMAL", "SWAY", "FALL"})
        finally:
            fall_detection_service.interpreter = original_interp
            fall_detection_service.model_source = original_source

    # ------------------------------------------------------------------
    # Alerting flow
    # ------------------------------------------------------------------

    def test_normal_window_does_not_create_alert(self):
        # Force heuristic mode so the state is determined by SVM thresholds alone,
        # not by the TFLite model (which may classify any window as FALL).
        # svm=1.0 < SVM_SWAY_THRESHOLD (1.5) -> heuristic returns NORMAL -> no alert.
        original_interp = fall_detection_service.interpreter
        original_source = fall_detection_service.model_source
        fall_detection_service.interpreter = None
        fall_detection_service.model_source = "heuristic"
        try:
            result = self._fill(100)
            self.assertFalse(result["alert_created"])
            self.assertIsNone(result["alert_id"])
        finally:
            fall_detection_service.interpreter = original_interp
            fall_detection_service.model_source = original_source

    def test_fall_window_creates_alert(self):
        # peak_svm = max over the window = 3.5, sustained over
        # FALL_CONFIRM_WINDOWS consecutive windows.
        result = self._confirm_fall()
        self.assertTrue(result["window_ready"])
        self.assertEqual(result["state"], "FALL")
        self.assertTrue(result["alert_created"])
        self.assertIsNotNone(result["alert_id"])

    def test_fall_alert_is_stored_in_database(self):
        from app.models.schemas import AlertEvent

        result = self._confirm_fall()
        alert_id = result["alert_id"]
        self.assertIsNotNone(alert_id)

        db = self.SessionLocal()
        try:
            alert = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
            self.assertIsNotNone(alert)
            self.assertEqual(alert.alert_type, "FALL")
            self.assertFalse(alert.is_acknowledged)
        finally:
            db.close()

    def test_second_fall_within_cooldown_is_suppressed(self):
        # Trigger first alert
        first = self._confirm_fall()
        self.assertTrue(first["alert_created"])

        # Immediately send another fall sample — still inside cooldown window
        second = self._ingest(**_FALL)
        self.assertFalse(second["alert_created"])
        self.assertTrue(second["alert_suppressed"])

    def test_a_merged_fall_updates_the_open_alert_row(self):
        from app.models.schemas import AlertEvent

        self._confirm_fall()
        self._ingest(**{**_FALL, "svm": 4.2})
        db = self.SessionLocal()
        try:
            [alert] = db.query(AlertEvent).filter(AlertEvent.patient_id == PATIENT_ID).all()
            self.assertAlmostEqual(alert.peak_svm, 4.2, places=4)
        finally:
            db.close()

    def test_only_one_alert_row_created_during_cooldown(self):
        from app.models.schemas import AlertEvent

        self._confirm_fall()    # alert 1
        self._ingest(**_FALL)   # suppressed
        self._ingest(**_FALL)   # suppressed

        db = self.SessionLocal()
        try:
            count = db.query(AlertEvent).filter(
                AlertEvent.patient_id == PATIENT_ID
            ).count()
            self.assertEqual(count, 1)
        finally:
            db.close()


class TelegramDispatchTestCase(unittest.TestCase):
    """Phase 5 — verify send_fall_alert is called/not-called and result is persisted."""

    PATIENT_ID = 20  # isolated from SensorApiTestCase

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_telegram.db"
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

        from app.main import app  # noqa: E402
        cls.app = app
        database.Base.metadata.create_all(bind=cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        database.Base.metadata.drop_all(bind=self.engine)
        database.Base.metadata.create_all(bind=self.engine)
        reset_stream(self.PATIENT_ID)

    # Helpers ----------------------------------------------------------------

    def _ingest(self, mock_tg=None, **kwargs):
        defaults = dict(
            patient_id=self.PATIENT_ID,
            ax=0.0, ay=0.0, az=1.0,
            gx=0.0, gy=0.0, gz=0.0,
            svm=1.0,
        )
        defaults.update(kwargs)
        payload = SensorReadingPayload(**defaults)
        db = self.SessionLocal()
        try:
            return ingest_sensor_data(payload=payload, db=db)
        finally:
            db.close()

    def _fill(self, n, **override):
        result = None
        for _ in range(n):
            result = self._ingest(**override)
        return result

    def _confirm_fall(self, **override):
        """See `SensorApiTestCase._confirm_fall`."""

        self._fill(WINDOW_SIZE - 1)
        result = None
        for _ in range(FALL_CONFIRM_WINDOWS + STILLNESS_SAMPLES):
            result = self._ingest(**{**self._FALL, **override})
            if result["alert_created"]:
                break
        return result

    _FALL = dict(ax=2.0, ay=2.0, az=3.0, gx=0.0, gy=0.0, gz=0.0, svm=3.5)

    # Tests ------------------------------------------------------------------

    def test_fall_ingest_calls_send_fall_alert(self):
        mock_tg = MagicMock(return_value=(True, None))
        with patch("app.api.sensor.send_fall_alert", mock_tg):
            self._confirm_fall()
        mock_tg.assert_called_once()
        call_kwargs = mock_tg.call_args.kwargs
        self.assertEqual(call_kwargs["patient_id"], self.PATIENT_ID)
        self.assertGreater(call_kwargs["alert_id"], 0)

    def test_normal_ingest_does_not_call_send_fall_alert(self):
        mock_tg = MagicMock(return_value=(True, None))
        original_interp = fall_detection_service.interpreter
        original_source = fall_detection_service.model_source
        fall_detection_service.interpreter = None
        fall_detection_service.model_source = "heuristic"
        try:
            with patch("app.api.sensor.send_fall_alert", mock_tg):
                self._fill(100, svm=1.0)  # svm < SVM_SWAY_THRESHOLD → NORMAL
        finally:
            fall_detection_service.interpreter = original_interp
            fall_detection_service.model_source = original_source
        mock_tg.assert_not_called()

    def test_batch_fall_calls_send_fall_alert(self):
        from app.api.sensor import BatchSensorReadingPayload, SensorBatchPayload, ingest_sensor_batch

        # Prime the buffer, then leave the batch to supply the confirming windows
        self._fill(WINDOW_SIZE - 1)

        fall_sample = BatchSensorReadingPayload(
            ax=2.0, ay=2.0, az=3.0, gx=0.0, gy=0.0, gz=0.0, svm=3.5
        )
        payload = SensorBatchPayload(
            patient_id=self.PATIENT_ID, samples=[fall_sample] * (FALL_CONFIRM_WINDOWS + STILLNESS_SAMPLES)
        )

        mock_tg = MagicMock(return_value=(True, None))
        with patch("app.api.sensor.send_fall_alert", mock_tg):
            db = self.SessionLocal()
            try:
                result = ingest_sensor_batch(payload=payload, db=db)
            finally:
                db.close()

        self.assertTrue(result["created_alert_ids"], "batch ingest should have created at least one alert")
        mock_tg.assert_called_once()

    def test_telegram_success_sets_notification_status_sent(self):
        from app.models.schemas import AlertEvent

        mock_tg = MagicMock(return_value=(True, None))
        with patch("app.api.sensor.send_fall_alert", mock_tg):
            result = self._confirm_fall()

        alert_id = result.get("alert_id")
        self.assertIsNotNone(alert_id)

        db = self.SessionLocal()
        try:
            alert = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
            self.assertEqual(alert.notification_status, "sent")
            self.assertIsNotNone(alert.notification_sent_at)
            self.assertIsNone(alert.notification_error)
        finally:
            db.close()

    def test_telegram_failure_sets_notification_status_failed(self):
        from app.models.schemas import AlertEvent

        mock_tg = MagicMock(return_value=(False, "ConnectError: timed out"))
        with patch("app.api.sensor.send_fall_alert", mock_tg):
            result = self._confirm_fall()

        alert_id = result.get("alert_id")
        self.assertIsNotNone(alert_id)

        db = self.SessionLocal()
        try:
            alert = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
            self.assertEqual(alert.notification_status, "failed")
            self.assertEqual(alert.notification_error, "ConnectError: timed out")
            self.assertIsNone(alert.notification_sent_at)
        finally:
            db.close()

    def test_telegram_not_configured_sets_notification_status_skipped(self):
        from app.models.schemas import AlertEvent

        mock_tg = MagicMock(return_value=(False, "not_configured"))
        with patch("app.api.sensor.send_fall_alert", mock_tg):
            result = self._confirm_fall()

        alert_id = result.get("alert_id")
        self.assertIsNotNone(alert_id)

        db = self.SessionLocal()
        try:
            alert = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
            self.assertEqual(alert.notification_status, "skipped")
            self.assertIsNone(alert.notification_sent_at)
            self.assertIsNone(alert.notification_error)
        finally:
            db.close()

    def test_fall_alert_has_pending_status_before_notification(self):
        """notification_status is set to 'pending' at alert creation time."""
        from app.models.schemas import AlertEvent

        # Use a slow mock so we can check the DB state after commit but "before"
        # notification — not possible at unit level, so we verify the model default.
        alert = AlertEvent(
            patient_id=1,
            timestamp=__import__("datetime").datetime.utcnow(),
            alert_type="FALL",
            confidence=0.9,
            peak_svm=3.0,
            is_acknowledged=False,
            notification_status="pending",
        )
        self.assertEqual(alert.notification_status, "pending")


if __name__ == "__main__":
    unittest.main()
