import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.learning_workshop_service import (
    _normalized_item_scores,
    get_knowledge_card,
    list_knowledge_cards,
    publish_agent_paper,
    upsert_knowledge_card,
)
from APP.backend.paper_submission_service import get_owned_paper


class LearningWorkshopServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([
                database.UserModel(id=1, username="u1", email="u1@example.com", hashed_password="x"),
                database.UserModel(id=2, username="u2", email="u2@example.com", hashed_password="x"),
            ])
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_knowledge_cards_are_upserted_and_isolated_by_user(self):
        with self.Session() as db:
            card = upsert_knowledge_card(
                db,
                user_id=1,
                kp_id="KP_1",
                title="四君子汤",
                resource_bundle={"schema_version": "1.0", "bundle_id": "B1"},
            )
            self.assertEqual(list_knowledge_cards(db, user_id=1, offset=0, limit=10)["total"], 1)
            self.assertEqual(list_knowledge_cards(db, user_id=2, offset=0, limit=10)["total"], 0)
            self.assertIsNone(get_knowledge_card(db, user_id=2, card_id=card["card_id"]))

    def test_agent_paper_is_published_with_options_and_server_timing(self):
        with self.Session() as db:
            published = publish_agent_paper(
                db,
                user_id=1,
                execution_id="EXE_1",
                paper={
                    "title": "四君子汤测试卷",
                    "duration_minutes": 25,
                    "items": [{
                        "sequence": 1,
                        "score": 25,
                        "question": {
                            "question_id": "Q_1",
                            "question_type": "single_choice",
                            "stem": "君药是？",
                            "options": ["A. 人参", "B. 甘草"],
                            "reference_answer": "A. 人参",
                            "bridges": [{"kp_id": "KP_1"}],
                        },
                    }],
                },
                blueprint={"blueprint_id": "BP_1"},
                evidence_pack={},
            )
            paper = get_owned_paper(db, 1, published["paper_id"])

            self.assertEqual(paper["timing"]["duration_minutes"], 25)
            self.assertIsNotNone(paper["timing"]["started_at"])
            self.assertGreater(paper["timing"]["remaining_seconds"], 0)
            self.assertEqual(paper["items"][0]["options"], ["A. 人参", "B. 甘草"])
            self.assertEqual(paper["items"][0]["max_score"], 25)

    def test_missing_item_scores_are_completed_to_the_authoritative_total(self):
        scores = _normalized_item_scores(
            [{"score": 30}, {"score": 20}, {"score": None}, {}],
            {"total_score": 100},
            {},
        )

        self.assertEqual(scores, [30, 20, 25, 25])
        self.assertEqual(sum(scores), 100)


if __name__ == "__main__":
    unittest.main()
