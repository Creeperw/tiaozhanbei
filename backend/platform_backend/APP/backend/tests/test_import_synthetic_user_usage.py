import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


def restore_modules(module_names, original_modules):
    """还原模块缓存与父包属性。

    重新导入会创建新的模块对象并挂到父包上；若只还原 sys.modules，父包属性会
    继续指向那个孤儿对象，此后 patch("APP.backend.config.X") 打在旧对象上，而
    业务代码 from APP.backend.config import X 读的是新对象，补丁静默失效。
    """
    for name in module_names:
        sys.modules.pop(name, None)
    for name, module in original_modules.items():
        if module is None:
            continue
        sys.modules[name] = module
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child, module)


class SyntheticUserUsageImportTests(unittest.TestCase):
    def setUp(self):
        self.dataset_path = (
            Path(__file__).resolve().parents[1]
            / "sample_data"
            / "synthetic_user_usage_v1.json"
        )

    def _dataset(self):
        return json.loads(self.dataset_path.read_text(encoding="utf-8"))

    def test_dataset_is_reproducible_and_valid(self):
        generator = importlib.import_module(
            "APP.backend.scripts.generate_synthetic_user_usage"
        )
        importer = importlib.import_module(
            "APP.backend.scripts.import_synthetic_user_usage"
        )
        dataset = self._dataset()

        self.assertEqual(generator.build_dataset(), dataset)
        summary = importer.validate_dataset(dataset)
        self.assertEqual(summary["users"], 12)
        self.assertEqual(summary["learner_groups"], 3)
        self.assertGreater(summary["question_attempts"], 500)
        self.assertGreater(summary["learning_activities"], 1000)
        self.assertFalse(dataset["contains_real_personal_data"])

    def test_two_imports_into_temporary_sqlite_are_idempotent(self):
        env_names = ["USE_SQLITE", "SQLITE_PATH", "DATABASE_URL"]
        original_env = {name: os.environ.get(name) for name in env_names}
        module_names = [
            "APP.backend.config",
            "APP.backend.database",
            "APP.backend.system_data_service",
        ]
        original_modules = {name: sys.modules.get(name) for name in module_names}
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["USE_SQLITE"] = "true"
                os.environ["SQLITE_PATH"] = str(Path(directory) / "usage.sqlite3")
                os.environ.pop("DATABASE_URL", None)
                for name in module_names:
                    sys.modules.pop(name, None)

                database = importlib.import_module("APP.backend.database")
                importer = importlib.import_module(
                    "APP.backend.scripts.import_synthetic_user_usage"
                )
                dataset = self._dataset()

                first = importer.import_dataset(dataset, database.SessionLocal)
                second = importer.import_dataset(dataset, database.SessionLocal)
                self.assertEqual(first, second)

                db = database.SessionLocal()
                try:
                    self.assertEqual(
                        db.query(database.UserModel)
                        .filter(database.UserModel.username.like("sim_%"))
                        .count(),
                        12,
                    )
                    self.assertEqual(
                        db.query(database.LearningActivityRecord).count(), 1299
                    )
                    self.assertEqual(db.query(database.QuestionAttempt).count(), 640)
                    self.assertEqual(db.query(database.MistakeRecord).count(), 250)
                    self.assertEqual(db.query(database.AgentEvent).count(), 144)
                    self.assertEqual(db.query(database.DailyTaskItemRecord).count(), 350)
                    self.assertEqual(db.query(database.SystemData).count(), 12)
                finally:
                    db.close()
                    database.engine.dispose()
        finally:
            for name, value in original_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            restore_modules(module_names, original_modules)


if __name__ == "__main__":
    unittest.main()
