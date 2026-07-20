from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from APP.backend.agent_contracts import AgentExecutionPlan
from APP.backend.agent_registry import get_agent_definition
from APP.backend.agent_runtime import validate_execution_plan
from APP.backend.database import AgentEvent
from APP.backend.planner_agent_service import generate_agent_execution_plan
from APP.backend.tool_runtime import ToolRuntime, build_default_tool_runtime


class OrchestrationTaskContext(BaseModel):
    correlation_id: str = Field(default="", max_length=120)
    kp_ids: list[str] = Field(default_factory=list, max_length=100)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    expected_duration_min: int | None = Field(default=None, ge=1, le=480)
    question_count: int | None = Field(default=None, ge=1, le=50)
    types: list[str] = Field(default_factory=list)
    distribution: dict[str, int] = Field(default_factory=dict)
    mistake_id: int | None = Field(default=None, ge=1)
    source_question_version_id: str = Field(default="", max_length=120)
    source_question_id: str = Field(default="", max_length=120)
    source_stem: str = Field(default="", max_length=8000)
    source_question_type: str = Field(default="single_choice", max_length=50)
    source_difficulty: int | None = Field(default=None, ge=1, le=5)

    @field_validator("correlation_id")
    @classmethod
    def normalize_correlation_id(cls, value: str) -> str:
        return value.strip()

    @field_validator("kp_ids")
    @classmethod
    def normalize_kp_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("kp_ids must not contain blank values")
        return list(dict.fromkeys(normalized))


class OrchestrationRequest(BaseModel):
    query: str = Field(min_length=1)
    task_type: str = "auto"
    requested_outputs: list[str] = Field(default_factory=list)
    task_context: OrchestrationTaskContext = Field(default_factory=OrchestrationTaskContext)
    persist: bool = True

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


class PlanValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_plan") -> None:
        super().__init__(message)
        self.code = code

    def to_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


PlannerCallable = Callable[..., AgentExecutionPlan]

HIGH_RISK_TCM_KEYWORDS = ("方剂", "四君子汤", "中药", "禁忌", "剂量", "孕妇", "儿童", "症状")
TCM_RESOURCE_TOPIC_KEYWORDS = ("四君子汤", "方剂", "中药", "证型", "辨证", "汤剂", "本草", "药材", "脾胃气虚")
AUDIT_DECISION_PRIORITY = {"reject": 4, "human_review": 3, "needs_human_review": 3, "revise": 2, "pass": 1}
TRACE_ARTIFACT_SUMMARY_KEYS = ("source_id", "source_scope", "title", "artifact_type", "kp_ids", "resolved_kp_ids", "confidence")


def _plan_tool_names(runtime: ToolRuntime) -> list[str]:
    return sorted({"search_rag", "search_health_web", *runtime.tool_names()})


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _step(step_id: str, agent: str, action: str, *, depends_on: list[str] | None = None) -> dict[str, Any]:
    return {"id": step_id, "agent": agent, "action": action, "depends_on": list(depends_on or [])}


def _artifact_steps_for_outputs(requested_outputs: list[str]) -> list[dict[str, Any]]:
    outputs = set(requested_outputs or [])
    steps: list[dict[str, Any]] = []
    if "handout" in outputs:
        steps.append(_step("artifact_handout", "expert_handout", "generate_handout", depends_on=["evidence"]))
    if "knowledge_card" in outputs:
        steps.append(_step("artifact_knowledge_card", "expert_knowledge_card", "generate_knowledge_card", depends_on=["evidence"]))
    if "paper" in outputs or "quiz" in outputs:
        steps.append(_step("artifact_paper", "expert_paper", "generate_paper", depends_on=["evidence"]))
    if "question_variation" in outputs:
        steps.append(_step("artifact_question_variation", "expert_question_variation", "generate_question_variation", depends_on=["evidence"]))
    return steps


def _infer_requested_outputs(query: str) -> list[str]:
    outputs: list[str] = []
    if _contains_any(query, ("讲解", "讲义", "知识卡", "卡片", "速记", "方剂组成", "禁忌")):
        outputs.append("knowledge_card")
    if "学习" in query and _contains_any(query, TCM_RESOURCE_TOPIC_KEYWORDS) and "knowledge_card" not in outputs:
        outputs.append("knowledge_card")
    if _contains_any(query, ("做题", "练习", "巩固", "试题", "试卷", "测试", "考试", "出题", "组卷")):
        outputs.append("quiz")
    return outputs


