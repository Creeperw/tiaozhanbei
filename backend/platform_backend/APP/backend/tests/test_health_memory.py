import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend import database
from APP.backend.health_memory import apply_confirmed_memory_replacements, get_or_create_profile


def _build_db(tmpdir):
    engine = create_engine(
        f"sqlite:///{Path(tmpdir) / 'profiles.db'}",
        connect_args={"check_same_thread": False},
    )
    database.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return engine, Session


class HealthMemoryProfileTests(unittest.TestCase):
    def test_concurrent_profile_creation_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(
                f"sqlite:///{Path(directory) / 'profiles.db'}",
                connect_args={"check_same_thread": False},
            )
            database.Base.metadata.create_all(bind=engine)
            Session = sessionmaker(bind=engine)
            with Session() as db:
                db.add(database.UserModel(
                    id=1,
                    username="learner",
                    email="learner@example.com",
                    hashed_password="x",
                ))
                db.commit()

            def create_profile():
                with Session() as db:
                    return get_or_create_profile(db, 1).user_id

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: create_profile(), range(2)))

            with Session() as db:
                self.assertEqual(results, [1, 1])
                self.assertEqual(db.query(database.UserProfile).filter_by(user_id=1).count(), 1)
            engine.dispose()

    def test_confirmed_replacement_syncs_profile_time(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, Session = _build_db(directory)
            with Session() as db:
                db.add(database.UserModel(
                    id=1,
                    username="learner",
                    email="learner@example.com",
                    hashed_password="x",
                ))
                profile = database.UserProfile(
                    user_id=1,
                    diet_restrictions="每天 75 分钟；偏好时段 晚间",
                )
                db.add(profile)
                db.add(database.PersonalizationMemory(
                    id=10,
                    user_id=1,
                    category="note",
                    title="Onboarding Survey",
                    content=json.dumps({
                        "status": "onboarding_completed",
                        "survey_answers": {
                            "daily_available_minutes": 75,
                            "preferred_time_slot": "晚间",
                        },
                    }, ensure_ascii=False),
                    is_active=True,
                    source="onboarding",
                ))
                db.commit()

                result = apply_confirmed_memory_replacements(
                    db, 1,
                    [{
                        "memory_id": 10,
                        "proposed_memory": "每日学习时长正式改为60分钟，替换原75分钟。",
                    }],
                )
                db.commit()

                self.assertEqual(len(result["replaced"]), 1)
                old = db.query(database.PersonalizationMemory).get(10)
                self.assertFalse(old.is_active)
                self.assertIsNotNone(old.superseded_by)
                successor = db.query(database.PersonalizationMemory).get(old.superseded_by)
                self.assertIn("60分钟", successor.content)
                refreshed = db.query(database.UserProfile).filter_by(user_id=1).first()
                self.assertEqual(refreshed.diet_restrictions, "每天 60 分钟；偏好时段 晚间")
            engine.dispose()

    def test_replacement_without_time_memory_keeps_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, Session = _build_db(directory)
            with Session() as db:
                db.add(database.UserModel(
                    id=1,
                    username="learner",
                    email="learner@example.com",
                    hashed_password="x",
                ))
                db.add(database.UserProfile(
                    user_id=1,
                    diet_restrictions="每天 75 分钟；偏好时段 晚间",
                ))
                db.add(database.PersonalizationMemory(
                    id=11,
                    user_id=1,
                    category="note",
                    title="普通记忆",
                    content="用户偏好晚间学习。",
                    is_active=True,
                    source="manual",
                ))
                db.commit()

                result = apply_confirmed_memory_replacements(
                    db, 1,
                    [{
                        "memory_id": 11,
                        "proposed_memory": "用户偏好改为早晨学习。",
                    }],
                )
                db.commit()

                self.assertEqual(len(result["replaced"]), 1)
                refreshed = db.query(database.UserProfile).filter_by(user_id=1).first()
                self.assertEqual(refreshed.diet_restrictions, "每天 75 分钟；偏好时段 晚间")
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
