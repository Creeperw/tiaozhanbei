import unittest
from datetime import datetime

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from APP.backend import database


class CoreLearningSchemaTests(unittest.TestCase):
    def make_engine(self):
        return create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    def test_runtime_schema_preserves_existing_utc_timestamps(self):
        engine = self.make_engine()
        self.addCleanup(engine.dispose)
        database.Base.metadata.create_all(bind=engine)
        before = datetime(2026, 7, 16, 1, 0)
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO users (username, email, hashed_password, role, created_at) "
                "VALUES ('legacy', 'legacy@example.com', 'x', 'user', :created_at)"
            ), {"created_at": before})

        database.ensure_runtime_schema_for(engine)
        database.ensure_runtime_schema_for(engine)

        with engine.connect() as connection:
            created_at = connection.execute(text(
                "SELECT created_at FROM users WHERE username = 'legacy'"
            )).scalar_one()

        self.assertEqual(datetime.fromisoformat(str(created_at)), before)

    def test_runtime_schema_creates_target_contract_tables(self):
        engine = self.make_engine()
        self.addCleanup(engine.dispose)

        database.ensure_runtime_schema_for(engine)

        self.assertTrue({
            "kp",
            "question",
            "user_profile",
            "learning_profile",
            "long_term_plan",
            "short_term_plan",
            "question_attempt",
            "learning_task",
            "learning_focus_sessions",
            "system_data",
            "agent_context",
            "question_learning_stats",
            "user_knowledge_state",
            "core_practice_submission_claims",
        } <= set(inspect(engine).get_table_names()))

    def test_learning_profile_is_owned_by_one_user(self):
        engine = self.make_engine()
        self.addCleanup(engine.dispose)

        database.ensure_runtime_schema_for(engine)

        indexes = inspect(engine).get_indexes("learning_profile")
        self.assertIn(["user_id"], [item["column_names"] for item in indexes if item["unique"]])

    def test_learning_task_has_system_managed_version(self):
        engine = self.make_engine()
        self.addCleanup(engine.dispose)

        database.ensure_runtime_schema_for(engine)

        columns = {item["name"] for item in inspect(engine).get_columns("learning_task")}
        self.assertIn("version", columns)

    def test_learning_question_attempt_has_target_contract_fields(self):
        engine = self.make_engine()
        self.addCleanup(engine.dispose)

        database.ensure_runtime_schema_for(engine)

        columns = {
            item["name"]
            for item in inspect(engine).get_columns("question_attempt")
        }
        self.assertTrue({
            "attempt_id",
            "user_id",
            "question_id",
            "task_id",
            "submitted_answer_json",
            "is_correct",
            "score",
            "response_time_seconds",
            "reason_for_mistake",
            "answered_at",
        } <= columns)


if __name__ == "__main__":
    unittest.main()