def _ensure_diagnosis_step(plan: AgentExecutionPlan) -> AgentExecutionPlan:
    steps = [step for step in plan.steps if isinstance(step, dict)]
    if any(step.get("agent") == "diagnosis_agent" for step in steps):
        return plan

    updated_steps: list[dict[str, Any]] = []
    inserted = False
    for step in steps:
        updated_steps.append(step)
        if not inserted and step.get("id") == "context":
            updated_steps.append(_step("diagnosis", "diagnosis_agent", "build_diagnosis_snapshot", depends_on=["context"]))
            inserted = True

    if not inserted:
        updated_steps.insert(0, _step("diagnosis", "diagnosis_agent", "build_diagnosis_snapshot"))

    for step in updated_steps:
        depends_on = list(step.get("depends_on") or [])
        if step.get("id") == "evidence" and "diagnosis" not in depends_on:
            step["depends_on"] = [*depends_on, "diagnosis"]
        elif step.get("agent", "").startswith("expert_") and "diagnosis" not in depends_on:
            step["depends_on"] = [*depends_on, "diagnosis"]
        elif step.get("agent") == "audit_agent" and "diagnosis" not in depends_on:
            step["depends_on"] = [*depends_on, "diagnosis"]

    plan.steps = updated_steps
    plan.assigned_agents = sorted({str(step.get("agent")) for step in updated_steps if step.get("agent")})
    return plan


def _resource_plan_from_outputs(plan: AgentExecutionPlan, requested_outputs: list[str]) -> AgentExecutionPlan:
    artifact_steps = _artifact_steps_for_outputs(requested_outputs)
    if not artifact_steps:
        return plan

    if "question_variation" in requested_outputs:
        plan.task_type = "mistake_variation"
        plan.need_cross_validation = True
        plan.steps = [
            _step("context", "memory_agent", "build_context"),
            _step("evidence", "knowledge_base_agent", "build_evidence_pack", depends_on=["context"]),
            *artifact_steps,
            _step("audit", "audit_agent", "review_artifact", depends_on=[step["id"] for step in artifact_steps]),
        ]
        plan.assigned_agents = sorted({str(step.get("agent")) for step in plan.steps if step.get("agent")})
        return plan

    plan.task_type = "resource_generation"
    plan.need_cross_validation = True
    plan.risk_level = plan.risk_level or "medium"
    plan.steps = [
        _step("context", "memory_agent", "build_context"),
        _step("diagnosis", "diagnosis_agent", "build_diagnosis_snapshot", depends_on=["context"]),
        _step("evidence", "knowledge_base_agent", "build_evidence_pack", depends_on=["context", "diagnosis"]),
        *artifact_steps,
        _step("audit", "audit_agent", "review_artifact", depends_on=[step["id"] for step in artifact_steps] + ["diagnosis"]),
    ]
    plan.assigned_agents = sorted({str(step.get("agent")) for step in plan.steps if step.get("agent")})
    return plan


def _apply_requested_outputs(plan: AgentExecutionPlan, requested_outputs: list[str]) -> AgentExecutionPlan:
    outputs = set(requested_outputs or [])
    if not outputs:
        return plan

    if plan.task_type != "resource_generation":
        return _resource_plan_from_outputs(plan, requested_outputs)

    steps = [step for step in plan.steps if isinstance(step, dict)]
    existing_ids = {str(step.get("id")) for step in steps}
    artifact_steps = []
    if "handout" in outputs and "artifact_handout" not in existing_ids:
        artifact_steps.append(
            _step(
                "artifact_handout",
                "expert_handout",
                "generate_handout",
                depends_on=["evidence", "diagnosis"],
            )
        )
    if "knowledge_card" in outputs and "artifact_knowledge_card" not in existing_ids:
        artifact_steps.append(_step("artifact_knowledge_card", "expert_knowledge_card", "generate_knowledge_card", depends_on=["evidence", "diagnosis"]))
    if ("paper" in outputs or "quiz" in outputs) and "artifact_paper" not in existing_ids:
        artifact_steps.append(_step("artifact_paper", "expert_paper", "generate_paper", depends_on=["evidence", "diagnosis"]))
    if not artifact_steps:
        return plan

    without_audit = [step for step in steps if step.get("agent") != "audit_agent"]
    all_artifact_ids = [step["id"] for step in without_audit if str(step.get("id", "")).startswith("artifact_")] + [step["id"] for step in artifact_steps]
    plan.steps = [
        *without_audit,
        *artifact_steps,
        _step("audit", "audit_agent", "review_artifact", depends_on=[*all_artifact_ids, "diagnosis"]),
    ]
    plan.assigned_agents = sorted({str(step.get("agent")) for step in plan.steps if step.get("agent")})
    return plan


