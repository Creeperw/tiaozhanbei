import unittest
from pathlib import Path
from unittest.mock import patch

from APP.backend.pdf_question_ingestion_service import (
    PdfQuestionIngestionService,
    StructuredMarkdownExtractor,
)


class PdfQuestionIngestionServiceTests(unittest.TestCase):
    def test_rejects_file_outside_upload_directory(self):
        service = PdfQuestionIngestionService()
        with patch("APP.backend.pdf_question_ingestion_service.FILES", {
            "FILE_001": {
                "uploader_id": 1,
                "original_name": "题目.pdf",
                "saved_path": str(Path("D:/outside/题目.pdf")),
            },
        }):
            with self.assertRaisesRegex(ValueError, "upload directory"):
                service.build_payload(file_id="FILE_001", submitted_by_user_id=1)

    def test_rejects_pdf_owned_by_another_user(self):
        service = PdfQuestionIngestionService()
        with patch("APP.backend.pdf_question_ingestion_service.FILES", {
            "FILE_001": {
                "uploader_id": 2,
                "original_name": "题目.pdf",
                "saved_path": str(Path("D:/uploads/题目.pdf")),
            },
        }):
            with self.assertRaisesRegex(ValueError, "does not belong"):
                service.build_payload(file_id="FILE_001", submitted_by_user_id=1)

    def test_default_extractor_does_not_depend_on_teammate_delivery_directory(self):
        rows = StructuredMarkdownExtractor().extract(
            "## 题目 1\n- 题型：简答题\n- 题干：阴阳关系是什么？\n- 答案：对立制约与互根互用。\n- 知识点：KP_YINYANG",
            "upload:FILE_001",
            "admin_pdf_upload",
            "1",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stem"], "阴阳关系是什么?")
        self.assertEqual(rows[0]["requested_kp_ids"], ["KP_YINYANG"])

    def test_runs_mineru_extracts_questions_and_returns_each_result(self):
        service = PdfQuestionIngestionService(
            mineru_factory=lambda: FakeMinerU(),
            extractor_factory=lambda: FakeExtractor(),
            ingestion_service_factory=lambda: FakeIngestionService(),
        )
        result = service.ingest(FakeSession(), {
            "file_id": "FILE_001",
            "file_path": "D:/uploads/题目.pdf",
            "original_filename": "题目.pdf",
            "source_ref": "upload:FILE_001",
            "owner_id": "1",
        })

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["question_count"], 2)
        self.assertEqual(result["status_counts"], {"active": 1, "needs_human_review": 1})
        self.assertEqual(result["results"][0]["question_id"], "Q_001")


class FakeSession:
    def commit(self):
        pass


class FakeMinerU:
    def parse(self, file_path: Path) -> str:
        return "## 题目\n题干：第一题\n答案：答案一\n\n## 题目\n题干：第二题\n答案：答案二"


class FakeExtractor:
    def extract(self, markdown, source_ref, source_type, owner_id):
        return [
            {"stem": "第一题", "answer": "答案一", "source_ref": source_ref, "source_type": source_type, "owner_id": owner_id},
            {"stem": "第二题", "answer": "答案二", "source_ref": source_ref, "source_type": source_type, "owner_id": owner_id},
        ]


class FakeIngestionService:
    def __init__(self):
        self.calls = 0

    def ingest(self, db, payload):
        self.calls += 1
        if self.calls == 1:
            return {"status": "active", "stored": True, "question_id": "Q_001"}
        return {"status": "needs_human_review", "stored": False, "question_id": "Q_002"}


if __name__ == "__main__":
    unittest.main()
