import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


class ShizhenMvpSeedTests(unittest.TestCase):
    def setUp(self):
        self.seed_path = Path(__file__).resolve().parents[1] / "sample_data" / "shizhen_mvp_seed.json"

    def _load_seed(self):
        return json.loads(self.seed_path.read_text(encoding="utf-8"))

    def test_seed_json_has_required_sections_and_demo_counts(self):
        seed = self._load_seed()

        self.assertEqual(seed["theme"], "脾胃气虚证 + 四君子汤")
        for key in [
            "workflow",
            "safety_labels",
            "learner_profiles",
            "knowledge_points",
            "question_bank",
            "mistakes",
            "plans",
            "diagnoses",
            "evaluation_tasks",
        ]:
            self.assertIn(key, seed)

        self.assertEqual(len(seed["learner_profiles"]), 3)
        self.assertGreaterEqual(len(seed["knowledge_points"]), 10)
        self.assertLessEqual(len(seed["knowledge_points"]), 20)
        self.assertGreaterEqual(len(seed["question_bank"]), 20)
        self.assertLessEqual(len(seed["question_bank"]), 30)
        self.assertGreaterEqual(len(seed["mistakes"]), 5)
        self.assertLessEqual(len(seed["mistakes"]), 8)
        self.assertEqual(len(seed["plans"]), 3)
        self.assertEqual(len(seed["diagnoses"]), 3)
        self.assertEqual(len(seed["evaluation_tasks"]), 10)
        for learner in seed["learner_profiles"]:
            self.assertNotIn("password", learner)

    def test_seed_references_are_consistent_and_safety_labels_exist(self):
        seed = self._load_seed()
        kp_ids = {item["kp_id"] for item in seed["knowledge_points"]}
        question_ids = {item["question_id"] for item in seed["question_bank"]}
        label_ids = {item["label_id"] for item in seed["safety_labels"]}

        self.assertIn("SL_MEDICAL_DISCLAIMER", label_ids)
        self.assertIn("SL_EDU_ONLY", label_ids)
        self.assertIn("SL_SOURCE_REQUIRED", label_ids)

        for question in seed["question_bank"]:
            self.assertTrue(question["kp_ids"], question["question_id"])
            self.assertLessEqual(set(question["kp_ids"]), kp_ids, question["question_id"])
            self.assertTrue(question["safety_label_ids"], question["question_id"])
            self.assertLessEqual(set(question["safety_label_ids"]), label_ids, question["question_id"])

        for mistake in seed["mistakes"]:
            self.assertIn(mistake["question_id"], question_ids, mistake["mistake_id"])
            self.assertLessEqual(set(mistake["kp_ids"]), kp_ids, mistake["mistake_id"])
            self.assertTrue(mistake["safety_label_ids"], mistake["mistake_id"])
            self.assertLessEqual(set(mistake["safety_label_ids"]), label_ids, mistake["mistake_id"])

        for plan in seed["plans"]:
            self.assertLessEqual(set(plan["target_kp_ids"]), kp_ids, plan["plan_id"])
            self.assertTrue(plan["safety_label_ids"], plan["plan_id"])
            self.assertLessEqual(set(plan["safety_label_ids"]), label_ids, plan["plan_id"])

        for diagnosis in seed["diagnoses"]:
            self.assertLessEqual(set(diagnosis["weak_kp_ids"]), kp_ids, diagnosis["diagnosis_id"])
            self.assertTrue(diagnosis["safety_label_ids"], diagnosis["diagnosis_id"])
            self.assertLessEqual(set(diagnosis["safety_label_ids"]), label_ids, diagnosis["diagnosis_id"])

        for task in seed["evaluation_tasks"]:
            self.assertLessEqual(set(task["required_kp_ids"]), kp_ids, task["task_id"])
            self.assertLessEqual(set(task["question_ids"]), question_ids, task["task_id"])
            self.assertTrue(task["safety_label_ids"], task["task_id"])
            self.assertLessEqual(set(task["safety_label_ids"]), label_ids, task["task_id"])

    def test_seed_script_loads_demo_data_into_temporary_sqlite(self):
        env_names = ["USE_SQLITE", "SQLITE_PATH", "DATABASE_URL"]
        original_env = {name: os.environ.get(name) for name in env_names}
        module_names = [
            "APP.backend.scripts.seed_shizhen_mvp",
            "APP.backend.diagnosis_agent_service",
            "APP.backend.auth",
            "APP.backend.database",
            "APP.backend.config",
        ]
        original_modules = {name: sys.modules.get(name) for name in module_names}
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                sqlite_path = Path(tmpdir) / "seed_test.sqlite3"
                os.environ["USE_SQLITE"] = "true"
                os.environ["SQLITE_PATH"] = str(sqlite_path)
                os.environ.pop("DATABASE_URL", None)

                for module_name in module_names:
                    sys.modules.pop(module_name, None)

                database = importlib.import_module("APP.backend.database")
                seed_script = importlib.import_module("APP.backend.scripts.seed_shizhen_mvp")

                setup_db = database.SessionLocal()
                try:
                    setup_db.add(database.KnowledgePoint(
                        kp_id="KP_ZD_021",
                        name="Existing KP",
                        source="external_fixture",
                        status="active",
                    ))
                    setup_db.commit()
                finally:
                    setup_db.close()

                result = seed_script.seed_shizhen_mvp_data(
                    seed_path=self.seed_path,
                    session_factory=database.SessionLocal,
                )

                db = database.SessionLocal()
                try:
                    seed = self._load_seed()
                    self.assertEqual(result["users"], 3)
                    self.assertEqual(result["knowledge_points"], len(seed["knowledge_points"]) - 1)
                    self.assertEqual(result["questions"], len(seed["question_bank"]))
                    self.assertEqual(result["mistakes"], len(seed["mistakes"]))
                    self.assertEqual(result["question_attempts"], len(seed["mistakes"]))
                    self.assertEqual(result["plans"], 3)
                    self.assertEqual(result["diagnoses"], 3)
                    self.assertEqual(result["evaluation_tasks"], 10)

                    self.assertEqual(db.query(database.UserModel).filter(database.UserModel.username.like("shizhen_%")).count(), 3)
                    self.assertEqual(db.query(database.KnowledgePoint).count(), len(seed["knowledge_points"]))
                    self.assertEqual(db.query(database.QuestionBankItem).count(), len(seed["question_bank"]))
                    self.assertEqual(db.query(database.MistakeRecord).count(), len(seed["mistakes"]))
                    self.assertEqual(db.query(database.MistakeRecord).filter(database.MistakeRecord.status == "active").count(), len(seed["mistakes"]))
                    self.assertEqual(db.query(database.QuestionAttempt).count(), len(seed["mistakes"]))
                    self.assertEqual(db.query(database.LearningPlanRecord).count(), 3)
                    self.assertEqual(db.query(database.LearningPlanRecord).filter(database.LearningPlanRecord.plan_type == "diagnosis_driven").count(), 3)
                    self.assertEqual(db.query(database.LearningActivityRecord).filter(database.LearningActivityRecord.activity_type == "diagnosis").count(), 3)
                    self.assertEqual(db.query(database.LearningActivityRecord).filter(database.LearningActivityRecord.activity_type == "question_attempt").count(), len(seed["mistakes"]))
                    self.assertEqual(db.query(database.AgentEvent).filter(database.AgentEvent.event_type == "evaluation_task").count(), 10)
                    demo_user = db.query(database.UserModel).filter(database.UserModel.username == "shizhen_cross_major").one()
                    external_kp = db.query(database.KnowledgePoint).filter(database.KnowledgePoint.kp_id == "KP_ZD_021").one()
                    self.assertEqual(demo_user.hashed_password, "!demo-login-disabled")
                    self.assertEqual(demo_user.role, "user")
                    self.assertEqual(external_kp.source, "external_fixture")

                    diagnosis_service = importlib.import_module("APP.backend.diagnosis_agent_service")
                    user = db.query(database.UserModel).filter(database.UserModel.username == "shizhen_cross_major").one()
                    profile = diagnosis_service.build_learning_profile(db, user.id)
                    behavior_window = diagnosis_service.build_l3_behavior_window(db, user.id)
                    plan = db.query(database.LearningPlanRecord).filter(
                        database.LearningPlanRecord.user_id == user.id,
                        database.LearningPlanRecord.plan_type == "diagnosis_driven",
                    ).one()
                    plan_payload = json.loads(plan.payload_json)
                    self.assertLess(profile["question_accuracy"], 1.0)
                    self.assertLess(behavior_window["task_completion_rate"], 1.0)
                    self.assertEqual(plan_payload["source"], "shizhen_mvp_seed")
                    self.assertTrue(plan_payload["target_kp_ids"])
                    self.assertIn("plan_summary", plan_payload)
                    self.assertIn("weekly_plan", plan_payload)
                    self.assertIn("constraints", plan_payload)
                    self.assertEqual(plan_payload["plan_summary"]["plan_id"], plan_payload["plan_id"])
                    self.assertTrue(plan_payload["weekly_plan"])
                    self.assertIn("daily_available_minutes", plan_payload["constraints"])
                finally:
                    db.close()
                    database.engine.dispose()
        finally:
            for name, value in original_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            for module_name in module_names:
                sys.modules.pop(module_name, None)
            for module_name, module in original_modules.items():
                if module is not None:
                    sys.modules[module_name] = module

    def test_seed_script_rejects_existing_non_demo_usernames(self):
        env_names = ["USE_SQLITE", "SQLITE_PATH", "DATABASE_URL"]
        original_env = {name: os.environ.get(name) for name in env_names}
        module_names = [
            "APP.backend.scripts.seed_shizhen_mvp",
            "APP.backend.auth",
            "APP.backend.database",
            "APP.backend.config",
        ]
        original_modules = {name: sys.modules.get(name) for name in module_names}
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                sqlite_path = Path(tmpdir) / "seed_test.sqlite3"
                os.environ["USE_SQLITE"] = "true"
                os.environ["SQLITE_PATH"] = str(sqlite_path)
                os.environ.pop("DATABASE_URL", None)
                for module_name in module_names:
                    sys.modules.pop(module_name, None)

                database = importlib.import_module("APP.backend.database")
                seed_script = importlib.import_module("APP.backend.scripts.seed_shizhen_mvp")
                setup_db = database.SessionLocal()
                try:
                    setup_db.add(database.UserModel(
                        username="shizhen_cross_major",
                        email="existing@example.test",
                        hashed_password="existing-hash",
                        role="admin",
                    ))
                    setup_db.commit()
                finally:
                    setup_db.close()

                with self.assertRaises(ValueError):
                    seed_script.seed_shizhen_mvp_data(
                        seed_path=self.seed_path,
                        session_factory=database.SessionLocal,
                    )

                db = database.SessionLocal()
                try:
                    user = db.query(database.UserModel).filter(database.UserModel.username == "shizhen_cross_major").one()
                    self.assertEqual(user.email, "existing@example.test")
                    self.assertEqual(user.hashed_password, "existing-hash")
                    self.assertEqual(user.role, "admin")
                    self.assertEqual(db.query(database.LearningActivityRecord).count(), 0)
                finally:
                    db.close()
                    database.engine.dispose()
        finally:
            for name, value in original_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            for module_name in module_names:
                sys.modules.pop(module_name, None)
            for module_name, module in original_modules.items():
                if module is not None:
                    sys.modules[module_name] = module


if __name__ == "__main__":
    unittest.main()
