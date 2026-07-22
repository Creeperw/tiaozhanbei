from __future__ import annotations

from typing import Any

from competition_app.contracts.planning_readiness import (
    PlanningAction,
    PlanningParentState,
    PlanningReadiness,
)
from competition_app.services.learning_plan import LearningPlanService
from competition_app.services.profile_readiness import ProfileReadinessService


class PlanningReadinessService:
    """One backend-owned prerequisite policy for every planning entry point."""

    def __init__(self, learning_plan_service: LearningPlanService | None = None) -> None:
        self.learning_plan_service = learning_plan_service
        self.profile_readiness = ProfileReadinessService()

    def evaluate(
        self,
        context: dict[str, Any],
        requested_scope: str,
        *,
        learner_id: str | None = None,
    ) -> PlanningReadiness:
        if requested_scope not in {"long_term", "short_term", "daily_task"}:
            raise ValueError("requested_scope must be long_term, short_term, or daily_task")

        actions = [
            PlanningAction(
                action="start_planning",
                method="POST",
                endpoint="/api/v1/review-cards/personalized",
                plan_scope=requested_scope,
            )
        ]
        if requested_scope == "long_term":
            profile = self.profile_readiness.evaluate(context, "long_term")
            if not profile.can_proceed:
                return PlanningReadiness(
                    requested_scope="long_term",
                    status="needs_profile",
                    can_generate=False,
                    required_action="complete_profile",
                    reason_codes=["profile_incomplete"],
                    questions=profile.questions,
                    missing_profile_fields=profile.missing_fields,
                    next_profile_field=profile.next_field,
                    available_actions=actions,
                )
            return PlanningReadiness(
                requested_scope="long_term",
                status="ready",
                can_generate=True,
                required_action="none",
                available_actions=actions,
            )

        long_state = self._parent_state(
            context.get("current_long_term_plan"), "long_term", learner_id
        )
        if not long_state.exists:
            return self._missing_parent(
                requested_scope, long_state, "needs_long_term_plan", "create_long_term_plan"
            )
        if not long_state.valid:
            return self._stale_parent(requested_scope, [long_state])
        if requested_scope == "short_term":
            return PlanningReadiness(
                requested_scope="short_term",
                status="ready",
                can_generate=True,
                required_action="none",
                parent_states=[long_state],
                available_actions=actions,
            )

        short_state = self._parent_state(
            context.get("current_short_term_plan"), "short_term", learner_id
        )
        if not short_state.exists:
            return self._missing_parent(
                requested_scope,
                short_state,
                "needs_short_term_plan",
                "create_short_term_plan",
                parents=[long_state, short_state],
            )
        if not short_state.valid or not self._short_belongs_to_long(
            context.get("current_short_term_plan"), context.get("current_long_term_plan")
        ):
            if short_state.valid:
                short_state = short_state.model_copy(
                    update={"valid": False, "reason_codes": ["parent_version_mismatch"]}
                )
            return self._stale_parent(requested_scope, [long_state, short_state])
        return PlanningReadiness(
            requested_scope="daily_task",
            status="ready",
            can_generate=True,
            required_action="none",
            parent_states=[long_state, short_state],
            available_actions=actions,
        )

    def _parent_state(
        self, supplied: Any, scope: str, learner_id: str | None
    ) -> PlanningParentState:
        raw = supplied if isinstance(supplied, dict) else {}
        if not raw or not str(raw.get("content") or "").strip():
            return PlanningParentState(
                scope=scope, exists=False, valid=False, reason_codes=["parent_missing"]
            )
        if scope == "long_term" and LearningPlanService.is_importable_long_term_parent(raw):
            return PlanningParentState(
                scope="long_term",
                exists=True,
                valid=True,
                persisted=False,
                reason_codes=["importable_inline_parent"],
            )
        plan_id = str(raw.get("plan_id") or "").strip() or None
        version = raw.get("version")
        persisted = bool(plan_id)
        valid = bool(
            persisted
            and raw.get("status") == "active"
            and isinstance(version, int)
            and not isinstance(version, bool)
            and version >= 1
            and (not learner_id or raw.get("learner_id") == learner_id)
        )
        if valid and learner_id and self.learning_plan_service is not None:
            valid = self.learning_plan_service.is_current_parent(
                learner_id, raw, "long" if scope == "long_term" else "short"
            )
        return PlanningParentState(
            scope=scope,
            exists=True,
            valid=valid,
            persisted=persisted,
            plan_id=plan_id,
            version=version if isinstance(version, int) and not isinstance(version, bool) else None,
            reason_codes=[] if valid else ["parent_not_current_or_invalid"],
        )

    @staticmethod
    def _short_belongs_to_long(short_plan: Any, long_plan: Any) -> bool:
        if not isinstance(short_plan, dict) or not isinstance(long_plan, dict):
            return False
        expected = short_plan.get("long_term_plan_id")
        actual = long_plan.get("plan_id")
        return not expected or expected == actual

    @staticmethod
    def _missing_parent(
        requested_scope: str,
        parent: PlanningParentState,
        status: str,
        action: str,
        *,
        parents: list[PlanningParentState] | None = None,
    ) -> PlanningReadiness:
        missing_scope = "long_term" if action == "create_long_term_plan" else "short_term"
        return PlanningReadiness(
            requested_scope=requested_scope,
            status=status,
            can_generate=False,
            required_action=action,
            reason_codes=[f"{missing_scope}_plan_required"],
            questions=[
                "当前还没有有效长期规划，是否先建立长期规划？"
                if missing_scope == "long_term"
                else "当前还没有有效短期计划，是否先制定短期计划？"
            ],
            parent_states=parents or [parent],
            available_actions=[
                PlanningAction(
                    action=action,
                    method="POST",
                    endpoint="/api/v1/review-cards/personalized",
                    plan_scope=missing_scope,
                )
            ],
        )

    @staticmethod
    def _stale_parent(
        requested_scope: str, parents: list[PlanningParentState]
    ) -> PlanningReadiness:
        return PlanningReadiness(
            requested_scope=requested_scope,
            status="stale_parent_plan",
            can_generate=False,
            required_action="refresh_parent_plan",
            reason_codes=["parent_plan_stale_or_invalid"],
            questions=["上层规划已经失效或不是当前版本，请先重新生成对应的上层规划。"],
            parent_states=parents,
            available_actions=[],
        )
