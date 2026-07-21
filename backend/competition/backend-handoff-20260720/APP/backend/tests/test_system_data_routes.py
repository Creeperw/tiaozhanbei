import json
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.auth import get_current_user
from APP.backend.database import get_db


class SystemDataRouteTests(unittest.TestCase):
    def setUp(self):
        from APP.backend.main import app

        self.app = app
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
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

    def test_dashboard_home_records_recommendation_view_and_exposes_snapshot(self):
        response = self.client.get("/dashboard/home")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["recommendation_view_id"].startswith("recommendation-view:"))
        self.assertIn("system_data", body)
        self.assertIn("login_frequency", body["system_data"]["time_data"])
        with self.Session() as db:
            self.assertEqual(
                db.query(database.LearningActivityRecord).filter_by(
                    user_id=1,
                    activity_type="dashboard_recommendations_view",
                ).count(),
                1,
            )

    def test_dashboard_home_uses_active_learning_target(self):
        with self.Session() as db:
            db.add(
                database.UserLearningTarget(
                    user_id=1,
                    target_type="certification",
                    exam_track_id="EXAM_2025_TCM_PHYSICIAN",
                    exam_name_snapshot="2025 中医执业医师资格考试",
                    syllabus_version="2.0.0",
                    is_active=True,
                    is_locked=True,
                )
            )
            db.commit()

        response = self.client.get("/dashboard/home")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["hero"]["goal"], "2025 中医执业医师资格考试")
        self.assertEqual(
            body["learning_target"]["exam_track_id"],
            "EXAM_2025_TCM_PHYSICIAN",
        )

    def test_recommendation_click_accepts_only_the_current_users_displayed_item(self):
        dashboard = self.client.get("/dashboard/home").json()
        response = self.client.post(
            "/dashboard/recommendations/click",
            json={
                "recommendation_key": "daily-question",
                "recommendation_view_id": dashboard["recommendation_view_id"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["system_data"]["resource_click_rate"]["value"], 0.25)
        rejected = self.client.post(
            "/dashboard/recommendations/click",
            json={
                "recommendation_key": "forged",
                "recommendation_view_id": dashboard["recommendation_view_id"],
            },
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json()["detail"], "recommendation was not displayed to current user")


if __name__ == "__main__":
    unittest.main()
