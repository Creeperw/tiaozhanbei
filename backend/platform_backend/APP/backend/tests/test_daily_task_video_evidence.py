import unittest
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.auth import get_current_user
from APP.backend.database import get_db


class DailyTaskVideoEvidenceTests(unittest.TestCase):
    def setUp(self):
        from APP.backend.main import app

        self.app = app
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([
                database.UserModel(id=1, username="learner", email="a@example.com", hashed_password="x"),
                database.UserModel(id=2, username="other", email="b@example.com", hashed_password="x"),
                database.DailyTaskItemRecord(
                    task_item_id="VIDEO_HTML5", host_task_id="TASK_1", host_task_version=1,
                    user_id=1, kp_id="KP_1", item_kind="video", ordinal=1,
                    required_question_count=0,
                    resource_ref={"url": "https://example.test/video.mp4", "start_seconds": 0, "end_seconds": 100},
                    completion_policy={"policy": "html5_coverage", "coverage_threshold": 0.9},
                    status="pending",
                ),
                database.DailyTaskItemRecord(
                    task_item_id="VIDEO_IFRAME", host_task_id="TASK_1", host_task_version=1,
                    user_id=1, kp_id="KP_1", item_kind="video", ordinal=2,
                    required_question_count=0,
                    resource_ref={"provider": "bilibili", "start_seconds": 10, "end_seconds": 110},
                    completion_policy={"policy": "iframe_focus_and_confirmation", "coverage_threshold": 0.9},
                    status="pending",
                ),
            ])
            db.commit()

        def override_db():
            with self.Session() as db:
                yield db

        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_current_user] = lambda: database.UserModel(
            id=1, username="learner", hashed_password="x"
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def test_html5_merges_reports_and_only_completes_at_ninety_percent(self):
        first = self.client.post("/daily-task-items/VIDEO_HTML5/video-evidence", json={
            "mode": "html5", "segment_start_seconds": 0, "segment_end_seconds": 100,
            "watched_intervals": [[0, 60]],
        })
        replay = self.client.post("/daily-task-items/VIDEO_HTML5/video-evidence", json={
            "mode": "html5", "segment_start_seconds": 0, "segment_end_seconds": 100,
            "watched_intervals": [[20, 60], [60, 89]],
        })
        completed = self.client.post("/daily-task-items/VIDEO_HTML5/video-evidence", json={
            "mode": "html5", "segment_start_seconds": 0, "segment_end_seconds": 100,
            "watched_intervals": [[89, 90]],
        })
        self.assertEqual(first.json()["active_seconds"], 60)
        self.assertEqual(replay.json()["active_seconds"], 89)
        self.assertEqual(replay.json()["status"], "pending")
        self.assertEqual(completed.json()["active_seconds"], 90)
        self.assertEqual(completed.json()["status"], "completed")

    def test_iframe_requires_effective_focus_and_confirmation(self):
        focus = self.client.post("/daily-task-items/VIDEO_IFRAME/video-evidence", json={
            "mode": "iframe", "segment_start_seconds": 10, "segment_end_seconds": 110,
            "active_seconds": 89,
        })
        rejected = self.client.post("/daily-task-items/VIDEO_IFRAME/video-evidence/confirm", json={
            "confirmed": True,
        })
        threshold = self.client.post("/daily-task-items/VIDEO_IFRAME/video-evidence", json={
            "mode": "iframe", "segment_start_seconds": 10, "segment_end_seconds": 110,
            "active_seconds": 90,
        })
        confirmed = self.client.post("/daily-task-items/VIDEO_IFRAME/video-evidence/confirm", json={
            "confirmed": True,
        })
        self.assertEqual(focus.json()["status"], "pending")
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(threshold.json()["status"], "pending")
        self.assertEqual(confirmed.json()["status"], "completed")

    def test_completed_iframe_remains_completed_after_later_heartbeat(self):
        self.client.post("/daily-task-items/VIDEO_IFRAME/video-evidence", json={
            "mode": "iframe", "active_seconds": 90,
        })
        confirmed = self.client.post("/daily-task-items/VIDEO_IFRAME/video-evidence/confirm", json={
            "confirmed": True,
        })
        heartbeat = self.client.post("/daily-task-items/VIDEO_IFRAME/video-evidence", json={
            "mode": "iframe", "active_seconds": 1,
        })

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(heartbeat.json()["status"], "completed")
        with self.Session() as db:
            evidence = db.query(database.DailyTaskVideoEvidenceRecord).filter_by(task_item_id="VIDEO_IFRAME").one()
            item = db.query(database.DailyTaskItemRecord).filter_by(task_item_id="VIDEO_IFRAME").one()
            self.assertEqual(evidence.status, "completed")
            self.assertEqual(evidence.active_seconds, 90)
            self.assertEqual(item.status, "completed")
            self.assertIsNotNone(item.completed_at)

    def test_iframe_spoofed_as_html5_is_rejected(self):
        response = self.client.post("/daily-task-items/VIDEO_IFRAME/video-evidence", json={
            "mode": "html5", "watched_intervals": [[10, 110]],
        })
        self.assertEqual(response.status_code, 409)

    def test_shortened_segment_or_full_client_interval_cannot_bypass_threshold(self):
        shortened = self.client.post("/daily-task-items/VIDEO_HTML5/video-evidence", json={
            "mode": "html5", "segment_start_seconds": 40, "segment_end_seconds": 50,
            "watched_intervals": [[40, 50]],
        })
        full_client_interval = self.client.post("/daily-task-items/VIDEO_HTML5/video-evidence", json={
            "mode": "html5", "segment_start_seconds": 0, "segment_end_seconds": 10,
            "watched_intervals": [[0, 10]],
        })

        self.assertEqual(shortened.json()["active_seconds"], 10)
        self.assertEqual(shortened.json()["status"], "pending")
        self.assertEqual(full_client_interval.json()["active_seconds"], 20)
        self.assertEqual(full_client_interval.json()["status"], "pending")
        with self.Session() as db:
            evidence = db.query(database.DailyTaskVideoEvidenceRecord).filter_by(task_item_id="VIDEO_HTML5").one()
            self.assertEqual(evidence.segment_start_seconds, 0)
            self.assertEqual(evidence.segment_end_seconds, 100)

    def test_out_of_range_intervals_are_rejected_and_persisted_intervals_are_clipped(self):
        rejected = self.client.post("/daily-task-items/VIDEO_HTML5/video-evidence", json={
            "mode": "html5", "watched_intervals": [[-10, 95]],
        })
        self.assertEqual(rejected.status_code, 400)

        with self.Session() as db:
            db.add(database.DailyTaskVideoEvidenceRecord(
                task_item_id="VIDEO_HTML5",
                user_id=1,
                mode="html5",
                segment_start_seconds=-50,
                segment_end_seconds=500,
                watched_intervals_json=json.dumps([[-20, 20], [90, 150]]),
                active_seconds=1000,
                status="pending",
            ))
            db.commit()

        clipped = self.client.post("/daily-task-items/VIDEO_HTML5/video-evidence", json={
            "mode": "html5", "watched_intervals": [],
        })
        self.assertEqual(clipped.status_code, 200)
        self.assertEqual(clipped.json()["active_seconds"], 30)
        self.assertEqual(clipped.json()["status"], "pending")
        with self.Session() as db:
            evidence = db.query(database.DailyTaskVideoEvidenceRecord).filter_by(task_item_id="VIDEO_HTML5").one()
            self.assertEqual(json.loads(evidence.watched_intervals_json), [[0.0, 20.0], [90.0, 100.0]])

    def test_video_evidence_is_scoped_to_authenticated_owner(self):
        self.app.dependency_overrides[get_current_user] = lambda: database.UserModel(
            id=2, username="other", hashed_password="x"
        )
        response = self.client.post("/daily-task-items/VIDEO_HTML5/video-evidence", json={
            "mode": "html5", "segment_start_seconds": 0, "segment_end_seconds": 100,
            "watched_intervals": [[0, 100]], "task_item_id": "VIDEO_IFRAME",
        })
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()