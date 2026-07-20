import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.formal_content_import_service import import_formal_learning_content


class FormalContentImportServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_import_activates_only_questions_with_valid_knowledge_point_links(self):
        with TemporaryDirectory() as tmp:
            paths = self._write_source_files(
                Path(tmp),
                bridges=[
                    {"题目id": "Q_1", "kp_id": "KP_1"},
                    {"题目id": "Q_2", "kp_id": "MISSING"},
                ],
            )

            summary = import_formal_learning_content(
                self.db,
                knowledge_points_path=paths["knowledge_points"],
                questions_path=paths["questions"],
                question_kp_links_path=paths["bridges"],
                data_version="2026-07-15",
            )
            self.db.commit()

        self.assertEqual(summary.knowledge_points, 1)
        self.assertEqual(summary.questions, 2)
        self.assertEqual(summary.active_questions, 1)
        self.assertEqual(summary.pending_link_questions, 1)
        self.assertEqual(summary.invalid_links, 1)
        self.assertEqual(
            self.db.query(database.QuestionBankItem).filter_by(question_id="Q_1").one().status,
            "active",
        )
        self.assertEqual(
            self.db.query(database.QuestionBankItem).filter_by(question_id="Q_2").one().status,
            "pending_link",
        )
        version = self.db.query(database.QuestionVersionRecord).filter_by(question_id="Q_1").one()
        self.assertEqual(version.source_kind, summary.source_tag)
        self.assertEqual(
            self.db.query(database.QuestionKPLinkRecord).filter_by(
                question_version_id=version.question_version_id,
                kp_id="KP_1",
                status="active",
            ).count(),
            1,
        )
        self.assertEqual(self.db.query(database.LearningQuestion).count(), 1)

    def test_new_content_version_supersedes_prior_version_and_normalizes_question_type(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_source_files(root, bridges=[{"question_id": "Q_1", "kp_id": "KP_1"}])
            import_formal_learning_content(
                self.db,
                knowledge_points_path=paths["knowledge_points"],
                questions_path=paths["questions"],
                question_kp_links_path=paths["bridges"],
                data_version="v1",
            )
            self.db.commit()
            questions = json.loads(paths["questions"].read_text(encoding="utf-8"))
            questions[0]["题目内容"] = "更新后的题干"
            paths["questions"].write_text(json.dumps(questions, ensure_ascii=False), encoding="utf-8")

            import_formal_learning_content(
                self.db,
                knowledge_points_path=paths["knowledge_points"],
                questions_path=paths["questions"],
                question_kp_links_path=paths["bridges"],
                data_version="v2",
            )
            self.db.commit()

        versions = self.db.query(database.QuestionVersionRecord).filter_by(question_id="Q_1").order_by(
            database.QuestionVersionRecord.version
        ).all()
        self.assertEqual([version.version for version in versions], [1, 2])
        self.assertEqual([version.status for version in versions], ["superseded", "active"])
        self.assertEqual(versions[-1].question_type, "single_choice")

    def test_new_snapshot_deactivates_formal_question_missing_from_the_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_source_files(root, bridges=[{"question_id": "Q_1", "kp_id": "KP_1"}])
            import_formal_learning_content(
                self.db,
                knowledge_points_path=paths["knowledge_points"],
                questions_path=paths["questions"],
                question_kp_links_path=paths["bridges"],
                data_version="v1",
            )
            self.db.commit()
            questions = json.loads(paths["questions"].read_text(encoding="utf-8"))
            paths["questions"].write_text(json.dumps([questions[1]], ensure_ascii=False), encoding="utf-8")

            import_formal_learning_content(
                self.db,
                knowledge_points_path=paths["knowledge_points"],
                questions_path=paths["questions"],
                question_kp_links_path=paths["bridges"],
                data_version="v2",
            )
            self.db.commit()

        item = self.db.query(database.QuestionBankItem).filter_by(question_id="Q_1").one()
        mirror = self.db.query(database.LearningQuestion).filter_by(question_id="Q_1").one()
        self.assertEqual(item.status, "inactive")
        self.assertEqual(mirror.kp_ids_json, "[]")
        self.assertEqual(
            self.db.query(database.QuestionVersionRecord).filter_by(question_id="Q_1", status="active").count(),
            0,
        )

    def test_removed_link_deactivates_the_core_question_mirror(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_source_files(root, bridges=[{"question_id": "Q_1", "kp_id": "KP_1"}])
            import_formal_learning_content(
                self.db,
                knowledge_points_path=paths["knowledge_points"],
                questions_path=paths["questions"],
                question_kp_links_path=paths["bridges"],
                data_version="v1",
            )
            self.db.commit()
            paths["bridges"].write_text("", encoding="utf-8")

            import_formal_learning_content(
                self.db,
                knowledge_points_path=paths["knowledge_points"],
                questions_path=paths["questions"],
                question_kp_links_path=paths["bridges"],
                data_version="v2",
            )
            self.db.commit()

        item = self.db.query(database.QuestionBankItem).filter_by(question_id="Q_1").one()
        mirror = self.db.query(database.LearningQuestion).filter_by(question_id="Q_1").one()
        self.assertEqual(item.status, "pending_link")
        self.assertEqual(mirror.kp_ids_json, "[]")

    def test_repeating_same_version_and_content_hash_does_not_duplicate_rows(self):
        with TemporaryDirectory() as tmp:
            paths = self._write_source_files(Path(tmp), bridges=[{"question_id": "Q_1", "kp_id": "KP_1"}])
            first = import_formal_learning_content(
                self.db,
                knowledge_points_path=paths["knowledge_points"],
                questions_path=paths["questions"],
                question_kp_links_path=paths["bridges"],
                data_version="2026-07-15",
            )
            self.db.commit()
            second = import_formal_learning_content(
                self.db,
                knowledge_points_path=paths["knowledge_points"],
                questions_path=paths["questions"],
                question_kp_links_path=paths["bridges"],
                data_version="2026-07-15",
            )
            self.db.commit()

        self.assertEqual(first.content_sha256, second.content_sha256)
        self.assertEqual(first.source_tag, second.source_tag)
        self.assertTrue(second.idempotent)
        self.assertEqual(self.db.query(database.KnowledgePoint).count(), 1)
        self.assertEqual(self.db.query(database.LearningKnowledgePoint).count(), 1)
        self.assertEqual(self.db.query(database.QuestionBankItem).count(), 2)
        self.assertEqual(self.db.query(database.QuestionVersionRecord).count(), 1)
        self.assertEqual(self.db.query(database.QuestionKPLinkRecord).count(), 1)

    @staticmethod
    def _write_source_files(root: Path, *, bridges: list[dict[str, str]]):
        knowledge_points = [
            {
                "kp_id": "KP_1",
                "kp_Lv1": "中医基础",
                "kp_Lv2": "阴阳学说",
                "kp_Lv3_standard": "阴阳平衡",
                "kp_Lv3_others": "阴阳协调",
                "raw_content": ["chunk-1"],
                "global_order": 1,
            }
        ]
        questions = [
            {
                "题目id": "Q_1",
                "题目内容": "阴阳平衡的含义是？",
                "题目答案": "A",
                "题目答案解析": "解析 1",
                "题型": "单项选择题",
            },
            {
                "题目id": "Q_2",
                "题目内容": "尚未关联的题目",
                "题目答案": "B",
                "题型": "单项选择题",
            },
        ]
        paths = {
            "knowledge_points": root / "knowledge_points.json",
            "questions": root / "questions.json",
            "bridges": root / "question_kp_links.jsonl",
        }
        paths["knowledge_points"].write_text(json.dumps(knowledge_points, ensure_ascii=False), encoding="utf-8")
        paths["questions"].write_text(json.dumps(questions, ensure_ascii=False), encoding="utf-8")
        paths["bridges"].write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in bridges),
            encoding="utf-8",
        )
        return paths


if __name__ == "__main__":
    unittest.main()
