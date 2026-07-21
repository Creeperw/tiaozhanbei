import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend.database import Base, FormalContentImportBatch, QuestionBankItem
from APP.backend.question_bank_import_service import import_question_bank_metadata


class QuestionBankImportServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.metadata_path = Path(self.temp_dir.name) / "metadata.jsonl"
        self.metadata_path.write_text(
            "\n".join([
                json.dumps({
                    "original": {
                        "题目id": "Q_VECTOR_001",
                        "题目内容": "四君子汤主治哪类证候？",
                        "题目答案": "脾胃气虚证",
                        "题型": "单项选择题",
                        "题目大来源": "方剂学题库",
                        "题目章节来源": "补益剂",
                    },
                }, ensure_ascii=False),
                json.dumps({
                    "original": {
                        "题目id": "Q_VECTOR_002",
                        "题目内容": "请简述四君子汤的组成。",
                        "题目答案": "人参、白术、茯苓、炙甘草。",
                        "题型": "简答题",
                        "题目大来源": "方剂学题库",
                    },
                }, ensure_ascii=False),
            ]),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()
        self.engine.dispose()

    def test_imports_vector_metadata_as_pending_link_formal_questions(self):
        with self.Session() as db:
            summary = import_question_bank_metadata(db, metadata_path=self.metadata_path)
            db.commit()

            rows = db.query(QuestionBankItem).order_by(QuestionBankItem.question_id).all()
            self.assertEqual(summary.created_count, 2)
            self.assertEqual(summary.unlinked_count, 2)
            self.assertEqual([row.question_id for row in rows], ["Q_VECTOR_001", "Q_VECTOR_002"])
            self.assertEqual(rows[0].question_type, "single_choice")
            self.assertEqual(rows[0].status, "pending_link")
            self.assertEqual(json.loads(rows[0].kp_ids_json), [])
            self.assertIn("方剂学题库", rows[0].source)
            self.assertEqual(db.query(FormalContentImportBatch).count(), 1)

    def test_imports_actual_case_and_question_types_without_downgrading_them(self):
        self.metadata_path.write_text("\n".join([
            json.dumps({"original": {"题目id": "Q_CASE", "题目内容": "病例分析", "题型": "临床案例问答"}}, ensure_ascii=False),
            json.dumps({"original": {"题目id": "Q_SKILL", "题目内容": "实践技能", "题型": "病例分析/实践技能"}}, ensure_ascii=False),
            json.dumps({"original": {"题目id": "Q_QA", "题目内容": "问答", "题型": "问答题"}}, ensure_ascii=False),
        ]), encoding="utf-8")

        with self.Session() as db:
            import_question_bank_metadata(db, metadata_path=self.metadata_path)
            db.commit()
            types = {row.question_id: row.question_type for row in db.query(QuestionBankItem).all()}

        self.assertEqual(types, {"Q_CASE": "case_quiz", "Q_SKILL": "case_quiz", "Q_QA": "short_answer"})

    def test_counts_non_object_json_line_as_invalid_without_aborting_valid_records(self):
        self.metadata_path.write_text("\n".join([
            "[]",
            json.dumps({"original": {"题目id": "Q_VALID", "题目内容": "有效题干", "题型": "简答题"}}, ensure_ascii=False),
        ]), encoding="utf-8")

        with self.Session() as db:
            summary = import_question_bank_metadata(db, metadata_path=self.metadata_path)
            db.commit()

        self.assertEqual(summary.invalid_count, 1)
        self.assertEqual(summary.created_count, 1)

    def test_same_metadata_file_is_idempotent(self):
        with self.Session() as db:
            first = import_question_bank_metadata(db, metadata_path=self.metadata_path)
            db.commit()
            second = import_question_bank_metadata(db, metadata_path=self.metadata_path)
            db.commit()

            self.assertEqual(first.created_count, 2)
            self.assertTrue(second.idempotent)
            self.assertEqual(db.query(QuestionBankItem).count(), 2)
    def test_conflicting_batch_insert_returns_existing_summary_as_idempotent(self):
        with self.Session() as db:
            first = import_question_bank_metadata(db, metadata_path=self.metadata_path)
            db.commit()
            second = import_question_bank_metadata(db, metadata_path=self.metadata_path)

        self.assertTrue(second.idempotent)
        self.assertEqual(second.content_sha256, first.content_sha256)



if __name__ == "__main__":
    unittest.main()
