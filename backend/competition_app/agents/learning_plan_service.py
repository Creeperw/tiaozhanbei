from __future__ import annotations

from typing import Any

from competition_app.agents.common import envelope
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.learning_plan import (
    LearningPlanClarificationResult,
    LearningPlanResult,
)
from competition_app.services.default_route import DefaultRouteRepository
from competition_app.services.learning_plan import LearningPlanService
from competition_app.repositories.learning_plan import PlanWriteConflictError
from competition_app.services.plan_audit import plan_audit_subject_digest


class LearningPlanServiceAdapter:
    """Orchestrator adapter for the backend-owned learning plan service."""

    def __init__(
        self,
        service: LearningPlanService | None = None,
        route_repository: DefaultRouteRepository | None = None,
    ) -> None:
        if service is not None and route_repository is not None:
            raise ValueError("provide service or route_repository, not both")
        self.service = service or LearningPlanService(route_repository)

    async def run(
        self, context: dict[str, Any]
    ) -> AgentEnvelope[LearningPlanResult | LearningPlanClarificationResult]:
        dependencies = context["dependency_outputs"]

        def concurrent_change_result(detail: str) -> AgentEnvelope:
            result = LearningPlanClarificationResult(
                clarification_questions=[
                    "另一会话刚刚更新了同一层学习计划。是否读取最新版本后重新制定？"
                ],
                reason=(
                    f"{detail} 系统已阻止旧版本覆盖新版本；其他对话任务不受影响。"
                ),
                requested_scope=context.get("requested_plan_scope"),
            )
            return envelope(
                context,
                "learning_plan_service",
                "learning_plan_concurrency_conflict",
                result,
            )

        if context.get("step_id") == "learning_plan" and {
            "diagnosis_long", "audit_long", "diagnosis_short", "audit_short"
        }.issubset(dependencies):
            long_diagnosis = dependencies["diagnosis_long"].payload
            short_diagnosis = dependencies["diagnosis_short"].payload
            long_audit = dependencies["audit_long"].payload
            short_audit = dependencies["audit_short"].payload
            clarifications = [
                item
                for item in (long_diagnosis, short_diagnosis)
                if getattr(item, "requires_clarification", False)
            ]
            if clarifications:
                questions = [
                    question
                    for item in clarifications
                    for question in item.clarification_questions
                ]
                result = LearningPlanClarificationResult(
                    clarification_questions=list(dict.fromkeys(questions)),
                    reason=(
                        "长期规划或短期计划尚未形成可独立审核的完整合同；"
                        "三审全部通过前不会发布计划或学习资源。"
                    ),
                    requested_scope="unspecified",
                )
                return envelope(
                    context,
                    "learning_plan_service",
                    "learning_plan_clarification",
                    result,
                )
            for scope, diagnosis, audit, subject_type in (
                ("long_term", long_diagnosis, long_audit, "long_term_plan"),
                ("short_term", short_diagnosis, short_audit, "short_term_plan"),
            ):
                expected_digest = plan_audit_subject_digest(
                    plan_scope=scope,
                    proposal=diagnosis.learning_plan_proposal,
                    compiled_plan_contract=diagnosis.compiled_plan_contract,
                    parent_plan_constraints=dict(
                        getattr(diagnosis, "parent_plan_constraints", {}) or {}
                    ),
                )
                failures = [
                    name
                    for name, failed in (
                        ("decision", audit.decision != "pass"),
                        ("subject_type", audit.subject_type != subject_type),
                        ("plan_scope", audit.plan_scope != scope),
                        ("subject_digest", audit.subject_digest != expected_digest),
                    )
                    if failed
                ]
                if failures:
                    raise RuntimeError(
                        "combined plan requires independent passing audits: "
                        f"{scope}:{','.join(failures)}"
                    )
            if short_audit.parent_subject_digest != long_audit.subject_digest:
                raise RuntimeError("short-term audit is not bound to the approved long-term plan")
            learner_id = str(context["learner_id"])
            try:
                with self.service.mutation_lock(learner_id):
                    long_result = self.service.materialize_long_term(
                        learner_id=learner_id,
                        proposal=long_diagnosis.learning_plan_proposal,
                        now=context.get("now"),
                    )
                    self.service.materialize_short_term(
                        learner_id=learner_id,
                        proposal=short_diagnosis.learning_plan_proposal,
                        now=context.get("now"),
                        current_long_term_plan=long_result.long_term_plan.model_dump(mode="json"),
                    )
            except PlanWriteConflictError as exc:
                return concurrent_change_result(str(exc))
            result = self.service.get_current(str(context["learner_id"]))
            if result is None:
                raise RuntimeError("combined plan publication did not persist")
            return envelope(
                context, "learning_plan_service", "learning_plan_result", result
            )
        diagnosis = context["dependency_outputs"]["diagnosis"].payload
        if getattr(diagnosis, "requires_clarification", False):
            clarification = LearningPlanClarificationResult(
                clarification_questions=diagnosis.clarification_questions,
                reason=diagnosis.clarification_reason or "需要补充重规划信息。",
                requested_scope=getattr(diagnosis, "plan_scope", None),
            )
            return envelope(
                context,
                "learning_plan_service",
                "learning_plan_clarification",
                clarification,
            )
        plan_scope = getattr(diagnosis, "plan_scope", None)
        if (
            context.get("requires_learning_plan_output") is True
            and plan_scope not in {"long_term", "short_term", "daily_task"}
        ):
            clarification = LearningPlanClarificationResult(
                clarification_questions=[
                    "请先明确要制定长期规划、短期计划还是今日任务；"
                    "对应计划独立审核通过后，再与本次学习资源联动。"
                ],
                reason=(
                    "资源审核不能替代长期或短期计划审核，系统不会在组合资源链中"
                    "直接激活未经独立审核的多层计划。"
                ),
                requested_scope="unspecified",
            )
            return envelope(
                context,
                "learning_plan_service",
                "learning_plan_clarification",
                clarification,
            )
        if plan_scope in {"long_term", "short_term"}:
            audit_output = context["dependency_outputs"].get("audit")
            audit = getattr(audit_output, "payload", None)
            if audit is None or audit.decision != "pass":
                raise RuntimeError("long/short-term plan requires a passing audit")
            if audit.plan_scope != plan_scope:
                raise RuntimeError("plan audit scope does not match proposal scope")
            parent_constraints = dict(
                getattr(diagnosis, "parent_plan_constraints", {}) or {}
            )
            expected_digest = plan_audit_subject_digest(
                plan_scope=plan_scope,
                proposal=diagnosis.learning_plan_proposal,
                compiled_plan_contract=diagnosis.compiled_plan_contract,
                parent_plan_constraints=parent_constraints,
            )
            if audit.subject_digest != expected_digest:
                raise RuntimeError("plan audit approval does not match current proposal")
        learner_id = str(context["learner_id"])
        try:
            with self.service.mutation_lock(learner_id):
                target_layer = {
                    "long_term": ("long_term_plan", context.get("current_long_term_plan")),
                    "short_term": ("short_term_plan", context.get("current_short_term_plan")),
                    "daily_task": ("learning_task", context.get("current_learning_task")),
                }.get(plan_scope)
                if target_layer and not self.service.is_current_layer_snapshot(
                    learner_id, target_layer[1], target_layer[0]
                ):
                    return concurrent_change_result("目标计划在本次生成期间已经变化。")

                parent_kind = (
                    "long"
                    if plan_scope == "short_term"
                    else "short"
                    if plan_scope == "daily_task"
                    else None
                )
                if parent_kind is not None:
                    parent_plan = (
                        context.get("current_long_term_plan")
                        if parent_kind == "long"
                        else context.get("current_short_term_plan")
                    ) or {}
                    if not self.service.is_current_parent(
                        learner_id, parent_plan, parent_kind
                    ):
                        parent_label = "长期规划" if parent_kind == "long" else "短期计划"
                        clarification = LearningPlanClarificationResult(
                            clarification_questions=[
                                f"当前{parent_label}已失效或不是最新版本，是否先重新制定{parent_label}？"
                            ],
                            reason=f"本层计划必须基于当前有效且已独立审核的{parent_label}制定。",
                            requested_scope=plan_scope,
                        )
                        return envelope(
                            context,
                            "learning_plan_service",
                            "learning_plan_clarification",
                            clarification,
                        )
                if plan_scope == "long_term":
                    result = self.service.materialize_long_term(
                        learner_id=learner_id,
                        proposal=diagnosis.learning_plan_proposal,
                        now=context.get("now"),
                    )
                elif plan_scope == "short_term":
                    result = self.service.materialize_short_term(
                        learner_id=learner_id,
                        proposal=diagnosis.learning_plan_proposal,
                        now=context.get("now"),
                        current_long_term_plan=context.get("current_long_term_plan") or {},
                    )
                elif plan_scope == "daily_task":
                    result = self.service.materialize_daily_task(
                        learner_id=learner_id,
                        proposal=diagnosis.learning_plan_proposal,
                        now=context.get("now"),
                        current_short_term_plan=context.get("current_short_term_plan") or {},
                        current_long_term_plan=context.get("current_long_term_plan"),
                        current_learning_task=context.get("current_learning_task"),
                        recommended_minutes=(
                            context.get("task_load_policy", {}).get("recommended_minutes")
                            if isinstance(context.get("task_load_policy"), dict)
                            else None
                        ),
                    )
                else:
                    result = self.service.materialize(
                        learner_id=learner_id,
                        proposal=diagnosis.learning_plan_proposal,
                        now=context.get("now"),
                        current_long_term_plan=context.get("current_long_term_plan"),
                        current_short_term_plan=context.get("current_short_term_plan"),
                        available_minutes=context.get("available_minutes"),
                    )
        except PlanWriteConflictError as exc:
            return concurrent_change_result(str(exc))
        return envelope(context, "learning_plan_service", "learning_plan_result", result)

    @staticmethod
    def _parent_plan_constraints(
        context: dict[str, Any], plan_scope: str | None
    ) -> dict[str, Any]:
        if plan_scope != "short_term":
            return {}
        parent = context.get("current_long_term_plan") or {}
        stages = parent.get("stages", []) if isinstance(parent, dict) else []
        if not stages:
            return {}
        current = next(
            (stage for stage in stages if stage.get("status") in {"active", "current"}),
            stages[0],
        )
        return {
            "current_stage_id": current.get("stage_id"),
            "current_stage_duration_days": current.get("duration_days"),
        }
