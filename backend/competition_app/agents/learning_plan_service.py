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
                str(context["learner_id"]), parent_plan, parent_kind
            ):
                imported_parent = None
                learner_id = str(context["learner_id"])
                if (
                    parent_kind == "long"
                    and self.service.get_current(learner_id) is None
                    and self.service.is_importable_long_term_parent(parent_plan)
                ):
                    imported_parent = self.service.import_long_term_parent(
                        learner_id,
                        parent_plan,
                        diagnosis.learning_plan_proposal,
                        now=context.get("now"),
                    )
                    parent_plan = imported_parent.model_dump(mode="json")
                    context["current_long_term_plan"] = parent_plan
                if imported_parent is None:
                    parent_label = "长期规划" if parent_kind == "long" else "短期计划"
                    clarification = LearningPlanClarificationResult(
                        clarification_questions=[
                            f"当前{parent_label}已失效或不是最新版本，是否先重新制定{parent_label}？"
                        ],
                        reason=f"本层计划必须基于当前有效的{parent_label}制定。",
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
                learner_id=str(context["learner_id"]),
                proposal=diagnosis.learning_plan_proposal,
                now=context.get("now"),
            )
        elif plan_scope == "short_term":
            result = self.service.materialize_short_term(
                learner_id=str(context["learner_id"]),
                proposal=diagnosis.learning_plan_proposal,
                now=context.get("now"),
                current_long_term_plan=context.get("current_long_term_plan") or {},
            )
        elif plan_scope == "daily_task":
            result = self.service.materialize_daily_task(
                learner_id=str(context["learner_id"]),
                proposal=diagnosis.learning_plan_proposal,
                now=context.get("now"),
                current_short_term_plan=context.get("current_short_term_plan") or {},
                current_long_term_plan=context.get("current_long_term_plan"),
                current_learning_task=context.get("current_learning_task"),
            )
        else:
            result = self.service.materialize(
                learner_id=str(context["learner_id"]),
                proposal=diagnosis.learning_plan_proposal,
                now=context.get("now"),
                current_long_term_plan=context.get("current_long_term_plan"),
                current_short_term_plan=context.get("current_short_term_plan"),
                available_minutes=context.get("available_minutes"),
            )
        return envelope(context, "learning_plan_service", "learning_plan_result", result)
