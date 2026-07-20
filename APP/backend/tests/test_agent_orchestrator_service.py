import json
import unittest
from unittest.mock import Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.agent_contracts import AgentExecutionPlan, LearnerContextBrief, ReviewDecision
from APP.backend.agent_orchestrator_service import (
    OrchestrationRequest,
    OrchestrationTaskContext,
    _tool_kwargs,
    run_agent_orchestration,
)
from APP.backend.tool_runtime import ToolDefinition, ToolRuntime


class ConstraintCaptureRuntime(ToolRuntime):
    def __init__(self):
        super().__init__()
        self.captured_generation_request = {}


def build_constraint_capture_runtime():
    runtime = ConstraintCaptureRuntime()
    runtime.register(
        ToolDefinition(
            "build_learner_context_brief",
            frozenset({"memory_agent"}),
            lambda **kwargs: LearnerContextBrief(
                learner_id="1",
                learner_group="学历教育群体",
                goal="四君子汤复习",
                source_scope="memory_agent",
                source_id="learner:1",
                kp_ids=[],
                confidence=0.9,
            ),
        )
    )
    runtime.register(
        ToolDefinition(
            "build_diagnosis_snapshot",
            frozenset({"diagnosis_agent"}),
            lambda **kwargs: {
                "diagnosis_id": "diag_1",
                "stage_id": "T0",
                "stage_name": "稳定学习",
                "summary": "按当前目标生成资料",
            },
        )
    )
    runtime.register(
        ToolDefinition(
            "build_evidence_pack",
            frozenset({"knowledge_base_agent"}),
            lambda **kwargs: {
                "pack_id": "EP_1",
                "source_scope": "public",
                "source_id": "SRC_1",
                "items": [{"source_id": "SRC_1", "summary": "四君子汤证据"}],
                "resolved_kp_ids": ["KP_FJ_001"],
            },
        )
    )

    def generate_handout(**kwargs):
        runtime.captured_generation_request = dict(kwargs["request"])
        return {
            "artifact_type": "handout",
            "title": "四君子汤讲义",
            "content": {"sections": []},
            "source_scope": "expert_handout",
            "source_id": "ART_1",
            "kp_ids": ["KP_FJ_001"],
        }

    runtime.register(ToolDefinition("generate_handout", frozenset({"expert_handout"}), generate_handout))
    runtime.register(
        ToolDefinition(
            "audit_artifact",
            frozenset({"audit_agent"}),
            lambda **kwargs: ReviewDecision(
                decision="pass",
                reason="证据与产物一致",
                source_scope="audit_agent",
                source_id="ART_1",
                confidence=0.95,
            ),
        )
    )
    return runtime


class AgentOrchestratorServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
            db.add(
                database.UserProfile(
                    user_id=1,
                    display_name="Alice",
                    constitution="学历教育群体",
                    health_goals="两周内掌握四君子汤与脾胃气虚证",
                    diet_restrictions="每天 45 分钟",
                    exercise_preferences="知识卡、短练、错题复盘",
                    medical_history="证型到方剂匹配薄弱",
                )
            )
            db.add(database.KnowledgePoint(kp_id="KP_FJ_001", name="四君子汤", aliases_json='["四君子"]'))
            db.add(database.KnowledgePoint(kp_id="KP_ZD_021", name="脾胃气虚证", aliases_json="[]"))
            db.add(
                database.QuestionBankItem(
                    question_id="Q_FJ_001",
                    stem="四君子汤主治哪类证候？",
                    answer="脾胃气虚证",
                    analysis="四君子汤益气健脾，主治脾胃气虚证。",
                    kp_ids_json='["KP_FJ_001", "KP_ZD_021"]',
                    difficulty=2.0,
                    quality_score=0.92,
                )
            )
            db.add(
                database.LearnerKnowledgeMastery(
                    user_id=1,
                    kp_id="KP_FJ_001",
                    mastery=0.58,
                    confidence=0.74,
                    wrong_count=2,
                    mastery_status="weak",
                )
            )
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_orchestrates_personalized_resource_generation_with_trace(self):
        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="我想学习四君子汤，并做几道题巩固"),
            )

            self.assertTrue(result["run_id"].startswith("run_"))
            self.assertEqual(result["task_type"], "resource_generation")
            self.assertIn("execution_plan", result)
            self.assertIn("steps", result)
            self.assertIn("final", result)
            self.assertIn("learner_context", result["final"])
            self.assertIn("diagnosis", result["final"])
            self.assertIn("evidence_pack", result["final"])
            self.assertIn("artifact", result["final"])
            self.assertIn("audit", result["final"])
            self.assertTrue(any(step["agent_name"] == "audit_agent" for step in result["steps"]))

            rows = db.query(database.AgentEvent).filter(database.AgentEvent.user_id == 1).all()
            self.assertGreaterEqual(len(rows), 5)
            run_ids = [json.loads(row.payload or "{}").get("run_id") for row in rows]
            self.assertIn(result["run_id"], run_ids)

    def test_requested_output_can_force_handout_plan_shape(self):
        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(
                    query="四君子汤复习资料",
                    requested_outputs=["handout"],
                ),
            )

            self.assertEqual(result["status"], "success")
            step_ids = [step["step_id"] for step in result["steps"]]
            self.assertIn("artifact_handout", step_ids)
            self.assertEqual(result["final"]["artifact"]["artifact_type"], "handout")
            self.assertEqual(result["final"]["audit"]["decision"], "pass")

    def test_default_runtime_generates_both_training_artifact_types_with_full_agent_chain(self):
        cases = [
            ("handout", "expert_handout"),
            ("knowledge_card", "expert_knowledge_card"),
        ]

        for output, expert_agent in cases:
            with self.subTest(output=output), self.Session() as db:
                result = run_agent_orchestration(
                    db,
                    user_id=1,
                    request=OrchestrationRequest(
                        query="学习四君子汤",
                        requested_outputs=[output],
                        task_context=OrchestrationTaskContext(
                            correlation_id=f"TT_{output}",
                            kp_ids=["KP_FJ_001", "KP_ZD_021"],
                            difficulty=2,
                            expected_duration_min=15,
                        ),
                    ),
                )

                self.assertEqual(result["status"], "success")
                agents = [step["agent_name"] for step in result["steps"]]
                self.assertIn("memory_agent", agents)
                self.assertIn("diagnosis_agent", agents)
                self.assertIn("knowledge_base_agent", agents)
                self.assertIn(expert_agent, agents)
                self.assertIn("audit_agent", agents)
                self.assertEqual(result["final"]["artifact"]["artifact_type"], output)
                self.assertTrue(result["final"]["evidence_pack"]["source_id"])
                self.assertEqual(result["final"]["audit"]["decision"], "pass")

    def test_generation_constraints_are_passed_structurally(self):
        runtime = build_constraint_capture_runtime()

        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(
                    query="四君子汤复习资料",
                    task_type="handout_generation",
                    requested_outputs=["handout"],
                    task_context=OrchestrationTaskContext(
                        correlation_id="TT_test_001",
                        kp_ids=["KP_FJ_001", "KP_ZD_021"],
                        difficulty=3,
                        expected_duration_min=20,
                    ),
                ),
                runtime=runtime,
            )

            self.assertEqual(result["status"], "success")
            captured = runtime.captured_generation_request
            self.assertEqual(captured["query"], "四君子汤复习资料")
            self.assertEqual(captured["topic"], "四君子汤复习资料")
            self.assertEqual(captured["kp_ids"], ["KP_FJ_001", "KP_ZD_021"])
            self.assertEqual(captured["difficulty"], 3)
            self.assertEqual(captured["expected_duration_min"], 20)
            self.assertNotIn("KP_FJ_001", result["execution_plan"]["objective"])

    def test_task_context_normalizes_kp_ids(self):
        context = OrchestrationTaskContext(kp_ids=[" KP_FJ_001 ", "KP_ZD_021", "KP_FJ_001"])

        self.assertEqual(context.kp_ids, ["KP_FJ_001", "KP_ZD_021"])

    def test_task_context_rejects_invalid_boundaries(self):
        invalid_contexts = (
            {"kp_ids": [" "]},
            {"kp_ids": [f"KP_{index}" for index in range(101)]},
            {"difficulty": 0},
            {"difficulty": 6},
            {"expected_duration_min": 0},
            {"expected_duration_min": 481},
        )

        for task_context in invalid_contexts:
            with self.subTest(task_context=task_context):
                with self.assertRaises(ValueError):
                    OrchestrationTaskContext(**task_context)

    def test_generation_constraints_are_structured_for_paper_only(self):
        request = OrchestrationRequest(
            query="四君子汤复习资料",
            task_context=OrchestrationTaskContext(
                kp_ids=["KP_FJ_001"],
                difficulty=3,
                expected_duration_min=20,
            ),
        )

        with self.Session() as db:
            paper_kwargs = _tool_kwargs(
                tool_name="generate_paper",
                db=db,
                user_id=1,
                request=request,
                results={},
            )
            case_kwargs = _tool_kwargs(
                tool_name="generate_case_training",
                db=db,
                user_id=1,
                request=request,
                results={},
            )

        self.assertEqual(
            paper_kwargs["request"],
            {
                "topic": "四君子汤复习资料",
                "query": "四君子汤复习资料",
                "kp_ids": ["KP_FJ_001"],
                "difficulty": 3,
                "expected_duration_min": 20,
            },
        )
        self.assertEqual(
            case_kwargs["request"],
            {"topic": "四君子汤复习资料", "query": "四君子汤复习资料"},
        )

    def test_requested_paper_creates_expert_paper_then_audit_plan_and_preserves_blueprint_fields(self):
        request = OrchestrationRequest(
            query="生成练习卷",
            requested_outputs=["paper"],
            task_context=OrchestrationTaskContext(
                kp_ids=["KP_FJ_001"],
                question_count=4,
                types=["single_choice", "short_answer"],
                distribution={"single_choice": 3, "short_answer": 1},
            ),
        )

        with self.Session() as db:
            result = run_agent_orchestration(db, user_id=1, request=request)

        steps = result["steps"]
        paper_step = next(step for step in steps if step["agent_name"] == "expert_paper")
        audit_step = next(step for step in steps if step["agent_name"] == "audit_agent")
        self.assertEqual(paper_step["step_id"], "artifact_paper")
        self.assertLess(steps.index(paper_step), steps.index(audit_step))
        self.assertEqual(result["final"]["artifact"]["content"]["paper_blueprint"]["question_count"], 4)
        self.assertEqual(result["final"]["artifact"]["content"]["paper_blueprint"]["types"], ["single_choice", "short_answer"])
        self.assertEqual(result["final"]["artifact"]["content"]["paper_blueprint"]["distribution"], {"single_choice": 3, "short_answer": 1})

    def test_requested_outputs_can_force_knowledge_card_and_quiz_plan_shape(self):
        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(
                    query="四君子汤",
                    requested_outputs=["knowledge_card", "quiz"],
                ),
            )

            step_ids = [step["step_id"] for step in result["steps"]]
            self.assertIn("artifact_knowledge_card", step_ids)
            self.assertIn("artifact_paper", step_ids)
            self.assertGreaterEqual(len(result["final"].get("artifacts") or []), 2)

            review_source_ids = {review.get("source_id") for review in result["final"].get("reviews") or []}
            self.assertGreaterEqual(len(review_source_ids), 2)
            self.assertIn("source_ids", result["final"].get("audit") or {})
            self.assertTrue(review_source_ids.issubset(set(result["final"]["audit"]["source_ids"])))

    def test_high_risk_formula_query_requires_audit_step(self):
        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="讲解四君子汤的方剂组成和禁忌"),
            )

            agents = [step["agent_name"] for step in result["steps"]]
            self.assertIn("audit_agent", agents)

    def test_plain_learn_query_infers_resource_generation_outputs(self):
        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="我想学习四君子汤"),
            )

            self.assertEqual(result["task_type"], "resource_generation")
            self.assertNotEqual(result["status"], "failed")
            self.assertIsNotNone(result["final"].get("artifact"))
            self.assertIsNotNone(result["final"].get("audit"))
            self.assertTrue(any(step["agent_name"] == "audit_agent" for step in result["steps"]))
            self.assertFalse(any("unknown_tool" in str(step.get("error") or "") for step in result["steps"]))

    def test_default_learning_path_query_generates_plan_without_unknown_tool(self):
        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="帮我规划两周学习路径"),
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["task_type"], "learning_path_planning")
            self.assertIn("plan", result["final"])
            self.assertTrue(result["final"]["plan"]["daily_tasks"])
            self.assertFalse(any("unknown_tool" in str(step.get("error") or "") for step in result["steps"]))

    def test_invalid_plan_fails_before_executing_unknown_agent(self):
        runtime = ToolRuntime()
        runtime.register(ToolDefinition("build_learner_context_brief", frozenset({"memory_agent"}), lambda **kwargs: {}))

        def bad_planner(**kwargs):
            return AgentExecutionPlan(
                task_type="resource_generation",
                objective="bad",
                assigned_agents=["missing_agent"],
                steps=[{"id": "bad", "agent": "missing_agent", "action": "bad"}],
            )

        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="bad"),
                runtime=runtime,
                planner=bad_planner,
            )

            self.assertEqual(result["status"], "failed")
            self.assertIn("Unknown agent", result["error"])
            self.assertEqual(result["steps"], [])

    def test_high_risk_query_without_audit_fails_before_executing_steps(self):
        runtime = ToolRuntime()
        execution_counts = {"artifact": 0}

        runtime.register(ToolDefinition("build_learner_context_brief", frozenset({"memory_agent"}), lambda **kwargs: {}))

        def generate_knowledge_card(**kwargs):
            execution_counts["artifact"] += 1
            return {"status": "should-not-run"}

        runtime.register(ToolDefinition("generate_knowledge_card", frozenset({"expert_knowledge_card"}), generate_knowledge_card))

        def planner_without_audit(**kwargs):
            return AgentExecutionPlan(
                task_type="resource_generation",
                objective="普通知识卡生成",
                assigned_agents=["expert_knowledge_card"],
                steps=[{"id": "artifact_knowledge_card", "agent": "expert_knowledge_card", "action": "generate_knowledge_card"}],
            )

        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="请讲解四君子汤对孕妇的禁忌和剂量"),
                runtime=runtime,
                planner=planner_without_audit,
            )

            self.assertEqual(result["status"], "failed")
            self.assertIn("High-risk TCM content requires audit_agent", result["error"])
            self.assertEqual(result["steps"], [])
            self.assertEqual(execution_counts["artifact"], 0)

    def test_reject_audit_does_not_publish_artifact(self):
        runtime = ToolRuntime()
        runtime.register(ToolDefinition("build_learner_context_brief", frozenset({"memory_agent"}), lambda **kwargs: {"learner_id": "1"}))
        runtime.register(ToolDefinition("build_diagnosis_snapshot", frozenset({"diagnosis_agent"}), lambda **kwargs: {"stage_id": "T1", "stage_name": "基础", "summary": "需要审慎输出", "diagnosis_id": "diag_1"}))
        runtime.register(ToolDefinition("build_evidence_pack", frozenset({"knowledge_base_agent"}), lambda **kwargs: {"source_id": "PACK_1", "source_scope": "knowledge_base_agent", "kp_ids": ["KP_FJ_001"], "resolved_kp_ids": ["KP_FJ_001"], "items": []}))
        runtime.register(ToolDefinition("generate_knowledge_card", frozenset({"expert_knowledge_card"}), lambda **kwargs: {"artifact_type": "knowledge_card", "title": "四君子汤", "content": {"full": "专家完整内容"}, "source_scope": "expert_knowledge_card", "source_id": "artifact_1", "kp_ids": ["KP_FJ_001"]}))
        runtime.register(
            ToolDefinition(
                "audit_artifact",
                frozenset({"audit_agent"}),
                lambda **kwargs: ReviewDecision(
                    decision="reject",
                    reason="存在高风险表述",
                    source_scope="audit_agent",
                    source_id="audit_1",
                    confidence=0.98,
                    risk_notes=["medical_high_risk"],
                ),
            )
        )

        def planner(**kwargs):
            return AgentExecutionPlan(
                task_type="resource_generation",
                objective="四君子汤知识卡",
                assigned_agents=["memory_agent", "diagnosis_agent", "knowledge_base_agent", "expert_knowledge_card", "audit_agent"],
                steps=[
                    {"id": "context", "agent": "memory_agent", "action": "build_context"},
                    {"id": "diagnosis", "agent": "diagnosis_agent", "action": "build_diagnosis_snapshot", "depends_on": ["context"]},
                    {"id": "evidence", "agent": "knowledge_base_agent", "action": "build_evidence_pack", "depends_on": ["context", "diagnosis"]},
                    {"id": "artifact_knowledge_card", "agent": "expert_knowledge_card", "action": "generate_knowledge_card", "depends_on": ["evidence", "diagnosis"]},
                    {"id": "audit", "agent": "audit_agent", "action": "review_artifact", "depends_on": ["artifact_knowledge_card", "diagnosis"]},
                ],
            )

        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="讲解四君子汤禁忌"),
                runtime=runtime,
                planner=planner,
            )

            self.assertNotEqual(result["status"], "success")
            self.assertEqual(result["final"]["artifact"], None)
            self.assertEqual(result["final"].get("artifacts") or [], [])
            self.assertEqual(result["final"]["audit"]["decision"], "reject")
            self.assertIn("reason", result["final"]["audit"])

    def test_human_review_audit_does_not_publish_artifact(self):
        runtime = ToolRuntime()
        runtime.register(ToolDefinition("build_learner_context_brief", frozenset({"memory_agent"}), lambda **kwargs: {"learner_id": "1"}))
        runtime.register(ToolDefinition("build_diagnosis_snapshot", frozenset({"diagnosis_agent"}), lambda **kwargs: {"stage_id": "T1", "stage_name": "基础", "summary": "需要人工复核", "diagnosis_id": "diag_1"}))
        runtime.register(ToolDefinition("build_evidence_pack", frozenset({"knowledge_base_agent"}), lambda **kwargs: {"source_id": "PACK_1", "source_scope": "knowledge_base_agent", "kp_ids": ["KP_FJ_001"], "resolved_kp_ids": ["KP_FJ_001"], "items": []}))
        runtime.register(ToolDefinition("generate_knowledge_card", frozenset({"expert_knowledge_card"}), lambda **kwargs: {"artifact_type": "knowledge_card", "title": "四君子汤", "content": {"full": "专家完整内容"}, "sections": [{"title": "要点", "body": "完整段落"}], "claims": ["完整论断"], "source_scope": "expert_knowledge_card", "source_id": "artifact_1", "kp_ids": ["KP_FJ_001"]}))
        runtime.register(
            ToolDefinition(
                "audit_artifact",
                frozenset({"audit_agent"}),
                lambda **kwargs: ReviewDecision(
                    decision="human_review",
                    reason="涉及高风险人群",
                    source_scope="audit_agent",
                    source_id="audit_1",
                    confidence=0.92,
                    risk_notes=["medical_high_risk:requires_human_review"],
                ),
            )
        )

        def planner(**kwargs):
            return AgentExecutionPlan(
                task_type="resource_generation",
                objective="四君子汤知识卡",
                assigned_agents=["memory_agent", "diagnosis_agent", "knowledge_base_agent", "expert_knowledge_card", "audit_agent"],
                steps=[
                    {"id": "context", "agent": "memory_agent", "action": "build_context"},
                    {"id": "diagnosis", "agent": "diagnosis_agent", "action": "build_diagnosis_snapshot", "depends_on": ["context"]},
                    {"id": "evidence", "agent": "knowledge_base_agent", "action": "build_evidence_pack", "depends_on": ["context", "diagnosis"]},
                    {"id": "artifact_knowledge_card", "agent": "expert_knowledge_card", "action": "generate_knowledge_card", "depends_on": ["evidence", "diagnosis"]},
                    {"id": "audit", "agent": "audit_agent", "action": "review_artifact", "depends_on": ["artifact_knowledge_card", "diagnosis"]},
                ],
            )

        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="讲解四君子汤儿童禁忌"),
                runtime=runtime,
                planner=planner,
            )

            self.assertNotEqual(result["status"], "success")
            self.assertEqual(result["final"]["artifact"], None)
            self.assertEqual(result["final"].get("artifacts") or [], [])
            self.assertEqual(result["final"]["audit"]["decision"], "human_review")
            self.assertIn("reason", result["final"]["audit"])

            event = (
                db.query(database.AgentEvent)
                .filter(database.AgentEvent.user_id == 1, database.AgentEvent.agent_name == "expert_knowledge_card")
                .order_by(database.AgentEvent.id.desc())
                .first()
            )
            self.assertIsNotNone(event)
            payload = json.loads(event.payload or "{}")
            recorded_result = payload.get("result") or {}
            serialized = json.dumps(recorded_result, ensure_ascii=False)
            self.assertIn("artifact_type", recorded_result)
            self.assertIn("title", recorded_result)
            self.assertIn("source_id", recorded_result)
            self.assertIn("kp_ids", recorded_result)
            self.assertNotIn("专家完整内容", serialized)
            self.assertNotIn("完整论断", serialized)
            self.assertNotIn("sections", recorded_result)
            self.assertNotIn("claims", recorded_result)
            self.assertNotIn("content", recorded_result)

    def test_needs_human_review_alias_does_not_publish_artifact(self):
        runtime = build_constraint_capture_runtime()
        runtime._tools["audit_artifact"] = ToolDefinition(
            "audit_artifact",
            frozenset({"audit_agent"}),
            lambda **kwargs: {
                "decision": "needs_human_review",
                "reason": "需人工核对特殊人群内容",
                "source_scope": "audit_agent",
                "source_id": "ART_1",
                "confidence": 0.9,
            },
        )

        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(
                    query="四君子汤儿童教学讲义",
                    requested_outputs=["handout"],
                ),
                runtime=runtime,
            )

        self.assertEqual(result["status"], "human_review")
        self.assertIsNone(result["final"]["artifact"])
        self.assertEqual(result["final"]["artifacts"], [])
        self.assertEqual(result["final"]["audit"]["decision"], "needs_human_review")

    def test_unknown_audit_decision_does_not_publish_artifact(self):
        runtime = build_constraint_capture_runtime()
        runtime._tools["generate_handout"] = ToolDefinition(
            "generate_handout",
            frozenset({"expert_handout"}),
            lambda **kwargs: {
                "artifact_type": "handout",
                "title": "四君子汤讲义",
                "content": {"full": "未知审核决策下的专家正文"},
                "source_scope": "expert_handout",
                "source_id": "ART_UNKNOWN",
                "kp_ids": ["KP_FJ_001"],
            },
        )
        runtime._tools["audit_artifact"] = ToolDefinition(
            "audit_artifact",
            frozenset({"audit_agent"}),
            lambda **kwargs: {
                "decision": "needs_review",
                "reason": "非标准审核决策",
                "source_scope": "audit_agent",
                "source_id": "ART_UNKNOWN",
                "confidence": 0.9,
            },
        )

        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(
                    query="四君子汤教学讲义",
                    requested_outputs=["handout"],
                ),
                runtime=runtime,
            )

        self.assertNotEqual(result["status"], "success")
        self.assertIsNone(result["final"]["artifact"])
        self.assertEqual(result["final"]["artifacts"], [])
        self.assertNotIn("未知审核决策下的专家正文", json.dumps(result["final"], ensure_ascii=False))

    def test_revise_audit_does_not_publish_artifact(self):
        runtime = ToolRuntime()
        runtime.register(ToolDefinition("build_learner_context_brief", frozenset({"memory_agent"}), lambda **kwargs: {"learner_id": "1"}))
        runtime.register(ToolDefinition("build_diagnosis_snapshot", frozenset({"diagnosis_agent"}), lambda **kwargs: {"stage_id": "T1", "stage_name": "基础", "summary": "需要修订", "diagnosis_id": "diag_1"}))
        runtime.register(ToolDefinition("build_evidence_pack", frozenset({"knowledge_base_agent"}), lambda **kwargs: {"source_id": "PACK_1", "source_scope": "knowledge_base_agent", "kp_ids": ["KP_FJ_001"], "resolved_kp_ids": ["KP_FJ_001"], "items": []}))
        runtime.register(ToolDefinition("generate_knowledge_card", frozenset({"expert_knowledge_card"}), lambda **kwargs: {"artifact_type": "knowledge_card", "title": "四君子汤", "content": {"full": "专家完整内容"}, "source_scope": "expert_knowledge_card", "source_id": "artifact_1", "kp_ids": ["KP_FJ_001"]}))
        runtime.register(
            ToolDefinition(
                "audit_artifact",
                frozenset({"audit_agent"}),
                lambda **kwargs: ReviewDecision(
                    decision="revise",
                    reason="论断需要降风险改写",
                    source_scope="audit_agent",
                    source_id="audit_1",
                    confidence=0.88,
                ),
            )
        )

        def planner(**kwargs):
            return AgentExecutionPlan(
                task_type="resource_generation",
                objective="四君子汤知识卡",
                assigned_agents=["memory_agent", "diagnosis_agent", "knowledge_base_agent", "expert_knowledge_card", "audit_agent"],
                steps=[
                    {"id": "context", "agent": "memory_agent", "action": "build_context"},
                    {"id": "diagnosis", "agent": "diagnosis_agent", "action": "build_diagnosis_snapshot", "depends_on": ["context"]},
                    {"id": "evidence", "agent": "knowledge_base_agent", "action": "build_evidence_pack", "depends_on": ["context", "diagnosis"]},
                    {"id": "artifact_knowledge_card", "agent": "expert_knowledge_card", "action": "generate_knowledge_card", "depends_on": ["evidence", "diagnosis"]},
                    {"id": "audit", "agent": "audit_agent", "action": "review_artifact", "depends_on": ["artifact_knowledge_card", "diagnosis"]},
                ],
            )

        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="讲解四君子汤方剂组成"),
                runtime=runtime,
                planner=planner,
            )

            self.assertEqual(result["status"], "needs_revision")
            self.assertEqual(result["final"]["artifact"], None)
            self.assertEqual(result["final"].get("artifacts") or [], [])
            self.assertEqual(result["final"]["audit"]["decision"], "revise")

    def test_audit_step_without_artifact_fails_with_trace(self):
        runtime = ToolRuntime()
        runtime.register(ToolDefinition("build_learner_context_brief", frozenset({"memory_agent"}), lambda **kwargs: {"learner_id": "1"}))
        runtime.register(ToolDefinition("build_diagnosis_snapshot", frozenset({"diagnosis_agent"}), lambda **kwargs: {"stage_id": "T1", "stage_name": "基础", "summary": "仅审核", "diagnosis_id": "diag_1"}))
        runtime.register(ToolDefinition("audit_artifact", frozenset({"audit_agent"}), lambda **kwargs: ReviewDecision(decision="pass", source_scope="audit_agent", source_id="audit_1", confidence=0.9)))

        def planner(**kwargs):
            return AgentExecutionPlan(
                task_type="resource_generation",
                objective="只跑审核",
                assigned_agents=["memory_agent", "diagnosis_agent", "audit_agent"],
                steps=[
                    {"id": "context", "agent": "memory_agent", "action": "build_context"},
                    {"id": "diagnosis", "agent": "diagnosis_agent", "action": "build_diagnosis_snapshot", "depends_on": ["context"]},
                    {"id": "audit", "agent": "audit_agent", "action": "review_artifact", "depends_on": ["diagnosis"]},
                ],
            )

        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="审核当前内容"),
                runtime=runtime,
                planner=planner,
            )

            self.assertEqual(result["status"], "failed")
            self.assertIsNone(result["final"].get("audit"))
            audit_steps = [step for step in result["steps"] if step["agent_name"] == "audit_agent"]
            self.assertEqual(len(audit_steps), 1)
            self.assertEqual(audit_steps[0]["status"], "failed")
            self.assertIn("artifact", audit_steps[0]["error"])

    def test_partial_multi_artifact_audit_failure_blocks_publication(self):
        runtime = ToolRuntime()
        runtime.register(ToolDefinition("build_learner_context_brief", frozenset({"memory_agent"}), lambda **kwargs: {"learner_id": "1"}))
        runtime.register(ToolDefinition("build_diagnosis_snapshot", frozenset({"diagnosis_agent"}), lambda **kwargs: {"stage_id": "T1", "stage_name": "基础", "summary": "多产物审核", "diagnosis_id": "diag_1"}))
        runtime.register(ToolDefinition("build_evidence_pack", frozenset({"knowledge_base_agent"}), lambda **kwargs: {"source_id": "PACK_1", "source_scope": "knowledge_base_agent", "kp_ids": ["KP_FJ_001"], "resolved_kp_ids": ["KP_FJ_001"], "items": []}))
        runtime.register(ToolDefinition("generate_knowledge_card", frozenset({"expert_knowledge_card"}), lambda **kwargs: {"artifact_type": "knowledge_card", "title": "知识卡", "content": {"full": "知识卡内容"}, "source_scope": "expert_knowledge_card", "source_id": "artifact_card", "kp_ids": ["KP_FJ_001"]}))
        runtime.register(ToolDefinition("generate_paper", frozenset({"expert_paper"}), lambda **kwargs: {"artifact_type": "paper", "title": "练习题", "content": {"full": "练习题内容"}, "source_scope": "expert_paper", "source_id": "artifact_paper", "kp_ids": ["KP_FJ_001"]}))

        audit_calls = {"count": 0}

        def audit_handler(**kwargs):
            audit_calls["count"] += 1
            if audit_calls["count"] == 1:
                return ReviewDecision(decision="pass", source_scope="audit_agent", source_id="audit_1", confidence=0.95)
            return None

        runtime.register(ToolDefinition("audit_artifact", frozenset({"audit_agent"}), audit_handler))

        def planner(**kwargs):
            return AgentExecutionPlan(
                task_type="resource_generation",
                objective="知识卡与练习题",
                assigned_agents=["memory_agent", "diagnosis_agent", "knowledge_base_agent", "expert_knowledge_card", "expert_paper", "audit_agent"],
                steps=[
                    {"id": "context", "agent": "memory_agent", "action": "build_context"},
                    {"id": "diagnosis", "agent": "diagnosis_agent", "action": "build_diagnosis_snapshot", "depends_on": ["context"]},
                    {"id": "evidence", "agent": "knowledge_base_agent", "action": "build_evidence_pack", "depends_on": ["context", "diagnosis"]},
                    {"id": "artifact_knowledge_card", "agent": "expert_knowledge_card", "action": "generate_knowledge_card", "depends_on": ["evidence", "diagnosis"]},
                    {"id": "artifact_paper", "agent": "expert_paper", "action": "generate_paper", "depends_on": ["evidence", "diagnosis"]},
                    {"id": "audit", "agent": "audit_agent", "action": "review_artifact", "depends_on": ["artifact_knowledge_card", "artifact_paper", "diagnosis"]},
                ],
            )

        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="生成知识卡并出题"),
                runtime=runtime,
                planner=planner,
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["final"]["artifact"], None)
            self.assertEqual(result["final"].get("artifacts") or [], [])
            self.assertEqual(result["final"]["audit"]["decision"], "failed")
            self.assertIn("audit_incomplete", result["final"]["audit"]["conflicts"][0])
            self.assertEqual(len(result["final"].get("reviews") or []), 1)

    def test_mixed_pass_and_unknown_audits_do_not_publish_artifacts(self):
        runtime = ToolRuntime()
        runtime.register(ToolDefinition("build_learner_context_brief", frozenset({"memory_agent"}), lambda **kwargs: {"learner_id": "1"}))
        runtime.register(ToolDefinition("build_diagnosis_snapshot", frozenset({"diagnosis_agent"}), lambda **kwargs: {"stage_id": "T1", "stage_name": "基础", "summary": "多产物审核", "diagnosis_id": "diag_1"}))
        runtime.register(ToolDefinition("build_evidence_pack", frozenset({"knowledge_base_agent"}), lambda **kwargs: {"source_id": "PACK_1", "source_scope": "knowledge_base_agent", "kp_ids": ["KP_FJ_001"], "resolved_kp_ids": ["KP_FJ_001"], "items": []}))
        runtime.register(ToolDefinition("generate_knowledge_card", frozenset({"expert_knowledge_card"}), lambda **kwargs: {"artifact_type": "knowledge_card", "title": "知识卡", "content": {"full": "混合审核知识卡正文"}, "source_scope": "expert_knowledge_card", "source_id": "artifact_card", "kp_ids": ["KP_FJ_001"]}))
        runtime.register(ToolDefinition("generate_paper", frozenset({"expert_paper"}), lambda **kwargs: {"artifact_type": "paper", "title": "练习题", "content": {"full": "混合审核练习题正文"}, "source_scope": "expert_paper", "source_id": "artifact_paper", "kp_ids": ["KP_FJ_001"]}))

        audit_calls = {"count": 0}

        def audit_handler(**kwargs):
            audit_calls["count"] += 1
            decision = "pass" if audit_calls["count"] == 1 else "needs_review"
            return ReviewDecision(
                decision=decision,
                source_scope="audit_agent",
                source_id=f"audit_{audit_calls['count']}",
                confidence=0.95,
            )

        runtime.register(ToolDefinition("audit_artifact", frozenset({"audit_agent"}), audit_handler))

        def planner(**kwargs):
            return AgentExecutionPlan(
                task_type="resource_generation",
                objective="知识卡与练习题",
                assigned_agents=["memory_agent", "diagnosis_agent", "knowledge_base_agent", "expert_knowledge_card", "expert_paper", "audit_agent"],
                steps=[
                    {"id": "context", "agent": "memory_agent", "action": "build_context"},
                    {"id": "diagnosis", "agent": "diagnosis_agent", "action": "build_diagnosis_snapshot", "depends_on": ["context"]},
                    {"id": "evidence", "agent": "knowledge_base_agent", "action": "build_evidence_pack", "depends_on": ["context", "diagnosis"]},
                    {"id": "artifact_knowledge_card", "agent": "expert_knowledge_card", "action": "generate_knowledge_card", "depends_on": ["evidence", "diagnosis"]},
                    {"id": "artifact_paper", "agent": "expert_paper", "action": "generate_paper", "depends_on": ["evidence", "diagnosis"]},
                    {"id": "audit", "agent": "audit_agent", "action": "review_artifact", "depends_on": ["artifact_knowledge_card", "artifact_paper", "diagnosis"]},
                ],
            )

        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="生成知识卡并出题"),
                runtime=runtime,
                planner=planner,
            )

        self.assertNotEqual(result["status"], "success")
        self.assertIsNone(result["final"]["artifact"])
        self.assertEqual(result["final"]["artifacts"], [])
        self.assertEqual(result["final"]["audit"]["decision"], "needs_review")
        serialized = json.dumps(result["final"], ensure_ascii=False)
        self.assertNotIn("混合审核知识卡正文", serialized)
        self.assertNotIn("混合审核练习题正文", serialized)

    def test_context_prefetch_failure_does_not_call_planner_and_returns_safe_error(self):
        runtime = ToolRuntime()
        planner = Mock(side_effect=AssertionError("planner should not be called"))
        runtime.register(
            ToolDefinition(
                "build_learner_context_brief",
                frozenset({"memory_agent"}),
                lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db secret failure")),
            )
        )

        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="生成知识卡"),
                runtime=runtime,
                planner=planner,
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["steps"], [])
            self.assertIn("context", result["error"])
            self.assertNotIn("db secret failure", result["error"])
            planner.assert_not_called()

    def test_persist_false_still_writes_agent_event_trace(self):
        with self.Session() as db:
            result = run_agent_orchestration(
                db,
                user_id=1,
                request=OrchestrationRequest(query="生成四君子汤知识卡", persist=False),
            )

            rows = db.query(database.AgentEvent).filter(database.AgentEvent.user_id == 1).all()
            self.assertGreater(len(rows), 0)
            run_ids = [json.loads(row.payload or "{}").get("run_id") for row in rows]
            self.assertIn(result["run_id"], run_ids)


if __name__ == "__main__":
    unittest.main()