def _has_artifact_step(plan: AgentExecutionPlan) -> bool:
    return any(
        isinstance(step, dict) and str(step.get("agent", "")).startswith("expert_")
        for step in plan.steps
    )


def _should_force_resource_generation(request: OrchestrationRequest, plan: AgentExecutionPlan) -> bool:
    if request.requested_outputs:
        return True
    if plan.task_type == "resource_generation":
        return not _has_artifact_step(plan)
    return bool(_infer_requested_outputs(request.query))


def _plan_validation_code(message: str) -> str:
    if message.startswith("Unknown agent:"):
        return "unknown_agent"
    if message.startswith("Missing dependency:"):
        return "missing_dependency"
    if message.startswith("Unauthorized tool") or message.startswith("Tool "):
        return "unauthorized_tool"
    if "requires audit_agent" in message:
        return "missing_required_audit"
    return "invalid_plan"


def validate_plan_for_orchestration(plan: AgentExecutionPlan, source_text: str = "") -> None:
    try:
        validate_execution_plan(plan, ["search_rag", "search_health_web"])
    except ValueError as exc:
        raise PlanValidationError(str(exc), code=_plan_validation_code(str(exc))) from None

    steps = [step for step in plan.steps if isinstance(step, dict)]
    for step in steps:
        if get_agent_definition(str(step.get("agent") or "")) is None:
            message = f"Unknown agent: {step.get('agent')}"
            raise PlanValidationError(message, code="unknown_agent")

    audit_present = any(step.get("agent") == "audit_agent" for step in steps)
    step_text = " ".join(
        filter(
            None,
            [
                str(plan.objective or ""),
                str(plan.task_type or ""),
                *[str(step.get("id") or "") for step in steps],
                *[str(step.get("agent") or "") for step in steps],
                *[str(step.get("action") or "") for step in steps],
            ],
        )
    )
    if _contains_any(f"{source_text} {step_text}", HIGH_RISK_TCM_KEYWORDS) and not audit_present:
        raise PlanValidationError("High-risk TCM content requires audit_agent", code="missing_required_audit")


