import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.auth import get_current_user
from APP.backend.database import get_db
from APP.backend.official_exam_repository import OfficialExamRepository
from APP.backend.tests.test_official_exam_repository import write_exam_fixture


class ExamLearningRoutesTests(unittest.TestCase):
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

        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        write_exam_fixture(data_dir)
        self.repository = OfficialExamRepository(data_dir)

        def override_db():
            with self.Session() as session:
                yield session

        def override_user():
            return database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x")

        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_current_user] = override_user
        self.repository_patch = patch("APP.backend.exam_learning_service.get_official_exam_repository", return_value=self.repository)
        self.repository_patch.start()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.repository_patch.stop()
        self.app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_routes_are_registered_in_openapi(self):
        paths = self.app.openapi()["paths"]
        self.assertIn("/exam-learning/tracks", paths)
        self.assertIn("/exam-learning/tracks/{track_id}/nodes", paths)
        self.assertIn("/personalization/learning-target", paths)

    def test_lists_tracks_and_direct_children_without_embedding_dependency(self):
        response = self.client.get("/exam-learning/tracks")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["track_id"] for row in response.json()["items"]], ["track-a", "track-b"])

        response = self.client.get("/exam-learning/tracks/track-a/nodes?parent_membership_id=root-a")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["membership_id"] for row in response.json()["items"]], ["subject-a"])
        self.assertNotIn("node", response.json()["items"][0])
        self.assertNotIn("source_refs", response.json()["items"][0])

    def test_returns_node_breadcrumb_and_accepted_only_knowledge_points(self):
        detail = self.client.get("/exam-learning/tracks/track-a/nodes/requirement-a")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual([row["membership_id"] for row in detail.json()["breadcrumb"]], ["root-a", "subject-a", "requirement-a"])

        response = self.client.get("/exam-learning/requirements/node-requirement/knowledge-points")
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual([row["kp_id"] for row in items], ["kp-accepted"])
        self.assertNotIn("decision", items[0])
        self.assertNotIn("scores", items[0])
        self.assertNotIn("rank", items[0])

    def test_node_knowledge_points_are_paginated_and_hide_internal_match_evidence(self):
        response = self.client.get(
            "/exam-learning/tracks/track-a/nodes/root-a/knowledge-points?offset=0&limit=1"
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["offset"], 0)
        self.assertEqual(body["limit"], 1)
        self.assertFalse(body["has_more"])
        self.assertEqual(body["items"][0]["kp_id"], "kp-accepted")
        self.assertNotIn("best_match", body["items"][0])
        self.assertNotIn("requirements", body["items"][0])

    def test_returns_current_users_learning_state_for_knowledge_point(self):
        from datetime import datetime, timedelta

        now = datetime(2020, 7, 18, 12, 0, 0)
        with self.Session() as db:
            db.add(database.KnowledgeMasteryState(
                mastery_state_id="mastery-1",
                learner_id=1,
                kp_id="kp-accepted",
                mastery_score=82.5,
                mastery_confidence=0.9,
                attempt_count=4,
                last_assessed_at=now,
            ))
            db.add(database.LearnerKPReviewState(
                review_state_id="review-1",
                learner_id=1,
                kp_id="kp-accepted",
                review_stage="2",
                next_review_at=now - timedelta(minutes=5),
                requires_remediation=True,
            ))
            db.add(database.MistakeRecord(
                user_id=1,
                question_id="question-1",
                kp_ids_json='["kp-accepted"]',
                status="active",
            ))
            db.add(database.KnowledgeMasteryState(
                mastery_state_id="mastery-other",
                learner_id=2,
                kp_id="kp-accepted",
                mastery_score=10,
            ))
            db.commit()

        response = self.client.get(
            "/exam-learning/knowledge-points/kp-accepted/learner-state"
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["kp_id"], "kp-accepted")
        self.assertEqual(body["mastery_score"], 82.5)
        self.assertEqual(body["mastery_status"], "mastered")
        self.assertTrue(body["review_due"])
        self.assertTrue(body["requires_remediation"])
        self.assertEqual(body["active_mistake_count"], 1)
        self.assertEqual(body["attempt_count"], 4)

    def test_returns_current_users_summary_for_exam_node(self):
        from datetime import datetime, timedelta

        now = datetime(2020, 7, 18, 12, 0, 0)
        with self.Session() as db:
            db.add(database.KnowledgeMasteryState(
                mastery_state_id="mastery-summary",
                learner_id=1,
                kp_id="kp-accepted",
                mastery_score=82.5,
                mastery_confidence=0.9,
                attempt_count=4,
                last_assessed_at=now,
            ))
            db.add(database.LearnerKPReviewState(
                review_state_id="review-summary",
                learner_id=1,
                kp_id="kp-accepted",
                review_stage="2",
                next_review_at=now - timedelta(minutes=5),
            ))
            db.commit()

        response = self.client.get(
            "/exam-learning/tracks/track-a/nodes/root-a/learner-summary"
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["track_id"], "track-a")
        self.assertEqual(body["membership_id"], "root-a")
        self.assertEqual(body["total_count"], 1)
        self.assertEqual(body["completed_count"], 1)
        self.assertEqual(body["incomplete_count"], 0)
        self.assertEqual(body["average_mastery"], 82.5)
        self.assertEqual(body["review_due_count"], 1)
        self.assertEqual(body["status"], "completed")

    def test_returns_batched_visible_node_learning_states_with_real_recency(self):
        from datetime import datetime, timedelta

        assessed_at = datetime(2026, 7, 18, 8, 0, 0)
        with self.Session() as db:
            db.add(database.KnowledgeMasteryState(
                mastery_state_id="mastery-visible",
                learner_id=1,
                kp_id="kp-accepted",
                mastery_score=86.0,
                mastery_confidence=0.9,
                attempt_count=3,
                last_assessed_at=assessed_at,
            ))
            db.add(database.LearnerKPReviewState(
                review_state_id="review-visible",
                learner_id=1,
                kp_id="kp-accepted",
                review_stage="2",
                next_review_at=assessed_at + timedelta(days=3),
            ))
            db.commit()

        response = self.client.post(
            "/exam-learning/tracks/track-a/nodes/learner-states",
            json={"membership_ids": ["root-a", "requirement-a"]},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([item["membership_id"] for item in body["items"]], [
            "root-a", "requirement-a",
        ])
        self.assertEqual(body["items"][0]["status"], "completed")
        self.assertEqual(body["items"][0]["mastery_score"], 86.0)
        self.assertEqual(body["items"][0]["last_assessed_at"], "2026-07-18T08:00:00")
        self.assertFalse(body["items"][0]["review_due"])
        self.assertIn("display_order", body["items"][0])
        self.assertIn("order_path", body["items"][0])

    def test_batched_visible_node_states_do_not_fabricate_history_and_validate_track(self):
        response = self.client.post(
            "/exam-learning/tracks/track-a/nodes/learner-states",
            json={"membership_ids": ["subject-a"]},
        )

        self.assertEqual(response.status_code, 200)
        state = response.json()["items"][0]
        self.assertEqual(state["status"], "unassessed")
        self.assertIsNone(state["last_assessed_at"])
        self.assertIsNone(state["next_review_at"])

        wrong_track = self.client.post(
            "/exam-learning/tracks/track-b/nodes/learner-states",
            json={"membership_ids": ["subject-a"]},
        )
        self.assertEqual(wrong_track.status_code, 404)

    def test_returns_404_for_wrong_track_or_node(self):
        self.assertEqual(self.client.get("/exam-learning/tracks/missing/nodes").status_code, 404)
        self.assertEqual(self.client.get("/exam-learning/tracks/track-b/nodes/requirement-a").status_code, 404)

    def test_get_and_put_learning_target(self):
        empty = self.client.get("/personalization/learning-target")
        self.assertEqual(empty.status_code, 200)
        self.assertIsNone(empty.json()["target"])

        saved = self.client.put(
            "/personalization/learning-target",
            json={
                "target_type": "certification",
                "exam_track_id": "track-a",
                "exam_date": "2026-10-01",
                "is_locked": True,
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["target"]["exam_track_id"], "track-a")
        self.assertTrue(saved.json()["target"]["is_locked"])

        invalid = self.client.put(
            "/personalization/learning-target",
            json={"target_type": "certification", "exam_track_id": "missing"},
        )
        self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
