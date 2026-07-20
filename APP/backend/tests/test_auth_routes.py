import unittest
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.database import get_db
from APP.backend.auth import get_password_hash
from APP.backend.time_utils import beijing_now


class AuthRoutesOnboardingTests(unittest.TestCase):
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

        def override_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        self.app.dependency_overrides[get_db] = override_db
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def test_register_returns_onboarding_popup_and_l0_baseline(self):
        with self.Session() as db:
            db.add(
                database.VerificationCode(
                    email="new@example.com",
                    code="123456",
                    purpose="register",
                    expires_at=beijing_now() + timedelta(minutes=5),
                    is_used=False,
                )
            )
            db.commit()

        response = self.client.post(
            "/register",
            json={
                "username": "newlearner",
                "email": "new@example.com",
                "password": "Password123!",
                "verification_code": "123456",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["token_type"], "bearer")
        self.assertTrue(payload["access_token"])
        self.assertTrue(payload["needs_survey_popup"])
        self.assertEqual(payload["onboarding_status"]["status"], "pending")
        self.assertEqual(payload["onboarding_status"]["l0_baseline"]["stage_id"], "L0")
        self.assertEqual(payload["onboarding_status"]["l0_baseline"]["learner_group"], "未选择用户群体")
        with self.Session() as db:
            self.assertEqual(db.query(database.LearningActivityRecord).filter_by(
                user_id=1,
                activity_type="login",
            ).count(), 1)

    def test_login_records_activity_only_after_successful_authentication(self):
        with self.Session() as db:
            db.add(database.UserModel(
                username="learner",
                email="learner@example.com",
                hashed_password=get_password_hash("Password123!"),
            ))
            db.commit()

        failed = self.client.post("/token", data={"username": "learner", "password": "wrong"})
        succeeded = self.client.post("/token", data={"username": "learner", "password": "Password123!"})

        self.assertEqual(failed.status_code, 401)
        self.assertEqual(succeeded.status_code, 200)
        with self.Session() as db:
            self.assertEqual(db.query(database.LearningActivityRecord).filter_by(
                activity_type="login",
            ).count(), 1)


if __name__ == "__main__":
    unittest.main()
