import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.learning_task_activity_service import (
    begin_learning_task,
    complete_learning_task,
    end_focus_session,
    record_focus_heartbeat,
    start_focus_session,
)


class LearningTaskActivityServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.now = datetime(2026, 7, 15, 14, 0)
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
            db.add(database.UserModel(id=2, username="other", email="other@example.com", hashed_password="x"))
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_task_completion_is_owned_and_idempotent(self):
        with self.Session() as db:
            task = begin_learning_task(
                db,
                user_id=1,
                task_type="practice",
                resource_type="question",
                resource_id="Q_1",
                now=self.now,
            )
            completed = complete_learning_task(db, user_id=1, task_id=task.task_id, now=self.now + timedelta(minutes=3))
            replay = complete_learning_task(db, user_id=1, task_id=task.task_id, now=self.now + timedelta(minutes=4))
            db.commit()

        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.version, 2)
        self.assertEqual(replay.version, 2)
        self.assertEqual(replay.completed_at, self.now + timedelta(minutes=3))
        with self.Session() as db:
            self.assertEqual(db.query(database.LearningTask).filter_by(user_id=1).count(), 1)
            self.assertIsNone(db.query(database.LearningTask).filter_by(user_id=2).one_or_none())

    def test_focus_counts_only_visible_recently_interacted_intervals(self):
        with self.Session() as db:
            task = begin_learning_task(
                db,
                user_id=1,
                task_type="video",
                resource_type="video",
                resource_id="V_1",
                now=self.now,
            )
            focus = start_focus_session(
                db,
                user_id=1,
                task_id=task.task_id,
                resource_type="video",
                resource_id="V_1",
                now=self.now,
            )
            record_focus_heartbeat(db, user_id=1, focus_session_id=focus.focus_session_id, visible=True, interacted=True, now=self.now + timedelta(seconds=10))
            record_focus_heartbeat(db, user_id=1, focus_session_id=focus.focus_session_id, visible=True, interacted=False, now=self.now + timedelta(seconds=40))
            record_focus_heartbeat(db, user_id=1, focus_session_id=focus.focus_session_id, visible=False, interacted=False, now=self.now + timedelta(seconds=70))
            record_focus_heartbeat(db, user_id=1, focus_session_id=focus.focus_session_id, visible=True, interacted=False, now=self.now + timedelta(seconds=100))
            record_focus_heartbeat(db, user_id=1, focus_session_id=focus.focus_session_id, visible=True, interacted=True, now=self.now + timedelta(seconds=130))
            record_focus_heartbeat(db, user_id=1, focus_session_id=focus.focus_session_id, visible=True, interacted=False, now=self.now + timedelta(seconds=160))
            completed = end_focus_session(db, user_id=1, focus_session_id=focus.focus_session_id, now=self.now + timedelta(seconds=170))
            db.commit()

        self.assertEqual(completed.active_seconds, 90)
        self.assertEqual(completed.status, "completed")

    def test_periodic_heartbeats_do_not_extend_activity_without_recent_interaction(self):
        with self.Session() as db:
            task = begin_learning_task(
                db,
                user_id=1,
                task_type="video",
                resource_type="video",
                resource_id="V_IDLE",
                now=self.now,
            )
            focus = start_focus_session(
                db,
                user_id=1,
                task_id=task.task_id,
                resource_type="video",
                resource_id="V_IDLE",
                now=self.now,
            )
            record_focus_heartbeat(db, user_id=1, focus_session_id=focus.focus_session_id, visible=True, interacted=True, now=self.now + timedelta(seconds=10))
            for minute in range(1, 8):
                record_focus_heartbeat(
                    db,
                    user_id=1,
                    focus_session_id=focus.focus_session_id,
                    visible=True,
                    interacted=False,
                    now=self.now + timedelta(minutes=minute),
                )
            completed = end_focus_session(db, user_id=1, focus_session_id=focus.focus_session_id, now=self.now + timedelta(minutes=8))
            db.commit()

        self.assertEqual(completed.active_seconds, 290)

    def test_hidden_or_idle_sessions_cannot_accrue_focus_time(self):
        with self.Session() as db:
            task = begin_learning_task(
                db,
                user_id=1,
                task_type="knowledge_card",
                resource_type="knowledge_card",
                resource_id="CARD_1",
                now=self.now,
            )
            focus = start_focus_session(
                db,
                user_id=1,
                task_id=task.task_id,
                resource_type="knowledge_card",
                resource_id="CARD_1",
                now=self.now,
            )
            record_focus_heartbeat(db, user_id=1, focus_session_id=focus.focus_session_id, visible=True, interacted=True, now=self.now + timedelta(seconds=10))
            record_focus_heartbeat(db, user_id=1, focus_session_id=focus.focus_session_id, visible=True, interacted=False, now=self.now + timedelta(minutes=6))
            completed = end_focus_session(db, user_id=1, focus_session_id=focus.focus_session_id, now=self.now + timedelta(minutes=6, seconds=10))
            db.commit()

        self.assertEqual(completed.active_seconds, 0)


if __name__ == "__main__":
    unittest.main()
