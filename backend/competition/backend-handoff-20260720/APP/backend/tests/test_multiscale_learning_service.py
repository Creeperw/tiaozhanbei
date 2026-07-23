import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.multiscale_learning_service import (
    HARD_CONSTRAINT_ORDER,
    build_multiscale_state,
    build_path_candidates,
)


def approved_plan_context(*, daily_minutes: int = 30) -> dict:
    return {
        "daily_available_minutes": daily_minutes,
        "long_term_plan": {
            "plan_id": "LONG_1",
            "content": "完成中医基础阶段",
            "status": "active",
            "planning_route": {
                "planning_status": "approved_route",
                "route_id": "ROUTE_1",
                "route_version": 1,
                "route_status": "approved",
                "goal_type": "credential",
                "goal_name": "中医执业医师资格考试",
                "sources": [
                    {
                        "source_id": "TEXTBOOK_ROUTE_1",
                        "source_type": "official_exam_reference",
                        "title": "正式考试教材路线",
                    }
                ],
                "phases": [
                    {
                        "phase_id": "PHASE_1",
                        "name": "中医基础",
                        "objective": "掌握基础理论",
                        "books": ["中医基础理论"],
                        "learning_focus": ["阴阳学说"],
                        "exit_evidence": ["完成基础测评"],
                        "source_refs": ["TEXTBOOK_ROUTE_1"],
                    }
                ],
            },
            "textbook_selection": {
                "route_id": "ROUTE_1",
                "route_version": 1,
                "stage_id": "PHASE_1",
                "stage_name": "中医基础",
                "books": ["中医基础理论"],
                "reason": "当前批准阶段",
            },
        },
        "short_term_plan": {
            "plan_id": "SHORT_1",
            "long_term_plan_id": "LONG_1",
            "content": "本周学习阴阳学说",
            "status": "active",
            "short_term_focus": {
                "focus_type": "knowledge_point",
                "focus_label": "阴阳学说",
                "knowledge_point_ids": ["KP_1"],
            },
            "textbook_selection": {
                "route_id": "ROUTE_1",
                "route_version": 1,
                "stage_id": "PHASE_1",
                "stage_name": "中医基础",
                "books": ["中医基础理论"],
                "reason": "当前批准阶段",
            },
        },
        "learning_task": {
            "task_id": "TASK_1",
            "short_term_plan_id": "SHORT_1",
            "task_content": "完成阴阳学说练习",
            "focus_knowledge_points": ["KP_1"],
            "estimated_minutes": 20,
            "status": "pending",
        },
    }


class MultiScaleLearningServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Session()
        self.db.add_all(
            [
                database.UserModel(
                    id=1,
                    username="multiscale-learner",
                    email="multiscale@example.com",
                    hashed_password="x",
                ),
                database.UserModel(
                    id=2,
                    username="new-learner",
                    email="new@example.com",
                    hashed_password="x",
                ),
                database.LearningUserProfile(
                    user_id=1,
                    user_name="林同学",
                    goals_json='{"target_exam_or_course":"中医执业医师资格考试"}',
                    user_preference_json='{"resource_preference":["knowledge_card"]}',
                    daily_available_minutes=30,
                ),
                database.LongTermPlan(
                    plan_id="LONG_1",
                    user_id=1,
                    content='{"current_stage_id":"PHASE_1"}',
                    status="active",
                ),
                database.ShortTermPlan(
                    plan_id="SHORT_1",
                    user_id=1,
                    long_term_plan_id="LONG_1",
                    content="本周学习阴阳学说",
                    status="active",
                ),
                database.KnowledgePoint(
                    kp_id="KP_1",
                    name="阴阳学说",
                    source="approved_textbook",
                ),
                database.LearningTask(
                    task_id="TASK_1",
                    user_id=1,
                    short_term_plan_id="SHORT_1",
                    task_type="learning",
                    kp_ids_json='["KP_1"]',
                    resource_ids_json='["RESOURCE_NO_DIFFICULTY"]',
                    task_content="完成阴阳学说练习",
                    estimated_minutes=20,
                    status="pending",
                ),
                database.KnowledgeMasteryState(
                    mastery_state_id="MASTER_1",
                    learner_id=1,
                    kp_id="KP_1",
                    mastery_score=40,
                    mastery_confidence=0.8,
                    attempt_count=4,
                ),
                database.TeachingResource(
                    resource_id="RESOURCE_NO_DIFFICULTY",
                    title="阴阳学说教材节选",
                    resource_type="textbook_excerpt",
                    kp_ids_json='["KP_1"]',
                    source="approved_textbook",
                    status="active",
                ),
            ]
        )
        now = datetime.utcnow()
        self.db.add_all(
            [
                database.LearningQuestion(
                    question_id="Q_RECENT",
                    question_content="阴阳的基本关系",
                    difficulty=2,
                    kp_ids_json='["KP_1"]',
                ),
                database.LearningQuestion(
                    question_id="Q_OLD",
                    question_content="旧题",
                    difficulty=2,
                    kp_ids_json='["KP_1"]',
                ),
                database.LearningQuestionAttempt(
                    attempt_id="ATTEMPT_RECENT",
                    user_id=1,
                    question_id="Q_RECENT",
                    task_id="TASK_1",
                    is_correct=True,
                    response_time_seconds=90,
                    answered_at=now - timedelta(days=2),
                ),
                database.LearningQuestionAttempt(
                    attempt_id="ATTEMPT_OLD",
                    user_id=1,
                    question_id="Q_OLD",
                    task_id="TASK_1",
                    is_correct=False,
                    response_time_seconds=300,
                    answered_at=now - timedelta(days=20),
                ),
                database.LearnerKPReviewState(
                    review_state_id="REVIEW_1",
                    learner_id=1,
                    kp_id="KP_1",
                    retention_estimate=0.4,
                    next_review_at=now - timedelta(hours=1),
                    status="active",
                ),
                database.MistakeRecord(
                    user_id=1,
                    question_id="Q_RECENT",
                    kp_ids_json='["KP_1"]',
                    error_type="概念混淆",
                    created_at=now - timedelta(days=2),
                ),
                database.LearningFocusSession(
                    focus_session_id="FOCUS_RECENT",
                    user_id=1,
                    task_id="TASK_1",
                    resource_type="textbook_excerpt",
                    resource_id="RESOURCE_NO_DIFFICULTY",
                    status="completed",
                    active_seconds=1200,
                    started_at=now - timedelta(days=2),
                    ended_at=now - timedelta(days=2),
                ),
            ]
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_new_user_state_does_not_invent_mastery_or_accuracy(self) -> None:
        state = build_multiscale_state(self.db, 2, plan_context={})

        self.assertFalse(state["micro"]["question_accuracy"]["available"])
        self.assertIsNone(state["micro"]["question_accuracy"]["value"])
        self.assertIn(
            "no_question_attempts",
            state["micro"]["question_accuracy"]["unavailable_reason"],
        )
        self.assertFalse(state["micro"]["average_mastery"]["available"])
        self.assertIsNone(state["micro"]["average_mastery"]["value"])

    def test_behavior_data_uses_the_selected_7_30_90_day_window(self) -> None:
        seven_day = build_multiscale_state(
            self.db, 1, approved_plan_context(), window_days=7
        )
        thirty_day = build_multiscale_state(
            self.db, 1, approved_plan_context(), window_days=30
        )

        self.assertEqual(seven_day["micro"]["question_accuracy"]["value"], 1.0)
        self.assertEqual(thirty_day["micro"]["question_accuracy"]["value"], 0.5)
        self.assertEqual(seven_day["data_quality"]["window_days"], 7)
        self.assertEqual(thirty_day["data_quality"]["window_days"], 30)
        with self.assertRaisesRegex(ValueError, "7, 30, 90"):
            build_multiscale_state(self.db, 1, approved_plan_context(), window_days=14)

    def test_state_digest_is_canonical_json_sha256_prefix(self) -> None:
        state = build_multiscale_state(
            self.db, 1, approved_plan_context(), window_days=7
        )
        digest_payload = {key: value for key, value in state.items() if key != "state_digest"}
        expected = hashlib.sha256(
            json.dumps(
                digest_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]

        self.assertEqual(state["state_digest"], expected)
        self.assertEqual(len(state["state_digest"]), 24)

    def test_state_sources_are_traceable_and_builder_does_not_persist_a_snapshot(self) -> None:
        counts_before = {
            table.name: self.db.query(mapper.class_).count()
            for mapper in database.Base.registry.mappers
            if (table := mapper.local_table).name
            in {
                "long_term_plan",
                "short_term_plan",
                "learning_task",
                "question_attempt",
                "knowledge_mastery_states",
            }
        }
        state = build_multiscale_state(self.db, 1, approved_plan_context())
        counts_after = {
            table.name: self.db.query(mapper.class_).count()
            for mapper in database.Base.registry.mappers
            if (table := mapper.local_table).name in counts_before
        }

        self.assertEqual(counts_after, counts_before)
        self.assertTrue(state["source_refs"])
        self.assertTrue(
            all(item.get("table") and item.get("source_id") for item in state["source_refs"])
        )

    def test_daily_candidates_are_blocked_without_short_term_plan(self) -> None:
        context = approved_plan_context()
        context["short_term_plan"] = {}
        context["learning_task"] = {}
        state = build_multiscale_state(self.db, 1, plan_context=context)
        candidates = build_path_candidates(
            self.db,
            1,
            state=state,
            scope="daily_task",
            plan_context=context,
        )

        self.assertTrue(candidates["items"])
        self.assertTrue(all(not item["eligible"] for item in candidates["items"]))
        self.assertTrue(
            any(
                "short_term_plan_required" in item["blocked_reasons"]
                for item in candidates["items"]
            )
        )

    def test_candidates_can_use_the_supplied_state_without_repassing_plan_context(self) -> None:
        context = approved_plan_context()
        context["short_term_plan"] = {}
        context["learning_task"] = {}
        state = build_multiscale_state(self.db, 2, plan_context=context)

        candidates = build_path_candidates(
            self.db,
            2,
            state=state,
            scope="daily_task",
        )

        self.assertTrue(candidates["items"])
        self.assertTrue(
            any(
                "short_term_plan_required" in item["blocked_reasons"]
                for item in candidates["items"]
            )
        )

    def test_hard_constraints_run_in_fixed_order_and_override_score(self) -> None:
        context = approved_plan_context(daily_minutes=1)
        state = build_multiscale_state(self.db, 1, plan_context=context)
        candidates = build_path_candidates(
            self.db,
            1,
            state=state,
            scope="daily_task",
            plan_context=context,
        )
        task = next(
            item for item in candidates["items"] if "task:TASK_1" in item["source_refs"]
        )

        self.assertEqual(
            [item["key"] for item in task["hard_constraint_results"]],
            list(HARD_CONSTRAINT_ORDER),
        )
        self.assertFalse(task["eligible"])
        self.assertIn("time_budget_exceeded", task["blocked_reasons"])
        self.assertGreaterEqual(task["score"], 0)

    def test_1440_minute_budget_is_a_cap_not_a_fill_target(self) -> None:
        context = approved_plan_context(daily_minutes=1440)
        state = build_multiscale_state(self.db, 1, plan_context=context)
        candidates = build_path_candidates(
            self.db,
            1,
            state=state,
            scope="daily_task",
            plan_context=context,
        )

        self.assertTrue(candidates["items"])
        self.assertTrue(
            all(item["estimated_minutes"] < 1440 for item in candidates["items"])
        )

    def test_one_hundred_due_reviews_are_prioritized_but_capacity_limited(self) -> None:
        now = datetime.utcnow()
        for index in range(2, 101):
            kp_id = f"KP_DUE_{index}"
            self.db.add(
                database.KnowledgePoint(
                    kp_id=kp_id,
                    name=f"到期知识点 {index}",
                    source="approved_textbook",
                )
            )
            self.db.add(
                database.LearnerKPReviewState(
                    review_state_id=f"REVIEW_{index}",
                    learner_id=1,
                    kp_id=kp_id,
                    retention_estimate=0.3,
                    next_review_at=now - timedelta(hours=index),
                    status="active",
                )
            )
        self.db.commit()
        context = approved_plan_context(daily_minutes=1440)
        state = build_multiscale_state(self.db, 1, plan_context=context)
        candidates = build_path_candidates(
            self.db,
            1,
            state=state,
            scope="daily_task",
            plan_context=context,
            limit=30,
            daily_capacity=12,
        )
        due_items = [
            item
            for item in candidates["items"]
            if item["recommended_action"] == "review"
        ]

        self.assertLessEqual(len(due_items), 12)
        self.assertTrue(due_items)
        self.assertTrue(
            all(
                item["score_components"]["retention_benefit"]["value"] > 0
                for item in due_items
            )
        )

    def test_missing_difficulty_is_not_scored_as_perfect(self) -> None:
        context = approved_plan_context()
        state = build_multiscale_state(self.db, 1, plan_context=context)
        candidates = build_path_candidates(
            self.db,
            1,
            state=state,
            scope="daily_task",
            plan_context=context,
        )
        candidate = next(
            item
            for item in candidates["items"]
            if "resource:RESOURCE_NO_DIFFICULTY" in item["source_refs"]
        )

        self.assertFalse(
            candidate["score_components"]["difficulty_fit"]["available"]
        )
        self.assertIsNone(
            candidate["score_components"]["difficulty_fit"]["value"]
        )
        self.assertIn(
            "difficulty",
            candidate["score_components"]["difficulty_fit"]["unavailable_reason"],
        )

    def test_candidates_retain_source_and_evidence_references(self) -> None:
        context = approved_plan_context()
        state = build_multiscale_state(self.db, 1, plan_context=context)
        candidates = build_path_candidates(
            self.db,
            1,
            state=state,
            scope="daily_task",
            plan_context=context,
        )

        self.assertTrue(candidates["items"])
        self.assertTrue(all(item["source_refs"] for item in candidates["items"]))
        self.assertTrue(
            all(
                item["evidence_refs"]
                for item in candidates["items"]
                if item["eligible"]
            )
        )


if __name__ == "__main__":
    unittest.main()
