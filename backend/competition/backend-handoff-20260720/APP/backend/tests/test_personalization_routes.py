import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.auth import get_current_user
from APP.backend.database import get_db


class MarkdownMemoryUploadLimitsTests(unittest.TestCase):
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

        db = self.Session()
        try:
            db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
            db.commit()
        finally:
            db.close()

        def override_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        def override_user():
            return database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x")

        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_current_user] = override_user
        self.conflict_sync = patch("APP.backend.routers.personalization_routes._sync_personalization_conflicts")
        self.conflict_sync.start()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.conflict_sync.stop()
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def _upload(self, content):
        return self.client.post(
            "/personalization/memories/upload-md",
            files={"file": ("memory.md", content, "text/markdown")},
        )

    def test_rejects_markdown_larger_than_one_megabyte(self):
        response = self._upload(b"a" * (1024 * 1024 + 1))

        self.assertEqual(response.status_code, 413)

    def test_rejects_more_than_one_hundred_memory_sections(self):
        content = "\n".join(f"# Memory {index}\ncontent" for index in range(101)).encode("utf-8")

        response = self._upload(content)

        self.assertEqual(response.status_code, 400)

    def test_rejects_memory_section_larger_than_ten_thousand_characters(self):
        response = self._upload(f"# Memory\n{'a' * 10001}".encode("utf-8"))

        self.assertEqual(response.status_code, 400)
    def test_returns_learning_trends_for_allowed_window(self):
        response = self.client.get("/personalization/learning-trends?days=7")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["days"], 7)
        self.assertEqual(len(body["series"]), 7)
        self.assertIn("task_completion_rate", body["series"][0])

    def test_rejects_unsupported_learning_trend_window(self):
        response = self.client.get("/personalization/learning-trends?days=14")

        self.assertEqual(response.status_code, 422)

    def test_api_settings_are_persisted_but_never_returned_in_plaintext(self):
        saved = self.client.put(
            "/personalization/api-settings",
            json={
                "deepseek_api_key": "sk-deepseek-secret-1234",
                "siliconflow_api_key": "sk-silicon-secret-5678",
                "mineru_api_token": "mineru-secret-9012",
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertNotIn("sk-deepseek-secret-1234", saved.text)
        self.assertTrue(saved.json()["providers"]["deepseek"]["configured"])

        loaded = self.client.get("/personalization/api-settings")
        self.assertEqual(loaded.status_code, 200)
        self.assertNotIn("secret", loaded.text)
        self.assertTrue(loaded.json()["providers"]["siliconflow"]["configured"])

        cleared = self.client.put(
            "/personalization/api-settings",
            json={"clear": ["deepseek"]},
        )
        self.assertFalse(cleared.json()["providers"]["deepseek"]["configured"])
        self.assertTrue(cleared.json()["providers"]["mineru"]["configured"])


if __name__ == "__main__":
    unittest.main()
