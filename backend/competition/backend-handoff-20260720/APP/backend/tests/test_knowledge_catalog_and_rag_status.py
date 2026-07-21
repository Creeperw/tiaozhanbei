import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException


class KnowledgeCatalogAndRagStatusTests(unittest.TestCase):
    def test_search_endpoint_returns_explicit_service_unavailable_state(self):
        from APP.backend.rag_core import RAGUnavailableError
        from APP.backend.routers import knowledge_routes

        failure = RAGUnavailableError(
            state="misconfigured",
            message="EMBEDDING_MODEL_PATH is required",
        )
        with patch.object(knowledge_routes.rag_service, "search", side_effect=failure):
            with self.assertRaises(HTTPException) as captured:
                knowledge_routes.search_test(
                    knowledge_routes.SearchRequest(query="折返", top_k=5),
                    current_user=SimpleNamespace(id=1),
                )

        self.assertEqual(captured.exception.status_code, 503)
        self.assertEqual(captured.exception.detail["state"], "misconfigured")
        self.assertIn("EMBEDDING_MODEL_PATH", captured.exception.detail["message"])

    def test_catalog_route_merges_isolated_atlas_datasets(self):
        from APP.backend.routers import knowledge_routes

        rag = SimpleNamespace(get_catalog=lambda **kwargs: {
            "documents": [{"name": "full.md"}],
            "datasets": [{"name": "official_exam_2025"}],
            "indexes": [],
        })
        atlas = SimpleNamespace(catalog_datasets=lambda: [
            {"id": "atlas_question_bank", "name": "Atlas 题库", "count": 93111}
        ])
        with patch.object(knowledge_routes, "rag_service", rag), patch.object(
            knowledge_routes, "atlas_service", atlas
        ):
            result = knowledge_routes.get_catalog(
                scope="public",
                current_user=SimpleNamespace(id=1),
            )

        self.assertEqual([item["name"] for item in result["documents"]], ["full.md"])
        self.assertEqual(
            [item.get("id") or item["name"] for item in result["datasets"]],
            ["official_exam_2025", "atlas_question_bank"],
        )

    def test_list_files_only_returns_real_files_and_catalog_separates_resource_kinds(self):
        from APP.backend.rag_core import RAGService

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_data = root / "data"
            public_indexes = root / "indexes"
            user_data = root / "users" / "data"
            user_indexes = root / "users" / "indexes"
            public_data.mkdir(parents=True)
            public_indexes.mkdir(parents=True)
            user_data.mkdir(parents=True)
            user_indexes.mkdir(parents=True)
            (public_data / "full.md").write_text("document", encoding="utf-8")
            (public_data / "official_exam_2025").mkdir()
            question_index = public_indexes / "题库"
            question_index.mkdir()
            (question_index / "index.faiss").write_bytes(b"index")
            (question_index / "metadata.jsonl").write_text("{}\n{}\n", encoding="utf-8")
            (question_index / "index_manifest.json").write_text(json.dumps({
                "embedding_model": "Qwen/Qwen3-Embedding-4B",
                "dimensions": 2560,
                "normalized": True,
                "count": 2,
            }), encoding="utf-8")

            class Config:
                PUBLIC_DATA_SOURCE_PATH = str(public_data)
                PUBLIC_INDEX_DIR = str(public_indexes)
                USER_DATA_ROOT = str(user_data)
                USER_INDEX_ROOT = str(user_indexes)
                EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-4B"

            service = object.__new__(RAGService)
            service.dbs = {}
            service.user_dbs = {}
            service.model = None
            service.embedding_state = "disabled"
            service.embedding_error = None
            service.is_processing = False
            service.current_progress = 0
            service.current_status = "disabled"
            with patch("APP.backend.rag_core.Config", Config):
                files = service.list_files(scope="public")
                catalog = service.get_catalog(scope="public")

            self.assertEqual([item["name"] for item in files], ["full.md"])
            self.assertEqual([item["name"] for item in catalog["documents"]], ["full.md"])
            self.assertEqual(catalog["datasets"][0]["name"], "official_exam_2025")
            self.assertEqual(catalog["indexes"][0]["name"], "题库")
            self.assertEqual(catalog["indexes"][0]["count"], 2)
            self.assertEqual(catalog["indexes"][0]["dimensions"], 2560)
            self.assertFalse(catalog["indexes"][0]["loaded"])

    def test_disabled_embedding_status_is_explicit(self):
        from APP.backend.rag_core import RAGService

        service = object.__new__(RAGService)
        with patch("APP.backend.rag_core.EMBEDDING_MODE", "disabled"):
            service.initialize()

        stats = service.get_stats()
        self.assertEqual(stats["embedding_state"], "disabled")
        self.assertIsNone(stats["embedding_error"])
        from APP.backend.rag_core import RAGUnavailableError

        with self.assertRaises(RAGUnavailableError) as captured:
            service.search("anything")
        self.assertEqual(captured.exception.state, "disabled")
        self.assertIn("disabled", captured.exception.message)

    def test_enabled_embedding_with_missing_path_is_misconfigured_without_importing_model(self):
        from APP.backend.rag_core import RAGService

        service = object.__new__(RAGService)
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing-model")
            with patch("APP.backend.rag_core.EMBEDDING_MODE", "enabled"), patch(
                "APP.backend.rag_core.Config.EMBEDDING_MODEL_PATH", missing, create=True
            ):
                service.initialize()

        stats = service.get_stats()
        self.assertEqual(stats["embedding_state"], "misconfigured")
        self.assertIn("EMBEDDING_MODEL_PATH", stats["embedding_error"])
        with self.assertRaisesRegex(RuntimeError, "misconfigured"):
            service.search("anything")

    def test_enabled_embedding_uses_explicit_factory_and_reports_ready(self):
        from APP.backend.rag_core import RAGService

        class FakeModel:
            def get_sentence_embedding_dimension(self):
                return 2560

        service = object.__new__(RAGService)
        with tempfile.TemporaryDirectory() as directory:
            with patch("APP.backend.rag_core.EMBEDDING_MODE", "enabled"), patch(
                "APP.backend.rag_core.Config.EMBEDDING_MODEL_PATH", directory, create=True
            ), patch("APP.backend.rag_core._create_sentence_transformer", return_value=FakeModel()) as factory, patch(
                "APP.backend.rag_core._validate_embedding_contract",
                return_value={"model_id": "Qwen/Qwen3-Embedding-4B", "dimensions": 2560},
            ), patch.object(
                service, "load_all_dbs"
            ):
                service.initialize()

        factory.assert_called_once()
        self.assertEqual(service.get_stats()["embedding_state"], "ready")

    def test_enabled_embedding_rejects_model_dimension_that_differs_from_active_manifest(self):
        from APP.backend.rag_core import RAGService

        class WrongDimensionModel:
            def get_sentence_embedding_dimension(self):
                return 1024

        service = object.__new__(RAGService)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_path = root / "model"
            index_root = root / "indexes"
            active = index_root / "题库-v2"
            model_path.mkdir()
            active.mkdir(parents=True)
            (index_root / ".question-index-active.json").write_text(
                json.dumps({"collection": "题库-v2"}), encoding="utf-8"
            )
            (active / "index_manifest.json").write_text(
                json.dumps({
                    "embedding_model": "Qwen/Qwen3-Embedding-4B",
                    "dimensions": 2560,
                    "normalized": True,
                }),
                encoding="utf-8",
            )
            (active / "index.faiss").write_bytes(b"contract-fixture")
            (active / "metadata.jsonl").write_text("", encoding="utf-8")
            with patch("APP.backend.rag_core.EMBEDDING_MODE", "enabled"), patch(
                "APP.backend.rag_core.Config.EMBEDDING_MODEL_PATH", str(model_path), create=True
            ), patch(
                "APP.backend.rag_core.Config.PUBLIC_INDEX_DIR", str(index_root), create=True
            ), patch(
                "APP.backend.rag_core.Config.EMBEDDING_MODEL_ID", "Qwen/Qwen3-Embedding-4B", create=True
            ), patch(
                "APP.backend.rag_core.Config.EMBEDDING_DIMENSIONS", 2560, create=True
            ), patch(
                "APP.backend.rag_core._create_sentence_transformer", return_value=WrongDimensionModel()
            ), patch.object(service, "load_all_dbs") as load:
                service.initialize()

        self.assertEqual(service.get_stats()["embedding_state"], "unavailable")
        self.assertIn("dimension", service.get_stats()["embedding_error"])
        load.assert_not_called()

    def test_enabled_embedding_rejects_manifest_model_identity_mismatch(self):
        from APP.backend.rag_core import RAGService

        class CompatibleDimensionModel:
            def get_sentence_embedding_dimension(self):
                return 2560

        service = object.__new__(RAGService)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_path = root / "model"
            index_root = root / "indexes"
            active = index_root / "题库-v2"
            model_path.mkdir()
            active.mkdir(parents=True)
            (index_root / ".question-index-active.json").write_text(
                json.dumps({"collection": "题库-v2"}), encoding="utf-8"
            )
            (active / "index_manifest.json").write_text(
                json.dumps({
                    "embedding_model": "wrong/model",
                    "dimensions": 2560,
                    "normalized": True,
                }),
                encoding="utf-8",
            )
            (active / "index.faiss").write_bytes(b"contract-fixture")
            (active / "metadata.jsonl").write_text("", encoding="utf-8")
            with patch("APP.backend.rag_core.EMBEDDING_MODE", "enabled"), patch(
                "APP.backend.rag_core.Config.EMBEDDING_MODEL_PATH", str(model_path), create=True
            ), patch(
                "APP.backend.rag_core.Config.PUBLIC_INDEX_DIR", str(index_root), create=True
            ), patch(
                "APP.backend.rag_core.Config.EMBEDDING_MODEL_ID", "Qwen/Qwen3-Embedding-4B", create=True
            ), patch(
                "APP.backend.rag_core.Config.EMBEDDING_DIMENSIONS", 2560, create=True
            ), patch(
                "APP.backend.rag_core._create_sentence_transformer", return_value=CompatibleDimensionModel()
            ), patch.object(service, "load_all_dbs") as load:
                service.initialize()

        self.assertEqual(service.get_stats()["embedding_state"], "unavailable")
        self.assertIn("model identity", service.get_stats()["embedding_error"])
        load.assert_not_called()

    def test_evidence_pack_route_maps_rag_runtime_failure_to_503(self):
        from APP.backend.rag_core import RAGUnavailableError
        from APP.backend.routers import knowledge_routes

        failure = RAGUnavailableError(state="misconfigured", message="model path required")
        with patch.object(
            knowledge_routes, "build_learner_context_brief", return_value=SimpleNamespace()
        ), patch.object(knowledge_routes, "build_evidence_pack", side_effect=failure):
            with self.assertRaises(HTTPException) as captured:
                knowledge_routes.create_evidence_pack(
                    knowledge_routes.EvidencePackRequest(query="折返"),
                    current_user=SimpleNamespace(id=1),
                    db=SimpleNamespace(),
                )

        self.assertEqual(captured.exception.status_code, 503)
        self.assertEqual(captured.exception.detail["state"], "misconfigured")

    def test_active_question_pointer_loads_only_v2_and_catalog_marks_it_active(self):
        from APP.backend.rag_core import RAGService

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            indexes = root / "indexes"
            users_data = root / "users-data"
            users_indexes = root / "users-indexes"
            for path in (data, indexes, users_data, users_indexes):
                path.mkdir()
            for name, count in (("题库", 4), ("题库-v2", 3)):
                target = indexes / name
                target.mkdir()
                (target / "index.faiss").write_bytes(b"index")
                (target / "metadata.jsonl").write_text("{}\n" * count, encoding="utf-8")
                (target / "index_manifest.json").write_text(
                    json.dumps({"count": count, "embedding_model": "model", "dimensions": 4}),
                    encoding="utf-8",
                )
            (indexes / ".question-index-active.json").write_text(
                json.dumps({"collection": "题库-v2"}), encoding="utf-8"
            )

            class Config:
                PUBLIC_DATA_SOURCE_PATH = str(data)
                PUBLIC_INDEX_DIR = str(indexes)
                USER_DATA_ROOT = str(users_data)
                USER_INDEX_ROOT = str(users_indexes)
                EMBEDDING_MODEL = "model"

            service = object.__new__(RAGService)
            service.dbs = {}
            service.user_dbs = {}
            service.model = None
            service.embedding_state = "disabled"
            service.embedding_error = None
            service._metadata_count_cache = {}
            fake_database = lambda index_path, metadata_path: SimpleNamespace(  # noqa: E731
                index_path=index_path, metadata_path=metadata_path, metadata=[]
            )
            with patch("APP.backend.rag_core.Config", Config), patch(
                "APP.backend.rag_core.VectorDatabase", side_effect=fake_database
            ):
                service._load_scope_dbs("public")
                catalog = service.get_catalog(scope="public")

            self.assertNotIn("题库", service.dbs)
            self.assertIn("题库-v2", service.dbs)
            active = {item["name"]: item["active"] for item in catalog["indexes"]}
            self.assertEqual(active, {"题库": False, "题库-v2": True})

    def test_document_rag_search_excludes_the_active_question_collection(self):
        import numpy as np

        from APP.backend.rag_core import RAGService

        class FakeModel:
            def encode(self, values, convert_to_numpy=True):
                del values, convert_to_numpy
                return np.asarray([[1.0, 0.0]], dtype="float32")

        class FakeIndex:
            d = 2
            ntotal = 1

            def __init__(self):
                self.called = False

            def search(self, vector, limit):
                del vector, limit
                self.called = True
                return np.asarray([[0.9]], dtype="float32"), np.asarray([[0]], dtype="int64")

        document_index = FakeIndex()
        question_index = FakeIndex()
        service = object.__new__(RAGService)
        service.model = FakeModel()
        service.embedding_state = "ready"
        service.embedding_error = None
        service.dbs = {
            "full.md": SimpleNamespace(
                index=document_index,
                metadata=[{"type": "text", "content": "文档证据"}],
            ),
            "题库-v2": SimpleNamespace(
                index=question_index,
                metadata=[{"type": "json_field", "content": "题目不应作为 chunk"}],
            ),
        }
        service.user_dbs = {}
        service._active_question_collection = "题库-v2"
        with patch(
            "APP.backend.rag_core.active_question_index_name", return_value="题库-v2"
        ):
            results = service.search("折返", include_personal=False)

        self.assertEqual([item["source"] for item in results], ["full.md"])
        self.assertTrue(document_index.called)
        self.assertFalse(question_index.called)

    def test_broken_question_hot_swap_does_not_disable_document_rag(self):
        import numpy as np

        from APP.backend.rag_core import RAGService, RAGUnavailableError
        from APP.backend.question_index_v2_service import (
            DEFAULT_QUESTION_COLLECTION,
            V2_QUESTION_COLLECTION,
        )

        class FakeModel:
            def encode(self, values, convert_to_numpy=True):
                del values, convert_to_numpy
                return np.asarray([[1.0, 0.0]], dtype="float32")

        class FakeIndex:
            d = 2
            ntotal = 1

            def __init__(self):
                self.called = False

            def search(self, vector, limit):
                del vector, limit
                self.called = True
                return np.asarray([[0.9]], dtype="float32"), np.asarray([[0]], dtype="int64")

        document_index = FakeIndex()
        old_question_index = FakeIndex()
        v2_question_index = FakeIndex()
        service = object.__new__(RAGService)
        service.model = FakeModel()
        service.embedding_state = "ready"
        service.embedding_error = None
        service.question_index_error = None
        service.dbs = {
            "full.md": SimpleNamespace(
                index=document_index,
                metadata=[{"type": "text", "content": "document evidence"}],
            ),
            DEFAULT_QUESTION_COLLECTION: SimpleNamespace(
                index=old_question_index,
                metadata=[{"type": "json_field", "content": "old question"}],
            ),
            V2_QUESTION_COLLECTION: SimpleNamespace(
                index=v2_question_index,
                metadata=[{"type": "json_field", "content": "new question"}],
            ),
        }
        service.user_dbs = {}
        service._active_question_collection = DEFAULT_QUESTION_COLLECTION
        failure = RAGUnavailableError(
            state="unavailable",
            message="active question index reload failed: corrupt target",
        )

        with patch(
            "APP.backend.rag_core.active_question_index_name",
            return_value=V2_QUESTION_COLLECTION,
        ), patch.object(service, "ensure_active_question_db", side_effect=failure):
            results = service.search("document", include_personal=False)

        self.assertEqual([item["source"] for item in results], ["full.md"])
        self.assertTrue(document_index.called)
        self.assertFalse(old_question_index.called)
        self.assertFalse(v2_question_index.called)
        self.assertIn("corrupt target", service.question_index_error)

    def test_active_question_pointer_change_loads_new_database_before_swapping(self):
        from APP.backend.rag_core import RAGService

        old_database = SimpleNamespace(index=SimpleNamespace(ntotal=4), metadata=[{}] * 4)
        document_database = SimpleNamespace(index=SimpleNamespace(ntotal=1), metadata=[{}])
        new_database = SimpleNamespace(index=SimpleNamespace(ntotal=3, d=2560), metadata=[{}] * 3)
        service = object.__new__(RAGService)
        service.model = SimpleNamespace()
        service.embedding_state = "ready"
        service.embedding_error = None
        service.dbs = {"题库": old_database, "full.md": document_database}
        service._active_question_collection = "题库"
        service._question_reload_lock = __import__("threading").RLock()

        with tempfile.TemporaryDirectory() as directory, patch(
            "APP.backend.rag_core.Config.PUBLIC_INDEX_DIR", directory, create=True
        ), patch(
            "APP.backend.rag_core.active_question_index_name", return_value="题库-v2"
        ), patch(
            "APP.backend.rag_core.VectorDatabase", return_value=new_database
        ) as database_factory:
            active = service.ensure_active_question_db()

        self.assertEqual(active, "题库-v2")
        self.assertIs(service.dbs["题库-v2"], new_database)
        self.assertIs(service.dbs["full.md"], document_database)
        self.assertNotIn("题库", service.dbs)
        database_factory.assert_called_once()

    def test_failed_active_question_reload_keeps_previous_database_and_reports_unavailable(self):
        from APP.backend.rag_core import RAGService, RAGUnavailableError

        old_database = SimpleNamespace(index=SimpleNamespace(ntotal=4), metadata=[{}] * 4)
        service = object.__new__(RAGService)
        service.model = SimpleNamespace()
        service.embedding_state = "ready"
        service.embedding_error = None
        service.dbs = {"题库": old_database}
        service._active_question_collection = "题库"
        service._question_reload_lock = __import__("threading").RLock()

        with tempfile.TemporaryDirectory() as directory, patch(
            "APP.backend.rag_core.Config.PUBLIC_INDEX_DIR", directory, create=True
        ), patch(
            "APP.backend.rag_core.active_question_index_name", return_value="题库-v2"
        ), patch(
            "APP.backend.rag_core.VectorDatabase", side_effect=RuntimeError("corrupt target")
        ):
            with self.assertRaises(RAGUnavailableError) as captured:
                service.ensure_active_question_db()

        self.assertEqual(captured.exception.state, "unavailable")
        self.assertIs(service.dbs["题库"], old_database)
        self.assertNotIn("题库-v2", service.dbs)


if __name__ == "__main__":
    unittest.main()
