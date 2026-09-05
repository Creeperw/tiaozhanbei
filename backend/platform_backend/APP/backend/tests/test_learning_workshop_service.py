import json
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
            db.add(database.KnowledgePoint(
                kp_id="KP_1",
                name="四君子汤",
                source="formal-content:test",
            ))
            db.commit()
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
                            "tags": ["四君子汤"],
                            "source_metadata": {
                                "kp_names": {"KP_1": "四君子汤"}
                            },
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
            self.assertEqual(paper["items"][0]["kp_names"], ["四君子汤"])
            self.assertEqual(paper["items"][0]["max_score"], 25)
            record = db.query(database.PaperInstanceRecord).filter_by(
                paper_id=published["paper_id"]
            ).one()
            self.assertIsNone(record.daily_task_item_id)
            version = db.query(database.QuestionVersionRecord).filter_by(
                question_version_id="Q_1:agent"
            ).one()
            link = db.query(database.QuestionKPLinkRecord).filter_by(
                question_version_id=version.question_version_id,
                kp_id="KP_1",
            ).one()
            self.assertEqual(version.answer, "A. 人参")
            self.assertEqual(link.status, "active")

    def test_agent_paper_canonicalizes_duplicate_bridge_before_snapshot(self):
        with self.Session() as db:
            db.add(database.KnowledgePoint(
                kp_id="KP_CANON",
                name="四君子汤配伍意义",
                source="agent_audited_paper",
            ))
            db.commit()

            published = publish_agent_paper(
                db,
                user_id=1,
                execution_id="EXE_CANONICAL",
                paper={
                    "title": "规范身份试卷",
                    "items": [{
                        "question": {
                            "question_id": "Q_CANONICAL",
                            "question_type": "short_answer",
                            "stem": "说明配伍意义",
                            "reference_answer": "益气健脾",
                            "source_metadata": {
                                "kp_names": {"KP_DUPLICATE": "四君子汤配伍意义"}
                            },
                            "bridges": [{"kp_id": "KP_DUPLICATE"}],
                        },
                    }],
                },
                blueprint={},
                evidence_pack={},
            )

            item = db.query(database.PaperItemRecord).filter_by(
                paper_id=published["paper_id"]
            ).one()
            self.assertEqual(item.kp_snapshot_json, '["KP_CANON"]')
            lineage = json.loads(item.evidence_refs_json)
            self.assertEqual(lineage["source_kp_ids"], ["KP_DUPLICATE"])
            mapping = db.query(database.KnowledgePointCanonicalMap).one()
            self.assertEqual(mapping.canonical_kp_id, "KP_CANON")
            self.assertEqual(
                db.query(database.QuestionKPLinkRecord).filter_by(
                    question_version_id="Q_CANONICAL:agent"
                ).one().kp_id,
                "KP_CANON",
            )

    def test_agent_paper_unknown_bridge_stays_pending_outside_learning_state(self):
        with self.Session() as db:
            published = publish_agent_paper(
                db,
                user_id=1,
                execution_id="EXE_PENDING_KP",
                paper={
                    "title": "待审知识点试卷",
                    "items": [{
                        "question": {
                            "question_id": "Q_PENDING_KP",
                            "question_type": "short_answer",
                            "stem": "说明新概念",
                            "reference_answer": "待审答案",
                            "source_metadata": {
                                "kp_names": {"MODEL_KP": "未经审核的新概念"}
                            },
                            "bridges": [{"kp_id": "MODEL_KP"}],
                        },
                    }],
                },
                blueprint={},
                evidence_pack={},
            )

            item = db.query(database.PaperItemRecord).filter_by(
                paper_id=published["paper_id"]
            ).one()
            self.assertEqual(item.kp_snapshot_json, "[]")
            self.assertEqual(db.query(database.CandidateKnowledgePoint).count(), 1)
            self.assertEqual(db.query(database.KnowledgePoint).count(), 0)
            self.assertEqual(
                db.query(database.QuestionKPLinkRecord).filter_by(
                    question_version_id="Q_PENDING_KP:agent"
                ).count(),
                0,
            )

    def test_agent_paper_rejects_wrong_exact_question_type_distribution(self):
        with self.Session() as db:
            paper = {
                "title": "题型错误试卷",
                "items": [
                    {
                        "sequence": index,
                        "question": {
                            "question_id": f"Q_{index}",
                            "question_type": "单项选择题",
                            "stem": f"题干{index}",
                            "options": ["A. 甲", "B. 乙"],
                            "reference_answer": "A",
                        },
                    }
                    for index in range(1, 16)
                ],
            }
            blueprint = {
                "required_total_question_count": 15,
                "question_count_is_hard_constraint": True,
                "required_question_type_distribution": {
                    "单项选择题": 10,
                    "多项选择题": 5,
                },
            }

            with self.assertRaisesRegex(ValueError, "question type distribution"):
                publish_agent_paper(
                    db,
                    user_id=1,
                    execution_id="EXE_WRONG_TYPES",
                    paper=paper,
                    blueprint=blueprint,
                    evidence_pack={},
                )

            self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)

    def test_agent_paper_reuses_task_five_frozen_snapshot_binding(self):
        with self.Session() as db:
            db.add(database.DailyTaskItemRecord(
                task_item_id="ITEM_BOUND",
                host_task_id="TASK_1",
                user_id=1,
                kp_id="KP_1",
                item_kind="knowledge_practice",
            ))
            db.add(database.DailyTaskQuestionSnapshotRecord(
                task_item_id="ITEM_BOUND",
                user_id=1,
                question_id="Q_FROZEN",
                question_version_id="Q_FROZEN:v1",
                question_type="short_answer",
                stem_snapshot="冻结题干",
                answer_snapshot="冻结答案",
                kp_snapshot_json='["KP_1"]',
                source_kind="curated",
            ))
            db.commit()

            published = publish_agent_paper(
                db,
                user_id=1,
                execution_id="EXE_BOUND",
                paper={
                    "title": "绑定试卷",
                    "duration_minutes": 20,
                    "items": [{"question": {"question_id": "AGENT_Q"}}],
                },
                blueprint={"total_score": 100},
                evidence_pack={},
                daily_task_item_id="ITEM_BOUND",
            )

            record = db.query(database.PaperInstanceRecord).filter_by(
                paper_id=published["paper_id"]
            ).one()
            item = db.query(database.PaperItemRecord).filter_by(
                paper_id=published["paper_id"]
            ).one()
            self.assertEqual(record.daily_task_item_id, "ITEM_BOUND")
            self.assertEqual(
                (item.question_id, item.question_version_id, item.stem_snapshot),
                ("Q_FROZEN", "Q_FROZEN:v1", "冻结题干"),
            )

    def test_missing_item_scores_are_completed_to_the_authoritative_total(self):
        scores = _normalized_item_scores(
            [{"score": 30}, {"score": 20}, {"score": None}, {}],
            {"total_score": 100},
            {},
        )

        self.assertEqual(scores, [30, 20, 25, 25])
        self.assertEqual(sum(scores), 100)

    def test_equal_item_scores_are_integers_and_sum_to_total(self):
        expected = {
            3: [34, 33, 33],
            4: [25, 25, 25, 25],
            5: [20, 20, 20, 20, 20],
            6: [17, 17, 17, 17, 16, 16],
            7: [15, 15, 14, 14, 14, 14, 14],
        }

        for question_count, allocation in expected.items():
            with self.subTest(question_count=question_count):
                scores = _normalized_item_scores(
                    [{} for _ in range(question_count)],
                    {"total_score": 100},
                    {},
                )
                self.assertEqual(scores, allocation)
                self.assertEqual(sum(scores), 100)
                self.assertTrue(all(float(score).is_integer() for score in scores))

    def test_twenty_six_item_scores_are_integer_and_sum_to_total(self):
        scores = _normalized_item_scores(
            [{} for _ in range(26)],
            {"total_score": 100},
            {},
        )

        self.assertEqual(scores.count(4), 22)
        self.assertEqual(scores.count(3), 4)
        self.assertEqual(sum(scores), 100)

    def test_explicit_fractional_weights_are_apportioned_to_integer_scores(self):
        scores = _normalized_item_scores(
            [{"score": 1.5}, {"score": 1}, {"score": 0.5}],
            {"total_score": 10},
            {},
        )

        self.assertEqual(scores, [5, 3, 2])
        self.assertEqual(sum(scores), 10)


if __name__ == "__main__":
    unittest.main()
