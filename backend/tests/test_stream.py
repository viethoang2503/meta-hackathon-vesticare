"""
test_stream.py

Tests for Phase 4 SSE broadcaster and stream route. Covers:
- broadcaster subscribe / unsubscribe
- broadcaster publish delivers events to subscribers
- QueueFull does not propagate as an unhandled callback error (P1 fix)
- /api/stream route is registered in the FastAPI app
- sensor ingest calls broadcaster.publish after a successful commit
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import FALL_CONFIRM_WINDOWS, WINDOW_SIZE  # noqa: E402
from app.services.broadcaster import SSEBroadcaster  # noqa: E402
from conftest import registered_route_paths  # noqa: E402


# ---------------------------------------------------------------------------
# Broadcaster unit tests
# ---------------------------------------------------------------------------

class BroadcasterTest(unittest.TestCase):
    """Each test gets its own SSEBroadcaster instance and event loop."""

    def setUp(self):
        self.bc = SSEBroadcaster()
        self.loop = asyncio.new_event_loop()
        self.bc.set_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    # ── subscribe / unsubscribe ──

    def test_subscribe_returns_asyncio_queue(self):
        q = self.bc.subscribe()
        self.assertIsInstance(q, asyncio.Queue)

    def test_subscribe_registers_queue_internally(self):
        q = self.bc.subscribe()
        self.assertIn(q, self.bc._queues)

    def test_unsubscribe_removes_queue(self):
        q = self.bc.subscribe()
        self.bc.unsubscribe(q)
        self.assertNotIn(q, self.bc._queues)

    def test_unsubscribe_unknown_queue_does_not_raise(self):
        q = asyncio.Queue()
        self.bc.unsubscribe(q)  # must not raise

    # ── publish ──

    def test_publish_without_loop_does_not_raise(self):
        bc = SSEBroadcaster()  # loop never set
        bc.publish({"state": "NORMAL"})  # must not raise

    def test_publish_delivers_event_to_subscriber(self):
        async def _run():
            q = self.bc.subscribe()
            await self.loop.run_in_executor(
                None, self.bc.publish, {"state": "NORMAL", "svm": 1.0}
            )
            event = await asyncio.wait_for(q.get(), timeout=1.0)
            self.assertEqual(event["state"], "NORMAL")
            self.assertEqual(event["svm"], 1.0)

        self.loop.run_until_complete(_run())

    def test_publish_delivers_to_all_subscribers(self):
        async def _run():
            q1 = self.bc.subscribe()
            q2 = self.bc.subscribe()
            await self.loop.run_in_executor(
                None, self.bc.publish, {"state": "FALL"}
            )
            e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
            e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
            self.assertEqual(e1["state"], "FALL")
            self.assertEqual(e2["state"], "FALL")

        self.loop.run_until_complete(_run())

    def test_full_queue_does_not_propagate_error(self):
        """P1 fix: QueueFull must be caught inside the scheduled callback."""
        async def _run():
            q = self.bc.subscribe()
            # Fill queue to its limit
            for i in range(q.maxsize):
                q.put_nowait({"i": i})

            # Publishing to a full queue must not raise and must not crash
            # the event loop with an unhandled callback exception.
            await self.loop.run_in_executor(
                None, self.bc.publish, {"state": "SWAY"}
            )
            # Give the event loop a tick to execute the scheduled callback
            await asyncio.sleep(0)

            # Queue is still full — the dropped event did not expand it
            self.assertEqual(q.qsize(), q.maxsize)

        self.loop.run_until_complete(_run())

    def test_unsubscribed_queue_does_not_receive_events(self):
        async def _run():
            q = self.bc.subscribe()
            self.bc.unsubscribe(q)
            await self.loop.run_in_executor(
                None, self.bc.publish, {"state": "NORMAL"}
            )
            await asyncio.sleep(0)
            self.assertEqual(q.qsize(), 0)

        self.loop.run_until_complete(_run())


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

class StreamRouteRegistrationTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.routes = registered_route_paths(app)

    def test_stream_route_is_registered(self):
        self.assertIn("/api/stream", self.routes)


# ---------------------------------------------------------------------------
# Sensor ingest publishes event
# ---------------------------------------------------------------------------

class SensorIngestPublishTest(unittest.TestCase):
    PATIENT_ID = 20

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app import database

        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_stream.db"

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
        cls.database = database

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.database.Base.metadata.drop_all(bind=self.engine)
        self.database.Base.metadata.create_all(bind=self.engine)
        from app.services.fall_detection import fall_detection_service
        fall_detection_service.reset_patient(self.PATIENT_ID)

    def _ingest(self, published_list, **overrides):
        from app.api.sensor import SensorReadingPayload, ingest_sensor_data
        defaults = dict(
            patient_id=self.PATIENT_ID,
            ax=0.0, ay=0.0, az=1.0,
            gx=0.0, gy=0.0, gz=0.0,
            svm=1.0,
        )
        payload = SensorReadingPayload(**{**defaults, **overrides})
        with patch("app.api.sensor.broadcaster") as mock_bc:
            mock_bc.publish.side_effect = published_list.append
            db = self.SessionLocal()
            try:
                result = ingest_sensor_data(payload=payload, db=db)
            finally:
                db.close()
        return result

    def test_ingest_calls_broadcast_publish_once(self):
        published = []
        self._ingest(published)
        self.assertEqual(len(published), 1)

    def test_published_event_contains_required_fields(self):
        published = []
        self._ingest(published)
        event = published[0]
        for field in ("patient_id", "state", "svm", "peak_svm",
                      "fall_probability", "window_ready",
                      "alert_created", "alert_id", "timestamp"):
            self.assertIn(field, event, msg=f"Missing field: {field}")

    def _assert_typed_probabilities(self, event):
        """Every IMU event carries a type tag and all three probabilities.

        The dashboard has to tell an IMU prediction apart from other events on
        the same SSE channel, and it shows all three states as bars — so the
        normal probability has to arrive rather than being recomputed per client,
        where three clients could disagree.
        """
        self.assertEqual(event["event_type"], "imu_prediction")
        self.assertIn("instability_probability", event)
        self.assertIn("normal_probability", event)
        self.assertTrue(event["normal_probability_derived"])
        total = (
            event["normal_probability"]
            + event["instability_probability"]
            + event["fall_probability"]
        )
        self.assertAlmostEqual(total, 1.0, places=3)

    def test_single_event_is_typed_with_three_probabilities(self):
        published = []
        self._ingest(published)
        self._assert_typed_probabilities(published[0])

    def test_derived_normal_probability_is_flagged_not_measured(self):
        """The model emits two probabilities; normal is 1 - fall - instability.
        The flag says so, because a reader must not treat it as a third model
        output."""
        published = []
        self._ingest(published)
        self.assertIs(published[0]["normal_probability_derived"], True)

    def test_published_event_patient_id_matches_payload(self):
        published = []
        self._ingest(published)
        self.assertEqual(published[0]["patient_id"], self.PATIENT_ID)

    def test_single_event_contains_one_six_axis_sample(self):
        published = []
        self._ingest(published, gx=0.1, gy=0.2, gz=0.3)
        event = published[0]
        self.assertIsNone(event["transport"])
        self.assertEqual(len(event["samples"]), 1)
        self.assertEqual(
            event["samples"][0],
            {
                "ax": 0.0, "ay": 0.0, "az": 1.0,
                "gx": 0.1, "gy": 0.2, "gz": 0.3,
                "svm": 1.0,
            },
        )

    def test_batch_event_contains_all_samples_and_transport(self):
        from app.api.sensor import BatchSensorReadingPayload, SensorBatchPayload, ingest_sensor_batch

        payload = SensorBatchPayload(
            patient_id=self.PATIENT_ID,
            device_id="esp32-usb",
            transport="usb",
            samples=[
                BatchSensorReadingPayload(ax=0, ay=0, az=1, gx=0, gy=0, gz=0),
                BatchSensorReadingPayload(ax=1, ay=0, az=0, gx=0.1, gy=0.2, gz=0.3),
            ],
        )
        published = []
        with patch("app.api.sensor.broadcaster") as mock_bc:
            mock_bc.publish.side_effect = published.append
            db = self.SessionLocal()
            try:
                ingest_sensor_batch(payload=payload, db=db)
            finally:
                db.close()

        event = published[0]
        self.assertEqual(event["transport"], "usb")
        self.assertEqual(len(event["samples"]), 2)
        self.assertEqual(event["samples"][1]["gx"], 0.1)
        self.assertEqual(event["samples"][0]["svm"], 1.0)
        # The batch path publishes its own dict, so it needs the same contract
        # asserted separately rather than assumed from the single-sample path.
        self._assert_typed_probabilities(event)

    def test_published_event_has_alert_id_on_fall(self):
        from app.api.sensor import SensorReadingPayload, ingest_sensor_data

        all_published = []

        # Prime the window (each sample gets its own patch context)
        for _ in range(WINDOW_SIZE - 1):
            self._ingest(all_published)

        # Then drive FALL_CONFIRM_WINDOWS consecutive FALL windows and keep the
        # event published by the one that confirms -- a single fall sample is one
        # FALL window, which sustained confirmation deliberately downgrades.
        payload = SensorReadingPayload(
            patient_id=self.PATIENT_ID,
            ax=2.0, ay=2.0, az=3.0,
            gx=0.0, gy=0.0, gz=0.0,
            svm=3.5,
        )
        fall_published = []
        for _ in range(FALL_CONFIRM_WINDOWS):
            fall_published = []
            with patch("app.api.sensor.broadcaster") as mock_bc:
                mock_bc.publish.side_effect = fall_published.append
                db = self.SessionLocal()
                try:
                    ingest_sensor_data(payload=payload, db=db)
                finally:
                    db.close()

        self.assertEqual(len(fall_published), 1)
        event = fall_published[0]
        self.assertTrue(event["alert_created"])
        self.assertIsNotNone(event["alert_id"])
        self.assertEqual(event["state"], "FALL")


# ---------------------------------------------------------------------------
# Batch ingest publishes event
# ---------------------------------------------------------------------------

class BatchIngestPublishTest(unittest.TestCase):
    PATIENT_ID = 21

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app import database

        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_stream_batch.db"

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
        cls.database = database

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.database.Base.metadata.drop_all(bind=self.engine)
        self.database.Base.metadata.create_all(bind=self.engine)
        from app.services.fall_detection import fall_detection_service
        fall_detection_service.reset_patient(self.PATIENT_ID)

    def _batch(self, samples_data, published_list):
        from app.api.sensor import BatchSensorReadingPayload, SensorBatchPayload, ingest_sensor_batch
        samples = [BatchSensorReadingPayload(**s) for s in samples_data]
        payload = SensorBatchPayload(patient_id=self.PATIENT_ID, samples=samples)
        with patch("app.api.sensor.broadcaster") as mock_bc:
            mock_bc.publish.side_effect = published_list.append
            db = self.SessionLocal()
            try:
                return ingest_sensor_batch(payload=payload, db=db)
            finally:
                db.close()

    def _normal_sample(self):
        return dict(ax=0.0, ay=0.0, az=1.0, gx=0.0, gy=0.0, gz=0.0, svm=1.0)

    def _fall_sample(self):
        return dict(ax=2.0, ay=2.0, az=3.0, gx=0.0, gy=0.0, gz=0.0, svm=3.5)

    def test_batch_ingest_publishes_exactly_one_event(self):
        published = []
        self._batch([self._normal_sample() for _ in range(5)], published)
        self.assertEqual(len(published), 1)

    def test_batch_event_contains_required_fields(self):
        published = []
        self._batch([self._normal_sample()], published)
        event = published[0]
        for field in ("patient_id", "state", "svm", "peak_svm",
                      "fall_probability", "window_ready",
                      "alert_created", "alert_id", "timestamp"):
            self.assertIn(field, event, msg=f"Missing field: {field}")

    def test_batch_event_patient_id_matches(self):
        published = []
        self._batch([self._normal_sample()], published)
        self.assertEqual(published[0]["patient_id"], self.PATIENT_ID)

    def test_batch_event_alert_created_on_fall(self):
        published = []
        # Fill the window, then supply the FALL_CONFIRM_WINDOWS confirming windows
        # in the same batch -- one fall sample is a single FALL window, which
        # sustained confirmation deliberately downgrades.
        samples = [self._normal_sample() for _ in range(WINDOW_SIZE - 1)] + [
            self._fall_sample() for _ in range(FALL_CONFIRM_WINDOWS)
        ]
        self._batch(samples, published)
        self.assertEqual(len(published), 1)
        event = published[0]
        self.assertTrue(event["alert_created"])
        self.assertIsNotNone(event["alert_id"])
        self.assertEqual(event["state"], "FALL")

    def test_batch_event_no_alert_on_normal_stream(self):
        published = []
        self._batch([self._normal_sample() for _ in range(5)], published)
        event = published[0]
        self.assertFalse(event["alert_created"])
        self.assertIsNone(event["alert_id"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# The broadcaster stays a dumb pipe
# ---------------------------------------------------------------------------

class BroadcasterPassthroughTest(unittest.TestCase):
    """Typing IMU events must not turn the broadcaster into a schema validator.

    Other event types can travel the same channel with a different shape. If publish() ever started requiring
    `event_type` or the probability fields, those would be silently dropped.
    """

    def test_arbitrary_legacy_dict_is_emitted_unchanged(self):
        async def _run():
            bc = SSEBroadcaster()
            bc.set_loop(asyncio.get_running_loop())
            q = bc.subscribe()
            legacy = {"state": "NORMAL", "svm": 1.0, "no_event_type": True}
            bc.publish(legacy)
            await asyncio.sleep(0)
            return await asyncio.wait_for(q.get(), timeout=1)

        self.assertEqual(
            asyncio.run(_run()),
            {"state": "NORMAL", "svm": 1.0, "no_event_type": True},
        )
