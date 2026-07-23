import copy
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, event, update
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
                        "status": "approved",
                    }
                ],
                "phases": [
                    {
                        "phase_id": "PHASE_1",
                        "name": "中医基础",
                        "objective": "掌握基础理论",
                        "books": ["中医基础理论"],
                        "learning_focus": ["阴阳学说"],
                        "knowledge_point_ids": ["KP_1"],
                        "prerequisites": [],
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
        digest_payload = {
            key: value
            for key, value in state.items()
            if key not in {"state_digest", "state_id", "generated_at"}
        }
        digest_payload["data_quality"] = {
            key: value
            for key, value in digest_payload["data_quality"].items()
            if key not in {"window_start", "window_end"}
        }
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

    def test_digest_is_stable_for_identical_source_state(self) -> None:
        first = build_multiscale_state(
            self.db, 1, approved_plan_context(), window_days=7
        )
        second = build_multiscale_state(
            self.db, 1, approved_plan_context(), window_days=7
        )

        self.assertNotEqual(first["state_id"], second["state_id"])
        self.assertEqual(first["state_digest"], second["state_digest"])

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

    def test_full_supplied_state_preserves_parent_plan_chain(self) -> None:
        state = build_multiscale_state(
            self.db, 1, plan_context=approved_plan_context()
        )

        candidates = build_path_candidates(
            self.db, 1, state=state, scope="daily_task"
        )
        parent_results = [
            result
            for item in candidates["items"]
            for result in item["hard_constraint_results"]
            if result["key"] == "parent_plan_exists"
        ]

        self.assertTrue(parent_results)
        self.assertTrue(all(result["passed"] for result in parent_results))

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

    def test_parent_gate_rejects_wrong_chain_and_inactive_plan(self) -> None:
        context = approved_plan_context()
        context["short_term_plan"]["long_term_plan_id"] = "LONG_OTHER"
        context["short_term_plan"]["status"] = "retired"
        state = build_multiscale_state(self.db, 1, plan_context=context)

        candidates = build_path_candidates(
            self.db, 1, state=state, scope="daily_task", plan_context=context
        )

        self.assertTrue(candidates["items"])
        self.assertTrue(all(not item["eligible"] for item in candidates["items"]))
        parent_results = [
            next(
                result
                for result in item["hard_constraint_results"]
                if result["key"] == "parent_plan_exists"
            )
            for item in candidates["items"]
        ]
        self.assertTrue(all(not result["passed"] for result in parent_results))
        self.assertTrue(
            any("parent_mismatch" in result["reason"] for result in parent_results)
        )

    def test_prerequisite_gate_uses_completed_courses_not_caller_boolean(self) -> None:
        context = approved_plan_context()
        context["prerequisite_satisfied"] = True
        context["long_term_plan"]["planning_route"]["phases"][0][
            "prerequisites"
        ] = ["中药学"]
        state = build_multiscale_state(self.db, 1, plan_context=context)

        candidates = build_path_candidates(
            self.db, 1, state=state, scope="daily_task", plan_context=context
        )
        result = next(
            value
            for value in candidates["items"][0]["hard_constraint_results"]
            if value["key"] == "prerequisite_satisfied"
        )

        self.assertFalse(result["passed"])
        self.assertIn("中药学", result["reason"])

    def test_unapproved_and_not_official_sources_are_not_trusted(self) -> None:
        self.db.query(database.TeachingResource).filter(
            database.TeachingResource.resource_id == "RESOURCE_NO_DIFFICULTY"
        ).update({"source": "unapproved_not_official"})
        self.db.commit()
        context = approved_plan_context()
        state = build_multiscale_state(self.db, 1, plan_context=context)

        candidates = build_path_candidates(
            self.db, 1, state=state, scope="daily_task", plan_context=context
        )
        candidate = next(
            item
            for item in candidates["items"]
            if "resource:RESOURCE_NO_DIFFICULTY" in item["source_refs"]
        )
        trusted = next(
            result
            for result in candidate["hard_constraint_results"]
            if result["key"] == "trusted_source"
        )

        self.assertFalse(trusted["passed"])

    def test_kp_outside_approved_stage_fails_stage_mapping(self) -> None:
        self.db.add_all(
            [
                database.KnowledgePoint(
                    kp_id="KP_OUTSIDE",
                    name="阶段外知识点",
                    source="approved_textbook",
                ),
                database.KnowledgeMasteryState(
                    mastery_state_id="MASTER_OUTSIDE",
                    learner_id=1,
                    kp_id="KP_OUTSIDE",
                    mastery_score=20,
                    mastery_confidence=0.8,
                    attempt_count=2,
                ),
                database.TeachingResource(
                    resource_id="RESOURCE_OUTSIDE",
                    title="阶段外教材",
                    resource_type="textbook_excerpt",
                    kp_ids_json='["KP_OUTSIDE"]',
                    source="approved_textbook",
                    status="active",
                ),
            ]
        )
        self.db.commit()
        context = approved_plan_context()
        context["short_term_plan"]["short_term_focus"][
            "knowledge_point_ids"
        ].append("KP_OUTSIDE")
        state = build_multiscale_state(self.db, 1, plan_context=context)

        candidates = build_path_candidates(
            self.db,
            1,
            state=state,
            scope="daily_task",
            plan_context=context,
            limit=30,
        )
        candidate = next(
            item
            for item in candidates["items"]
            if "resource:RESOURCE_OUTSIDE" in item["source_refs"]
        )
        mapping = next(
            result
            for result in candidate["hard_constraint_results"]
            if result["key"] == "approved_stage_mapping"
        )

        self.assertFalse(mapping["passed"])

    def test_book_outside_approved_stage_fails_stage_mapping(self) -> None:
        context = approved_plan_context()
        context["long_term_plan"]["textbook_selection"]["books"] = ["伪造教材"]
        context["short_term_plan"]["textbook_selection"]["books"] = ["伪造教材"]
        state = build_multiscale_state(self.db, 1, plan_context=context)

        candidates = build_path_candidates(
            self.db, 1, state=state, scope="daily_task", plan_context=context
        )
        mapping = next(
            result
            for result in candidates["items"][0]["hard_constraint_results"]
            if result["key"] == "approved_stage_mapping"
        )

        self.assertFalse(mapping["passed"])

    def test_missing_goal_cannot_be_overridden_as_aligned(self) -> None:
        context = approved_plan_context()
        context["goal_route_aligned"] = True
        state = build_multiscale_state(self.db, 2, plan_context=context)

        candidates = build_path_candidates(
            self.db, 2, state=state, scope="daily_task", plan_context=context
        )
        alignment = next(
            result
            for result in candidates["items"][0]["hard_constraint_results"]
            if result["key"] == "goal_route_alignment"
        )

        self.assertFalse(alignment["passed"])
        self.assertEqual(alignment["reason"], "learner_goal_missing")

    def test_low_data_with_unknown_difficulty_is_blocked(self) -> None:
        self.db.add(
            database.LearningUserProfile(
                user_id=2,
                goals_json='{"target_exam_or_course":"中医执业医师资格考试"}',
                daily_available_minutes=30,
            )
        )
        self.db.commit()
        context = approved_plan_context()
        state = build_multiscale_state(self.db, 2, plan_context=context)

        candidates = build_path_candidates(
            self.db, 2, state=state, scope="daily_task", plan_context=context
        )
        low_data_result = next(
            result
            for result in candidates["items"][0]["hard_constraint_results"]
            if result["key"] == "low_data_protection"
        )

        self.assertFalse(low_data_result["passed"])
        self.assertIn("difficulty_unknown", low_data_result["reason"])

    def test_missing_estimated_minutes_stays_unknown_and_is_blocked(self) -> None:
        self.db.add(
            database.LearningTask(
                task_id="TASK_NO_DURATION",
                user_id=1,
                short_term_plan_id="SHORT_1",
                task_type="learning",
                kp_ids_json='["KP_1"]',
                task_content="未知用时任务",
                estimated_minutes=None,
                status="pending",
            )
        )
        self.db.commit()
        context = approved_plan_context()
        state = build_multiscale_state(self.db, 1, plan_context=context)

        candidates = build_path_candidates(
            self.db,
            1,
            state=state,
            scope="daily_task",
            plan_context=context,
            limit=30,
        )
        candidate = next(
            item
            for item in candidates["items"]
            if "task:TASK_NO_DURATION" in item["source_refs"]
        )

        self.assertEqual(candidate["estimated_minutes"], 0)
        self.assertIn("estimated_minutes_missing", candidate["blocked_reasons"])
        self.assertFalse(candidate["score_components"]["time_fit"]["available"])

    def test_task_completion_current_load_and_focus_use_event_boundaries(self) -> None:
        now = datetime.utcnow()
        self.db.add_all(
            [
                database.LearningTask(
                    task_id="TASK_COMPLETED_IN_WINDOW",
                    user_id=1,
                    short_term_plan_id="SHORT_1",
                    task_type="learning",
                    kp_ids_json='["KP_1"]',
                    task_content="窗口内完成",
                    estimated_minutes=15,
                    status="completed",
                    created_at=now - timedelta(days=40),
                    due_at=now - timedelta(days=1),
                    completed_at=now - timedelta(days=2),
                ),
                database.LearningTask(
                    task_id="TASK_OLD_PENDING",
                    user_id=1,
                    short_term_plan_id="SHORT_1",
                    task_type="learning",
                    kp_ids_json='["KP_1"]',
                    task_content="窗口前创建仍待完成",
                    estimated_minutes=15,
                    status="pending",
                    created_at=now - timedelta(days=40),
                ),
                database.LearningFocusSession(
                    focus_session_id="FOCUS_BOUNDARY",
                    user_id=1,
                    task_id="TASK_1",
                    status="completed",
                    active_seconds=2 * 24 * 60 * 60,
                    started_at=now - timedelta(days=8),
                    ended_at=now - timedelta(days=6),
                ),
            ]
        )
        self.db.commit()

        state = build_multiscale_state(
            self.db, 1, approved_plan_context(), window_days=7
        )

        self.assertEqual(state["meso"]["task_completion_rate"]["value"], 1.0)
        self.assertEqual(state["micro"]["current_task_load"]["value"], 35)
        self.assertAlmostEqual(
            state["micro"]["recent_focus_minutes"]["value"],
            24 * 60 + 20,
            delta=1,
        )

    def test_nullable_mastery_and_partial_task_duration_are_unavailable(self) -> None:
        self.db.execute(
            update(database.KnowledgeMasteryState)
            .where(database.KnowledgeMasteryState.mastery_state_id == "MASTER_1")
            .values(mastery_score=None, mastery_confidence=None)
        )
        self.db.add(
            database.LearningTask(
                task_id="TASK_PARTIAL_DURATION",
                user_id=1,
                short_term_plan_id="SHORT_1",
                task_type="learning",
                kp_ids_json='["KP_1"]',
                task_content="缺失时长",
                estimated_minutes=None,
                status="pending",
            )
        )
        self.db.commit()

        state = build_multiscale_state(
            self.db, 1, approved_plan_context(), window_days=7
        )

        self.assertFalse(state["micro"]["average_mastery"]["available"])
        self.assertIsNone(
            state["micro"]["mastery_by_knowledge_point"][0]["mastery"]
        )
        self.assertIsNone(
            state["micro"]["mastery_by_knowledge_point"][0]["confidence"]
        )
        self.assertFalse(state["micro"]["current_task_load"]["available"])
        self.assertIn(
            "duration_missing",
            state["micro"]["current_task_load"]["unavailable_reason"],
        )

    def test_supplied_state_is_validated_for_owner_and_digest(self) -> None:
        state = build_multiscale_state(self.db, 1, approved_plan_context())
        cross_user = copy.deepcopy(state)
        cross_user["learner_id"] = "2"
        tampered = copy.deepcopy(state)
        tampered["micro"]["recent_resource_ids"].append("FOREIGN_RESOURCE")

        with self.assertRaisesRegex(ValueError, "learner"):
            build_path_candidates(
                self.db, 1, state=cross_user, scope="daily_task"
            )
        with self.assertRaisesRegex(ValueError, "digest"):
            build_path_candidates(
                self.db, 1, state=tampered, scope="daily_task"
            )
        with self.assertRaises(ValueError):
            build_path_candidates(
                self.db, 1, state={"learner_id": "1"}, scope="daily_task"
            )

    def test_metric_sources_use_real_rows_and_plan_context_provenance(self) -> None:
        context = approved_plan_context()
        state = build_multiscale_state(self.db, 1, context)
        source_ids = {item["source_id"] for item in state["source_refs"]}

        self.assertIn("plan_context:long_term_plan:LONG_1", source_ids)
        self.assertIn("plan_context:short_term_plan:SHORT_1", source_ids)
        self.assertNotIn("long_term_plan:LONG_1", source_ids)
        self.assertTrue(
            all(
                metric["source_refs"]
                for metric in (
                    state["micro"]["question_accuracy"],
                    state["micro"]["average_mastery"],
                    state["micro"]["recent_focus_minutes"],
                )
                if metric["available"]
            )
        )
        candidates = build_path_candidates(
            self.db, 1, state=state, scope="daily_task", plan_context=context
        )
        resource = next(
            item
            for item in candidates["items"]
            if "resource:RESOURCE_NO_DIFFICULTY" in item["source_refs"]
        )
        self.assertEqual(
            resource["score_components"]["difficulty_fit"]["source_refs"],
            ["resource:RESOURCE_NO_DIFFICULTY"],
        )
        task = next(
            item for item in candidates["items"] if "task:TASK_1" in item["source_refs"]
        )
        self.assertIn(
            "knowledge_mastery_states:MASTER_1",
            task["score_components"]["learning_gain"]["source_refs"],
        )

    def test_time_fit_is_one_after_passing_time_gate(self) -> None:
        context = approved_plan_context(daily_minutes=30)
        state = build_multiscale_state(self.db, 1, context)
        candidates = build_path_candidates(
            self.db, 1, state=state, scope="daily_task", plan_context=context
        )
        task = next(
            item for item in candidates["items"] if "task:TASK_1" in item["source_refs"]
        )

        self.assertEqual(task["score_components"]["time_fit"]["value"], 1.0)

    def test_repetition_penalty_uses_recent_kp_overlap_ratio(self) -> None:
        context = approved_plan_context()
        state = build_multiscale_state(self.db, 1, context)
        candidates = build_path_candidates(
            self.db, 1, state=state, scope="daily_task", plan_context=context
        )
        task = next(
            item for item in candidates["items"] if "task:TASK_1" in item["source_refs"]
        )

        self.assertEqual(
            task["score_components"]["repetition_penalty"]["value"], 1.0
        )

    def test_source_task_at_or_above_1440_cannot_pass_time_gate(self) -> None:
        self.db.add_all(
            [
                database.LearningTask(
                    task_id="TASK_1440",
                    user_id=1,
                    short_term_plan_id="SHORT_1",
                    task_type="learning",
                    kp_ids_json='["KP_1"]',
                    task_content="全天任务",
                    estimated_minutes=1440,
                    status="pending",
                ),
                database.LearningTask(
                    task_id="TASK_OVER_1440",
                    user_id=1,
                    short_term_plan_id="SHORT_1",
                    task_type="learning",
                    kp_ids_json='["KP_1"]',
                    task_content="超长任务",
                    estimated_minutes=1600,
                    status="pending",
                ),
            ]
        )
        self.db.commit()
        context = approved_plan_context(daily_minutes=1440)
        state = build_multiscale_state(self.db, 1, context)

        candidates = build_path_candidates(
            self.db,
            1,
            state=state,
            scope="daily_task",
            plan_context=context,
            limit=30,
        )
        long_tasks = [
            item
            for item in candidates["items"]
            if {"task:TASK_1440", "task:TASK_OVER_1440"}.intersection(
                item["source_refs"]
            )
        ]

        self.assertEqual(len(long_tasks), 2)
        self.assertTrue(all(not item["eligible"] for item in long_tasks))
        self.assertTrue(
            all(
                "candidate_duration_must_be_less_than_1440"
                in item["blocked_reasons"]
                for item in long_tasks
            )
        )

    def test_candidate_queries_are_batched_filtered_and_bounded(self) -> None:
        for index in range(205):
            self.db.add(
                database.TeachingResource(
                    resource_id=f"IRRELEVANT_{index:03d}",
                    title="无关资源",
                    resource_type="textbook_excerpt",
                    kp_ids_json='["KP_OTHER"]',
                    source="approved_textbook",
                    status="active",
                )
            )
        self.db.add(
            database.TeachingResource(
                resource_id="ZZZ_MATCH_AFTER_200",
                title="后置匹配资源",
                resource_type="textbook_excerpt",
                kp_ids_json='["KP_1"]',
                source="approved_textbook",
                status="active",
            )
        )
        for index in range(3):
            question_id = f"Q_BATCH_{index}"
            self.db.add(
                database.LearningQuestion(
                    question_id=question_id,
                    question_content="批量难度题",
                    difficulty=2,
                    kp_ids_json='["KP_1"]',
                )
            )
            self.db.add(
                database.LearningTask(
                    task_id=f"TASK_BATCH_{index}",
                    user_id=1,
                    short_term_plan_id="SHORT_1",
                    task_type="learning",
                    kp_ids_json='["KP_1"]',
                    question_ids_json=json.dumps([question_id]),
                    task_content="批量难度任务",
                    estimated_minutes=10,
                    status="pending",
                )
            )
        self.db.commit()
        context = approved_plan_context()
        state = build_multiscale_state(self.db, 1, context)
        statements: list[str] = []

        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            candidates = build_path_candidates(
                self.db,
                1,
                state=state,
                scope="daily_task",
                plan_context=context,
                limit=30,
                daily_capacity=1,
            )
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)

        self.assertTrue(
            any(
                "resource:ZZZ_MATCH_AFTER_200" in item["source_refs"]
                for item in candidates["items"]
            )
        )
        question_selects = [
            statement
            for statement in statements
            if "FROM question " in statement
        ]
        self.assertLessEqual(len(question_selects), 1)
        review_selects = [
            statement
            for statement in statements
            if "FROM learner_kp_review_states" in statement
        ]
        self.assertTrue(any("LIMIT" in statement.upper() for statement in review_selects))

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
