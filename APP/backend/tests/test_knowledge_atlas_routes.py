import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from APP.backend.auth import get_current_user
from APP.backend.database import UserModel
from APP.backend.tests.test_knowledge_atlas_service import write_atlas_fixture


class KnowledgeAtlasRoutesTests(unittest.TestCase):
    def setUp(self):
        from APP.backend.main import app
        from APP.backend.knowledge_atlas_service import KnowledgeAtlasStore
        from APP.backend.routers import knowledge_atlas_routes

        self.app = app
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data_root = root / "backend_delivery"
        self.video_root = root / "videos"
        write_atlas_fixture(self.data_root, self.video_root)
        self.store = KnowledgeAtlasStore(self.data_root, video_root=self.video_root)
        self.service_patch = patch.object(knowledge_atlas_routes, "atlas_service", self.store)
        self.service_patch.start()
        self.app.dependency_overrides[get_current_user] = lambda: UserModel(
            id=1, username="learner", email="learner@example.com", hashed_password="x"
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.service_patch.stop()
        warm_thread = getattr(self.store, "_warm_thread", None)
        if warm_thread is not None:
            warm_thread.join(timeout=5)
        self.temp.cleanup()

    def test_routes_are_authenticated_and_registered(self):
        paths = self.app.openapi()["paths"]
        for path in (
            "/knowledge/atlas/status",
            "/knowledge/atlas/routes",
            "/knowledge/atlas/nodes",
            "/knowledge/atlas/detail/{kp_id}",
            "/knowledge/atlas/images/{filename}",
            "/knowledge/atlas/warm",
            "/knowledge/atlas/resolve-context",
            "/knowledge/atlas/questions/search",
        ):
            self.assertIn(path, paths)

        self.app.dependency_overrides.pop(get_current_user)
        response = self.client.get("/knowledge/atlas/status")
        self.assertEqual(response.status_code, 401)

    def test_status_routes_nodes_detail_and_search_contracts(self):
        status = self.client.get("/knowledge/atlas/status")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["available"])

        routes = self.client.get("/knowledge/atlas/routes")
        self.assertEqual(routes.status_code, 200)
        self.assertEqual(routes.json()["routes"][0]["id"], "textbook_14_5")

        nodes = self.client.get("/knowledge/atlas/nodes", params={"level": 1, "route": "textbook_14_5"})
        self.assertEqual(nodes.status_code, 200)
        self.assertEqual(nodes.json()["nodes"][0]["name"], "药理学")

        detail = self.client.get("/knowledge/atlas/detail/kp-reentry", params={"question_limit": 1})
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["questions"][0]["question_id"], "q-linked")

        search = self.client.get(
            "/knowledge/atlas/questions/search",
            params={"q": "折返", "kp_id": "kp-reentry", "mode": "lexical"},
        )
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["items"][0]["question_id"], "q-linked")

    def test_semantic_question_search_exposes_runtime_state_and_full_contract(self):
        from APP.backend.rag_core import RAGUnavailableError
        from APP.backend.routers import knowledge_atlas_routes

        semantic_item = {
            "question_id": "q-linked",
            "stem": "question",
            "options": [],
            "answer": ["A"],
            "explanation": "why",
            "kp_ids": ["kp-reentry"],
            "score": 0.9,
            "channels": ["question_index_v2", "semantic_search"],
        }
        with patch.object(
            knowledge_atlas_routes.question_index_search_service,
            "search",
            return_value=[semantic_item],
        ):
            response = self.client.get(
                "/knowledge/atlas/questions/search",
                params={"q": "question", "mode": "semantic"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "semantic")
        self.assertEqual(response.json()["items"][0], semantic_item)

        failure = RAGUnavailableError(state="misconfigured", message="model path required")
        with patch.object(
            knowledge_atlas_routes.question_index_search_service,
            "search",
            side_effect=failure,
        ):
            response = self.client.get(
                "/knowledge/atlas/questions/search",
                params={"q": "question", "mode": "semantic"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["state"], "misconfigured")

    def test_bad_node_request_and_unknown_detail_are_local_errors(self):
        response = self.client.get("/knowledge/atlas/nodes", params={"level": 4})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

        response = self.client.get("/knowledge/atlas/detail/missing")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["ok"])

    def test_image_route_blocks_directory_traversal_and_serves_known_image(self):
        image = self.client.get("/knowledge/atlas/images/reentry.png")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.content, b"PNG")

        traversal = self.client.get("/knowledge/atlas/images/..%2F01_question_bank%2Fformatted_questions.json")
        self.assertIn(traversal.status_code, {400, 404})

    def test_warm_returns_background_contract(self):
        response = self.client.post("/knowledge/atlas/warm")
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["ok"])
        self.assertIn(response.json()["status"], {"warming", "warm"})


if __name__ == "__main__":
    unittest.main()
