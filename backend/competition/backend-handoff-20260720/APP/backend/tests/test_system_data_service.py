import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.system_data_service import (
    record_dashboard_recommendation_click,
    record_dashboard_recommendations_view,
    rebuild_system_data,
    system_data_payload,
)


class SystemDataServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Session()
        self.db.add_all((
            database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"),
            database.UserModel(id=2, username="other", email="other@example.com", hashed_password="x"),
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_uses_utc_for_snapshot_storage_and_beijing_for_payload_windows(self):
        utc_now = datetime(2026, 7, 16, 1, 12, 34)
        from APP.backend.system_data_service import record_login_activity

        snapshot = record_login_activity(self.db, user_id=1, now=utc_now)

        record = self.db.query(database.LearningActivityRecord).filter_by(
            user_id=1,
            activity_type="login",
        ).one()
        payload = system_data_payload(snapshot)
        self.assertEqual(record.created_at, utc_now)
        self.assertEqual(snapshot.calculated_at, utc_now)
        self.assertEqual(payload["calculated_at"], "2026-07-16T09:12:34+08:00")
        self.assertEqual(payload["time_data"]["login_frequency"]["window_end"], "2026-07-16T09:12:34+08:00")
        self.assertEqual(payload["time_data"]["login_frequency"]["window_start"], "2026-06-16T09:12:34+08:00")

    def test_converts_utc_events_to_beijing_for_daily_and_hourly_metrics(self):
        now = datetime(2026, 7, 16, 1, 0)
        self.db.add_all((
            database.LearningActivityRecord(
                user_id=1,
                activity_type="login",
                completion_status="completed",
                created_at=datetime(2026, 7, 15, 15, 30),
            ),
            database.LearningActivityRecord(
                user_id=1,
                activity_type="login",
                completion_status="completed",
                created_at=datetime(2026, 7, 15, 16, 30),
            ),
            database.LearningFocusSession(
                focus_session_id="FOCUS_BEIJING",
                user_id=1,
                status="completed",
                active_seconds=60,
                started_at=datetime(2026, 7, 15, 16, 15),
            ),
        ))
        self.db.commit()

        snapshot = rebuild_system_data(self.db, user_id=1, now=now)
        time_data = json.loads(snapshot.time_data_json)

        self.assertEqual(snapshot.calculated_at, now)
        self.assertEqual(time_data["login_frequency"]["value"], 2)
        self.assertEqual(time_data["focus_time_period"]["value"], "00:00-00:59")
        self.assertEqual(time_data["login_frequency"]["window_end"], "2026-07-16T09:00:00+08:00")

    def test_builds_beijing_daily_learning_trends_for_selected_window(self):
        from APP.backend.system_data_service import build_learning_trends

        now = datetime(2026, 7, 16, 1, 0)
        self.db.add_all((
            database.LearningActivityRecord(
                user_id=1,
                activity_type="login",
                completion_status="completed",
                created_at=datetime(2026, 7, 14, 16, 30),
            ),
            database.LearningFocusSession(
                focus_session_id="FOCUS_TREND",
                user_id=1,
                status="completed",
                active_seconds=180,
                started_at=datetime(2026, 7, 15, 16, 15),
            ),
            database.LearningFocusSession(
                focus_session_id="FOCUS_TREND_LIVE",
                user_id=1,
                status="active",
                active_seconds=60,
                started_at=datetime(2026, 7, 15, 16, 30),
            ),
            database.LearningTask(
                task_id="TASK_TREND_DONE",
                user_id=1,
                task_type="practice",
                status="completed",
                created_at=datetime(2026, 7, 15, 16, 20),
                completed_at=datetime(2026, 7, 15, 16, 25),
            ),
            database.LearningTask(
                task_id="TASK_TREND_PENDING",
                user_id=1,
                task_type="video",
                status="pending",
                created_at=datetime(2026, 7, 15, 16, 35),
            ),
        ))
        self.db.commit()

        trend = build_learning_trends(self.db, user_id=1, days=7, now=now)

        self.assertEqual(trend["days"], 7)
        self.assertEqual([item["date"] for item in trend["series"]], [
            "2026-07-10",
            "2026-07-11",
            "2026-07-12",
            "2026-07-13",
            "2026-07-14",
            "2026-07-15",
            "2026-07-16",
        ])
        self.assertEqual(trend["series"][5]["login_days"], 1)
        self.assertEqual(trend["series"][6]["focus_minutes"], 4)
        self.assertEqual(trend["series"][6]["task_completion_rate"], 0.5)
        self.assertEqual(trend["calculated_at"], "2026-07-16T09:00:00+08:00")

    def test_splits_focus_duration_across_beijing_midnight(self):
        from APP.backend.system_data_service import build_learning_trends

        self.db.add(database.LearningFocusSession(
            focus_session_id="FOCUS_CROSSES_MIDNIGHT",
            user_id=1,
            status="completed",
            active_seconds=1_200,
            started_at=datetime(2026, 7, 15, 15, 50),
            ended_at=datetime(2026, 7, 15, 16, 10),
            updated_at=datetime(2026, 7, 15, 16, 10),
        ))
        self.db.commit()

        trend = build_learning_trends(self.db, user_id=1, days=7, now=datetime(2026, 7, 16, 1, 0))

        self.assertEqual(trend["series"][5]["focus_minutes"], 10)
        self.assertEqual(trend["series"][6]["focus_minutes"], 10)

    def test_rebuilds_snapshot_from_formal_tasks_and_focus_sessions(self):
        now = datetime(2026, 7, 15, 14, 30)
        self.db.add_all((
            database.LearningActivityRecord(
                user_id=1,
                activity_type="login",
                completion_status="completed",
                created_at=now - timedelta(days=1),
            ),
            database.LearningTask(
                task_id="TASK_PRACTICE",
                user_id=1,
                task_type="practice",
                status="completed",
                created_at=now,
                completed_at=now,
            ),
            database.LearningTask(
                task_id="TASK_VIDEO",
                user_id=1,
                task_type="video",
                status="pending",
                created_at=now,
            ),
            database.LearningTask(
                task_id="TASK_CANCELLED",
                user_id=1,
                task_type="knowledge_card",
                status="cancelled",
                created_at=now,
            ),
            database.LearningFocusSession(
                focus_session_id="FOCUS_1",
                user_id=1,
                task_id="TASK_PRACTICE",
                status="completed",
                active_seconds=240,
                started_at=now,
                ended_at=now + timedelta(minutes=4),
            ),
            database.LearningFocusSession(
                focus_session_id="FOCUS_2",
                user_id=1,
                task_id="TASK_VIDEO",
                status="completed",
                active_seconds=120,
                started_at=now + timedelta(hours=1),
                ended_at=now + timedelta(hours=1, minutes=2),
            ),
            database.LearningActivityRecord(
                user_id=1,
                activity_type="dashboard_recommendations_view",
                resource_id="view-1",
                completion_status="viewed",
                payload_json=json.dumps({"recommendation_keys": ["daily-question", "case-training"]}),
                created_at=now,
            ),
            database.LearningActivityRecord(
                user_id=1,
                activity_type="resource_click",
                resource_id="daily-question",
                completion_status="clicked",
                payload_json=json.dumps({"recommendation_view_id": "view-1"}),
                created_at=now,
            ),
            database.LearningTask(
                task_id="OTHER_USER_TASK",
                user_id=2,
                task_type="practice",
                status="completed",
                created_at=now,
                completed_at=now,
            ),
        ))
        self.db.commit()

        snapshot = rebuild_system_data(self.db, user_id=1, now=now + timedelta(hours=2))
        self.db.commit()

        time_data = json.loads(snapshot.time_data_json)
        completion_rate = json.loads(snapshot.task_completion_rate_json)
        resource_click_rate = json.loads(snapshot.resource_click_rate_json)
        self.assertEqual(time_data["login_frequency"]["value"], 1)
        self.assertEqual(time_data["focus_time_period"]["value"], "22:00-22:59")
        self.assertEqual(completion_rate["value"], 0.5)
        self.assertNotIn("learning_task_completion_rate", completion_rate)
        self.assertNotIn("review_task_completion_rate", completion_rate)
        self.assertEqual(resource_click_rate["value"], 0.5)
        self.assertEqual(self.db.query(database.SystemData).filter_by(user_id=1).count(), 1)

    def test_migrates_legacy_task_activities_without_duplicating_formal_tasks(self):
        now = datetime(2026, 7, 15, 14, 30)
        self.db.add_all((
            database.LearningActivityRecord(
                user_id=1,
                activity_type="question_attempt",
                resource_id="Q_1",
                resource_type="question",
                completion_status="needs_review",
                created_at=now,
            ),
            database.LearningActivityRecord(
                user_id=1,
                activity_type="training_workspace_task",
                resource_id="WORKSPACE_1",
                resource_type="training_task",
                completion_status="completed",
                payload_json=json.dumps({"task_type": "practice_grading"}),
                created_at=now,
            ),
            database.LearningTask(
                task_id="WORKSPACE_1",
                user_id=1,
                task_type="practice_grading",
                status="completed",
                created_at=now,
                completed_at=now,
            ),
        ))
        self.db.commit()

        snapshot = rebuild_system_data(self.db, user_id=1, now=now + timedelta(minutes=1))
        self.db.commit()

        completion_rate = json.loads(snapshot.task_completion_rate_json)
        self.assertEqual(completion_rate["value"], 1.0)
        self.assertEqual(self.db.query(database.LearningTask).filter_by(user_id=1).count(), 2)
        migrated = self.db.query(database.LearningTask).filter(
            database.LearningTask.task_id.like("LEGACY_ACTIVITY_%")
        ).one()
        self.assertEqual(migrated.task_type, "question_attempt")
        self.assertEqual(migrated.status, "completed")

        rebuild_system_data(self.db, user_id=1, now=now + timedelta(minutes=2))
        self.db.commit()
        self.assertEqual(self.db.query(database.LearningTask).filter_by(user_id=1).count(), 2)

    def test_recommendation_click_requires_a_owned_displayed_recommendation(self):
        view = record_dashboard_recommendations_view(
            self.db,
            user_id=1,
            recommendation_keys=("daily-question", "case-training"),
        )
        self.db.commit()

        snapshot = record_dashboard_recommendation_click(
            self.db,
            user_id=1,
            recommendation_key="daily-question",
            recommendation_view_id=view.resource_id,
        )
        self.db.commit()

        self.assertEqual(json.loads(snapshot.resource_click_rate_json)["value"], 0.5)
        with self.assertRaises(ValueError):
            record_dashboard_recommendation_click(
                self.db,
                user_id=2,
                recommendation_key="daily-question",
                recommendation_view_id=view.resource_id,
            )
        with self.assertRaises(ValueError):
            record_dashboard_recommendation_click(
                self.db,
                user_id=1,
                recommendation_key="not-displayed",
                recommendation_view_id=view.resource_id,
            )


if __name__ == "__main__":
    unittest.main()
