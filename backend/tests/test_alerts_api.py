import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import database  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from app.api.alerts import (  # noqa: E402
    AcknowledgeAlertPayload,
    FeedbackPayload,
    acknowledge_alert,
    get_alert_stats,
    get_recent_alerts,
    submit_feedback,
)
from app.models.schemas import AlertEvent  # noqa: E402
from conftest import registered_route_paths  # noqa: E402


class AlertsApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_alerts.db"
        cls.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        cls.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=cls.engine,
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

        now = datetime.now(timezone.utc)
        seed_alerts = [
            AlertEvent(
                patient_id=1,
                timestamp=now - timedelta(minutes=20),
                alert_type="FALL",
                confidence=0.91,
                peak_svm=2.9,
                is_acknowledged=False,
            ),
            AlertEvent(
                patient_id=1,
                timestamp=now - timedelta(minutes=10),
                alert_type="FALL",
                confidence=0.82,
                peak_svm=2.7,
                is_acknowledged=True,
                acknowledged_at=now - timedelta(minutes=5),
                notes="Reviewed",
            ),
            AlertEvent(
                patient_id=2,
                timestamp=now - timedelta(minutes=2),
                alert_type="FALL",
                confidence=0.96,
                peak_svm=3.1,
                is_acknowledged=False,
            ),
        ]

        db = self.SessionLocal()
        try:
            db.add_all(seed_alerts)
            db.commit()
        finally:
            db.close()

    def _call_recent_alerts(self, **kwargs):
        payload = {
            "limit": 10,
            "offset": 0,
            "patient_id": None,
            "include_acknowledged": True,
            "since": None,
            "until": None,
        }
        payload.update(kwargs)
        db = self.SessionLocal()
        try:
            return get_recent_alerts(db=db, **payload).model_dump(mode="json")
        finally:
            db.close()

    def test_alerts_route_is_registered(self):
        route_paths = registered_route_paths(self.app)
        self.assertIn("/api/alerts/recent", route_paths)
        self.assertIn("/api/alerts/acknowledge/{alert_id}", route_paths)

    def test_recent_alerts_returns_latest_sorted_first(self):
        body = self._call_recent_alerts()
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["limit"], 10)
        self.assertEqual(len(body["alerts"]), 3)

        returned_ids = [item["patient_id"] for item in body["alerts"]]
        self.assertEqual(returned_ids, [2, 1, 1])

    def test_recent_alerts_can_filter_by_patient(self):
        body = self._call_recent_alerts(patient_id=1)
        self.assertEqual(body["total"], 2)
        self.assertTrue(all(item["patient_id"] == 1 for item in body["alerts"]))

    def test_recent_alerts_can_exclude_acknowledged(self):
        body = self._call_recent_alerts(include_acknowledged=False)
        self.assertEqual(body["total"], 2)
        self.assertTrue(all(not item["is_acknowledged"] for item in body["alerts"]))

    def test_recent_alerts_respects_limit(self):
        body = self._call_recent_alerts(limit=2)
        # total is the full matching count (3 seeded), not the page size
        self.assertEqual(body["total"], 3)
        self.assertEqual(len(body["alerts"]), 2)

    def test_acknowledge_alert_sets_ack_fields_and_notes(self):
        db = self.SessionLocal()
        try:
            target = (
                db.query(AlertEvent)
                .filter(AlertEvent.is_acknowledged.is_(False))
                .order_by(AlertEvent.id.asc())
                .first()
            )
            target_id = target.id
        finally:
            db.close()

        db = self.SessionLocal()
        try:
            response = acknowledge_alert(
                payload=AcknowledgeAlertPayload(notes="Checked by caregiver"),
                alert_id=target_id,
                db=db,
            ).model_dump(mode="json")
        finally:
            db.close()

        self.assertEqual(response["id"], target_id)
        self.assertTrue(response["is_acknowledged"])
        self.assertEqual(response["notes"], "Checked by caregiver")
        self.assertIsNotNone(response["acknowledged_at"])

        db = self.SessionLocal()
        try:
            updated = db.query(AlertEvent).filter(AlertEvent.id == target_id).first()
            self.assertTrue(updated.is_acknowledged)
            self.assertEqual(updated.notes, "Checked by caregiver")
            self.assertIsNotNone(updated.acknowledged_at)
        finally:
            db.close()

    def test_acknowledge_alert_keeps_existing_timestamp_if_already_acknowledged(self):
        db = self.SessionLocal()
        try:
            target = (
                db.query(AlertEvent)
                .filter(AlertEvent.is_acknowledged.is_(True))
                .order_by(AlertEvent.id.asc())
                .first()
            )
            target_id = target.id
            original_ack_time = target.acknowledged_at
        finally:
            db.close()

        db = self.SessionLocal()
        try:
            response = acknowledge_alert(
                payload=AcknowledgeAlertPayload(notes="Updated note"),
                alert_id=target_id,
                db=db,
            ).model_dump(mode="json")
        finally:
            db.close()

        self.assertTrue(response["is_acknowledged"])
        self.assertEqual(response["notes"], "Updated note")
        self.assertEqual(response["acknowledged_at"], original_ack_time.isoformat())

    def test_acknowledge_alert_not_found_raises_404(self):
        db = self.SessionLocal()
        try:
            with self.assertRaises(HTTPException) as ctx:
                acknowledge_alert(
                    payload=AcknowledgeAlertPayload(notes="Missing"),
                    alert_id=999999,
                    db=db,
                )
        finally:
            db.close()

        self.assertEqual(ctx.exception.status_code, 404)


