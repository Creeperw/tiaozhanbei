import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend.database import Base, QuestionBankItem
from APP.backend.scripts.import_formal_question_bank import run_import


class ImportFormalQuestionBankTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.metadata_path = Path(self.temp_dir.name) / "metadata.jsonl"
        self.metadata_path.write_text(json.dumps({"original": {
            "题目id": "Q_SCRIPT_001",
            "题目内容": "测试题干",
            "题目答案": "测试答案",
            "题型": "简答题",
        }}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()
        self.engine.dispose()

    def test_dry_run_reports_creation_without_writing_database(self):
        with self.Session() as db:
            report = run_import(db, self.metadata_path, dry_run=True)
            self.assertEqual(report["created_count"], 1)
            self.assertEqual(db.query(QuestionBankItem).count(), 0)

    def test_apply_writes_formal_question(self):
        with self.Session() as db:
            report = run_import(db, self.metadata_path, dry_run=False)
            db.commit()
            self.assertEqual(report["created_count"], 1)
            self.assertEqual(db.query(QuestionBankItem).count(), 1)
    def test_dry_run_reads_metadata_as_a_stream(self):
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("whole-file read")):
            with self.Session() as db:
                report = run_import(db, self.metadata_path, dry_run=True)

        self.assertEqual(report["created_count"], 1)


if __name__ == "__main__":
    unittest.main()
