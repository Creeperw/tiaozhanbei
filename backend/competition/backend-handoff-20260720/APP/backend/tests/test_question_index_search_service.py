from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np


class _FakeModel:
    def encode(self, texts, convert_to_numpy=True):
        del texts, convert_to_numpy
        return np.asarray([[3.0, 4.0]], dtype="float32")


class _FakeIndex:
    ntotal = 2
    d = 2

    def search(self, vector, limit):
        np.testing.assert_allclose(vector, np.asarray([[0.6, 0.8]], dtype="float32"))
        self.last_limit = limit
        return (
            np.asarray([[0.94, 0.71]], dtype="float32"),
            np.asarray([[0, 1]], dtype="int64"),
        )


class QuestionIndexSearchServiceTests(unittest.TestCase):
    def _rag(self, *, state="ready"):
        metadata = [
            {
                "atlas": {
                    "question_id": "q-reentry",
                    "stem": "折返形成需要什么条件？",
                    "options": [{"option_id": "A", "content": "单向阻滞"}],
                    "answer": ["A"],
                    "explanation": "存在单向阻滞和可激动组织。",
                    "kp_ids": ["062438"],
                    "status": "active",
                    "channels": ["question_index_v2", "knowledge_atlas"],
                },
                "original": {"difficulty": 2.0},
            },
            {
                "atlas": {
                    "question_id": "q-other",
                    "stem": "另一个问题",
                    "options": [],
                    "answer": "答案",
                    "explanation": "解析",
                    "kp_ids": ["999999"],
                    "status": "active",
                },
            },
        ]
        return SimpleNamespace(
            embedding_state=state,
            embedding_error="EMBEDDING_MODEL_PATH is required" if state != "ready" else None,
            model=_FakeModel() if state == "ready" else None,
            dbs={"题库-v2": SimpleNamespace(index=_FakeIndex(), metadata=metadata)},
        )

    def test_semantic_search_returns_full_question_contract_and_filters_kp(self):
        from APP.backend.question_index_search_service import QuestionIndexSearchService

        service = QuestionIndexSearchService(
            rag=self._rag(),
            active_collection="题库-v2",
        )

        result = service.search("折返", kp_ids=["062438"], limit=5)

        self.assertEqual([item["question_id"] for item in result], ["q-reentry"])
        self.assertEqual(result[0]["stem"], "折返形成需要什么条件？")
        self.assertEqual(result[0]["options"][0]["option_id"], "A")
        self.assertEqual(result[0]["answer"], ["A"])
        self.assertEqual(result[0]["explanation"], "存在单向阻滞和可激动组织。")
        self.assertEqual(result[0]["kp_ids"], ["062438"])
        self.assertAlmostEqual(result[0]["score"], 0.94, places=5)
        self.assertEqual(
            result[0]["channels"],
            ["question_index_v2", "knowledge_atlas", "semantic_search"],
        )

    def test_non_ready_embedding_state_is_an_explicit_error(self):
        from APP.backend.question_index_search_service import QuestionIndexSearchService
        from APP.backend.rag_core import RAGUnavailableError

        service = QuestionIndexSearchService(
            rag=self._rag(state="misconfigured"),
            active_collection="题库-v2",
        )

        with self.assertRaises(RAGUnavailableError) as captured:
            service.search("折返")

        self.assertEqual(captured.exception.state, "misconfigured")
        self.assertIn("EMBEDDING_MODEL_PATH", captured.exception.message)

    def test_missing_active_v2_database_is_not_reported_as_empty_results(self):
        from APP.backend.question_index_search_service import QuestionIndexSearchService
        from APP.backend.rag_core import RAGUnavailableError

        rag = self._rag()
        rag.dbs = {}
        service = QuestionIndexSearchService(rag=rag, active_collection="题库-v2")

        with self.assertRaises(RAGUnavailableError) as captured:
            service.search("折返")

        self.assertEqual(captured.exception.state, "unavailable")
        self.assertIn("题库-v2", captured.exception.message)

    def test_search_refreshes_runtime_database_after_pointer_switch(self):
        from APP.backend.question_index_search_service import QuestionIndexSearchService

        rag = self._rag()
        calls = []
        rag.ensure_active_question_db = lambda: calls.append("refresh") or "题库-v2"
        service = QuestionIndexSearchService(rag=rag, active_collection="题库-v2")

        result = service.search("折返", limit=1)

        self.assertEqual(calls, ["refresh"])
        self.assertEqual(result[0]["question_id"], "q-reentry")


if __name__ == "__main__":
    unittest.main()
