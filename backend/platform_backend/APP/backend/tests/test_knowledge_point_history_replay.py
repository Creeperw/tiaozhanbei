import unittest
from datetime import datetime, timedelta
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database

from APP.backend.knowledge_point_history_replay import (
    CanonicalReplayEvent,
    collect_canonical_replay_events,
    replay_canonical_events,
)


class KnowledgePointHistoryReplayTests(unittest.TestCase):
    def test_replays_each_attempt_item_once_after_source_ids_merge(self):
        started = datetime(2026, 8, 25, 6, 20, 54)
        events = (
            CanonicalReplayEvent(
                learner_id=62,
                attempt_id="attempt-wrong",
                attempt_item_id="item-wrong",
                occurred_at=started,
                q_t=0.0,
                is_correct=False,
                confidence=0.91,
                exam_track_ids=("exam",),
                source_kp_ids=("006307", "015781", "019607", "038517", "050095"),
                sequence=10,
            ),
            CanonicalReplayEvent(
                learner_id=62,
                attempt_id="attempt-correct",
                attempt_item_id="item-correct",
                occurred_at=started + timedelta(minutes=30),
                q_t=1.0,
                is_correct=True,
                confidence=0.91,
                exam_track_ids=("exam",),
                source_kp_ids=("006307", "015781", "019607", "019674"),
                sequence=11,
            ),
        )

        projection = replay_canonical_events(events)[0]

        self.assertEqual(projection.attempt_count, 2)
        self.assertEqual(projection.wrong_count, 1)
        self.assertEqual(projection.review_count, 2)
        self.assertEqual(len(projection.event_history), 2)
        self.assertAlmostEqual(projection.event_history[0]["mastery_score"], 0.0)
        self.assertAlmostEqual(projection.event_history[1]["mastery_score"], 35.0)

    def test_replay_is_independent_for_each_learner(self):
        occurred_at = datetime(2026, 7, 26, 7, 15, 32)
        events = (
            CanonicalReplayEvent(5, "a", "one", occurred_at, 0.0, False, 0.91, (), ("019674",), 1),
            CanonicalReplayEvent(62, "b", "two", occurred_at, 1.0, True, 0.91, ("exam",), ("019674",), 2),
        )

        projections = replay_canonical_events(events)

        self.assertEqual([row.learner_id for row in projections], [5, 62])
        self.assertEqual([row.attempt_count for row in projections], [1, 1])
        self.assertEqual([row.mastery_score for row in projections], [0.0, 100.0])

    def test_same_timestamp_uses_persisted_sequence(self):
        occurred_at = datetime(2026, 8, 25, 6, 20, 54)
        events = (
            CanonicalReplayEvent(62, "later", "aaa", occurred_at, 0.0, False, 0.91, (), ("b",), 20),
            CanonicalReplayEvent(62, "earlier", "zzz", occurred_at, 1.0, True, 0.91, (), ("a",), 10),
        )

        projection = replay_canonical_events(events)[0]

        self.assertEqual(
            [row["attempt_item_id"] for row in projection.event_history],
            ["zzz", "aaa"],
        )
        self.assertAlmostEqual(projection.mastery_score, 65.0)


class KnowledgePointHistoryReplayDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(database.UserModel(id=1, username="learner", hashed_password="x"))
        self.db.add(database.LearningAttemptRecord(
            attempt_id="attempt",
            learner_id=1,
            status="submitted",
            submitted_at=datetime(2026, 8, 25, 6, 20, 54),
        ))
        self.db.add(database.LearningAttemptItemRecord(
            attempt_item_id="item",
            attempt_id="attempt",
            question_version_id="question",
            kp_snapshot_json=json.dumps([{"kp_id": "source"}]),
        ))
        self.db.add(database.GradingResultRecord(
            artifact_id="grading",
            attempt_item_id="item",
            version=1,
            score=100,
            max_score=100,
            is_correct=True,
            kp_ids_json=json.dumps(["source"]),
            evidence_pack_id="pack",
            confidence=0.91,
            status="reviewed",
        ))
        self.db.add(database.AuditResultRecord(
            audit_id="audit",
            source_artifact_id="grading",
            source_artifact_version=1,
            decision="pass",
            status="completed",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_requires_applied_writeback_receipt(self):
        self.assertEqual(
            collect_canonical_replay_events(self.db, source_kp_ids=("source",)),
            (),
        )

        self.db.add(database.LearningWritebackReceipt(
            receipt_id="receipt",
            idempotency_key="item:grading:v1",
            attempt_item_id="item",
            grading_artifact_id="grading",
            grading_artifact_version=1,
            audit_id="audit",
            status="applied",
            created_at=datetime(2026, 8, 25, 6, 20, 55),
        ))
        self.db.commit()

        event = collect_canonical_replay_events(
            self.db, source_kp_ids=("source",)
        )[0]
        self.assertEqual(event.attempt_item_id, "item")
        self.assertEqual(event.occurred_at, datetime(2026, 8, 25, 6, 20, 55))


if __name__ == "__main__":
    unittest.main()