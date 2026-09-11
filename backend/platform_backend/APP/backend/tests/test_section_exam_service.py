import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend.database import (
    Base,
    LearningActivityRecord,
    LearningAttemptRecord,
    LearningQuestion,
    MistakeRecord,
    QuestionAttempt,
    QuestionBankItem,
    UserModel,
)
from APP.backend.section_exam_service import (
    SectionExamError,
    format_reference_answer,
    judge_answer,
    reference_option_keys,
    submit_section_exam_answer,
)
from APP.backend.learning_statistics_service import unified_practice_rows


class SectionExamJudgingTests(unittest.TestCase):
    """The worksheet grades in one place, so these rules are the whole contract."""

    def test_true_false_reference_stored_as_a_json_array_is_graded_correctly(self):
        # 24 of 37 rows stored ``["√"]``; comparing it to 「正确」 always failed.
        self.assertIs(judge_answer("true_false", "正确", '["√"]'), True)
        self.assertIs(judge_answer("true_false", "正确", '["×"]'), False)

    def test_true_false_accepts_every_conventional_marker(self):
        for submitted in ("正确", "√", "对", "是", "true", "A", "✓"):
            self.assertIs(judge_answer("true_false", submitted, "√"), True, submitted)
        for submitted in ("错误", "×", "错", "否", "false", "B", "✗"):
            self.assertIs(judge_answer("true_false", submitted, "×"), True, submitted)

    def test_fill_blank_accepts_a_different_separator(self):
        # 「整体观念和辨证论治」 and 「整体观念、辨证论治」 are the same answer.
        self.assertIs(
            judge_answer("fill_blank", "整体观念和辨证论治", '["整体观念、辨证论治"]'),
            True,
        )
        self.assertIs(
            judge_answer("fill_blank", "整体观念,辨证论治", "整体观念、辨证论治"),
            True,
        )

    def test_fill_blank_rejects_a_fragment_of_a_multi_part_reference(self):
        self.assertIs(judge_answer("fill_blank", "整体观念", "整体观念、辨证论治"), False)

    def test_fill_blank_accepts_a_restated_single_item_reference(self):
        self.assertIs(
            judge_answer("fill_blank", "辨证（决定治疗的前提和依据）", "辨证"),
            True,
        )

    def test_answer_prefix_is_ignored(self):
        self.assertIs(judge_answer("fill_blank", "答：胃阴虚", "胃阴虚"), True)

    def test_single_choice_reference_with_option_text_is_graded_by_letter(self):
        self.assertIs(judge_answer("single_choice", "D", '["D（口渴喜冷饮）"]'), True)
        self.assertIs(judge_answer("single_choice", "A", '["D（口渴喜冷饮）"]'), False)

    def test_multiple_choice_is_order_insensitive(self):
        self.assertIs(judge_answer("multiple_choice", "E,D,C,B", '["B", "C", "D", "E"]'), True)
        self.assertIs(judge_answer("multiple_choice", "B,C,D", '["B", "C", "D", "E"]'), False)

    def test_a_joiner_inside_a_term_is_not_treated_as_a_separator(self):
        # 「和法」 is one of the eight treatment methods, not 「法」 joined by 和.
        self.assertIs(judge_answer("fill_blank", "法", "和法"), False)
        self.assertIs(judge_answer("fill_blank", "和法", "和法"), True)

    def test_subjective_answers_are_never_auto_graded(self):
        self.assertIsNone(judge_answer("short_answer", "肺和脾", "与气的生成关系最密切的脏是肺和脾。"))
        self.assertIsNone(judge_answer("case_quiz", "任何内容", "任何参考答案"))
        self.assertIsNone(judge_answer("临床案例问答", "任何内容", "任何参考答案"))

    def test_blank_answers_are_wrong_rather_than_unparsed(self):
        self.assertIs(judge_answer("fill_blank", "", "胃阴虚"), False)
        self.assertIs(judge_answer("single_choice", "   ", "A"), False)

    def test_format_reference_answer_unwraps_a_json_array(self):
        self.assertEqual(format_reference_answer('["√"]'), "√")
        self.assertEqual(format_reference_answer('["B", "C", "D", "E"]'), "B；C；D；E")
        self.assertEqual(format_reference_answer("D, E"), "D, E")
        self.assertEqual(format_reference_answer('["整体观念、辨证论治"]'), "整体观念、辨证论治")

    def test_reference_options_drive_the_option_highlighting(self):
        self.assertEqual(reference_option_keys("single_choice", '["D（口渴喜冷饮）"]'), ["D"])
        self.assertEqual(reference_option_keys("multiple_choice", '["B", "C", "D", "E"]'), ["B", "C", "D", "E"])
        self.assertEqual(reference_option_keys("true_false", '["√"]'), ["正确"])
        self.assertEqual(reference_option_keys("true_false", '["×"]'), ["错误"])
        self.assertEqual(reference_option_keys("fill_blank", '["胃阴虚"]'), [])


class SectionExamPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        event.listen(self.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(UserModel(id=1, username="learner", hashed_password="x"))
        self.db.add(
            QuestionBankItem(
                question_id="Q_TF",
                stem="中医理论体系形成于先秦、秦、汉时期。",
                question_type="true_false",
                answer='["√"]',
                analysis="",
                kp_ids_json='["kp-1"]',
                source="formal_question_bank",
                status="active",
            )
        )
        self.db.add(
            QuestionBankItem(
                question_id="Q_SA",
                stem="请指出与气的生成关系最密切的两个脏，并说明理由。",
                question_type="short_answer",
                answer='["与气的生成关系最密切的脏是肺和脾。"]',
                analysis="肺主气，脾主运化。",
                kp_ids_json='["kp-1"]',
                source="formal_question_bank",
                status="active",
            )
        )
        self.db.add(
            LearningQuestion(
                question_id="Q_TF",
                question_type="true_false",
                question_content="中医理论体系形成于先秦、秦、汉时期。",
                options_json="[]",
                answer_json='["√"]',
                explanation="",
                kp_ids_json='["kp-1"]',
            )
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def submit(self, **overrides):
        payload = {
            "question_id": "Q_TF",
            "submitted_answer": "正确",
            "request_id": "req-1",
            "section_id": "SEC_1",
            "section_name": "第一节",
        }
        payload.update(overrides)
        return submit_section_exam_answer(self.db, 1, **payload)

    def test_a_correct_answer_records_an_attempt_and_an_activity(self):
        result = self.submit()

        self.assertIs(result["is_correct"], True)
        self.assertEqual(result["score"], 100.0)
        self.assertEqual(result["reference_answer"], "√")
        self.assertTrue(result["recorded"])
        self.assertIsNone(result["mistake_id"])

        attempt = self.db.query(QuestionAttempt).filter_by(user_id=1).one()
        self.assertEqual(attempt.question_id, "Q_TF")
        self.assertEqual(attempt.answer, "正确")
        self.assertIs(attempt.is_correct, True)
        self.assertEqual(attempt.score, 100.0)

        activity = self.db.query(LearningActivityRecord).filter_by(activity_type="question_attempt").one()
        self.assertEqual(activity.resource_id, "Q_TF")
        self.assertEqual(activity.score, 100.0)
        self.assertEqual(activity.completion_status, "completed")
        # The study report pairs the activity with the attempt by question and
        # timestamp, so both rows must carry the same instant.
        self.assertEqual(activity.created_at, attempt.created_at)

        canonical = self.db.query(LearningAttemptRecord).filter_by(learner_id=1).one()
        self.assertEqual(canonical.attempt_type, "textbook_section_exam")
        self.assertEqual(canonical.request_id, "req-1")
        self.assertEqual(self.db.query(MistakeRecord).count(), 0)

    def test_a_wrong_answer_lands_in_the_mistake_book(self):
        result = self.submit(submitted_answer="错误")

        self.assertIs(result["is_correct"], False)
        self.assertEqual(result["score"], 0.0)
        mistake = self.db.query(MistakeRecord).filter_by(user_id=1, question_id="Q_TF").one()
        self.assertEqual(mistake.status, "active")
        self.assertEqual(result["mistake_id"], int(mistake.id))

    def test_repeating_the_same_request_id_does_not_write_a_second_attempt(self):
        first = self.submit()
        second = self.submit()

        self.assertTrue(first["recorded"])
        self.assertFalse(second["recorded"])
        # Re-revealing an unchanged answer must replay the verdict, not inflate
        # the study report or the mistake book.
        self.assertIs(second["is_correct"], True)
        self.assertEqual(self.db.query(QuestionAttempt).count(), 1)
        self.assertEqual(self.db.query(LearningActivityRecord).filter_by(activity_type="question_attempt").count(), 1)
        self.assertEqual(self.db.query(LearningAttemptRecord).count(), 1)

    def test_editing_the_answer_writes_a_new_attempt(self):
        self.submit(request_id="req-1")
        self.submit(request_id="req-2", submitted_answer="错误")

        self.assertEqual(self.db.query(QuestionAttempt).count(), 2)
        self.assertEqual(self.db.query(MistakeRecord).count(), 1)

    def test_a_subjective_answer_is_recorded_without_a_score(self):
        result = self.submit(
            question_id="Q_SA",
            submitted_answer="肺和脾",
            request_id="req-sa",
        )

        self.assertIsNone(result["is_correct"])
        self.assertIsNone(result["score"])
        self.assertEqual(result["reference_answer"], "与气的生成关系最密切的脏是肺和脾。")
        self.assertTrue(result["recorded"])

        attempt = self.db.query(QuestionAttempt).filter_by(question_id="Q_SA").one()
        self.assertIsNone(attempt.score)
        # An ungraded answer must not be filed as a mistake.
        self.assertEqual(self.db.query(MistakeRecord).count(), 0)

    def test_unknown_questions_are_rejected(self):
        with self.assertRaises(SectionExamError) as caught:
            self.submit(question_id="MISSING")
        self.assertEqual(caught.exception.status_code, 404)

    def test_recorded_attempts_reach_the_study_report(self):
        # 「近30天作答」 and 「练习得分率」 read ``unified_practice_rows``, so the
        # write path is only correct if the row shows up there.
        self.submit(submitted_answer="正确", request_id="req-1")
        self.submit(submitted_answer="错误", request_id="req-2")
        self.submit(question_id="Q_SA", submitted_answer="肺和脾", request_id="req-3")

        rows = unified_practice_rows(self.db, 1)

        self.assertEqual(len(rows), 3)
        scored = [row for row in rows if row["score"] is not None]
        self.assertEqual(len(scored), 2)
        self.assertEqual(sum(row["score"] for row in scored), 100.0)
        self.assertEqual(sum(row["max_score"] for row in scored), 200.0)


if __name__ == "__main__":
    unittest.main()
