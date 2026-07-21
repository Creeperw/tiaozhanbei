import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend import database


class DocumentIngestionServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def test_rejected_document_does_not_write_knowledge_or_rebuild_index(self):
        from APP.backend.document_ingestion_service import ingest_document

        rebuild_calls = []
        writer_calls = []
        db = self.Session()
        try:
            result = ingest_document(
                db,
                file_path="/tmp/rejected.pdf",
                original_filename="rejected.pdf",
                scope="public",
                user_id=1,
                document_kind="exam_outline",
                extractor=lambda path: "# 未授权试卷\n题目：禁止入库内容",
                audit_reviewer=lambda markdown, metadata: {
                    "decision": "reject",
                    "reason": "版权或来源风险过高",
                    "risk_notes": ["copyright_risk"],
                },
                knowledge_source_writer=lambda **kwargs: writer_calls.append(kwargs),
                rag_rebuild=lambda **kwargs: rebuild_calls.append(kwargs),
            )

            payload = result.model_dump()

            self.assertEqual(payload["status"], "rejected")
            self.assertEqual(payload["audit_decision"]["decision"], "reject")
            self.assertEqual(db.query(database.KnowledgePoint).count(), 0)
            self.assertEqual(db.query(database.QuestionBankItem).count(), 0)
            self.assertEqual(db.query(database.TeachingResource).count(), 0)
            self.assertEqual(writer_calls, [])
            self.assertEqual(rebuild_calls, [])
        finally:
            db.close()

    def test_approved_document_extracts_structured_assets_and_rebuilds_index(self):
        from APP.backend.document_ingestion_service import ingest_document

        markdown = """
# 四君子汤专题讲义
知识点：KP_FJ_001｜四君子汤｜四君子,四君子方｜补气健脾基础方
题目：Q_FJ_101｜四君子汤主治哪类证候？｜脾胃气虚证｜围绕补气健脾的方证对应关系。｜KP_FJ_001｜2
资源：RES_FJ_101｜knowledge_card｜四君子汤知识卡｜用于复习方剂组成和主治。｜KP_FJ_001
"""
        rebuild_calls = []
        writer_calls = []
        db = self.Session()
        try:
            result = ingest_document(
                db,
                file_path="/tmp/approved.pdf",
                original_filename="approved.pdf",
                scope="public",
                user_id=1,
                document_kind="handout",
                extractor=lambda path: markdown,
                audit_reviewer=lambda markdown, metadata: {"decision": "pass", "reason": "可入库", "risk_notes": []},
                knowledge_source_writer=lambda **kwargs: writer_calls.append(kwargs),
                rag_rebuild=lambda **kwargs: rebuild_calls.append(kwargs),
            )

            payload = result.model_dump()
            kp = db.query(database.KnowledgePoint).filter_by(kp_id="KP_FJ_001").one()
            question = db.query(database.QuestionBankItem).filter_by(question_id="Q_FJ_101").one()
            resource = db.query(database.TeachingResource).filter_by(resource_id="RES_FJ_101").one()

            self.assertEqual(payload["status"], "approved")
            self.assertEqual(payload["extracted_knowledge_points"], ["KP_FJ_001"])
            self.assertEqual(payload["extracted_questions"], ["Q_FJ_101"])
            self.assertEqual(payload["extracted_resources"], ["RES_FJ_101"])
            self.assertEqual(kp.name, "四君子汤")
            self.assertEqual(question.stem, "四君子汤主治哪类证候？")
            self.assertEqual(resource.title, "四君子汤知识卡")
            self.assertEqual(writer_calls[0]["markdown"].strip().splitlines()[0], "# 四君子汤专题讲义")
            self.assertEqual(rebuild_calls, [{"scope": "public", "user_id": None}])
        finally:
            db.close()
    def test_writer_failure_rolls_back_structured_assets(self):
        from APP.backend.document_ingestion_service import ingest_document

        markdown = "知识点：KP_FAIL｜失败知识点｜｜不应落库"
        db = self.Session()
        try:
            with self.assertRaises(OSError):
                ingest_document(
                    db,
                    file_path="/tmp/fail.pdf",
                    original_filename="fail.pdf",
                    scope="public",
                    user_id=1,
                    document_kind="handout",
                    extractor=lambda path: markdown,
                    audit_reviewer=lambda markdown, metadata: {"decision": "pass", "reason": "可入库", "risk_notes": []},
                    knowledge_source_writer=lambda **kwargs: (_ for _ in ()).throw(OSError("disk full")),
                    rag_rebuild=lambda **kwargs: None,
                )

            self.assertEqual(db.query(database.KnowledgePoint).filter_by(kp_id="KP_FAIL").count(), 0)
        finally:
            db.close()

    def test_default_audit_reviewer_delegates_to_audit_agent_service(self):
        from APP.backend import document_ingestion_service

        calls = []
        original = document_ingestion_service.audit_agent_service.review_document_ingestion
        document_ingestion_service.audit_agent_service.review_document_ingestion = lambda markdown, metadata: calls.append((markdown, metadata)) or {
            "decision": "pass",
            "reason": "audit facade",
            "risk_notes": [],
        }
        try:
            result = document_ingestion_service.review_document_ingestion("# 四君子汤", {"document_kind": "handout"})
        finally:
            document_ingestion_service.audit_agent_service.review_document_ingestion = original

        self.assertEqual(result["reason"], "audit facade")
        self.assertEqual(calls[0][1]["document_kind"], "handout")

    def test_extract_document_with_markitdown_uses_configured_timeout(self):
        from APP.backend import document_ingestion_service

        calls = []
        original_run = document_ingestion_service.subprocess.run
        document_ingestion_service.subprocess.run = lambda *args, **kwargs: calls.append((args, kwargs)) or type(
            "Completed",
            (),
            {"stdout": "# converted markdown"},
        )()
        try:
            markdown = document_ingestion_service.extract_document_with_markitdown("/tmp/outline.pdf")
        finally:
            document_ingestion_service.subprocess.run = original_run

        self.assertEqual(markdown, "# converted markdown")
        self.assertEqual(calls[0][1]["timeout"], document_ingestion_service.MARKITDOWN_EXTRACT_TIMEOUT_SECONDS)
        self.assertEqual(calls[0][1]["check"], True)

    def test_markitdown_config_is_available(self):
        from APP.backend import config

        self.assertTrue(config.MARKITDOWN_EXTRACT_TIMEOUT_SECONDS > 0)
        self.assertEqual(config.MARKITDOWN_OUTPUT_DIR, "./APP/backend/markitdown_output")


if __name__ == "__main__":
    unittest.main()
