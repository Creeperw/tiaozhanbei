import unittest
from datetime import datetime, timedelta
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.knowledge_point_identity_service import (
    canonicalize_knowledge_point_ids,
    register_reviewed_equivalence,
    resolve_agent_knowledge_point,
    source_ids_by_canonical,
)


class KnowledgePointIdentityServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(database.UserModel(
            id=1,
            username="identity-user",
            hashed_password="x",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_reviewed_mapping_canonicalizes_and_preserves_lineage(self):
        self.db.add_all([
            database.KnowledgePoint(kp_id="KP_CANON", name="四君子汤"),
            database.KnowledgePoint(
                kp_id="KP_SOURCE",
                name="四君子汤",
                source="agent_audited_paper",
            ),
        ])
        self.db.flush()
        register_reviewed_equivalence(
            self.db,
            source_kp_id="KP_SOURCE",
            canonical_kp_id="KP_CANON",
            decision_basis="human_reviewed_exact_equivalence",
            evidence={"ticket": "review-1"},
            decided_by="reviewer:test",
        )
        self.db.flush()

        self.assertEqual(
            canonicalize_knowledge_point_ids(
                self.db, ["KP_SOURCE", "KP_CANON", "KP_SOURCE"]
            ),
            ("KP_CANON",),
        )
        self.assertEqual(
            source_ids_by_canonical(self.db, ["KP_CANON"])["KP_CANON"],
            ("KP_CANON", "KP_SOURCE"),
        )
        mapping = self.db.query(database.KnowledgePointCanonicalMap).one()
        self.assertEqual(mapping.decision_basis, "human_reviewed_exact_equivalence")
        self.assertEqual(mapping.decided_by, "reviewer:test")

    def test_agent_duplicate_uses_oldest_same_provenance_exact_label(self):
        self.db.add_all([
            database.KnowledgePoint(
                kp_id="KP_OLD",
                name=" 四君子汤配伍意义 ",
                source="agent_audited_paper",
                created_at=datetime.utcnow() - timedelta(days=2),
            ),
            database.KnowledgePoint(
                kp_id="KP_NEW",
                name="四君子汤配伍意义",
                source="agent_audited_paper",
                created_at=datetime.utcnow() - timedelta(days=1),
            ),
        ])
        self.db.flush()

        resolution = resolve_agent_knowledge_point(
            self.db,
            source_kp_id="KP_NEW",
            name="四君子汤配伍意义",
            user_id=1,
        )

        self.assertTrue(resolution.admitted)
        self.assertEqual(resolution.canonical_kp_id, "KP_OLD")
        mapping = self.db.query(database.KnowledgePointCanonicalMap).one()
        self.assertEqual(mapping.source_kp_id, "KP_NEW")
        self.assertEqual(mapping.canonical_kp_id, "KP_OLD")

    def test_unknown_agent_concept_stays_pending_and_is_not_activated(self):
        resolution = resolve_agent_knowledge_point(
            self.db,
            source_kp_id="MODEL_KP_9",
            name="一个未经审核的新概念",
            user_id=1,
        )

        self.assertFalse(resolution.admitted)
        self.assertEqual(resolution.status, "pending")
        self.assertIsNotNone(resolution.candidate_id)
        candidate = self.db.query(database.CandidateKnowledgePoint).one()
        self.assertEqual(candidate.status, "pending")
        self.assertEqual(self.db.query(database.KnowledgePoint).count(), 0)
        self.assertEqual(self.db.query(database.KnowledgePointCanonicalMap).count(), 0)

    def test_review_can_map_external_source_id_to_active_canonical(self):
        self.db.add(database.KnowledgePoint(
            kp_id="KP_CANON",
            name="四君子汤配伍意义",
        ))
        self.db.flush()

        mapping = register_reviewed_equivalence(
            self.db,
            source_kp_id="MODEL_GENERATED_ID",
            canonical_kp_id="KP_CANON",
            decision_basis="human_reviewed_exact_equivalence",
            evidence={"review": "approved"},
            decided_by="reviewer:test",
        )

        self.assertEqual(mapping.canonical_kp_id, "KP_CANON")
        evidence = json.loads(mapping.evidence_json)
        self.assertEqual(evidence["source_kp_record_status"], "external_source_id")


if __name__ == "__main__":
    unittest.main()