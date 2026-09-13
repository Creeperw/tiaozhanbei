"""Regression coverage for mistake-record identity.

The mistake drawer resolves a question through ``mistake_records.question_id``.
Grading writeback used to persist the question *version* id there, and
knowledge-atlas versions are named ``<question_id>:atlas:<hash>``, so the
lookup resolved against a key no question is stored under: the drawer rendered
no options and reported the standard answer as not recorded.

The same row also kept overwriting its attempt pointer on every wrong attempt,
so the answer displayed under "首次作答" was really the most recent one.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.auth import get_current_user
from APP.backend.database import get_db
from APP.backend.learning_writeback_service import (
    GradingWritebackCommand,
    apply_grading_writeback,
)

QUESTION_ID = "generated_临床案例问答__20aed1cc375b"
VERSION_ID = f"{QUESTION_ID}:atlas:8d42cfbd3013cd5d"
KP_ID = "KP_WUXING"
STEM = "在五行归类中，五脏与五声相应。请问，“咳”属于哪一行的病变表现？"
FIRST_ANSWER = "“咳”属肺，肺在五行中属金，故“咳”属金行的病变表现。"
FIRST_ATTEMPT_AT = datetime(2026, 9, 12, 16, 5, 11)
SECOND_ATTEMPT_AT = datetime(2026, 9, 12, 16, 11, 39)


class MistakeRecordIdentityTests(unittest.TestCase):
    def setUp(self):
        from APP.backend.main import app

        self.app = app
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add(database.UserModel(
                id=1,
                username="learner",
                email="learner@example.com",
                hashed_password="x",
            ))
            db.add(database.QuestionVersionRecord(
                question_version_id=VERSION_ID,
                question_id=QUESTION_ID,
                version=1,
                question_type="case_quiz",
                stem=STEM,
                answer=json.dumps(["金。"], ensure_ascii=False),
                analysis="金在五行中属肺。",
                source_kind="formal-content:knowledge-atlas-2026-07-18",
                status="active",
            ))
            db.add(database.LearningQuestion(
                question_id=QUESTION_ID,
                question_type="case_quiz",
                question_content=STEM,
                options_json="[]",
                answer_json=json.dumps(["金。"], ensure_ascii=False),
                explanation="金在五行中属肺。",
            ))
            db.commit()

        def override_db():
            with self.Session() as session:
                yield session

        def override_user():
            with self.Session() as db:
                return db.query(database.UserModel).filter_by(id=1).one()

        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_current_user] = override_user
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def _seed_attempt(
        self,
        db,
        attempt_item_id: str,
        *,
        answer: str,
        is_correct: bool,
        created_at: datetime,
    ) -> None:
        attempt_id = f"ATT_{attempt_item_id}"
        artifact_id = f"GRADE_{attempt_item_id}"
        pack_id = f"EP_{attempt_item_id}"
        # The attempt item keeps the authoritative snapshot form (objects),
        # while grading and evidence carry plain knowledge point ids.
        kp_snapshot = json.dumps([{"kp_id": KP_ID}], ensure_ascii=False)
        kp_ids = json.dumps([KP_ID], ensure_ascii=False)

        db.add(database.LearningAttemptRecord(
            attempt_id=attempt_id,
            learner_id=1,
            attempt_type="practice",
            status="submitted",
            submitted_at=created_at,
            source_kind="training_workshop",
            created_at=created_at,
        ))
        db.flush()
        db.add(database.LearningAttemptItemRecord(
            attempt_item_id=attempt_item_id,
            attempt_id=attempt_id,
            question_version_id=VERSION_ID,
            submitted_answer=answer,
            kp_snapshot_json=kp_snapshot,
            created_at=created_at,
        ))
        db.add(database.EvidencePackRecord(
            pack_id=pack_id,
            user_id=1,
            resolved_kp_ids_json=kp_ids,
            payload_json=json.dumps(
                {"attempt_item_id": attempt_item_id, "question_version_id": VERSION_ID},
                ensure_ascii=False,
            ),
        ))
        db.add(database.GradingResultRecord(
            artifact_id=artifact_id,
            attempt_item_id=attempt_item_id,
            version=1,
            score=100.0 if is_correct else 0.0,
            max_score=100.0,
            is_correct=is_correct,
            error_types_json="[]",
            error_reason="" if is_correct else "知识点遗漏",
            kp_ids_json=kp_ids,
            evidence_pack_id=pack_id,
            confidence=0.9,
            status="reviewed",
            payload_json=json.dumps(
                {"question_version_id": VERSION_ID}, ensure_ascii=False
            ),
        ))
        db.add(database.AuditResultRecord(
            audit_id=f"AUDIT_{attempt_item_id}",
            source_artifact_id=artifact_id,
            source_artifact_version=1,
            decision="pass",
            reason="评分与参考答案一致",
            confidence=0.95,
            status="completed",
        ))
        db.flush()

    def _graded_attempt(
        self,
        attempt_item_id: str,
        *,
        answer: str,
        is_correct: bool,
        created_at: datetime,
    ):
        with self.Session() as db:
            self._seed_attempt(
                db,
                attempt_item_id,
                answer=answer,
                is_correct=is_correct,
                created_at=created_at,
            )
            result = apply_grading_writeback(
                db,
                1,
                GradingWritebackCommand(
                    attempt_item_id=attempt_item_id,
                    grading_artifact_id=f"GRADE_{attempt_item_id}",
                    grading_artifact_version=1,
                    audit_id=f"AUDIT_{attempt_item_id}",
                ),
            )
            db.commit()
            return result

    def test_writeback_stores_the_question_id_not_the_atlas_version_id(self):
        result = self._graded_attempt(
            "ITEM_FIRST",
            answer=FIRST_ANSWER,
            is_correct=False,
            created_at=FIRST_ATTEMPT_AT,
        )

        self.assertEqual(result.status, "applied")
        with self.Session() as db:
            mistake = db.query(database.MistakeRecord).one()
            self.assertEqual(mistake.question_id, QUESTION_ID)
            self.assertEqual(mistake.question_version_id, VERSION_ID)
            self.assertEqual(mistake.first_attempt_item_id, "ITEM_FIRST")

    def test_repeat_wrong_attempts_keep_the_first_attempt_snapshot(self):
        self._graded_attempt(
            "ITEM_FIRST",
            answer=FIRST_ANSWER,
            is_correct=False,
            created_at=FIRST_ATTEMPT_AT,
        )
        self._graded_attempt(
            "ITEM_SECOND",
            answer="MARKER 最近一次作答",
            is_correct=False,
            created_at=SECOND_ATTEMPT_AT,
        )

        history = self.client.get(
            "/v1/workshop/practice/mistakes", params={"status": "all"}
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["total"], 1)
        mistake = history.json()["items"][0]
        self.assertEqual(mistake["attempt_item_id"], "ITEM_SECOND")
        self.assertEqual(mistake["first_attempt_item_id"], "ITEM_FIRST")
        self.assertEqual(mistake["student_answer"], FIRST_ANSWER)

    def test_mistake_detail_is_reachable_through_the_stored_question_id(self):
        self._graded_attempt(
            "ITEM_FIRST",
            answer=FIRST_ANSWER,
            is_correct=False,
            created_at=FIRST_ATTEMPT_AT,
        )

        history = self.client.get(
            "/v1/workshop/practice/mistakes", params={"status": "all"}
        )
        question_id = history.json()["items"][0]["question_id"]
        detail = self.client.get(
            f"/v1/workshop/practice/question-detail/{quote(question_id, safe='')}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["answer"], ["金。"])

    def test_legacy_row_without_snapshot_backfills_the_earliest_attempt(self):
        self._graded_attempt(
            "ITEM_FIRST",
            answer=FIRST_ANSWER,
            is_correct=False,
            created_at=FIRST_ATTEMPT_AT,
        )
        # Simulate a row written before the snapshot column existed.
        with self.Session() as db:
            row = db.query(database.MistakeRecord).one()
            row.first_attempt_item_id = None
            db.commit()

        self._graded_attempt(
            "ITEM_SECOND",
            answer="MARKER 最近一次作答",
            is_correct=False,
            created_at=SECOND_ATTEMPT_AT,
        )

        with self.Session() as db:
            row = db.query(database.MistakeRecord).one()
            self.assertEqual(row.first_attempt_item_id, "ITEM_FIRST")


if __name__ == "__main__":
    unittest.main()
