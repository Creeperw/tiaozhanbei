import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.auth import get_current_user
from APP.backend.database import get_db


class LearningActivityRouteTests(unittest.TestCase):
    def setUp(self):
        from APP.backend.main import app

        self.app = app
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
            db.commit()

        def override_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_current_user] = lambda: database.UserModel(
            id=1, username="learner", email="learner@example.com", hashed_password="x"
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def test_video_interaction_creates_and_completes_a_unified_task(self):
        started = self.client.post(
            "/learning-activity/tasks",
            json={"task_type": "video", "resource_type": "video", "resource_id": 'VIDEO_"1'},
        )
        self.assertEqual(started.status_code, 201)
        task_id = started.json()["task_id"]

        completed = self.client.post(f"/learning-activity/tasks/{task_id}/complete")

        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "completed")
        self.assertEqual(completed.json()["version"], 2)
        self.assertEqual(completed.json()["system_data"]["task_completion_rate"]["value"], 1.0)
        with self.Session() as db:
            task = db.query(database.LearningTask).filter_by(task_id=task_id, user_id=1).one()
            self.assertEqual(task.resource_ids_json, '["VIDEO_\\\"1"]')

    def test_focus_session_is_owned_and_updates_snapshot_when_ended(self):
        task = self.client.post(
            "/learning-activity/tasks",
            json={"task_type": "question", "resource_type": "question", "resource_id": "Q_1"},
        ).json()
        focus = self.client.post(
            "/learning-activity/focus-sessions",
            json={"task_id": task["task_id"], "resource_type": "question", "resource_id": "Q_1"},
        )
        self.assertEqual(focus.status_code, 201)
        focus_session_id = focus.json()["focus_session_id"]

        heartbeat = self.client.post(
            f"/learning-activity/focus-sessions/{focus_session_id}/heartbeat",
            json={"visible": True, "interacted": True},
        )
        ended = self.client.post(f"/learning-activity/focus-sessions/{focus_session_id}/end")

        self.assertEqual(heartbeat.status_code, 200)
        self.assertEqual(ended.status_code, 200)
        self.assertEqual(ended.json()["status"], "completed")
        self.assertIn("focus_time_period", ended.json()["system_data"]["time_data"])

    def test_focus_session_rejects_an_unowned_task(self):
        with self.Session() as db:
            db.add(database.UserModel(id=2, username="other", email="other@example.com", hashed_password="x"))
            db.add(database.LearningTask(task_id="OTHER_TASK", user_id=2, task_type="video"))
            db.commit()

        response = self.client.post(
            "/learning-activity/focus-sessions",
            json={"task_id": "OTHER_TASK", "resource_type": "video", "resource_id": "V_1"},
        )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
