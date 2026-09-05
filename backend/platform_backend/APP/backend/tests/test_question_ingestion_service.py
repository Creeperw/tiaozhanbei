import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.question_ingestion_service import (
    QuestionIngestionService,
    build_question_pipeline,
    question_pipeline_settings,
)


class FakePipeline:
    def __init__(self, result):
        self.result = result
        self.received = None

    def ingest(self, raw):
        self.received = raw
        return self.result


class QuestionIngestionServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.payload = {
            "stem": "四君子汤主治哪类证候？",
            "answer": "脾胃气虚证",
            "analysis": "四君子汤益气健脾。",
            "question_type": "single_choice",
            "difficulty": 2.0,
            "requested_kp_ids": ["KP_FJ_001"],
            "source_type": "user_upload",
            "owner_id": "1",
            "source_ref": "upload:1",
        }

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_persists_active_pipeline_result_as_question_bank_item(self):
        pipeline = FakePipeline(
            {
                "status": "active",
                "question_id": "Q_FJ_001",
                "audit": {"quality_score": 0.93},
                "question": {"answer": "脾胃气虚证", "analysis": "四君子汤益气健脾。"},
                "kp_matches": [{"kp_id": "KP_FJ_001"}],
            }
        )

        self.payload["answer"] = ""
        self.payload["analysis"] = ""
        result = QuestionIngestionService(lambda: pipeline).ingest(self.db, self.payload)

        item = self.db.query(database.QuestionBankItem).filter_by(question_id="Q_FJ_001").one()
        self.assertTrue(result["stored"])
        self.assertEqual(item.stem, self.payload["stem"])
        self.assertEqual(item.answer, "脾胃气虚证")
        self.assertEqual(item.analysis, "四君子汤益气健脾。")
        self.assertEqual(json.loads(item.kp_ids_json), ["KP_FJ_001"])
        self.assertEqual(item.quality_score, 0.93)
        self.assertEqual(item.status, "active")

    def test_does_not_persist_non_active_pipeline_result(self):
        pipeline = FakePipeline(
            {
                "status": "needs_human_review",
                "question_id": "Q_PENDING_001",
                "audit": {"quality_score": 0.62},
                "kp_matches": [],
            }
        )

        result = QuestionIngestionService(lambda: pipeline).ingest(self.db, self.payload)

        self.assertFalse(result["stored"])
        self.assertEqual(self.db.query(database.QuestionBankItem).count(), 0)

    def test_uses_formal_database_for_exact_duplicate_before_pipeline_runs(self):
        self.db.add(database.QuestionBankItem(question_id="Q_EXISTING", stem=self.payload["stem"]))
        self.db.commit()
        pipeline = FakePipeline({"status": "active", "question_id": "Q_UNEXPECTED"})

        result = QuestionIngestionService(lambda: pipeline).ingest(self.db, self.payload)

        self.assertEqual(result, {"status": "duplicate", "question_id": "Q_EXISTING", "stored": False})
        self.assertIsNone(pipeline.received)

    def test_returns_existing_item_as_duplicate_when_pipeline_id_is_already_persisted(self):
        self.db.add(database.QuestionBankItem(question_id="Q_FJ_001", stem="已存在题目"))
        self.db.commit()
        pipeline = FakePipeline(
            {
                "status": "active",
                "question_id": "Q_FJ_001",
                "audit": {"quality_score": 0.93},
                "question": {"answer": "脾胃气虚证", "analysis": "四君子汤益气健脾。"},
                "kp_matches": [{"kp_id": "KP_FJ_001"}],
            }
        )

        result = QuestionIngestionService(lambda: pipeline).ingest(self.db, self.payload)

        self.assertEqual(result["status"], "duplicate")
        self.assertFalse(result["stored"])
        self.assertEqual(self.db.query(database.QuestionBankItem).count(), 1)
    def test_default_pipeline_does_not_depend_on_teammate_delivery_directory(self):
        settings = question_pipeline_settings()
        pipeline = build_question_pipeline()

        self.assertNotIn("division of labor", json.dumps(settings, ensure_ascii=False))
        result = pipeline.ingest(self.payload)
        self.assertEqual(result["status"], "needs_human_review")
        self.assertIsNone(result["question_id"])
        self.assertEqual(result["question"]["stem"], self.payload["stem"])
        self.assertEqual(result["kp_matches"], [{"kp_id": "KP_FJ_001"}])

    def test_builds_remote_embedding_and_deepseek_audit_settings(self):
        settings = question_pipeline_settings()

        self.assertEqual(settings["embedding_provider"], "openai_compatible")
        self.assertEqual(settings["embedding_key_env"], "SILICONFLOW_API_KEY")
        self.assertEqual(settings["audit_provider"], "remote")
        self.assertEqual(settings["expert_provider"], "remote")
        self.assertEqual(settings["judge_provider"], "remote")
        self.assertEqual(settings["revision_provider"], "remote")
        self.assertEqual(settings["llm_key_env"], "DEEPSEEK_API_KEY")
        self.assertTrue(settings["question_vdb_dir"].endswith("vdb_store\\indexes\\题库") or settings["question_vdb_dir"].endswith("vdb_store/indexes/题库"))


if __name__ == "__main__":
    unittest.main()
