import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.paper_submission_service import (
    PaperSubmissionInvalid,
    PaperSubmissionNotFound,
    get_owned_paper,
    pause_paper_timer,
    resume_paper_timer,
    save_paper_answers,
    submit_paper,
)
from APP.backend.mistake_variation_service import list_available_variation_sources


class PaperSubmissionServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        database.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all((
                database.UserModel(id=1, username="learner", hashed_password="x"),
                database.UserModel(id=2, username="other", hashed_password="x"),
                database.PaperInstanceRecord(paper_id="PAPER_1", task_id="TASK_1", learner_id=1, title="测试卷"),
                database.PaperItemRecord(paper_item_id="PI_1", paper_id="PAPER_1", position=1, question_id="Q_1", question_version_id="QV_1", question_type="short_answer", stem_snapshot="题干一", standard_answer_snapshot="脾胃气虚证", standard_difficulty=2),
                database.QuestionVersionRecord(question_version_id="QV_1", question_id="Q_1", version=1, stem="题干一", answer="脾胃气虚证", status="active"),
                database.QuestionKPLinkRecord(question_version_id="QV_1", kp_id="KP_1", status="active"),
            ))
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def runner(self, *, submission, **_):
        correct = submission["submitted_answer"] == submission["standard_answer"]
        return {
            "score": 100 if correct else 0,
            "max_score": 100,
            "is_correct": correct,
            "error_types": [] if correct else ["incorrect"],
            "error_reason": "" if correct else "答案不正确",
            "confidence": 0.9,
            "audit": {"decision": "pass", "confidence": 0.9},
        }

    def test_owned_paper_read_omits_standard_answer_and_returns_saved_answer(self):
        with self.Session() as db:
            saved = save_paper_answers(db, 1, "PAPER_1", {"PI_1": "脾胃气虚证"})
            loaded = get_owned_paper(db, 1, "PAPER_1")

        self.assertEqual(saved["items"][0]["answer"], "脾胃气虚证")
        self.assertEqual(loaded["items"][0]["stem"], "题干一")
        self.assertNotIn("standard_answer", str(loaded))
        self.assertNotIn("脾胃气虚证", str({key: value for key, value in loaded["items"][0].items() if key != "answer"}))

    def test_foreign_or_unknown_paper_is_not_found(self):
        with self.Session() as db:
            for paper_id in ("PAPER_1", "missing"):
                with self.subTest(paper_id=paper_id):
                    with self.assertRaises(PaperSubmissionNotFound):
                        get_owned_paper(db, 2, paper_id)

    def test_timer_pause_and_resume_are_persisted_and_user_scoped(self):
        with self.Session() as db:
            started = get_owned_paper(db, 1, "PAPER_1")
            paused = pause_paper_timer(db, 1, "PAPER_1")
            restored = get_owned_paper(db, 1, "PAPER_1")

            self.assertFalse(started["timing"]["paused"])
            self.assertTrue(paused["timing"]["paused"])
            self.assertEqual(restored["timing"]["remaining_seconds"], paused["timing"]["remaining_seconds"])
            with self.assertRaises(PaperSubmissionNotFound):
                pause_paper_timer(db, 2, "PAPER_1")

            resumed = resume_paper_timer(db, 1, "PAPER_1")
            self.assertFalse(resumed["timing"]["paused"])
            self.assertIsNotNone(resumed["timing"]["expires_at"])

    def test_submit_requires_every_answer_and_replays_same_request(self):
        with self.Session() as db:
            with self.assertRaises(PaperSubmissionInvalid):
                submit_paper(db, 1, "PAPER_1", "request-1", runner=self.runner)
            save_paper_answers(db, 1, "PAPER_1", {"PI_1": "脾胃气虚证"})
            first = submit_paper(db, 1, "PAPER_1", "request-1", runner=self.runner)
            replay = submit_paper(db, 1, "PAPER_1", "request-1", runner=lambda **_: self.fail("runner must not repeat"))

            self.assertEqual(first, replay)
            self.assertEqual(first["score"], 100)
            self.assertEqual(db.query(database.PaperSubmissionRecord).count(), 1)
            self.assertEqual(db.query(database.LearningAttemptRecord).count(), 1)

    def test_submit_scales_grading_result_to_blueprint_item_score(self):
        with self.Session() as db:
            db.query(database.PaperItemRecord).filter_by(paper_item_id="PI_1").update({
                database.PaperItemRecord.max_score_snapshot: 25,
            })
            db.commit()
            save_paper_answers(db, 1, "PAPER_1", {"PI_1": "脾胃气虚证"})
            result = submit_paper(db, 1, "PAPER_1", "weighted-1", runner=self.runner)

        self.assertEqual(result["score"], 25)
        self.assertEqual(result["max_score"], 25)

    def test_historical_per_question_hundreds_are_normalized_to_one_paper_total(self):
        with self.Session() as db:
            db.add(database.PaperItemRecord(
                paper_item_id="PI_2", paper_id="PAPER_1", position=2,
                question_id="Q_2", question_version_id="QV_2",
                question_type="short_answer", stem_snapshot="题干二",
                standard_answer_snapshot="益气健脾", standard_difficulty=2,
                max_score_snapshot=100,
            ))
            db.commit()
            loaded = get_owned_paper(db, 1, "PAPER_1")

        self.assertEqual(loaded["total_score"], 100)
        self.assertEqual([item["max_score"] for item in loaded["items"]], [50, 50])

    def test_historical_submission_result_is_normalized_with_paper_items(self):
        with self.Session() as db:
            db.add(database.PaperItemRecord(
                paper_item_id="PI_2", paper_id="PAPER_1", position=2,
                question_id="Q_2", question_version_id="QV_2",
                question_type="short_answer", stem_snapshot="题干二",
                standard_answer_snapshot="益气健脾", standard_difficulty=2,
                max_score_snapshot=100,
            ))
            db.add(database.PaperSubmissionRecord(
                paper_id="PAPER_1", learner_id=1, request_id="legacy-result",
                status="completed",
                result_json=json.dumps({
                    "paper_id": "PAPER_1", "status": "completed",
                    "score": 100, "max_score": 200,
                    "items": [
                        {"paper_item_id": "PI_1", "score": 100, "max_score": 100},
                        {"paper_item_id": "PI_2", "score": 0, "max_score": 100},
                    ],
                }),
            ))
            db.commit()
            loaded = get_owned_paper(db, 1, "PAPER_1")

        self.assertEqual(loaded["result"]["max_score"], 100)
        self.assertEqual(loaded["result"]["score"], 50)
        self.assertEqual([row["max_score"] for row in loaded["result"]["items"]], [50, 50])

    def test_wrong_paper_answer_is_available_for_audited_variation_and_has_explanation(self):
        with self.Session() as db:
            save_paper_answers(db, 1, "PAPER_1", {"PI_1": "错误答案"})
            result = submit_paper(db, 1, "PAPER_1", "wrong-1", runner=self.runner)
            sources = list_available_variation_sources(db, 1)
            version = db.query(database.QuestionVersionRecord).filter_by(
                question_version_id="QV_1"
            ).one()

        self.assertFalse(result["items"][0]["is_correct"])
        self.assertEqual(result["items"][0]["explanation"], "答案不正确")
        self.assertEqual(version.analysis, "答案不正确")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["question_version_id"], "QV_1")

    def test_submit_writes_a_completed_paper_activity_and_system_snapshot(self):
        with self.Session() as db:
            save_paper_answers(db, 1, "PAPER_1", {"PI_1": "脾胃气虚证"})
            result = submit_paper(db, 1, "PAPER_1", "request-1", runner=self.runner)

            activity = db.query(database.LearningActivityRecord).filter_by(
                user_id=1,
                activity_type="paper_submission",
                resource_id="PAPER_1",
            ).one()
            snapshot = db.query(database.SystemData).filter_by(user_id=1).one()

        rates = json.loads(snapshot.task_completion_rate_json)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(activity.completion_status, "completed")
        self.assertEqual(json.loads(activity.payload_json)["task_type"], "paper_submission")
        self.assertEqual(rates["value"], 1.0)

    def test_successful_submission_locks_answers_and_replays_for_new_request_id(self):
        with self.Session() as db:
            save_paper_answers(db, 1, "PAPER_1", {"PI_1": "脾胃气虚证"})
            first = submit_paper(db, 1, "PAPER_1", "request-1", runner=self.runner)
            replay = submit_paper(db, 1, "PAPER_1", "request-2", runner=lambda **_: self.fail("runner must not repeat"))
            with self.assertRaises(PaperSubmissionInvalid):
                save_paper_answers(db, 1, "PAPER_1", {"PI_1": "修改后的答案"})
            restored = get_owned_paper(db, 1, "PAPER_1")

        self.assertEqual(first, replay)
        self.assertEqual(restored["status"], "submitted")
        self.assertEqual(restored["result"], first)

    def test_submission_uses_standard_answer_snapshot_created_with_the_paper(self):
        with self.Session() as db:
            save_paper_answers(db, 1, "PAPER_1", {"PI_1": "脾胃气虚证"})
            db.query(database.QuestionVersionRecord).filter_by(question_version_id="QV_1").update({
                database.QuestionVersionRecord.answer: "已变更的答案",
            })
            db.commit()

            result = submit_paper(db, 1, "PAPER_1", "request-1", runner=self.runner)

        self.assertEqual(result["score"], 100)

    def test_failed_submission_restores_paper_for_retry(self):
        with self.Session() as db:
            save_paper_answers(db, 1, "PAPER_1", {"PI_1": "脾胃气虚证"})
            with self.assertRaisesRegex(RuntimeError, "runner failed"):
                submit_paper(db, 1, "PAPER_1", "request-1", runner=lambda **_: (_ for _ in ()).throw(RuntimeError("runner failed")))
            result = submit_paper(db, 1, "PAPER_1", "request-2", runner=self.runner)

        self.assertEqual(result["status"], "completed")

    def test_save_rejects_unknown_item_without_mutating_existing_answers(self):
        with self.Session() as db:
            save_paper_answers(db, 1, "PAPER_1", {"PI_1": "初始答案"})
            with self.assertRaises(PaperSubmissionInvalid):
                save_paper_answers(db, 1, "PAPER_1", {"PI_missing": "伪造答案"})
            loaded = get_owned_paper(db, 1, "PAPER_1")

        self.assertEqual(loaded["items"][0]["answer"], "初始答案")


if __name__ == "__main__":
    unittest.main()
