import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from APP.backend import database
from APP.backend.learning_target_service import (
    LearningTargetLockedError,
    LearningTargetValidationError,
    get_active_learning_target,
    set_active_learning_target,
)
from APP.backend.tests.test_official_exam_repository import write_exam_fixture
from APP.backend.official_exam_repository import OfficialExamRepository


class LearningTargetServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        write_exam_fixture(data_dir)
        self.repository = OfficialExamRepository(data_dir)
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
            db.commit()

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_creates_one_active_target_and_archives_previous_target(self):
        with self.Session() as db:
            first = set_active_learning_target(
                db,
                user_id=1,
                target_type="certification",
                exam_track_id="track-a",
                repository=self.repository,
            )
            second = set_active_learning_target(
                db,
                user_id=1,
                target_type="certification",
                exam_track_id="track-b",
                repository=self.repository,
            )
            rows = db.query(database.UserLearningTarget).order_by(database.UserLearningTarget.id).all()

        self.assertFalse(rows[0].is_active)
        self.assertIsNotNone(rows[0].archived_at)
        self.assertTrue(rows[1].is_active)
        self.assertEqual(second.exam_track_id, "track-b")
        self.assertEqual(first.exam_name_snapshot, "中医执业医师")

    def test_rejects_unknown_track_and_invalid_target_type(self):
        with self.Session() as db:
            with self.assertRaises(LearningTargetValidationError):
                set_active_learning_target(db, user_id=1, target_type="certification", exam_track_id="missing", repository=self.repository)
            with self.assertRaises(LearningTargetValidationError):
                set_active_learning_target(db, user_id=1, target_type="other", exam_track_id="track-a", repository=self.repository)

    def test_locked_manual_target_rejects_automatic_overwrite(self):
        with self.Session() as db:
            set_active_learning_target(
                db,
                user_id=1,
                target_type="certification",
                exam_track_id="track-a",
                repository=self.repository,
                is_locked=True,
                source="manual",
            )
            with self.assertRaises(LearningTargetLockedError):
                set_active_learning_target(
                    db,
                    user_id=1,
                    target_type="certification",
                    exam_track_id="track-b",
                    repository=self.repository,
                    source="behavior_analysis",
                )
            active = get_active_learning_target(db, 1)
            self.assertEqual(active.exam_track_id, "track-a")

    def test_runtime_schema_is_restart_safe_and_creates_target_indexes(self):
        migration_engine = create_engine("sqlite://")
        try:
            database.ensure_runtime_schema_for(migration_engine)
            database.ensure_runtime_schema_for(migration_engine)
            inspector = inspect(migration_engine)
            self.assertIn("user_learning_targets", inspector.get_table_names())
            foreign_keys = inspector.get_foreign_keys("user_learning_targets")
            self.assertTrue(
                any(
                    item.get("referred_table") == "users"
                    and item.get("constrained_columns") == ["user_id"]
                    for item in foreign_keys
                )
            )
            index_names = {item["name"] for item in inspector.get_indexes("user_learning_targets")}
            self.assertIn("ix_user_learning_targets_user_active", index_names)
            self.assertIn("uq_user_learning_targets_user_active_slot", index_names)
        finally:
            migration_engine.dispose()


if __name__ == "__main__":
    unittest.main()