class AlertStatsTestCase(unittest.TestCase):
    """Tests for GET /api/alerts/stats."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_stats.db"
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

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        database.Base.metadata.drop_all(bind=self.engine)
        database.Base.metadata.create_all(bind=self.engine)

        now = datetime.now(timezone.utc)
        seed = [
            # patient 1: 2 alerts, 1 acked, latency set on both
            AlertEvent(
                patient_id=1,
                timestamp=now - timedelta(hours=1),
                alert_type="FALL",
                confidence=0.9,
                peak_svm=2.8,
                is_acknowledged=True,
                latency_ms=120.0,
            ),
            AlertEvent(
                patient_id=1,
                timestamp=now - timedelta(minutes=5),
                alert_type="FALL",
                confidence=0.85,
                peak_svm=2.6,
                is_acknowledged=False,
                latency_ms=80.0,
            ),
            # patient 2: 1 alert, unacked, no latency
            AlertEvent(
                patient_id=2,
                timestamp=now - timedelta(minutes=30),
                alert_type="FALL",
                confidence=0.95,
                peak_svm=3.0,
                is_acknowledged=False,
                latency_ms=None,
            ),
        ]
        db = self.SessionLocal()
        try:
            db.add_all(seed)
            db.commit()
        finally:
            db.close()

    def _stats(self, patient_id=None):
        db = self.SessionLocal()
        try:
            return get_alert_stats(patient_id=patient_id, db=db).model_dump()
        finally:
            db.close()

    def test_stats_route_is_registered(self):
        from app.main import app

        paths = registered_route_paths(app)
        self.assertIn("/api/alerts/stats", paths)

    def test_stats_total_counts_all_alerts(self):
        stats = self._stats()
        self.assertEqual(stats["total_alerts"], 3)

    def test_stats_unacknowledged_count(self):
        stats = self._stats()
        self.assertEqual(stats["unacknowledged"], 2)

    def test_stats_alerts_today_includes_recent(self):
        stats = self._stats()
        # All 3 alerts were seeded within the last hour — all qualify as "today"
        self.assertGreaterEqual(stats["alerts_today"], 2)

    def test_stats_avg_latency_ms_excludes_nulls(self):
        # 2 alerts with latency (120 + 80) / 2 = 100.0
        stats = self._stats()
        self.assertAlmostEqual(stats["avg_latency_ms"], 100.0, places=0)

    def test_stats_avg_latency_ms_is_none_when_no_data(self):
        # patient 2 has only null latency
        stats = self._stats(patient_id=2)
        self.assertIsNone(stats["avg_latency_ms"])

    def test_stats_filtered_by_patient(self):
        stats = self._stats(patient_id=1)
        self.assertEqual(stats["total_alerts"], 2)
        self.assertEqual(stats["unacknowledged"], 1)

    def test_stats_response_has_required_fields(self):
        stats = self._stats()
        for field in ("total_alerts", "unacknowledged", "alerts_today", "avg_latency_ms"):
            self.assertIn(field, stats, msg=f"Missing field: {field}")


class TimeFilterTestCase(unittest.TestCase):
    """Tests for since/until/offset params on GET /api/alerts/recent."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_timefilter.db"
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

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        database.Base.metadata.drop_all(bind=self.engine)
        database.Base.metadata.create_all(bind=self.engine)

        self.now = datetime.now(timezone.utc)
        seed = [
            AlertEvent(
                patient_id=1,
                timestamp=self.now - timedelta(hours=3),
                alert_type="FALL", confidence=0.9, peak_svm=2.5, is_acknowledged=False,
            ),
            AlertEvent(
                patient_id=1,
                timestamp=self.now - timedelta(hours=2),
                alert_type="FALL", confidence=0.8, peak_svm=2.3, is_acknowledged=False,
            ),
            AlertEvent(
                patient_id=1,
                timestamp=self.now - timedelta(hours=1),
                alert_type="FALL", confidence=0.85, peak_svm=2.4, is_acknowledged=False,
            ),
        ]
        db = self.SessionLocal()
        try:
            db.add_all(seed)
            db.commit()
        finally:
            db.close()

    def _recent(self, **kwargs):
        payload = {
            "limit": 10, "offset": 0, "patient_id": None,
            "include_acknowledged": True, "since": None, "until": None,
        }
        payload.update(kwargs)
        db = self.SessionLocal()
        try:
            return get_recent_alerts(db=db, **payload).model_dump(mode="json")
        finally:
            db.close()

    def test_since_excludes_older_alerts(self):
        cutoff = self.now - timedelta(hours=2, minutes=30)
        body = self._recent(since=cutoff)
        # Only the -2h and -1h alerts fall at or after the cutoff
        self.assertEqual(body["total"], 2)

    def test_until_excludes_newer_alerts(self):
        cutoff = self.now - timedelta(hours=1, minutes=30)
        body = self._recent(until=cutoff)
        # Only the -3h and -2h alerts fall at or before the cutoff
        self.assertEqual(body["total"], 2)

    def test_since_and_until_narrow_range(self):
        since = self.now - timedelta(hours=2, minutes=30)
        until = self.now - timedelta(hours=1, minutes=30)
        body = self._recent(since=since, until=until)
        self.assertEqual(body["total"], 1)

    def test_offset_skips_records(self):
        body = self._recent(limit=2, offset=1)
        # 3 total; offset 1 → 2 returned (the 2nd and 3rd newest)
        self.assertEqual(body["total"], 3)
        self.assertEqual(len(body["alerts"]), 2)

    def test_offset_beyond_total_returns_empty(self):
        body = self._recent(offset=10)
        self.assertEqual(body["total"], 3)
        self.assertEqual(len(body["alerts"]), 0)


