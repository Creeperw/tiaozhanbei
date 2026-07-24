import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.auth import get_current_user
from APP.backend.database import get_db


class KnowledgeRoutesOpenApiTests(unittest.TestCase):
    def test_knowledge_agent_routes_are_registered_in_openapi(self):
        from APP.backend.main import app

        paths = app.openapi()["paths"]

        self.assertIn("/knowledge/evidence-pack", paths)
        self.assertIn("/knowledge/points/align", paths)
        self.assertIn("/knowledge/questions", paths)


class KnowledgeRoutesBehaviorTests(unittest.TestCase):
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
            self.user = database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x")
            db.add(self.user)
            db.add(database.KnowledgePoint(kp_id="KP_FJ_001", name="四君子汤", aliases_json='["四君子"]'))
            db.add(
                database.QuestionBankItem(
                    question_id="Q_FJ_001",
                    stem="四君子汤主治哪类证候？",
                    answer="脾胃气虚证",
                    kp_ids_json='["KP_FJ_001"]',
                    difficulty=2.0,
                    quality_score=0.9,
                )
            )
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
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def test_align_rejects_blank_text(self):
        response = self.client.post("/knowledge/points/align", json={"text": "   "})

        self.assertEqual(response.status_code, 422)

    def test_align_uses_current_user_for_candidate_owner(self):
        response = self.client.post("/knowledge/points/align", json={"text": "尚未建库的专题"})

        self.assertEqual(response.status_code, 200)
        candidate_id = response.json()["candidate_kp_ids"][0]
        db = self.Session()
        try:
            candidate = db.query(database.CandidateKnowledgePoint).filter_by(candidate_id=candidate_id).one()
            self.assertEqual(candidate.created_by_user_id, 1)
        finally:
            db.close()

    def test_questions_filters_by_kp_and_limit(self):
        response = self.client.get("/knowledge/questions", params={"kp_id": "KP_FJ_001", "limit": 1})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["questions"]), 1)
        self.assertEqual(response.json()["questions"][0]["question_id"], "Q_FJ_001")

    def test_pdf_upload_runs_mineru_and_indexes_generated_markdown(self):
        from APP.backend.routers import knowledge_routes

        class FakeRag:
            is_processing = False

            def __init__(self, root):
                self.root = root
                self.rebuilds = []

            def _paths_for_scope(self, scope, user_id):
                return str(self.root), str(self.root / "index")

            def _safe_filename(self, filename):
                return Path(filename).name

            def delete_file(self, *args, **kwargs):
                return None

            def rebuild_index(self, scope, user_id):
                self.rebuilds.append((scope, user_id))

        class FakeMinerU:
            def validate(self):
                return None

            def parse(self, path):
                self.path = path
                return "# 四君子汤\n\n益气健脾。"

        with tempfile.TemporaryDirectory() as directory:
            fake_rag = FakeRag(Path(directory))
            with patch.object(knowledge_routes, "rag_service", fake_rag), patch.object(
                knowledge_routes,
                "mineru_pdf_parser_factory",
                FakeMinerU,
            ):
                response = self.client.post(
                    "/knowledge/upload?scope=personal",
                    files={"files": ("tcm-formulas.pdf", b"%PDF-test", "application/pdf")},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["pdf_processing"][0]["parser"], "mineru_precision")
            self.assertEqual(payload["files"], ["tcm-formulas.mineru.md"])
            self.assertIn("益气健脾", (Path(directory) / "tcm-formulas.mineru.md").read_text(encoding="utf-8"))
            self.assertEqual(fake_rag.rebuilds, [("personal", 1)])


if __name__ == "__main__":
    unittest.main()
