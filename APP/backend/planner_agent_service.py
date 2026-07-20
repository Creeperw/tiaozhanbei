from __future__ import annotations

from typing import Any

from APP.backend.agent_contracts import AgentExecutionPlan, LearnerContextBrief
from APP.backend.agent_runtime import build_runtime_trace, validate_execution_plan
from APP.backend.cross_validation_service import validate_execution_plan as cross_validate_output


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _classify_task(user_request: str) -> str:
    text = user_request or ""
    if _contains_any(text, ("上传", "PDF", "pdf", "文档", "加入知识库", "入库", "导入")):
        return "document_ingestion"
    if _contains_any(text, ("批改", "作业", "错题", "补救", "评分", "答案")):
        return "grading_remediation"
    if _contains_any(text, ("生成", "知识卡", "讲义", "出题", "组卷", "试卷", "试题", "案例", "资源")):
        return "resource_generation"
    return "learning_path_planning"


def _step(step_id: str, agent: str, action: str, tools: list[str] | None = None, depends_on: list[str] | None = None) -> dict[str, Any]:
    return {"id": step_id, "agent": agent, "action": action, "tools": tools or [], "depends_on": depends_on or []}


def _expert_steps_for_resource(user_request: str) -> list[dict[str, str]]:
    text = user_request or ""
    steps: list[dict[str, str]] = []
    if _contains_any(text, ("讲义", "讲解", "课件")):
        steps.append({"id": "artifact_handout", "agent": "expert_handout", "action": "generate_handout"})
    if _contains_any(text, ("知识卡", "卡片", "速记")):
        steps.append({"id": "artifact_knowledge_card", "agent": "expert_knowledge_card", "action": "generate_knowledge_card"})
    if _contains_any(text, ("试卷", "试题", "测试", "考试", "出题", "组卷")):
        steps.append({"id": "artifact_paper", "agent": "expert_paper", "action": "generate_paper"})
    if _contains_any(text, ("案例", "病例", "辨证训练")):
        steps.append({"id": "artifact_case_training", "agent": "expert_case_training", "action": "generate_case_training"})
    return steps or [{"id": "artifact_knowledge_card", "agent": "expert_knowledge_card", "action": "generate_knowledge_card"}]


def _steps_for_task(task_type: str, available_tools: list[str], user_request: str = "") -> tuple[list[dict[str, Any]], bool, str]:
    rag_tools = [tool for tool in ("search_rag", "search_health_web") if tool in available_tools]
    if task_type == "document_ingestion":
        return (
            [
                _step("audit_upload", "audit_agent", "review_document_before_ingestion"),
                _step("extract_document", "knowledge_base_agent", "extract_with_markitdown", depends_on=["audit_upload"]),
                _step("build_evidence", "knowledge_base_agent", "extract_kp_questions_resources", tools=[tool for tool in ["search_rag"] if tool in available_tools], depends_on=["extract_document"]),
                _step("final_audit", "audit_agent", "review_ingestion_result", depends_on=["build_evidence"]),
            ],
            True,
            "high",
        )
    if task_type == "grading_remediation":
        return (
            [
                _step("context", "memory_agent", "build_context"),
                _step("evidence", "knowledge_base_agent", "build_evidence_pack", tools=rag_tools, depends_on=["context"]),
                _step("grading", "expert_grading", "grade_submission", depends_on=["evidence"]),
                _step("audit", "audit_agent", "review_grading", depends_on=["grading"]),
            ],
            True,
            "medium",
        )
    if task_type == "resource_generation":
        artifact_steps = [
            _step(item["id"], item["agent"], item["action"], depends_on=["evidence"])
            for item in _expert_steps_for_resource(user_request)
        ]
        return (
            [
                _step("context", "memory_agent", "build_context"),
                _step("evidence", "knowledge_base_agent", "build_evidence_pack", tools=rag_tools, depends_on=["context"]),
                *artifact_steps,
                _step("audit", "audit_agent", "review_artifact", depends_on=[step["id"] for step in artifact_steps]),
            ],
            True,
            "medium",
        )
    return (
        [
            _step("context", "memory_agent", "build_context"),
            _step("diagnosis", "diagnosis_agent", "diagnose_learning_state", depends_on=["context"]),
            _step("plan", "planner_agent", "generate_learning_path", depends_on=["diagnosis"]),
            _step("audit", "audit_agent", "review_plan", depends_on=["plan"]),
        ],
        False,
        "low",
    )


def generate_agent_execution_plan(
    *,
    learner_context: LearnerContextBrief,
    user_request: str,
    available_tools: list[str] | None = None,
) -> AgentExecutionPlan:
    tools = list(available_tools or [])
    task_type = _classify_task(user_request)
    steps, need_cross_validation, risk_level = _steps_for_task(task_type, tools, user_request)
    plan = AgentExecutionPlan(
        task_type=task_type,
        objective=user_request or learner_context.goal,
        assigned_agents=sorted({step["agent"] for step in steps}),
        steps=steps,
        need_cross_validation=need_cross_validation,
        risk_level=risk_level,
        source_scope="planner_agent",
        source_id=f"learner:{learner_context.learner_id}:{task_type}",
        kp_ids=list(learner_context.kp_ids),
        confidence=0.78,
    )
    validate_execution_plan(plan, tools)
    review, summary = cross_validate_output(plan=plan, learner_context=learner_context)
    plan.plan_summary = {
        "goal": plan.objective,
        "review_decision": review.model_dump(),
        "review_summary": summary,
    }
    plan.agent_trace = [
        *build_runtime_trace(plan),
        {"agent": "cross_validation_service", "action": "cross_validate_output", "status": review.decision},
    ]
    return plan