class FeedbackTestCase(unittest.TestCase):
    """Tests for POST /api/alerts/feedback/{id} and false_alert_count in stats."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_feedback.db"
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

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        database.Base.metadata.drop_all(bind=self.engine)
        database.Base.metadata.create_all(bind=self.engine)

        seed = AlertEvent(
            patient_id=1,
            timestamp=datetime.now(timezone.utc),
            alert_type="FALL",
            confidence=0.9,
            peak_svm=3.0,
            is_acknowledged=False,
        )
        db = self.SessionLocal()
        try:
            db.add(seed)
            db.commit()
            db.refresh(seed)
            self._alert_id = seed.id
        finally:
            db.close()

    def _submit(self, label, alert_id=None):
        if alert_id is None:
            alert_id = self._alert_id
        db = self.SessionLocal()
        try:
            return submit_feedback(
                payload=FeedbackPayload(label=label),
                alert_id=alert_id,
                db=db,
            ).model_dump(mode="json")
        finally:
            db.close()

    def test_feedback_valid_persists_in_db(self):
        self._submit("valid")
        db = self.SessionLocal()
        try:
            alert = db.query(AlertEvent).filter(AlertEvent.id == self._alert_id).first()
            self.assertEqual(alert.feedback, "valid")
        finally:
            db.close()

    def test_feedback_false_persists_in_db(self):
        self._submit("false")
        db = self.SessionLocal()
        try:
            alert = db.query(AlertEvent).filter(AlertEvent.id == self._alert_id).first()
            self.assertEqual(alert.feedback, "false")
        finally:
            db.close()

    def test_feedback_response_contains_label(self):
        resp = self._submit("valid")
        self.assertEqual(resp["feedback"], "valid")

    def test_feedback_not_found_raises_404(self):
        db = self.SessionLocal()
        try:
            with self.assertRaises(HTTPException) as ctx:
                submit_feedback(
                    payload=FeedbackPayload(label="false"),
                    alert_id=999999,
                    db=db,
                )
        finally:
            db.close()
        self.assertEqual(ctx.exception.status_code, 404)

    def test_feedback_overwrite_allowed(self):
        """Second feedback call replaces the first."""
        self._submit("valid")
        self._submit("false")
        db = self.SessionLocal()
        try:
            alert = db.query(AlertEvent).filter(AlertEvent.id == self._alert_id).first()
            self.assertEqual(alert.feedback, "false")
        finally:
            db.close()

    def test_stats_false_alert_count_reflects_feedback(self):
        self._submit("false")
        db = self.SessionLocal()
        try:
            stats = get_alert_stats(patient_id=None, db=db).model_dump()
        finally:
            db.close()
        self.assertEqual(stats["false_alert_count"], 1)

    def test_stats_false_alert_count_zero_without_feedback(self):
        db = self.SessionLocal()
        try:
            stats = get_alert_stats(patient_id=None, db=db).model_dump()
        finally:
            db.close()
        self.assertEqual(stats["false_alert_count"], 0)

    def test_stats_response_has_false_alert_count_field(self):
        db = self.SessionLocal()
        try:
            stats = get_alert_stats(patient_id=None, db=db).model_dump()
        finally:
            db.close()
        self.assertIn("false_alert_count", stats)

    def test_feedback_route_is_registered(self):
        from app.main import app

        paths = registered_route_paths(app)
        self.assertIn("/api/alerts/feedback/{alert_id}", paths)


if __name__ == "__main__":
    unittest.main()
