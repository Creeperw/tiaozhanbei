import json
import unittest
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.daily_task_progress_service import (
    DailyTaskProgressError,
    confirm_iframe_video,
    daily_task_progress,
    ensure_executable_knowledge_bundle,
    record_reviewed_question,
    record_video_evidence,
    resolve_executable_knowledge_point,
    upsert_daily_task_snapshot,
)


class DailyTaskProgressServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        database.ensure_runtime_schema_for(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.now = datetime(2026, 7, 16, 10, 0)
        with self.session_factory() as db:
            db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
            db.add(database.KnowledgePoint(
                kp_id="KP_1",
                name="菌斑",
                aliases_json=json.dumps(["牙菌斑"]),
                source="formal-content:test",
                status="active",
            ))
            db.add(database.LearningQuestion(question_id="Q_1", question_type="single_choice", question_content="题干1", options_json=json.dumps(["A", "B"]), answer_json=json.dumps(["A"]), explanation="解释", difficulty=1.0, kp_ids_json=json.dumps(["KP_1"]), key_points="key", scoring_rubric="rubric"))
            db.add(database.LearningQuestion(question_id="Q_2", question_type="single_choice", question_content="题干2", options_json=json.dumps(["A", "B"]), answer_json=json.dumps(["A"]), explanation="解释", difficulty=1.0, kp_ids_json=json.dumps(["KP_1"]), key_points="key", scoring_rubric="rubric"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_1", question_id="Q_1", version=1, question_type="single_choice", stem="题干1", answer="A", analysis="解释", source_kind="formal-content:test", status="active"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_2", question_id="Q_2", version=1, question_type="single_choice", stem="题干2", answer="A", analysis="解释", source_kind="formal-content:test", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_1", kp_id="KP_1", is_primary=True, status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_2", kp_id="KP_1", is_primary=True, status="active"))
            db.commit()

    def test_formal_resolver_requires_unique_exact_name_and_three_freezable_questions(self):
        with self.session_factory() as db:
            self.assertIsNone(resolve_executable_knowledge_point(db, "牙菌斑"))
            db.add(database.LearningQuestion(question_id="Q_3", question_type="single_choice", question_content="题干3", options_json="[]", answer_json='["A"]', explanation="解释", difficulty=1.0, kp_ids_json='["KP_1"]', key_points="key", scoring_rubric="rubric"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_3", question_id="Q_3", version=1, question_type="single_choice", stem="题干3", answer="A", analysis="解释", source_kind="formal-content:test", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_3", kp_id="KP_1", is_primary=True, status="active"))
            db.flush()

            self.assertEqual(resolve_executable_knowledge_point(db, " 牙菌斑 "), "KP_1")
            self.assertIsNone(resolve_executable_knowledge_point(db, "不存在"))

    def test_trusted_atlas_bundle_becomes_an_executable_frozen_question_item(self):
        bundle = {
            "source": "knowledge_atlas",
            "kp_id": "KP_ATLAS",
            "knowledge_point_name": "阴阳转化",
            "kp": {
                "kp_id": "KP_ATLAS",
                "kp_lv1": "中医学基础",
                "kp_lv2": "阴阳学说",
                "kp_lv3": "阴阳转化",
                "other_name": "阴阳的相互转化",
                "raw_content": ["C_1"],
                "order": "1",
            },
            "questions": [
                {
                    "question_id": f"Q_ATLAS_{index}",
                    "question_type": "单项选择题",
                    "question_content": f"阴阳转化题目{index}",
                    "options": [
                        {"option_id": "A", "content": "正确"},
                        {"option_id": "B", "content": "错误"},
                    ],
                    "answer": ["A"],
                    "explanation": "教材解析",
                    "kp_ids": ["KP_ATLAS"],
                }
                for index in range(1, 4)
            ],
        }
        with self.session_factory() as db:
            kp_id = ensure_executable_knowledge_bundle(db, bundle)
            snapshot = upsert_daily_task_snapshot(
                db,
                user_id=1,
                payload={
                    "host_task_id": "TASK_ATLAS",
                    "host_task_version": 1,
                    "items": [
                        {
                            "task_item_id": "ITEM_ATLAS",
                            "item_type": "knowledge_practice",
                            "kp_id": kp_id,
                            "required_question_count": 3,
                        }
                    ],
                },
            )

            self.assertEqual(kp_id, "KP_ATLAS")
            self.assertEqual(len(snapshot["items"][0]["questions"]), 3)
            self.assertEqual(
                resolve_executable_knowledge_point(db, "阴阳的相互转化"),
                "KP_ATLAS",
            )
            imported = db.query(database.QuestionBankItem).filter_by(
                question_id="Q_ATLAS_1"
            ).one()
            self.assertIsNone(imported.difficulty)
            self.assertIsNone(imported.difficulty_source)

    def test_trusted_atlas_can_promote_same_kp_from_audited_agent_paper(self):
        bundle = {
            "source": "knowledge_atlas",
            "kp_id": "KP_AUDITED",
            "knowledge_point_name": "阴阳的特性",
            "kp": {
                "kp_id": "KP_AUDITED",
                "kp_lv1": "中医学基础",
                "kp_lv2": "阴阳学说",
                "kp_lv3": "阴阳的特性",
                "raw_content": ["C_AUDITED"],
                "order": "2",
            },
            "questions": [
                {
                    "question_id": f"Q_AUDITED_{index}",
                    "question_type": "单项选择题",
                    "question_content": f"阴阳特性题目{index}",
                    "options": [
                        {"option_id": "A", "content": "正确"},
                        {"option_id": "B", "content": "错误"},
                    ],
                    "answer": ["A"],
                    "explanation": "教材解析",
                    "kp_ids": ["KP_AUDITED"],
                }
                for index in range(1, 4)
            ],
        }
        with self.session_factory() as db:
            db.add(
                database.KnowledgePoint(
                    kp_id="KP_AUDITED",
                    name="阴阳的特性",
                    aliases_json="[]",
                    source="agent_audited_paper",
                    status="active",
                )
            )
            db.flush()

            kp_id = ensure_executable_knowledge_bundle(db, bundle)

            self.assertEqual(kp_id, "KP_AUDITED")
            point = db.query(database.KnowledgePoint).filter_by(
                kp_id="KP_AUDITED"
            ).one()
            self.assertEqual(
                point.source,
                "formal-content:knowledge-atlas-2026-07-18",
            )

    def test_publication_rejects_uncompletable_recall_before_persisting_parent(self):
        with self.session_factory() as db:
            with self.assertRaises(DailyTaskProgressError) as captured:
                upsert_daily_task_snapshot(db, user_id=1, payload={
                    "host_task_id": "TASK_BLOCKED_RECALL",
                    "host_task_version": 1,
                    "items": [{"task_item_id": "RECALL_1", "item_type": "recall"}],
                })

            self.assertEqual(captured.exception.code, 409)
            self.assertIn("without a verifiable completion path", str(captured.exception))
            self.assertEqual(db.query(database.DailyTaskInstanceRecord).count(), 0)
            self.assertEqual(db.query(database.DailyTaskItemRecord).count(), 0)

    def tearDown(self):
        self.engine.dispose()

    def test_same_kp_unbound_question_does_not_complete_item(self):
        with self.session_factory() as db:
            payload = {
                "host_task_id": "TASK_1",
                "host_task_version": 1,
                "items": [{"kp_id": "KP_1", "required_question_count": 1}],
            }
            result = upsert_daily_task_snapshot(db, user_id=1, payload=payload)
            self.assertEqual(len(result["items"]), 1)
            first_item = result["items"][0]
            self.assertEqual(first_item["status"], "pending")
            self.assertEqual(first_item["required_question_count"], 1)

    def test_metadata_only_parent_version_keeps_frozen_item_progress_visible(self):
        with self.session_factory() as db:
            payload = {
                "host_task_id": "TASK_VERSIONED",
                "host_task_version": 1,
                "items": [
                    {
                        "task_item_id": "ITEM_VERSIONED",
                        "kp_id": "KP_1",
                        "required_question_count": 1,
                    }
                ],
            }
            upsert_daily_task_snapshot(db, user_id=1, payload=payload)
            payload["host_task_version"] = 2
            upsert_daily_task_snapshot(db, user_id=1, payload=payload)

            progress = daily_task_progress(
                db,
                user_id=1,
                payload={
                    "host_task_id": "TASK_VERSIONED",
                    "host_task_version": 2,
                },
            )
            self.assertEqual(progress["total_items"], 1)
            self.assertEqual(progress["items"][0]["task_item_id"], "ITEM_VERSIONED")

    def test_all_frozen_questions_terminally_reviewed_complete_item(self):
        with self.session_factory() as db:
            payload = {
                "host_task_id": "TASK_1",
                "host_task_version": 1,
                "items": [{"kp_id": "KP_1", "required_question_count": 2}],
            }
            snapshot = upsert_daily_task_snapshot(db, user_id=1, payload=payload)
            item_id = snapshot["items"][0]["task_item_id"]
            questions = snapshot["items"][0]["questions"]
            for question in questions:
                record_reviewed_question(db, user_id=1, payload={
                    "task_item_id": item_id,
                    "question_version_id": question["question_version_id"],
                    "submitted_answer": "wrong",
                    "attempt_status": "submitted",
                    "audit_decision": "pass",
                    "audit_status": "completed",
                })
            db.commit()
            progress = daily_task_progress(db, user_id=1, payload={
                "host_task_id": "TASK_1",
                "host_task_version": 1,
            })
            self.assertEqual(progress["status"], "completed")

    def test_needs_human_review_is_not_terminal(self):
        with self.session_factory() as db:
            payload = {
                "host_task_id": "TASK_2",
                "host_task_version": 1,
                "items": [{"kp_id": "KP_1", "required_question_count": 1}],
            }
            snapshot = upsert_daily_task_snapshot(db, user_id=1, payload=payload)
            item_id = snapshot["items"][0]["task_item_id"]
            question = snapshot["items"][0]["questions"][0]
            record_reviewed_question(db, user_id=1, payload={
                "task_item_id": item_id,
                "question_version_id": question["question_version_id"],
                "submitted_answer": "wrong",
                "attempt_status": "submitted",
                "audit_decision": "needs_human_review",
                "audit_status": "pending",
            })
            db.commit()
            progress = daily_task_progress(db, user_id=1, payload={
                "host_task_id": "TASK_2",
                "host_task_version": 1,
            })
            self.assertEqual(progress["status"], "in_progress")

    def test_wrong_answer_still_completes_frozen_question(self):
        with self.session_factory() as db:
            payload = {
                "host_task_id": "TASK_3",
                "host_task_version": 1,
                "items": [{"kp_id": "KP_1", "required_question_count": 1}],
            }
            snapshot = upsert_daily_task_snapshot(db, user_id=1, payload=payload)
            item_id = snapshot["items"][0]["task_item_id"]
            question = snapshot["items"][0]["questions"][0]
            record_reviewed_question(db, user_id=1, payload={
                "task_item_id": item_id,
                "question_version_id": question["question_version_id"],
                "submitted_answer": "wrong-answer",
                "attempt_status": "submitted",
                "audit_decision": "revise",
                "audit_status": "completed",
            })
            db.commit()
            progress = daily_task_progress(db, user_id=1, payload={
                "host_task_id": "TASK_3",
                "host_task_version": 1,
            })
            self.assertEqual(progress["status"], "completed")

    def test_sync_replay_does_not_reselect_questions(self):
        with self.session_factory() as db:
            payload = {
                "host_task_id": "TASK_4",
                "host_task_version": 1,
                "items": [{"kp_id": "KP_1", "required_question_count": 2}],
            }
            first = upsert_daily_task_snapshot(db, user_id=1, payload=payload)
            second = upsert_daily_task_snapshot(db, user_id=1, payload=payload)
            snapshot_count = db.execute(text("SELECT COUNT(*) FROM daily_task_question_snapshots")).scalar()
            self.assertEqual(len(first["items"][0]["questions"]), 2)
            self.assertEqual(len(second["items"][0]["questions"]), 2)
            self.assertEqual(snapshot_count, 2)

    def test_snapshot_preserves_real_question_options_and_scrubs_answers(self):
        with self.session_factory() as db:
            payload = {
                "host_task_id": "TASK_5",
                "host_task_version": 1,
                "items": [{"kp_id": "KP_1", "required_question_count": 1}],
            }
            snapshot = upsert_daily_task_snapshot(db, user_id=1, payload=payload)
            question = snapshot["items"][0]["questions"][0]
            self.assertEqual(question["options_snapshot_json"], ["A", "B"])
            self.assertNotIn("answer_snapshot", question)
            self.assertNotIn("rubric_snapshot", question)

    def test_video_only_task_completes_via_html5_threshold_and_iframe_confirmation(self):
        with self.session_factory() as db:
            payload = {
                "host_task_id": "TASK_6",
                "host_task_version": 1,
                "items": [
                    {
                        "task_item_id": "TASK_6_HTML5",
                        "item_kind": "video",
                        "resource_ref": {"url": "https://example.test/video.mp4", "start_seconds": 0, "end_seconds": 10},
                        "completion_policy": {"policy": "html5_coverage", "coverage_threshold": 0.9},
                    },
                    {
                        "task_item_id": "TASK_6_IFRAME",
                        "item_kind": "video",
                        "resource_ref": {"provider": "bilibili", "start_seconds": 0, "end_seconds": 10},
                        "completion_policy": {"policy": "iframe_focus_and_confirmation", "coverage_threshold": 0.9},
                    },
                ],
            }
            snapshot = upsert_daily_task_snapshot(db, user_id=1, payload=payload)

            html5 = record_video_evidence(db, user_id=1, payload={
                "task_item_id": "TASK_6_HTML5",
                "mode": "html5",
                "watched_intervals": [[0, 9]],
            })
            self.assertEqual(html5["status"], "completed")
            progress = daily_task_progress(db, user_id=1, payload={
                "host_task_id": "TASK_6",
                "host_task_version": 1,
            })
            self.assertEqual(progress["status"], "in_progress")

            iframe = record_video_evidence(db, user_id=1, payload={
                "task_item_id": "TASK_6_IFRAME",
                "mode": "iframe",
                "active_seconds": 9,
            })
            self.assertEqual(iframe["status"], "pending")
            confirmed = confirm_iframe_video(db, user_id=1, payload={
                "task_item_id": "TASK_6_IFRAME",
                "confirmed": True,
            })
            self.assertEqual(confirmed["code"], 200)
            progress = daily_task_progress(db, user_id=1, payload={
                "host_task_id": "TASK_6",
                "host_task_version": 1,
            })
            self.assertEqual(progress["status"], "completed")

    def test_html5_video_completion_and_iframe_confirmation(self):
        with self.session_factory() as db:
            payload = {
                "host_task_id": "TASK_5",
                "host_task_version": 1,
                "items": [{
                    "task_item_id": "TASK_5_IFRAME",
                    "item_kind": "video",
                    "resource_ref": {"provider": "bilibili", "start_seconds": 0, "end_seconds": 10},
                    "completion_policy": {"policy": "iframe_focus_and_confirmation", "coverage_threshold": 0.9},
                }],
            }
            snapshot = upsert_daily_task_snapshot(db, user_id=1, payload=payload)
            item_id = snapshot["items"][0]["task_item_id"]
            iframe = record_video_evidence(db, user_id=1, payload={
                "task_item_id": item_id,
                "mode": "iframe",
                "active_seconds": 2,
            })
            self.assertEqual(iframe["status"], "pending")
            confirmed = confirm_iframe_video(db, user_id=1, payload={
                "task_item_id": item_id,
            })
            self.assertEqual(confirmed["code"], 409)


if __name__ == "__main__":
    unittest.main()
