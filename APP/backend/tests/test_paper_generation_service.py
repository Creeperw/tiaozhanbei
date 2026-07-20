import json
import unittest
from unittest.mock import Mock

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.paper_generation_service import generate_and_publish_paper
from APP.backend.question_repository import QuestionRepository, QuestionShortage, QuestionVersionView


class PaperGenerationServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        database.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.blueprint = {
            "question_count": 1, "kp_ids": ["KP_1"], "types": ["short_answer"],
            "distribution": {"short_answer": 1}, "difficulty": 2,
        }
        self.orchestration = {
            "task_id": "TT_1", "task_type": "paper_generation", "status": "completed",
            "title": "测试卷", "orchestration_run_id": "RUN_1",
            "artifact": {"artifact_type": "paper", "title": "测试卷", "content": {"paper_blueprint": self.blueprint}},
            "evidence_pack": {"pack_id": "EP_1", "source_scope": "knowledge", "source_id": "EP_1", "resolved_kp_ids": ["KP_1"], "items": [{"source_scope": "knowledge_point", "source_id": "KP_1", "summary": "依据", "kp_ids": ["KP_1"]}]},
            "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "artifact:paper", "reason": "passed"},
            "trace": [{"step_id": "orchestration", "run_id": "RUN_1"}], "learning_updates": {}, "next_actions": [],
        }
        self.question = QuestionVersionView("Q1:v3", "Q1", "short_answer", "冻结题干", "秘密答案", "解析", ("KP_1",), 2, "curated")

    def tearDown(self):
        self.engine.dispose()

    def test_audit_pass_selects_and_atomically_publishes_answer_free_snapshot(self):
        repository = Mock()
        repository.select.return_value = (self.question,)
        with self.Session() as db:
            result = generate_and_publish_paper(db=db, user_id=7, orchestration_result=self.orchestration, repository=repository)
            repository.select.assert_called_once()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["paper_id"], db.query(database.PaperInstanceRecord).one().paper_id)
            item = db.query(database.PaperItemRecord).one()
            self.assertEqual((item.question_version_id, item.stem_snapshot), ("Q1:v3", "冻结题干"))
            self.assertEqual(json.loads(item.kp_snapshot_json), ["KP_1"])
            self.assertEqual(json.loads(item.evidence_refs_json), [{"source_scope": "knowledge_point", "source_id": "KP_1"}])
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertNotIn('"answer":', serialized.lower())
            self.assertNotIn('"analysis":', serialized.lower())
            self.assertNotIn("秘密答案", serialized)

    def test_shortage_returns_needs_clarification_without_paper(self):
        repository = Mock()
        repository.select.return_value = QuestionShortage(Mock(), 1, 0)
        with self.Session() as db:
            result = generate_and_publish_paper(db=db, user_id=7, orchestration_result=self.orchestration, repository=repository)
            self.assertEqual(result["status"], "needs_clarification")
            self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)
            self.assertEqual(db.query(database.PaperItemRecord).count(), 0)

    def test_nonpass_and_disabled_audit_publish_nothing(self):
        for orchestration, expected in (({**self.orchestration, "audit": {"decision": "reject"}}, "failed"), (self.orchestration, "error")):
            with self.subTest(expected=expected), self.Session() as db:
                repository = Mock()
                if expected == "error":
                    with self.assertRaisesRegex(ValueError, "need_audit"):
                        generate_and_publish_paper(db=db, user_id=7, orchestration_result=orchestration, repository=repository, need_audit=False)
                else:
                    result = generate_and_publish_paper(db=db, user_id=7, orchestration_result=orchestration, repository=repository)
                    self.assertEqual(result["status"], "failed")
                repository.select.assert_not_called()
                self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)

    def test_mixed_type_distribution_selects_exact_deterministic_quotas(self):
        questions = (
            QuestionVersionView("Q1:v1", "Q1", "single_choice", "选择一", "A", "解析一", ("KP_1",), 2, "curated"),
            QuestionVersionView("Q2:v1", "Q2", "single_choice", "选择二", "B", "解析二", ("KP_1",), 2, "curated"),
            QuestionVersionView("Q3:v1", "Q3", "short_answer", "简答一", "答案", "解析三", ("KP_1",), 2, "curated"),
        )
        repository = Mock()
        repository.select.return_value = questions
        orchestration = json.loads(json.dumps(self.orchestration))
        orchestration["artifact"]["content"]["paper_blueprint"] = {
            "question_count": 3,
            "kp_ids": ["KP_1"],
            "types": ["single_choice", "short_answer"],
            "distribution": {"single_choice": 2, "short_answer": 1},
            "difficulty": 2,
        }

        with self.Session() as db:
            result = generate_and_publish_paper(
                db=db, user_id=7, orchestration_result=orchestration, repository=repository
            )

        criteria = repository.select.call_args.args[0]
        self.assertEqual(
            criteria.type_difficulty_counts,
            (("single_choice", 2, 2), ("short_answer", 2, 1)),
        )
        self.assertEqual(
            [item["question_type"] for item in result["artifact"]["content"]["items"]],
            ["single_choice", "single_choice", "short_answer"],
        )

    def test_one_type_short_does_not_fill_from_other_type(self):
        with self.Session() as db:
            for question_id, question_type in (
                ("Q1", "single_choice"),
                ("Q2", "single_choice"),
                ("Q3", "short_answer"),
            ):
                db.add(database.QuestionBankItem(
                    question_id=question_id,
                    question_type=question_type,
                    stem=question_id,
                    answer="secret",
                    analysis="secret analysis",
                    kp_ids_json='["KP_1"]',
                    difficulty=2,
                    status="active",
                    source="curated",
                ))
            db.commit()
            repository = QuestionRepository(lambda: self.Session())
            orchestration = json.loads(json.dumps(self.orchestration))
            orchestration["artifact"]["content"]["paper_blueprint"] = {
                "question_count": 3,
                "kp_ids": ["KP_1"],
                "types": ["single_choice", "short_answer"],
                "distribution": {"single_choice": 1, "short_answer": 2},
                "difficulty": 2,
            }
            result = generate_and_publish_paper(
                db=db, user_id=7, orchestration_result=orchestration, repository=repository
            )
            self.assertEqual(result["status"], "needs_clarification")
            self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)
            self.assertEqual(db.query(database.PaperItemRecord).count(), 0)

    def test_missing_distribution_fails_closed_without_selecting_or_publishing(self):
        repository = Mock()
        orchestration = json.loads(json.dumps(self.orchestration))
        del orchestration["artifact"]["content"]["paper_blueprint"]["distribution"]
        with self.Session() as db:
            result = generate_and_publish_paper(
                db=db, user_id=7, orchestration_result=orchestration, repository=repository
            )
            self.assertEqual(result["status"], "needs_clarification")
            repository.select.assert_not_called()
            self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)

    def test_duplicate_types_fail_closed_without_expanding_quota_or_publishing(self):
        repository = Mock()
        repository.select.return_value = (self.question, self.question)
        orchestration = json.loads(json.dumps(self.orchestration))
        orchestration["artifact"]["content"]["paper_blueprint"] = {
            "question_count": 1,
            "kp_ids": ["KP_1"],
            "types": ["short_answer", "short_answer"],
            "distribution": {"short_answer": 1},
            "difficulty": 2,
        }
        with self.Session() as db:
            result = generate_and_publish_paper(
                db=db, user_id=7, orchestration_result=orchestration, repository=repository
            )
            self.assertEqual(result["status"], "needs_clarification")
            repository.select.assert_not_called()
            self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)
            self.assertEqual(db.query(database.PaperItemRecord).count(), 0)

    def test_nonpass_result_removes_unreviewed_agent_content(self):
        orchestration = json.loads(json.dumps(self.orchestration))
        orchestration["audit"] = {"decision": "reject", "reason": "unsafe"}
        orchestration["artifact"] = {
            "artifact_type": "paper",
            "title": "SENTINEL_AGENT_TITLE",
            "content": {
                "question": "SENTINEL_QUESTION",
                "answer": "SENTINEL_ANSWER",
                "analysis": "SENTINEL_ANALYSIS",
                "agent_output": "SENTINEL_AGENT_OUTPUT",
            },
        }
        with self.Session() as db:
            result = generate_and_publish_paper(
                db=db, user_id=7, orchestration_result=orchestration, repository=Mock()
            )
        serialized = json.dumps(result, ensure_ascii=False)
        for sentinel in (
            "SENTINEL_AGENT_TITLE", "SENTINEL_QUESTION", "SENTINEL_ANSWER",
            "SENTINEL_ANALYSIS", "SENTINEL_AGENT_OUTPUT",
        ):
            self.assertNotIn(sentinel, serialized)
        self.assertEqual(result["artifact"]["content"], {})

    def test_direct_call_flushes_without_hidden_commit(self):
        repository = Mock()
        repository.select.return_value = (self.question,)
        with self.Session() as db:
            result = generate_and_publish_paper(
                db=db, user_id=7, orchestration_result=self.orchestration, repository=repository
            )
            self.assertEqual(db.query(database.PaperInstanceRecord).count(), 1)
            db.rollback()
        with self.Session() as db:
            self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)
            self.assertEqual(db.query(database.PaperItemRecord).count(), 0)

    def test_repository_result_with_invalid_identity_or_quota_fails_closed_before_writes(self):
        invalid_results = {
            "blank version": (QuestionVersionView(" ", "Q1", "short_answer", "题干", "答案", "解析", ("KP_1",), 2, "curated"),),
            "duplicate version": (self.question, QuestionVersionView("Q1:v3", "Q2", "short_answer", "题干二", "答案", "解析", ("KP_1",), 2, "curated")),
            "duplicate question": (self.question, QuestionVersionView("Q2:v1", "Q1", "short_answer", "题干二", "答案", "解析", ("KP_1",), 2, "curated")),
            "wrong count": (),
            "wrong quota": (QuestionVersionView("Q2:v1", "Q2", "single_choice", "题干二", "答案", "解析", ("KP_1",), 2, "curated"),),
        }
        for label, selected in invalid_results.items():
            with self.subTest(label=label), self.Session() as db:
                repository = Mock()
                repository.select.return_value = selected
                result = generate_and_publish_paper(
                    db=db, user_id=7, orchestration_result=self.orchestration, repository=repository
                )
                self.assertEqual(result["status"], "needs_clarification")
                self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)
                self.assertEqual(db.query(database.PaperItemRecord).count(), 0)

    def test_item_insert_failure_rolls_back_entire_paper(self):
        repository = Mock()
        repository.select.return_value = (self.question,)

        def fail_item_insert(_connection, _cursor, statement, _parameters, _context, _executemany):
            if "INSERT INTO paper_items" in statement:
                raise RuntimeError("item failed")

        event.listen(self.engine, "before_cursor_execute", fail_item_insert)
        try:
            with self.Session() as db:
                with self.assertRaisesRegex(RuntimeError, "item failed"):
                    generate_and_publish_paper(db=db, user_id=7, orchestration_result=self.orchestration, repository=repository)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_item_insert)
        with self.Session() as db:
            self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)
            self.assertEqual(db.query(database.PaperItemRecord).count(), 0)


if __name__ == "__main__":
    unittest.main()
