import importlib
import json
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend import database


class DiagnosisAgentServiceTests(unittest.TestCase):
    SAMPLE_PROFILE_ID = "PROFILE_000072"

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _service(self):
        try:
            return importlib.import_module("APP.backend.diagnosis_agent_service")
        except ModuleNotFoundError as exc:
            self.fail(f"diagnosis_agent_service module is missing: {exc}")

    def _sample_path(self) -> Path:
        for parent in Path(__file__).resolve().parents:
            sample_path = parent / "test_data" / "synthetic_user_profiles.jsonl"
            if sample_path.exists():
                return sample_path
        self.fail("test_data/synthetic_user_profiles.jsonl is missing")

    def _load_sample(self, *, profile_id: str | None = None, predicate=None):
        with self._sample_path().open("r", encoding="utf-8") as handle:
            for line in handle:
                item = json.loads(line)
                if profile_id and item.get("profile_id") == profile_id:
                    return item
                if predicate and predicate(item):
                    return item
        target = profile_id or getattr(predicate, "__name__", "predicate")
        self.fail(f"No sample found for {target}")

    def test_onboarding_answers_create_l0_baseline_from_sample_profile(self):
        service = self._service()
        sample = self._load_sample(profile_id=self.SAMPLE_PROFILE_ID)

        db = self.Session()
        try:
            db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
            db.commit()

            result = service.submit_onboarding_survey(
                db,
                user_id=1,
                survey_answers=sample["onboarding_answers"],
                learner_group=sample["user_group"],
            )

            self.assertEqual(result["status"], "onboarding_completed")
            self.assertEqual(result["l0_baseline"]["stage_id"], "L0")
            self.assertEqual(result["l0_baseline"]["daily_available_minutes"], 60)
            self.assertEqual(result["l0_baseline"]["preferred_time_slot"], "晚间20:00–21:00")
            self.assertNotIn("preferred_difficulty", result["l0_baseline"])
            self.assertFalse(result["needs_survey_popup"])

            status = service.get_onboarding_status(db, 1)
            self.assertEqual(status["status"], "onboarding_completed")
            self.assertEqual(status["learner_group"], sample["user_group"])
            self.assertEqual(status["l0_baseline"]["resource_preference"], ["知识卡片"])

            profile = db.query(database.UserProfile).filter_by(user_id=1).one()
            self.assertEqual(profile.constitution, sample["user_group"])
            self.assertIn("掌握阴阳五行", profile.health_goals)
            self.assertIn("60", profile.diet_restrictions)
        finally:
            db.close()

    def test_normalizes_existing_frontend_aliases_and_chinese_duration(self):
        service = self._service()

        normalized = service.normalize_onboarding_answers(
            {
                "background": {
                    "education_major": "中医药相关专业",
                    "foundation_level": "学过核心课程",
                    "weak_area": "中药方剂",
                },
                "preferences": {
                    "daily_available_minutes": "30-60 分钟",
                },
            },
            learner_group="学历教育",
        )

        self.assertEqual(normalized["major_or_role"], "中医药相关专业")
        self.assertEqual(normalized["tcm_foundation"], "学过核心课程")
        self.assertEqual(normalized["current_difficulties"], "中药方剂")
        self.assertEqual(normalized["daily_available_minutes"], 45)
        self.assertNotIn("difficulty_preference", normalized)

    def test_onboarding_status_strips_legacy_difficulty_ratings(self):
        service = self._service()
        db = self.Session()
        try:
            db.add(database.UserModel(id=9, username="legacy", email="legacy@example.com", hashed_password="x"))
            db.add(database.LearningActivityRecord(
                user_id=9,
                activity_type=service.ONBOARDING_ACTIVITY_TYPE,
                payload_json=json.dumps({
                    "status": "onboarding_completed",
                    "learner_group": "跨专业进阶",
                    "survey_answers": {
                        "current_difficulties": "术语容易混淆",
                        "difficulty_preference": "D2",
                        "field_sources": {
                            "goals.current_difficulties": "user_confirmed",
                            "preferences.difficulty_preference": "defaulted",
                        },
                    },
                    "field_sources": {
                        "preferences.difficulty_preference": "defaulted",
                    },
                    "l0_baseline": {
                        "preferred_difficulty": "D2",
                        "daily_available_minutes": 30,
                    },
                }, ensure_ascii=False),
            ))
            db.commit()

            status = service.get_onboarding_status(db, 9)

            self.assertEqual(status["survey_answers"]["current_difficulties"], "术语容易混淆")
            self.assertNotIn("difficulty_preference", status["survey_answers"])
            self.assertNotIn("preferred_difficulty", status["l0_baseline"])
            self.assertNotIn("preferences.difficulty_preference", status["field_sources"])
        finally:
            db.close()

    def test_question_attempts_update_weak_kp_mastery_and_error_patterns(self):
        service = self._service()

        db = self.Session()
        try:
            db.add(database.UserModel(id=2, username="trainee", email="trainee@example.com", hashed_password="x"))
            db.add(
                database.UserProfile(
                    user_id=2,
                    constitution="学历教育",
                    health_goals="完成方剂学复习",
                    diet_restrictions="每天 45 分钟",
                    exercise_preferences="知识卡片、刷题",
                    medical_history="方剂辨析薄弱",
                )
            )
            db.commit()

            summary = service.record_question_attempts(
                db,
                user_id=2,
                attempts=[
                    {
                        "question_id": "Q1",
                        "answer": "理中丸",
                        "is_correct": False,
                        "score": 40,
                        "kp_ids": ["KP_FJ_001", "KP_ZD_021"],
                        "feedback": "将脾胃气虚证误判为中焦虚寒证",
                        "error_type": "证型-方剂匹配错误",
                        "summary": "四君子汤与理中丸混淆",
                    },
                    {
                        "question_id": "Q2",
                        "answer": "四君子汤",
                        "is_correct": True,
                        "score": 100,
                        "kp_ids": ["KP_FJ_001"],
                        "feedback": "已能识别主治证型",
                    },
                    {
                        "question_id": "Q3",
                        "answer": "阴阳互根",
                        "is_correct": False,
                        "score": 50,
                        "kp_ids": ["KP_JC_001"],
                        "feedback": "基础概念辨析不稳",
                        "error_type": "概念混淆",
                        "summary": "阴阳概念区分不清",
                    },
                ],
            )

            self.assertIn("KP_FJ_001", summary["weak_kp_ids"])
            self.assertIn("KP_JC_001", summary["weak_kp_ids"])
            self.assertEqual(summary["error_patterns"]["证型-方剂匹配错误"], 1)
            self.assertEqual(summary["error_patterns"]["概念混淆"], 1)
            self.assertLess(summary["mastery_by_kp"]["KP_FJ_001"], 0.7)
            self.assertLess(summary["question_accuracy"], 0.5)

            mastery = {
                row.kp_id: row
                for row in db.query(database.LearnerKnowledgeMastery).filter_by(user_id=2).all()
            }
            self.assertEqual(mastery["KP_FJ_001"].wrong_count, 1)
            self.assertEqual(mastery["KP_ZD_021"].wrong_count, 1)
            self.assertEqual(mastery["KP_JC_001"].wrong_count, 1)
            self.assertEqual(db.query(database.QuestionAttempt).filter_by(user_id=2).count(), 3)
            self.assertEqual(db.query(database.MistakeRecord).filter_by(user_id=2).count(), 2)
        finally:
            db.close()

    def test_diagnosis_report_supports_t0_t1_t2_t4_t5(self):
        service = self._service()

        l0_baseline = {
            "daily_available_minutes": 45,
            "preferred_time_slot": "20:00-21:00",
            "resource_preference": ["知识卡片", "案例训练"],
            "default_daily_tasks": 3,
        }

        cases = [
            ("T0", {"task_completion_rate": 0.92, "login_weekly_change": 0.0, "focus_time_change": 0.05, "retry_count": 0}, []),
            ("T1", {"task_completion_rate": 0.6, "login_weekly_change": 0.0, "focus_time_change": 0.0, "retry_count": 4}, []),
            ("T2", {"task_completion_rate": 0.42, "login_weekly_change": -0.5, "focus_time_change": -0.45, "retry_count": 1}, []),
            ("T4", {"task_completion_rate": 0.88, "login_weekly_change": 0.0, "focus_time_change": 0.0, "retry_count": 0, "path_deviation": 0.55}, []),
            ("T5", {"task_completion_rate": 0.7, "login_weekly_change": 0.0, "focus_time_change": 0.0, "retry_count": 0}, [{"kp_ids": ["KP_FJ_001"], "error_type": "证型-方剂匹配错误"}]),
        ]

        for expected_stage, l3_window, mistakes in cases:
            with self.subTest(stage=expected_stage):
                report = service.generate_diagnosis_report(
                    learner_context={
                        "learner_id": "2",
                        "learner_group": "学历教育",
                        "goal": "完成方剂学复习",
                    },
                    l0_baseline=l0_baseline,
                    l3_behavior=l3_window,
                    learning_profile={
                        "mastery_by_kp": {"KP_FJ_001": 0.58},
                        "weak_kp_ids": ["KP_FJ_001"],
                        "strong_kp_ids": [],
                        "error_patterns": {"证型-方剂匹配错误": len(mistakes)},
                        "case_reasoning_level": "developing",
                        "question_accuracy": 0.67,
                        "review_stability": 0.5,
                    },
                    mistakes=mistakes,
                )

                self.assertEqual(report.stage_id, expected_stage)
                self.assertEqual(report.t_stage["stage_id"], expected_stage)
                self.assertEqual(report.l0_baseline["daily_available_minutes"], 45)
                self.assertEqual(report.attribution["primary"], report.attribution["primary"])
                self.assertTrue(report.summary)

    def test_zero_behavior_samples_do_not_label_new_learner_as_idle(self):
        report = self._service().generate_diagnosis_report(
            learner_context={"learner_id": "new-learner"},
            l0_baseline={"daily_available_minutes": 30},
            l3_behavior={
                "task_completion_rate": 0.0,
                "login_weekly_change": -1.0,
                "focus_time_change": -1.0,
                "retry_count": 0,
                "evidence_status": "insufficient",
                "sample_counts": {
                    "activities_current_window": 0,
                    "question_attempts_current_window": 0,
                },
            },
            learning_profile={"weak_kp_ids": [], "error_patterns": {}},
            mistakes=[],
        )

        self.assertEqual(report.stage_id, "T0")
        self.assertEqual(report.stage_name, "证据积累中")
        self.assertEqual(report.attribution["primary"], "证据不足")
        self.assertEqual(report.t_stage["evidence"], ["当前观察窗口内没有已完成的学习活动或题目作答"])

    def test_l3_window_uses_canonical_tasks_login_focus_and_audited_attempts(self):
        service = self._service()
        current_metrics = {
            "daily_atomic_task_completion_rate": {
                "available": True,
                "value": 0.4,
            },
            "counts": {
                "activity_records": 3,
                "tasks": 5,
                "focus_sessions": 2,
                "focus_seconds": 600,
                "distinct_login_days": 1,
            },
        }
        previous_metrics = {
            "daily_atomic_task_completion_rate": {
                "available": True,
                "value": 0.8,
            },
            "counts": {
                "activity_records": 8,
                "tasks": 5,
                "focus_sessions": 4,
                "focus_seconds": 2_400,
                "distinct_login_days": 4,
            },
        }
        current_outcomes = {
            "current_window": {
                "audited_question_items_completed": 2,
                "retry_count": 1,
            }
        }
        previous_outcomes = {
            "current_window": {
                "audited_question_items_completed": 5,
                "retry_count": 0,
            }
        }

        with (
            mock.patch.object(
                service.system_data_service,
                "build_learning_window_metrics",
                side_effect=[current_metrics, previous_metrics],
            ),
            mock.patch.object(
                service.learning_statistics_service,
                "build_learning_statistics",
                side_effect=[current_outcomes, previous_outcomes],
            ),
        ):
            result = service.build_l3_behavior_window(mock.Mock(), 7)

        self.assertEqual(result["task_completion_rate"], 0.4)
        self.assertEqual(result["login_weekly_change"], -0.75)
        self.assertEqual(result["focus_time_change"], -0.75)
        self.assertEqual(result["retry_count"], 1)
        self.assertEqual(result["evidence_status"], "sufficient")
        self.assertEqual(result["data_source"], "canonical_learning_monitoring")
        self.assertEqual(
            result["sample_counts"]["question_attempts_current_window"],
            2,
        )

    def test_l3_window_does_not_treat_missing_daily_tasks_as_zero_completion(self):
        service = self._service()
        empty_task_metrics = {
            "daily_atomic_task_completion_rate": {
                "available": False,
                "value": None,
                "unavailable_reason": "no_planned_daily_task_items",
            },
            "counts": {
                "activity_records": 1,
                "tasks": 0,
                "focus_sessions": 0,
                "focus_seconds": 0,
                "distinct_login_days": 1,
            },
        }
        previous_metrics = {
            **empty_task_metrics,
            "counts": {
                **empty_task_metrics["counts"],
                "activity_records": 0,
                "distinct_login_days": 0,
            },
        }
        no_attempts = {
            "current_window": {
                "audited_question_items_completed": 0,
                "retry_count": 0,
            }
        }

        with (
            mock.patch.object(
                service.system_data_service,
                "build_learning_window_metrics",
                side_effect=[empty_task_metrics, previous_metrics],
            ),
            mock.patch.object(
                service.learning_statistics_service,
                "build_learning_statistics",
                side_effect=[no_attempts, no_attempts],
            ),
        ):
            result = service.build_l3_behavior_window(mock.Mock(), 8)

        self.assertEqual(result["task_completion_rate"], 1.0)
        self.assertFalse(
            result["metric_availability"]["task_completion_rate"]
        )
        self.assertEqual(result["evidence_status"], "limited")

    def test_build_difficulty_accuracy_groups_by_real_annotation_only(self):
        service = self._service()
        db = self.Session()
        try:
            db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
            db.add(database.LearningQuestion(
                question_id="ACC_Q1", question_type="single_choice",
                question_content="难度1", options_json="[]", answer_json='["A"]',
                explanation="", difficulty=1.0, kp_ids_json="[]",
                key_points="k", scoring_rubric="r",
            ))
            db.add(database.LearningQuestion(
                question_id="ACC_Q3", question_type="single_choice",
                question_content="难度3", options_json="[]", answer_json='["A"]',
                explanation="", difficulty=3.0, kp_ids_json="[]",
                key_points="k", scoring_rubric="r",
            ))
            db.add(database.LearningQuestion(
                question_id="ACC_UL", question_type="single_choice",
                question_content="未标注", options_json="[]", answer_json='["A"]',
                explanation="", difficulty=None, kp_ids_json="[]",
                key_points="k", scoring_rubric="r",
            ))
            db.flush()
            # 难度1：2 对 0 错；难度3：1 对 1 错；未标注：1 对。
            for index, (qid, correct) in enumerate(
                [
                    ("ACC_Q1", True), ("ACC_Q1", True),
                    ("ACC_Q3", True), ("ACC_Q3", False),
                    ("ACC_UL", True),
                ]
            ):
                db.add(database.LearningQuestionAttempt(
                    attempt_id=f"ACC_ATT_{index}", user_id=1, question_id=qid,
                    submitted_answer_json="[]", is_correct=correct,
                ))
            db.commit()

            result = service.build_difficulty_accuracy(db, 1)

            self.assertEqual(result["overall"]["attempts"], 5)
            self.assertEqual(result["overall"]["correct"], 4)
            self.assertEqual(result["overall"]["accuracy"], 0.8)
            self.assertEqual(result["by_difficulty"]["1"]["accuracy"], 1.0)
            self.assertEqual(result["by_difficulty"]["3"]["accuracy"], 0.5)
            self.assertEqual(result["by_difficulty"]["2"]["attempts"], 0)
            self.assertEqual(result["by_difficulty"]["unlabeled"]["attempts"], 1)
            self.assertEqual(result["by_difficulty"]["unlabeled"]["accuracy"], 1.0)
        finally:
            db.close()

    def test_report_summary_includes_difficulty_accuracy(self):
        service = self._service()
        db = self.Session()
        try:
            db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
            db.commit()
            summary = service.build_report_summary(db, 1)
            self.assertIn("difficulty_accuracy", summary)
            self.assertIn("overall", summary["difficulty_accuracy"])
            self.assertIn("by_difficulty", summary["difficulty_accuracy"])
            self.assertIn("unlabeled", summary["difficulty_accuracy"]["by_difficulty"])
            self.assertIsNone(summary["difficulty_accuracy"]["overall"]["accuracy"])
            legacy = summary.get("learner_overview")
            self.assertIsNotNone(legacy)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
