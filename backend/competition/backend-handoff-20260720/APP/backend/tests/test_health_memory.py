import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend import database
from APP.backend.health_memory import get_or_create_profile


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


if __name__ == "__main__":
    unittest.main()
