import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend import database
from APP.backend.health_memory import (
    _is_subjective_memory_content,
    apply_confirmed_memory_replacements,
    get_or_create_profile,
    save_extracted_memories,
)


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


class HealthMemoryAutoConfirmGatingTests(unittest.TestCase):
    """门控一/四：主观感受与 preference/feedback 类别即使被模型误标为
    requires_confirmation=false 也绝不直接沉淀，退回候选池由用户确认。"""

    def _seed_user(self, db):
        db.add(database.UserModel(
            id=1,
            username="learner",
            email="learner@example.com",
            hashed_password="x",
        ))
        db.commit()

    def _extract_with(self, db, important_item):
        return save_extracted_memories(
            db,
            1,
            {
                "candidates": [],
                "important_short_term": [important_item],
            },
            source="memory_agent",
            session_id=None,
            commit=False,
        )

    def test_subjective_content_never_auto_confirms(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, Session = _build_db(directory)
            with Session() as db:
                self._seed_user(db)
                self._extract_with(db, {
                    "content": "这道题讲解太浅了，希望讲得更细",
                    "title": "讲解反馈",
                    "requires_confirmation": False,
                    "confidence": 0.95,
                    "category": "long_term",
                })
                db.flush()
                active = db.query(database.PersonalizationMemory).filter_by(user_id=1).count()
                candidates = db.query(database.MemoryCandidate).filter(
                    database.MemoryCandidate.user_id == 1,
                    database.MemoryCandidate.status == "pending",
                ).all()
                self.assertEqual(active, 0, "主观感受不得直接沉淀为正式记忆")
                self.assertEqual(len(candidates), 1, "主观感受应退回候选池")
                self.assertIn("等待用户确认", candidates[0].reason)
            engine.dispose()

    def test_preference_category_never_auto_confirms(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, Session = _build_db(directory)
            with Session() as db:
                self._seed_user(db)
                self._extract_with(db, {
                    "content": "用户更喜欢听案例讲解",
                    "title": "偏好",
                    "requires_confirmation": False,
                    "confidence": 0.95,
                    "category": "preference",
                })
                db.flush()
                active = db.query(database.PersonalizationMemory).filter_by(user_id=1).count()
                candidates = db.query(database.MemoryCandidate).filter(
                    database.MemoryCandidate.user_id == 1,
                    database.MemoryCandidate.status == "pending",
                ).count()
                self.assertEqual(active, 0, "preference 类别即使高置信也绝不自动沉淀")
                self.assertEqual(candidates, 1)
            engine.dispose()

    def test_feedback_category_never_auto_confirms(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, Session = _build_db(directory)
            with Session() as db:
                self._seed_user(db)
                self._extract_with(db, {
                    "content": "用户对错题复习方式提出了反馈",
                    "title": "纠偏反馈",
                    "requires_confirmation": False,
                    "confidence": 0.95,
                    "category": "feedback",
                })
                db.flush()
                active = db.query(database.PersonalizationMemory).filter_by(user_id=1).count()
                candidates = db.query(database.MemoryCandidate).filter(
                    database.MemoryCandidate.user_id == 1,
                    database.MemoryCandidate.status == "pending",
                ).count()
                self.assertEqual(active, 0, "feedback 类别不得直接沉淀")
                self.assertEqual(candidates, 1)
            engine.dispose()

    def test_objective_fact_still_auto_confirms(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, Session = _build_db(directory)
            with Session() as db:
                self._seed_user(db)
                self._extract_with(db, {
                    "content": "用户以后每天只有 30 分钟学习时间",
                    "title": "学习时间",
                    "requires_confirmation": False,
                    "confidence": 0.95,
                    "category": "long_term",
                })
                db.flush()
                active = db.query(database.PersonalizationMemory).filter(
                    database.PersonalizationMemory.user_id == 1,
                    database.PersonalizationMemory.is_active == True,
                ).all()
                candidates = db.query(database.MemoryCandidate).filter(
                    database.MemoryCandidate.user_id == 1,
                    database.MemoryCandidate.status == "pending",
                ).count()
                self.assertEqual(len(active), 1, "客观确定性事实仍应直接沉淀")
                self.assertEqual(active[0].category, "long_term")
                self.assertEqual(candidates, 0)
            engine.dispose()

    def test_subjective_detector_markers(self):
        self.assertTrue(_is_subjective_memory_content("这道题太难了"))
        self.assertTrue(_is_subjective_memory_content("讲解太快了跟不上"))
        self.assertTrue(_is_subjective_memory_content("用户觉得题目难度有点大"))
        self.assertTrue(_is_subjective_memory_content("用户不喜欢这种复习方式"))
        # 客观锚点豁免：含时间/计划等可核验锚点的陈述不判主观
        self.assertFalse(_is_subjective_memory_content("用户希望每天学习 60 分钟"))
        self.assertFalse(_is_subjective_memory_content("用户计划每周二晚上学习"))
        # 无感受词的客观事实
        self.assertFalse(_is_subjective_memory_content("用户对海鲜过敏"))
        self.assertFalse(_is_subjective_memory_content("用户要考中医内科"))
        # 弱感受词但没有主观对象词
        self.assertFalse(_is_subjective_memory_content("用户感觉身体状况良好"))


if __name__ == "__main__":
    unittest.main()
