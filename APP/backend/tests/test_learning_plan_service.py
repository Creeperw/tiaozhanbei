import importlib
import json
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend import database
from APP.backend.agent_contracts import DiagnosisReport


class LearningPlanServiceTests(unittest.TestCase):
    SAMPLE_PROFILE_ID = "PROFILE_000064"

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _service(self):
        try:
            return importlib.import_module("APP.backend.learning_plan_service")
        except ModuleNotFoundError as exc:
            self.fail(f"learning_plan_service module is missing: {exc}")

    def _sample_path(self) -> Path:
        for parent in Path(__file__).resolve().parents:
            sample_path = parent / "test_data" / "synthetic_user_profiles.jsonl"
            if sample_path.exists():
                return sample_path
        self.fail("test_data/synthetic_user_profiles.jsonl is missing")

    def _sample(self, *, profile_id: str | None = None, predicate=None):
        with self._sample_path().open("r", encoding="utf-8") as handle:
            for line in handle:
                item = json.loads(line)
                if profile_id and item.get("profile_id") == profile_id:
                    return item
                if predicate and predicate(item):
                    return item
        target = profile_id or getattr(predicate, "__name__", "predicate")
        self.fail(f"No sample found for {target}")

    def test_generates_long_term_phase_plan_weekly_plan_and_daily_task_cards(self):
        service = self._service()
        sample = self._sample(profile_id=self.SAMPLE_PROFILE_ID)

        diagnosis = DiagnosisReport(
            diagnosis_id="diag-1",
            stage_id="T1",
            stage_name="高耗低效",
            summary="当前需要降低讲解粒度并强化错题复盘。",
            source_scope="diagnosis_agent",
            source_id="diag-1",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            confidence=0.88,
        )

        payload = service.generate_learning_plan(
            learner_id="1",
            learner_group=sample["user_group"],
            onboarding_answers=sample["onboarding_answers"],
            diagnosis_report=diagnosis,
            learning_profile={
                "mastery_by_kp": {"KP_FJ_001": 0.52, "KP_ZD_021": 0.48},
                "weak_kp_ids": ["KP_FJ_001", "KP_ZD_021"],
                "strong_kp_ids": [],
                "error_patterns": {"证型-方剂匹配错误": 2},
                "case_reasoning_level": "developing",
                "question_accuracy": 0.62,
                "review_stability": 0.44,
                "preferred_difficulty": "D2",
            },
        )

        self.assertTrue(payload["plan_summary"]["goal"])
        self.assertGreaterEqual(len(payload["phase_plan"]), 3)
        self.assertEqual(payload["weekly_plan"]["week_goal"], payload["plan_summary"]["current_focus"])
        self.assertEqual(payload["weekly_plan"]["focus"], payload["weekly_plan"]["week_goal"])
        self.assertGreaterEqual(len(payload["daily_task_cards"]), 3)
        self.assertGreaterEqual(len(payload["daily_tasks"]), 3)
        self.assertTrue(all(task.get("key") and task.get("reason") for task in payload["daily_tasks"]))
        self.assertTrue(any(card["type"] == "mistake_review" for card in payload["daily_task_cards"]))
        self.assertEqual(payload["constraints"]["daily_available_minutes"], 60)
        self.assertEqual(payload["diagnosis_stage"]["stage_id"], "T1")
        self.assertTrue(all("title" in phase and "acceptance" in phase for phase in payload["phase_plan"]))

    def test_persists_learning_plan_record(self):
        service = self._service()
        sample = self._sample(profile_id=self.SAMPLE_PROFILE_ID)

        db = self.Session()
        try:
            db.add(database.UserModel(id=1, username="planner", email="planner@example.com", hashed_password="x"))
            db.commit()

            diagnosis = DiagnosisReport(
                diagnosis_id="diag-2",
                stage_id="T5",
                stage_name="难度不适",
                summary="需要先补前置知识，再安排小步快练。",
                source_scope="diagnosis_agent",
                source_id="diag-2",
                kp_ids=["KP_JC_001"],
                confidence=0.9,
            )

            payload = service.create_or_update_learning_plan_record(
                db,
                user_id=1,
                learner_group=sample["user_group"],
                onboarding_answers=sample["onboarding_answers"],
                diagnosis_report=diagnosis,
                learning_profile={
                    "mastery_by_kp": {"KP_JC_001": 0.4},
                    "weak_kp_ids": ["KP_JC_001"],
                    "strong_kp_ids": [],
                    "error_patterns": {"概念混淆": 3},
                    "case_reasoning_level": "emerging",
                    "question_accuracy": 0.35,
                    "review_stability": 0.3,
                    "preferred_difficulty": "D1",
                },
            )

            record = db.query(database.LearningPlanRecord).filter_by(user_id=1).one()
            self.assertEqual(record.plan_type, "diagnosis_driven")
            self.assertEqual(record.status, "active")
            self.assertIn("长期规划", record.summary)
            saved = json.loads(record.payload_json)
            self.assertEqual(saved["diagnosis_stage"]["stage_id"], "T5")
            self.assertEqual(saved["constraints"]["daily_available_minutes"], 60)
            self.assertEqual(payload["record_id"], record.id)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
