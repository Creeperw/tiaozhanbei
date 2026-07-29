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

    def _candidate(self, *, status="pending"):
        db = self.Session()
        try:
            candidate = database.MemoryCandidate(
                user_id=1,
                title="学习偏好",
                content="喜欢先理解后练习。",
                status=status,
            )
            db.add(candidate)
            db.commit()
            db.refresh(candidate)
            return candidate.id
        finally:
            db.close()

    def test_promote_candidate_retry_returns_same_memory(self):
        candidate_id = self._candidate()
        payload = {"category": "preference", "importance": "normal"}

        first = self.client.patch(
            f"/personalization/candidates/{candidate_id}/promote", json=payload
        )
        second = self.client.patch(
            f"/personalization/candidates/{candidate_id}/promote", json=payload
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["memory"]["id"], second.json()["memory"]["id"])
        self.assertTrue(second.json()["replayed"])
        db = self.Session()
        try:
            self.assertEqual(db.query(database.PersonalizationMemory).count(), 1)
        finally:
            db.close()

    def test_promote_ignored_candidate_is_rejected(self):
        candidate_id = self._candidate(status="ignored")

        response = self.client.patch(
            f"/personalization/candidates/{candidate_id}/promote",
            json={"category": "preference", "importance": "normal"},
        )

        self.assertEqual(response.status_code, 409)

    def test_promoted_candidate_cannot_be_reset(self):
        candidate_id = self._candidate()
        self.client.patch(
            f"/personalization/candidates/{candidate_id}/promote",
            json={"category": "preference", "importance": "normal"},
        )

        response = self.client.put(
            f"/personalization/candidates/{candidate_id}", json={"status": "pending"}
        )

        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