def _dump_contract(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _summarize_trace_result(step_id: str, result: Any | None) -> Any | None:
    dumped = _dump_contract(result)
    if dumped is None:
        return None
    if not str(step_id).startswith("artifact_"):
        return dumped
    if not isinstance(dumped, dict):
        return dumped
    return {key: dumped[key] for key in TRACE_ARTIFACT_SUMMARY_KEYS if key in dumped}


def _collect_artifacts(results: dict[str, Any]) -> list[Any]:
    ordered_step_ids = list(results.get("artifact_step_ids") or [])
    artifact_map = results.get("artifact_map") or {}
    artifacts = [artifact_map[step_id] for step_id in ordered_step_ids if step_id in artifact_map]
    fallback_artifact = results.get("artifact")
    if fallback_artifact is not None and fallback_artifact not in artifacts:
        artifacts.append(fallback_artifact)
    return artifacts


def _primary_artifact(artifacts: list[Any]) -> Any | None:
    if not artifacts:
        return None
    return artifacts[-1]


def _aggregate_reviews(reviews: list[Any]) -> dict[str, Any] | None:
    dumped_reviews = [_dump_contract(review) for review in reviews if review is not None]
    if not dumped_reviews:
        return None

    dominant = max(
        dumped_reviews,
        key=lambda review: (
            str(review.get("decision")) != "pass",
            AUDIT_DECISION_PRIORITY.get(str(review.get("decision")), 0),
            float(review.get("confidence") or 0.0),
        ),
    )
    source_ids = [str(review.get("source_id")) for review in dumped_reviews if review.get("source_id")]
    kp_ids: list[str] = []
    risk_notes: list[str] = []
    conflicts: list[str] = []
    agent_trace: list[dict[str, Any]] = []
    for review in dumped_reviews:
        kp_ids.extend(str(item) for item in review.get("kp_ids") or [])
        risk_notes.extend(str(item) for item in review.get("risk_notes") or [])
        conflicts.extend(str(item) for item in review.get("conflicts") or [])
        agent_trace.extend(review.get("agent_trace") or [])

    merged = dict(dominant)
    merged["source_scope"] = "audit_agent"
    merged["source_ids"] = list(dict.fromkeys(source_ids))
    merged["kp_ids"] = list(dict.fromkeys(kp_ids))
    merged["risk_notes"] = list(dict.fromkeys(risk_notes))
    merged["conflicts"] = list(dict.fromkeys(conflicts))
    merged["agent_trace"] = agent_trace
    return merged


def _trace_payload(run_id: str, step_id: str, status: str, result: Any | None = None, error: str | None = None) -> str:
    return json.dumps(
        {
            "run_id": run_id,
            "step_id": step_id,
            "status": status,
            "result": _summarize_trace_result(step_id, result),
            "error": error,
        },
        ensure_ascii=False,
    )


def _record_event(
    db: Session,
    *,
    user_id: int,
    run_id: str,
    step_id: str,
    agent_name: str,
    event_type: str,
    status: str,
    input_summary: str,
    output_summary: str,
    result: Any | None = None,
    error: str | None = None,
) -> None:
    db.add(
        AgentEvent(
            user_id=user_id,
            agent_name=agent_name,
            event_type=event_type,
            input_summary=input_summary,
            output_summary=output_summary,
            payload=_trace_payload(run_id, step_id, status, result, error),
        )
    )
    db.commit()


def _failure_result(run_id: str, *, error: str, error_code: str, final: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": "failed",
        "error": error,
        "error_code": error_code,
        "steps": [],
        "final": final or {},
    }


def _record_and_fail(
    db: Session,
    *,
    user_id: int,
    run_id: str,
    step_id: str,
    agent_name: str,
    input_summary: str,
    output_summary: str,
    error: str,
    error_code: str,
    final: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _record_event(
        db,
        user_id=user_id,
        run_id=run_id,
        step_id=step_id,
        agent_name=agent_name,
        event_type="orchestration_failed",
        status="failed",
        input_summary=input_summary,
        output_summary=output_summary,
        error=error_code,
    )
    return _failure_result(run_id, error=error, error_code=error_code, final=final)


def _topic_from_query(query: str) -> str:
    return query.strip() or "四君子汤"


def _tool_for_step(step: dict[str, Any]) -> str:
    agent = step.get("agent")
    action = step.get("action")
    if agent == "memory_agent":
        return "build_learner_context_brief"
    if agent == "diagnosis_agent":
        return "build_diagnosis_snapshot"
    if agent == "knowledge_base_agent":
        return "build_evidence_pack"
    if agent == "expert_handout":
        return "generate_handout"
    if agent == "expert_knowledge_card":
        return "generate_knowledge_card"
    if agent == "expert_paper":
        return "generate_paper"
    if agent == "expert_grading":
        return "grade_submission"
    if agent == "expert_case_training":
        return "generate_case_training"
    if agent == "expert_question_variation":
        return "generate_question_variation"
    if agent == "planner_agent":
        return "generate_learning_path"
    if agent == "audit_agent" and action == "review_plan":
        return "review_learning_plan"
    if agent == "audit_agent":
        return "audit_artifact"
    return str(action or "")


def _tool_kwargs(
    *,
    tool_name: str,
    db: Session,
    user_id: int,
    request: OrchestrationRequest,
    results: dict[str, Any],
    artifact: Any | None = None,
) -> dict[str, Any]:
    if tool_name == "build_learner_context_brief":
        return {"db": db, "user_id": user_id}
    if tool_name == "build_diagnosis_snapshot":
        return {"db": db, "user_id": user_id, "persist": False}

    learner_context = results.get("learner_context")
    diagnosis = results.get("diagnosis")
    evidence_pack = results.get("evidence_pack")

    if tool_name == "build_evidence_pack":
        return {
            "db": db,
            "query": request.query,
            "learner_context": learner_context,
            "task_type": request.task_type if request.task_type != "auto" else None,
        }
    if tool_name in {"generate_handout", "generate_knowledge_card", "generate_paper", "generate_case_training"}:
        generation_request: dict[str, Any] = {
            "topic": _topic_from_query(request.query),
            "query": request.query,
        }
        if tool_name in {"generate_handout", "generate_knowledge_card"}:
            if request.task_context.kp_ids:
                generation_request["kp_ids"] = list(request.task_context.kp_ids)
            if request.task_context.difficulty is not None:
                generation_request["difficulty"] = request.task_context.difficulty
            if request.task_context.expected_duration_min is not None:
                generation_request["expected_duration_min"] = request.task_context.expected_duration_min
        if tool_name == "generate_paper":
            if request.task_context.kp_ids:
                generation_request["kp_ids"] = list(request.task_context.kp_ids)
            if request.task_context.question_count is not None:
                generation_request["question_count"] = request.task_context.question_count
            if request.task_context.types:
                generation_request["types"] = list(request.task_context.types)
            if request.task_context.distribution:
                generation_request["distribution"] = dict(request.task_context.distribution)
            if request.task_context.difficulty is not None:
                generation_request["difficulty"] = request.task_context.difficulty
            if request.task_context.expected_duration_min is not None:
                generation_request["expected_duration_min"] = request.task_context.expected_duration_min
        return {
            "learner_context": learner_context,
            "evidence_pack": evidence_pack,
            "diagnosis_report": diagnosis,
            "request": generation_request,
        }
    if tool_name == "generate_question_variation":
        return {
            "learner_context": learner_context,
            "evidence_pack": evidence_pack,
            "request": {
                "mistake_id": request.task_context.mistake_id,
                "source_question_version_id": request.task_context.source_question_version_id,
                "source_question_id": request.task_context.source_question_id,
                "source_stem": request.task_context.source_stem,
                "source_question_type": request.task_context.source_question_type,
                "source_difficulty": request.task_context.source_difficulty,
                "kp_ids": list(request.task_context.kp_ids),
            },
        }
    if tool_name == "grade_submission":
        return {
            "learner_context": learner_context,
            "evidence_pack": evidence_pack,
            "diagnosis_report": diagnosis,
            "submission": {"question_id": "manual", "stem": request.query, "difficulty": 2},
        }
    if tool_name == "generate_learning_path":
        profile = getattr(learner_context, "profile", {}) or {}
        learning_state = getattr(learner_context, "learning_state", {}) or {}
        baseline = getattr(diagnosis, "l0_baseline", None) or {}
        return {
            "learner_id": str(user_id),
            "learner_group": getattr(learner_context, "learner_group", ""),
            "onboarding_answers": {
                "long_term_goal": getattr(learner_context, "goal", "") or profile.get("learning_goal", ""),
                "daily_available_minutes": baseline.get("daily_available_minutes") or 30,
                "resource_preference": profile.get("resource_preferences"),
            },
            "diagnosis_report": diagnosis,
            "learning_profile": {
                "learner_group": getattr(learner_context, "learner_group", ""),
                "learning_goal": getattr(learner_context, "goal", "") or profile.get("learning_goal", ""),
                "preferred_resources": profile.get("resource_preferences"),
                "weak_kp_ids": learning_state.get("weak_kp_ids", []),
                "learning_state": learning_state,
            },
        }
    if tool_name == "review_learning_plan":
        return {"plan": results.get("plan") or {}}
    if tool_name == "audit_artifact":
        return {
            "artifact": artifact,
            "evidence_pack": evidence_pack,
            "learner_context": learner_context,
            "diagnosis_report": diagnosis,
        }
    return {}


def _result_key(step: dict[str, Any], tool_name: str) -> str:
    if tool_name == "build_learner_context_brief":
        return "learner_context"
    if tool_name == "build_diagnosis_snapshot":
        return "diagnosis"
    if tool_name == "build_evidence_pack":
        return "evidence_pack"
    if tool_name == "generate_learning_path":
        return "plan"
    if tool_name in {"audit_artifact", "review_learning_plan"}:
        return "audit"
    if str(step.get("agent", "")).startswith("expert_"):
        return str(step.get("id") or "artifact")
    return str(step.get("id") or tool_name)


def _audit_publication_state(
    final_audit: dict[str, Any] | None,
    final_artifacts: list[Any],
    *,
    audit_planned: bool,
    successful_review_count: int,
) -> tuple[Any | None, list[Any], str | None, str | None]:
    artifact = _dump_contract(_primary_artifact(final_artifacts))
    artifacts = [_dump_contract(item) for item in final_artifacts]
    if audit_planned and final_audit is None:
        return None, [], "failed", "审核未完成，未发布专家内容。"
    if audit_planned and final_artifacts and successful_review_count != len(final_artifacts):
        return None, [], "failed", "存在未完成审核的专家内容，未发布结果。"
    if final_audit is None:
        return artifact, artifacts, None, None

    decision = str(final_audit.get("decision") or "")
    reason = str(final_audit.get("reason") or "").strip()
    if decision == "reject":
        return None, [], "rejected", f"审核未通过，未发布专家内容。{reason}".strip()
    if decision in {"human_review", "needs_human_review"}:
        return None, [], "human_review", f"内容需人工复核，未发布专家内容。{reason}".strip()
    if decision == "revise":
        return None, [], "needs_revision", f"内容需修订后再发布。{reason}".strip()
    if decision == "pass":
        return artifact, artifacts, None, None
    return None, [], "failed", f"审核决策无效，未发布专家内容。{reason}".strip()


def _derive_status(steps_output: list[dict[str, Any]], audit_status: str | None) -> str:
    if any(step["status"] == "failed" for step in steps_output):
        return "failed"
    if audit_status is not None:
        return audit_status
    if steps_output and all(step["status"] == "success" for step in steps_output):
        return "success"
    return "degraded"


def run_agent_orchestration(
    db: Session,
    user_id: int,
    request: OrchestrationRequest,
    runtime: ToolRuntime | None = None,
    planner: PlannerCallable = generate_agent_execution_plan,
    *,
    raise_on_validation_error: bool = False,
) -> dict[str, Any]:
    runtime = runtime or build_default_tool_runtime()
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    steps_output: list[dict[str, Any]] = []
    results: dict[str, Any] = {"artifact_map": {}, "artifact_step_ids": [], "reviews": []}

    context_invocation = runtime.execute("build_learner_context_brief", "memory_agent", db=db, user_id=user_id)
    if context_invocation.status != "success" or context_invocation.result is None:
        return _record_and_fail(
            db,
            user_id=user_id,
            run_id=run_id,
            step_id="context_prefetch",
            agent_name="memory_agent",
            input_summary=context_invocation.input_summary,
            output_summary="learner context prefetch failed",
            error="learner context prefetch failed",
            error_code="context_prefetch_failed",
        )
    results["learner_context"] = context_invocation.result

    try:
        plan = planner(
            learner_context=context_invocation.result,
            user_request=request.query,
            available_tools=_plan_tool_names(runtime),
        )
        if _should_force_resource_generation(request, plan):
            inferred_outputs = request.requested_outputs or _infer_requested_outputs(request.query)
            plan = _resource_plan_from_outputs(plan, inferred_outputs)
        else:
            plan = _apply_requested_outputs(plan, request.requested_outputs)
        if plan.task_type == "resource_generation":
            plan = _ensure_diagnosis_step(plan)
        validate_plan_for_orchestration(plan, request.query)
    except PlanValidationError as exc:
        _record_event(
            db,
            user_id=user_id,
            run_id=run_id,
            step_id="plan_validation",
            agent_name="agent_orchestrator",
            event_type="orchestration_failed",
            status="failed",
            input_summary=request.query,
            output_summary="plan validation failed",
            error=exc.code,
        )
        if raise_on_validation_error:
            raise
        return _failure_result(run_id, error=str(exc), error_code=exc.code)
    except Exception:
        return _record_and_fail(
            db,
            user_id=user_id,
            run_id=run_id,
            step_id="planner",
            agent_name="planner_agent",
            input_summary=request.query,
            output_summary="planner execution failed",
            error="planner execution failed",
            error_code="planner_execution_failed",
        )

    audit_planned = any(isinstance(step, dict) and step.get("agent") == "audit_agent" for step in plan.steps)

    for raw_step in plan.steps:
        if not isinstance(raw_step, dict):
            continue
        step_id = str(raw_step.get("id") or "step")
        agent_name = str(raw_step.get("agent") or "")
        tool_name = _tool_for_step(raw_step)

        if tool_name == "audit_artifact":
            artifact_batch = _collect_artifacts(results)
            if not artifact_batch:
                error_code = "audit_artifact_missing:artifact_required_for_review"
                _record_event(
                    db,
                    user_id=user_id,
                    run_id=run_id,
                    step_id=step_id,
                    agent_name=agent_name,
                    event_type="orchestration_step",
                    status="failed",
                    input_summary="artifact_count=0",
                    output_summary="audit step failed because no artifact was available",
                    error=error_code,
                )
                steps_output.append(
                    {
                        "step_id": step_id,
                        "agent_name": agent_name,
                        "action": raw_step.get("action"),
                        "status": "failed",
                        "input_summary": "artifact_count=0",
                        "output_summary": "audit step failed because no artifact was available",
                        "error": error_code,
                    }
                )
                break
        else:
            artifact_batch = [None]

        for index, target_artifact in enumerate(artifact_batch):
            current_step_id = step_id if len(artifact_batch) == 1 else f"{step_id}:{index + 1}"
            invocation = runtime.execute(
                tool_name,
                agent_name,
                **_tool_kwargs(
                    tool_name=tool_name,
                    db=db,
                    user_id=user_id,
                    request=request,
                    results=results,
                    artifact=target_artifact,
                ),
            )
            status = invocation.status
            error = invocation.error
            output_summary = invocation.output_summary
            if tool_name == "audit_artifact" and invocation.status == "success" and invocation.result is None:
                status = "failed"
                error = "audit_result_missing:audit_agent_returned_empty_result"
                output_summary = "audit step failed because audit result was empty"

            result_key = _result_key(raw_step, tool_name)
            if status == "success":
                if str(raw_step.get("agent", "")).startswith("expert_"):
                    artifact_step_id = str(raw_step.get("id") or result_key)
                    results["artifact_map"][artifact_step_id] = invocation.result
                    if artifact_step_id not in results["artifact_step_ids"]:
                        results["artifact_step_ids"].append(artifact_step_id)
                    results["artifact"] = invocation.result
                elif tool_name in {"audit_artifact", "review_learning_plan"}:
                    results["reviews"].append(invocation.result)
                    results[result_key] = invocation.result
                else:
                    results[result_key] = invocation.result
            _record_event(
                db,
                user_id=user_id,
                run_id=run_id,
                step_id=current_step_id,
                agent_name=agent_name,
                event_type="orchestration_step",
                status=status,
                input_summary=invocation.input_summary,
                output_summary=output_summary,
                result=invocation.result if status == "success" else None,
                error=error,
            )
            steps_output.append(
                {
                    "step_id": current_step_id,
                    "agent_name": agent_name,
                    "action": raw_step.get("action"),
                    "status": status,
                    "input_summary": invocation.input_summary,
                    "output_summary": output_summary,
                    "error": error,
                }
            )
            if status == "failed":
                break
        if steps_output and steps_output[-1]["status"] == "failed":
            break

    final_artifacts = _collect_artifacts(results)
    final_reviews = [_dump_contract(review) for review in results.get("reviews") or [] if review is not None]
    final_audit = _aggregate_reviews(results.get("reviews") or [])
    if audit_planned and final_artifacts and len(results.get("reviews") or []) != len(final_artifacts):
        final_audit = {
            "source_scope": "audit_agent",
            "decision": "failed",
            "reason": "存在未完成审核的专家内容",
            "source_ids": [getattr(item, "source_id", None) or (_dump_contract(item) or {}).get("source_id") for item in final_artifacts],
            "reviewed_source_ids": [review.get("source_id") for review in final_reviews if review.get("source_id")],
            "conflicts": ["audit_incomplete:artifact_review_count_mismatch"],
            "confidence": 0.0,
        }
    published_artifact, published_artifacts, audit_status, publication_note = _audit_publication_state(
        final_audit,
        final_artifacts,
        audit_planned=audit_planned,
        successful_review_count=len(results.get("reviews") or []),
    )
    final = {
        "learner_context": _dump_contract(results.get("learner_context")),
        "diagnosis": _dump_contract(results.get("diagnosis")),
        "evidence_pack": _dump_contract(results.get("evidence_pack")),
        "plan": _dump_contract(results.get("plan")),
        "artifact": published_artifact,
        "artifacts": published_artifacts,
        "audit": final_audit,
        "reviews": final_reviews,
    }
    if publication_note:
        final["message"] = publication_note
    return {
        "run_id": run_id,
        "status": _derive_status(steps_output, audit_status),
        "task_type": plan.task_type,
        "execution_plan": plan.model_dump(mode="json"),
        "steps": steps_output,
        "final": final,
        "trace": steps_output,
    }
