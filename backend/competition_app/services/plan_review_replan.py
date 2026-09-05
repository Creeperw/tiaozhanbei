"""复盘接受后的短期计划→每日任务级联协调器。"""

from __future__ import annotations

from typing import Any


class PlanReviewReplanCoordinator:
    """Run the two scoped materializations required by a short-term replan.

    The existing learning-plan service deliberately materializes one scope at a
    time.  This coordinator preserves that invariant while making the
    user-facing operation atomic at the workflow level: a new short-term plan
    invalidates the old daily task, then a second run recreates today's task
    against the new short-term parent.
    """

    def __init__(self, *, review_card_use_case: Any, plan_service: Any) -> None:
        self.review_card_use_case = review_card_use_case
        self.plan_service = plan_service

    async def run(self, learner_id: str, review: dict[str, Any]) -> dict[str, Any]:
        stored_before = self.plan_service.get_current(learner_id)
        previous_short = getattr(stored_before, "short_term_plan", None)
        previous_short_version = int(getattr(previous_short, "version", 0) or 0)
        proposal = review.get("proposal") or {}
        workflow_request = proposal.get("workflow_request") or {}
        request_text = str(
            workflow_request.get("user_request")
            or proposal.get("summary")
            or review.get("summary")
            or "请根据近期学习监控证据调整短期计划。"
        ).strip()
        short_result = await self.review_card_use_case.execute(
            self._request(
                learner_id,
                request_text,
                plan_scope="short_term",
                operation_id=f"plan-review:{review.get('review_id')}:short-term-v1",
            )
        )
        short_learning_plan = self._learning_plan(short_result)
        short_plan = getattr(short_learning_plan, "short_term_plan", None)
        if short_plan is None:
            raise RuntimeError("短期重规划未生成新的短期计划")
        stored_after_short = self.plan_service.get_current(learner_id)
        current_short = getattr(stored_after_short, "short_term_plan", None)
        if current_short is None or str(getattr(current_short, "plan_id", "")) != str(
            getattr(short_plan, "plan_id", "")
        ):
            raise RuntimeError("短期重规划结果未成为当前短期计划")
        if int(getattr(current_short, "version", 0) or 0) <= previous_short_version:
            raise RuntimeError("短期重规划未产生新版本")
        invalidated = list(getattr(short_learning_plan, "invalidated_layers", []) or [])
        if "daily_task" not in invalidated:
            raise RuntimeError("短期重规划未使旧每日任务失效")
        if getattr(stored_after_short, "learning_task", None) is not None:
            raise RuntimeError("短期重规划后旧每日任务仍处于当前计划")

        daily_result = await self.review_card_use_case.execute(
            self._request(
                learner_id,
                "请基于刚刚生成的最新短期计划生成今日学习任务。",
                plan_scope="daily_task",
                operation_id=f"plan-review:{review.get('review_id')}:daily-task-v1",
            )
        )
        daily_learning_plan = self._learning_plan(daily_result)
        daily_task = getattr(daily_learning_plan, "learning_task", None)
        if daily_task is None:
            raise RuntimeError("短期重规划后的每日任务生成失败")
        if str(getattr(daily_task, "short_term_plan_id", "")) != str(
            getattr(current_short, "plan_id", "")
        ):
            raise RuntimeError("新每日任务未引用最新短期计划")
        stored_after_daily = self.plan_service.get_current(learner_id)
        stored_daily = getattr(stored_after_daily, "learning_task", None)
        if stored_daily is None or str(getattr(stored_daily, "task_id", "")) != str(
            getattr(daily_task, "task_id", "")
        ):
            raise RuntimeError("新每日任务未成为当前任务")
        return {
            "short_term_plan_id": str(getattr(current_short, "plan_id", "")),
            "short_term_version": int(getattr(current_short, "version", 0) or 0),
            "daily_task_id": str(getattr(daily_task, "task_id", "")),
            "daily_task_version": int(getattr(daily_task, "version", 0) or 0),
            "generated_scopes": ["short_term", "daily_task"],
            "invalidated_layers": ["daily_task"],
        }

    @staticmethod
    def _learning_plan(result: Any) -> Any:
        """Unwrap the public result envelope without breaking test doubles."""

        return getattr(result, "learning_plan", None) or result

    @staticmethod
    def _request(
        learner_id: str,
        user_request: str,
        *,
        plan_scope: str,
        operation_id: str,
    ) -> Any:
        # Local import avoids importing the application container at module
        # load time and keeps this service straightforward to unit-test.
        from competition_app.application.personalized_review_card import (
            ReviewCardRequest,
        )

        return ReviewCardRequest(
            learner_id=learner_id,
            user_request=user_request,
            available_minutes=30,
            plan_scope=plan_scope,
            operation_id=operation_id,
            system_operation="plan_review_replan",
        )