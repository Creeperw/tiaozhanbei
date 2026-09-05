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
    quiz_learner_profile,
    record_reviewed_question,
    record_video_evidence,
    resolve_executable_knowledge_point,
    select_knowledge_practice_questions,
    select_quiz_questions,
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

    def test_quiz_selection_stratifies_by_real_annotated_difficulty(self):
        with self.session_factory() as db:
            # KP_2 提供 5 个难度各 2 题 + 2 道未标注题，共 12 题。
            db.add(database.KnowledgePoint(
                kp_id="KP_2", name="五行", aliases_json="[]",
                source="formal-content:test", status="active",
            ))
            for index in range(12):
                difficulty = index // 2 + 1 if index < 10 else None
                qid = f"QUIZ_Q_{index}"
                db.add(database.LearningQuestion(
                    question_id=qid, question_type="single_choice",
                    question_content=f"测验题{index}", options_json="[]",
                    answer_json='["A"]', explanation="解析",
                    difficulty=float(difficulty) if difficulty else None,
                    kp_ids_json='["KP_2"]', key_points="k", scoring_rubric="r",
                ))
                db.add(database.QuestionVersionRecord(
                    question_version_id=f"QUIZ_QV_{index}", question_id=qid,
                    version=1, question_type="single_choice", stem=f"测验题{index}",
                    answer="A", analysis="解析",
                    standard_difficulty=difficulty,
                    source_kind="formal-content:test", status="active",
                ))
                db.add(database.QuestionKPLinkRecord(
                    question_version_id=f"QUIZ_QV_{index}", kp_id="KP_2",
                    is_primary=True, status="active",
                ))
            db.flush()

            advanced = select_quiz_questions(db, ["KP_2"], "advanced", 12)
            self.assertEqual(len(advanced), 12)
            levels = sorted(
                int(row.standard_difficulty)
                for row in advanced
                if row.standard_difficulty is not None
            )
            # advanced 画像 5 难度期望 2 题：难度 4/5 每题各取 2。
            self.assertEqual(levels.count(4), 2)
            self.assertEqual(levels.count(5), 2)

            foundation = select_quiz_questions(db, ["KP_2"], "foundation", 12)
            foundation_levels = [
                int(row.standard_difficulty)
                for row in foundation
                if row.standard_difficulty is not None
            ]
            # foundation 需要 3 道难度 1，但候选池仅 2 道：缺口由无标注题补足。
            self.assertEqual(foundation_levels.count(1), 2)
            self.assertEqual(foundation_levels.count(2), 2)
            self.assertEqual(len(foundation), 12)

    def test_quiz_selection_crosses_kps_and_never_blocks_on_shortage(self):
        with self.session_factory() as db:
            # KP_1 已有 Q_1/Q_2（无难度），KP_2 提供 4 题（难度 5）。
            db.add(database.KnowledgePoint(
                kp_id="KP_2", name="五行", aliases_json="[]",
                source="formal-content:test", status="active",
            ))
            for index in range(4):
                qid = f"SHORT_Q_{index}"
                db.add(database.LearningQuestion(
                    question_id=qid, question_type="single_choice",
                    question_content=f"短缺题{index}", options_json="[]",
                    answer_json='["A"]', explanation="解析",
                    difficulty=5.0, kp_ids_json='["KP_2"]',
                    key_points="k", scoring_rubric="r",
                ))
                db.add(database.QuestionVersionRecord(
                    question_version_id=f"SHORT_QV_{index}", question_id=qid,
                    version=1, question_type="single_choice", stem=f"短缺题{index}",
                    answer="A", analysis="解析", standard_difficulty=5,
                    source_kind="formal-content:test", status="active",
                ))
                db.add(database.QuestionKPLinkRecord(
                    question_version_id=f"SHORT_QV_{index}", kp_id="KP_2",
                    is_primary=True, status="active",
                ))
            db.flush()

            # 候选共 6 题（KP_1 的 Q_1/Q_2 + KP_2 的 4 题），不足 10 不抛错。
            result = upsert_daily_task_snapshot(
                db, user_id=1, payload={
                    "host_task_id": "TASK_QUIZ",
                    "host_task_version": 1,
                    "items": [
                        {"task_item_id": "ITEM_QUIZ_MAIN", "kp_id": "KP_1", "required_question_count": 1},
                        {
                            "task_item_id": "ITEM_QUIZ",
                            "kp_id": "KP_2",
                            "required_question_count": 12,
                            "completion_policy": {
                                "policy": "frozen_question_set",
                                "quiz": True,
                                "quiz_target_count": 12,
                            },
                        },
                    ],
                },
            )
            quiz_item = next(
                item for item in result["items"]
                if item["task_item_id"] == "ITEM_QUIZ"
            )
            # 候选 6 题全部冻结，不阻塞任务。
            self.assertEqual(len(quiz_item["questions"]), 6)
            self.assertEqual(quiz_item["status"], "pending")

    def test_quiz_learner_profile_uses_accuracy_then_balanced_fallback(self):
        with self.session_factory() as db:
            self.assertEqual(quiz_learner_profile(db, 1), "balanced")
            db.add(database.LearningQuestion(
                question_id="PROF_Q", question_type="single_choice",
                question_content="画像题", options_json="[]",
                answer_json='["A"]', explanation="", difficulty=3.0,
                kp_ids_json='[]', key_points="k", scoring_rubric="r",
            ))
            db.flush()
            for index, correct in enumerate((True, True, True, True)):
                db.add(database.LearningQuestionAttempt(
                    attempt_id=f"PA_{index}",
                    user_id=1, question_id="PROF_Q", task_id=None,
                    request_id=f"PA_REQ_{index}",
                    submitted_answer_json='[]', is_correct=correct,
                ))
            db.commit()
            self.assertEqual(quiz_learner_profile(db, 1), "advanced")

    def _add_difficulty_candidates(self, db, *, kp_id: str = "KP_1"):
        for level in range(1, 6):
            for ordinal in range(2):
                question_id = f"PRACTICE_Q_{level}_{ordinal}"
                version_id = f"PRACTICE_QV_{level}_{ordinal}"
                db.add(database.LearningQuestion(
                    question_id=question_id,
                    question_type="single_choice",
                    question_content=f"难度{level}题{ordinal}",
                    options_json="[]",
                    answer_json='["A"]',
                    explanation="解析",
                    difficulty=float(level),
                    kp_ids_json=json.dumps([kp_id]),
                    key_points="k",
                    scoring_rubric="r",
                ))
                db.add(database.QuestionVersionRecord(
                    question_version_id=version_id,
                    question_id=question_id,
                    version=1,
                    question_type="single_choice",
                    stem=f"难度{level}题{ordinal}",
                    answer="A",
                    analysis="解析",
                    standard_difficulty=level,
                    source_kind="formal-content:test",
                    status="active",
                ))
                db.add(database.QuestionKPLinkRecord(
                    question_version_id=version_id,
                    kp_id=kp_id,
                    is_primary=True,
                    status="active",
                ))
        db.flush()

    def test_knowledge_practice_selects_foundation_target_difficulties(self):
        with self.session_factory() as db:
            self._add_difficulty_candidates(db)

            selected = select_knowledge_practice_questions(
                db, "KP_1", "foundation", 3
            )

            self.assertEqual(
                [row.standard_difficulty for row in selected],
                [1, 1, 2],
            )

    def test_knowledge_practice_selects_advanced_target_difficulties(self):
        with self.session_factory() as db:
            self._add_difficulty_candidates(db)

            selected = select_knowledge_practice_questions(
                db, "KP_1", "advanced", 3
            )

            self.assertEqual(
                [row.standard_difficulty for row in selected],
                [3, 4, 5],
            )

    def test_non_quiz_snapshot_uses_persisted_evaluation_profile(self):
        with self.session_factory() as db:
            self._add_difficulty_candidates(db)
            db.add(database.LearningUserProfile(
                user_id=1,
                user_group_json=json.dumps({"evaluation_profile": "foundation"}),
            ))
            db.flush()

            snapshot = upsert_daily_task_snapshot(
                db,
                user_id=1,
                payload={
                    "host_task_id": "TASK_PROFILED_PRACTICE",
                    "host_task_version": 1,
                    "items": [{
                        "task_item_id": "ITEM_PROFILED_PRACTICE",
                        "item_kind": "knowledge_practice",
                        "kp_id": "KP_1",
                        "required_question_count": 3,
                        "completion_policy": {"policy": "frozen_question_set"},
                    }],
                },
            )

            version_ids = [
                question["question_version_id"]
                for question in snapshot["items"][0]["questions"]
            ]
            rows = (
                db.query(database.QuestionVersionRecord)
                .filter(database.QuestionVersionRecord.question_version_id.in_(version_ids))
                .all()
            )
            levels_by_id = {
                row.question_version_id: row.standard_difficulty for row in rows
            }
            self.assertEqual(
                [levels_by_id[version_id] for version_id in version_ids],
                [1, 1, 2],
            )


if __name__ == "__main__":
    unittest.main()
