import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.case_repository import CaseRepository
from APP.backend.case_training_models import CaseVersionRecord
from APP.backend.case_training_service import (
    CaseTrainingService,
    CaseTrainingStateError,
)


def valid_case_grading(mode="full"):
    dimensions = {
        "full": {"syndrome": 50, "formula_name": 15, "formula_composition": 25, "inquiry": 10},
        "diagnosis_only": {"syndrome": 70, "inquiry": 30},
    }[mode]
    return {
        "score": 100,
        "max_score": 100,
        "is_correct": True,
        "error_types": [],
        "error_reason": "",
        "confidence": 0.9,
        "dimension_scores": {
            name: {"score": score, "reason": "回答完整", "evidence": ["案例证据"]}
            for name, score in dimensions.items()
        },
        "audit": {"decision": "pass", "confidence": 0.9},
    }


class CaseTrainingServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
            db.add(database.UserModel(id=2, username="other", email="other@example.com", hashed_password="x"))
            db.commit()
        self.repository = CaseRepository(self.Session)
        self.repository.create_case(
            case_definition_id="CASE_001",
            case_version_id="CASEV_001",
            title="虚劳案例",
            visible_context={"chief_complaint": "乏力纳差", "case_type": "internal", "kp_ids": ["KP_CASE_001"]},
            patient_context={"reported_symptoms": ["乏力", "食欲不振"]},
            golden_standard={
                "schema_version": "case_standard_v1",
                "answers": {
                    "full": {
                        "syndrome": {"answer": "脾胃气虚"},
                        "formula_name": {"answer": "四君子汤"},
                        "formula_composition": {"answer": ["人参", "白术", "茯苓", "甘草"]},
                        "inquiry": {"answer": ["食欲", "大便"]},
                    },
                    "diagnosis_only": {
                        "syndrome": {"answer": "脾胃气虚"},
                        "inquiry": {"answer": ["食欲", "大便"]},
                    },
                },
            },
            rubric={
                "full": {"syndrome": 50, "formula_name": 15, "formula_composition": 25, "inquiry": 10},
                "diagnosis_only": {"syndrome": 70, "inquiry": 30},
            },
        )
        self.now = datetime(2026, 7, 13, tzinfo=timezone.utc)

    def tearDown(self):
        self.engine.dispose()

    def build_service(self, **kwargs):
        return CaseTrainingService(
            self.Session,
            patient_runner=lambda **kwargs: {"reply": "我主要觉得乏力，吃饭也没胃口。"},
            patient_auditor=lambda **kwargs: {"decision": "pass"},
            clock=lambda: self.now,
            **kwargs,
        )

    def test_start_session_selects_case_by_type(self):
        service = self.build_service()

        started = service.start_session(1, selection="by_type", case_type="internal", mode="diagnosis_only")

        self.assertEqual(started["case_version_id"], "CASEV_001")
        self.assertEqual(started["mode"], "diagnosis_only")

    def test_start_session_selects_an_available_case_randomly(self):
        self.repository.create_case(
            case_definition_id="CASE_002",
            case_version_id="CASEV_002",
            title="随机案例",
            visible_context={"chief_complaint": "头晕", "case_type": "external", "kp_ids": ["KP_CASE_002"]},
            patient_context={"reported_symptoms": ["头晕"]},
            golden_standard={
                "schema_version": "case_standard_v1",
                "answers": {
                    "full": {
                        "syndrome": {"answer": "气血不足"},
                        "formula_name": {"answer": "归脾汤"},
                        "formula_composition": {"answer": ["人参"]},
                        "inquiry": {"answer": ["睡眠"]},
                    },
                    "diagnosis_only": {
                        "syndrome": {"answer": "气血不足"},
                        "inquiry": {"answer": ["睡眠"]},
                    },
                },
            },
            rubric={"full": {"syndrome": 50, "formula_name": 15, "formula_composition": 25, "inquiry": 10}},
        )
        service = self.build_service()

        with patch("APP.backend.case_training_service.choice", return_value="CASEV_002"):
            started = service.start_session(1, selection="random", mode="full")

        self.assertEqual(started["case_version_id"], "CASEV_002")
        self.assertEqual(started["title"], "随机案例")

    def test_diagnosis_only_submission_passes_selected_rubric_to_runner(self):
        captured = {}

        def grading_runner(**kwargs):
            captured.update(kwargs)
            return valid_case_grading("diagnosis_only")

        service = self.build_service(grading_runner=grading_runner)
        started = service.start_session(1, case_version_id="CASEV_001", mode="diagnosis_only")
        service.submit(1, started["session_id"], {"syndrome": "脾胃气虚"})

        rubric = json.loads(captured["submission"]["rubric"])
        self.assertEqual(
            rubric,
            {
                "rubric_version": "case_training_v1",
                "mode": "diagnosis_only",
                "dimensions": {"syndrome": 70, "inquiry": 30},
            },
        )

    def test_diagnosis_only_submission_persists_versioned_rubric_projection(self):
        service = self.build_service(
            grading_runner=lambda **kwargs: valid_case_grading("diagnosis_only")
        )
        started = service.start_session(1, case_version_id="CASEV_001", mode="diagnosis_only")
        result = service.submit(1, started["session_id"], {"syndrome": "脾胃气虚"})

        with self.Session() as db:
            payload = json.loads(
                db.query(database.GradingResultRecord)
                .filter_by(artifact_id=result["grading_artifact_id"])
                .one()
                .payload_json
            )
        self.assertEqual(
            payload["rubric"],
            {
                "rubric_version": "case_training_v1",
                "mode": "diagnosis_only",
                "dimensions": {"syndrome": 70, "inquiry": 30},
            },
        )

    def test_submit_requires_a_versioned_standard_for_the_selected_mode(self):
        with self.Session() as db:
            version = db.query(CaseVersionRecord).filter_by(case_version_id="CASEV_001").one()
            version.golden_standard_json = json.dumps({"syndrome": "脾胃气虚"})
            db.commit()
        service = self.build_service(grading_runner=lambda **kwargs: valid_case_grading())
        started = service.start_session(1, case_version_id="CASEV_001", mode="full")

        with self.assertRaisesRegex(CaseTrainingStateError, "^case standard unavailable$"):
            service.submit(1, started["session_id"], {"syndrome": "脾胃气虚"})

        self.assertEqual(service.get_session(1, started["session_id"])["status"], "active")
        with self.Session() as db:
            self.assertEqual(db.query(database.LearningAttemptRecord).count(), 0)

    def test_submit_rejects_malformed_hidden_case_json_without_attempt(self):
        with self.Session() as db:
            version = db.query(CaseVersionRecord).filter_by(case_version_id="CASEV_001").one()
            version.rubric_json = "{"
            db.commit()
        service = self.build_service(grading_runner=lambda **kwargs: valid_case_grading())
        started = service.start_session(1, case_version_id="CASEV_001", mode="full")

        with self.assertRaisesRegex(CaseTrainingStateError, "^case standard unavailable$"):
            service.submit(1, started["session_id"], {"syndrome": "脾胃气虚"})

        with self.Session() as db:
            self.assertEqual(db.query(database.LearningAttemptRecord).count(), 0)

    def test_submit_scores_all_required_full_dimensions(self):
        service = self.build_service(grading_runner=lambda **kwargs: valid_case_grading())
        started = service.start_session(1, case_version_id="CASEV_001", mode="full")

        result = service.submit(1, started["session_id"], {
            "syndrome": "脾胃气虚",
            "formula_name": "四君子汤",
            "formula_composition": ["人参", "白术", "茯苓", "甘草"],
            "inquiry": ["食欲", "大便"],
        })

        with self.Session() as db:
            payload = json.loads(
                db.query(database.GradingResultRecord)
                .filter_by(artifact_id=result["grading_artifact_id"])
                .one()
                .payload_json
            )
        self.assertEqual(payload["score"], 100)
        self.assertEqual(payload["dimension_scores"]["formula_composition"]["score"], 25)
        self.assertEqual(payload["dimension_scores"]["inquiry"]["score"], 10)

    def test_submit_with_production_shape_runner_persists_strict_scores(self):
        service = self.build_service(
            grading_runner=lambda **kwargs: {
                "grading": {"standard_answer": "脾胃气虚", "analysis": "包含隐藏答案"},
                "audit": {"decision": "pass", "confidence": 0.9},
                "remediation": {"reference": "四君子汤"},
            },
        )
        started = service.start_session(1, case_version_id="CASEV_001", mode="diagnosis_only")

        result = service.submit(1, started["session_id"], {
            "syndrome": "脾胃气虚", "inquiry": ["食欲", "大便"],
        })

        with self.Session() as db:
            payload = json.loads(
                db.query(database.GradingResultRecord)
                .filter_by(artifact_id=result["grading_artifact_id"])
                .one()
                .payload_json
            )
        self.assertEqual(payload["score"], 100)
        self.assertNotIn("脾胃气虚", str(payload))
        self.assertNotIn("四君子汤", str(payload))

    def test_submit_maps_revise_and_reject_audits_to_terminal_statuses(self):
        for decision, expected_status in (("revise", "needs_revision"), ("reject", "rejected")):
            with self.subTest(decision=decision):
                service = self.build_service(
                    grading_runner=lambda **kwargs: {
                        "audit": {"decision": decision, "confidence": 0.9},
                    },
                )
                started = service.start_session(1, case_version_id="CASEV_001", mode="diagnosis_only")

                result = service.submit(1, started["session_id"], {"syndrome": "脾胃气虚"})

                self.assertEqual(result["status"], expected_status)

    def test_submit_persists_strict_dimension_scores_without_hidden_standard(self):
        service = self.build_service(grading_runner=lambda **kwargs: valid_case_grading("diagnosis_only"))
        started = service.start_session(1, case_version_id="CASEV_001", mode="diagnosis_only")

        result = service.submit(1, started["session_id"], {"syndrome": "脾胃气虚"})

        with self.Session() as db:
            payload = json.loads(
                db.query(database.GradingResultRecord)
                .filter_by(artifact_id=result["grading_artifact_id"])
                .one()
                .payload_json
            )
        self.assertEqual(set(payload["dimension_scores"]), {"syndrome", "inquiry"})
        self.assertNotIn("answers", str(payload))
        self.assertNotIn("脾胃气虚", str(payload["dimension_scores"]))

    def test_start_and_get_session_excludes_hidden_case_data(self):
        service = self.build_service()

        started = service.start_session(1, case_version_id="CASEV_001", mode="full")
        restored = service.get_session(1, started["session_id"])

        self.assertEqual(started["status"], "active")
        self.assertEqual(restored["visible_context"], {"chief_complaint": "乏力纳差"})
        self.assertNotIn("golden_standard", restored)
        self.assertNotIn("patient_context", restored)
        self.assertNotIn("脾胃气虚", str(restored))

    def test_ask_persists_only_audited_patient_reply(self):
        service = self.build_service()
        started = service.start_session(1, case_version_id="CASEV_001", mode="full")

        result = service.ask(1, started["session_id"], "您哪里不舒服？")

        self.assertEqual(result["status"], "active")
        self.assertEqual(result["patient_message"]["content"], "我主要觉得乏力，吃饭也没胃口。")
        restored = service.get_session(1, started["session_id"])
        self.assertEqual([message["role"] for message in restored["messages"]], ["learner", "patient"])

    def test_ten_learner_messages_make_help_available_and_answer_help_disables_scoring(self):
        service = self.build_service()
        started = service.start_session(1, case_version_id="CASEV_001", mode="full")

        for index in range(10):
            service.ask(1, started["session_id"], f"第 {index} 个问题？")

        available = service.get_session(1, started["session_id"])
        helped = service.request_help(1, started["session_id"], help_type="answer")

        self.assertEqual(available["status"], "help_available")
        self.assertFalse(helped["scoring_enabled"])
        self.assertTrue(helped["help_used"])

    def test_submit_claims_session_before_grading_to_prevent_duplicate_writeback(self):
        runner_calls = []
        writeback_calls = []
        service = None

        def grading_runner(**kwargs):
            runner_calls.append(kwargs)
            if len(runner_calls) == 1:
                with self.assertRaises(CaseTrainingStateError):
                    service.submit(1, started["session_id"], {"syndrome": "脾胃气虚"})
            return valid_case_grading()

        service = self.build_service(
            grading_runner=grading_runner,
            writeback=lambda *args, **kwargs: writeback_calls.append((args, kwargs)),
        )
        started = service.start_session(1, case_version_id="CASEV_001", mode="full")

        service.submit(1, started["session_id"], {"syndrome": "脾胃气虚"})

        self.assertEqual(len(runner_calls), 1)
        self.assertEqual(len(writeback_calls), 1)
        with self.Session() as db:
            self.assertEqual(db.query(database.LearningAttemptRecord).count(), 1)

    def test_submit_persists_case_attempt_grading_and_audit(self):
        service = self.build_service(
            grading_runner=lambda **kwargs: valid_case_grading(),
        )
        started = service.start_session(1, case_version_id="CASEV_001", mode="full")

        result = service.submit(1, started["session_id"], {"syndrome": "脾胃气虚"})

        with self.Session() as db:
            attempt = db.query(database.LearningAttemptRecord).filter_by(attempt_id=result["attempt_id"]).one()
            self.assertEqual(attempt.attempt_type, "case")
            self.assertEqual(db.query(database.GradingResultRecord).count(), 1)
            self.assertEqual(db.query(database.AuditResultRecord).count(), 1)

    def test_completed_submission_writes_case_activity_and_system_snapshot(self):
        service = self.build_service(grading_runner=lambda **kwargs: valid_case_grading())
        started = service.start_session(1, case_version_id="CASEV_001", mode="full")

        result = service.submit(1, started["session_id"], {
            "syndrome": "脾胃气虚",
            "formula_name": "四君子汤",
            "formula_composition": ["人参", "白术", "茯苓", "甘草"],
            "inquiry": ["食欲", "大便"],
        })

        with self.Session() as db:
            activity = db.query(database.LearningActivityRecord).filter_by(
                user_id=1,
                activity_type="case_training",
                resource_id=started["session_id"],
            ).one()
            snapshot = db.query(database.SystemData).filter_by(user_id=1).one()

        rates = json.loads(snapshot.task_completion_rate_json)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(activity.completion_status, "completed")
        self.assertEqual(json.loads(activity.payload_json)["task_type"], "case_training")
        self.assertEqual(rates["value"], 1.0)

    def test_submit_without_audit_does_not_write_formal_learning_state(self):
        writeback_calls = []
        service = self.build_service(
            grading_runner=lambda **kwargs: {
                **valid_case_grading(),
                "audit": None,
            },
            writeback=lambda *args, **kwargs: writeback_calls.append((args, kwargs)),
        )
        started = service.start_session(1, case_version_id="CASEV_001", mode="full")

        result = service.submit(1, started["session_id"], {"syndrome": "脾胃气虚"})

        self.assertEqual(result["status"], "needs_human_review")
        self.assertEqual(writeback_calls, [])

    def test_submit_after_answer_help_does_not_call_writeback(self):
        writeback_calls = []
        service = self.build_service(
            grading_runner=lambda **kwargs: valid_case_grading(),
            writeback=lambda *args, **kwargs: writeback_calls.append((args, kwargs)),
        )
        started = service.start_session(1, case_version_id="CASEV_001", mode="full")
        for index in range(10):
            service.ask(1, started["session_id"], f"第 {index} 个问题？")
        service.request_help(1, started["session_id"], help_type="answer")

        result = service.submit(1, started["session_id"], {"syndrome": "脾胃气虚", "prescription": "四君子汤"})

        self.assertEqual(result["status"], "completed")
        self.assertEqual(writeback_calls, [])

    def test_other_user_and_missing_session_share_not_found_boundary(self):
        service = self.build_service()
        started = service.start_session(1, case_version_id="CASEV_001", mode="full")

        self.assertIsNone(service.get_session(2, started["session_id"]))
        self.assertIsNone(service.get_session(1, "missing"))
        with self.assertRaises(CaseTrainingStateError):
            service.ask(2, started["session_id"], "哪里不舒服？")


if __name__ == "__main__":
    unittest.main()
