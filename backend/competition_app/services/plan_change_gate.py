from __future__ import annotations

from typing import Any

from competition_app.contracts.learning_plan import PlanChangeDecision


class PlanChangeGate:
    """Choose plan mutations from explicit facts, never from model preference."""

    _REPLAN_WORDS = (
        "重新规划",
        "重新计划",
        "调整计划",
        "修改计划",
        "计划不合适",
        "规划不满意",
        "计划不满意",
        "重做计划",
    )
    _LONG_WORDS = ("长期", "最终目标", "长期目标", "考试日期", "期限", "路线", "教材版本")
    _SHORT_WORDS = ("短期", "本周", "下周", "未来一周", "未来两周", "每天", "每日", "可用时间")

    def decide(
        self,
        *,
        user_request: str,
        current_long_term_plan: Any = None,
        current_short_term_plan: Any = None,
        explicit_long_term_change: bool = False,
        explicit_short_term_change: bool = False,
        sustained_learning_change: bool = False,
        route_changed: bool = False,
        single_performance_change: bool = False,
        semantic_decision: PlanChangeDecision | dict[str, Any] | None = None,
        # Kept only for old direct callers/tests. Production Diagnosis always
        # passes the semantic contract and explicitly disables this branch.
        allow_legacy_heuristics: bool = True,
    ) -> PlanChangeDecision:
        # The semantic assessment is produced by DiagnosisAgent.  The gate is
        # deliberately only a safety boundary here: it normalizes the three
        # layers and propagates invalidation, but does not infer meaning from
        # user wording.
        if semantic_decision is not None:
            decision = (
                semantic_decision
                if isinstance(semantic_decision, PlanChangeDecision)
                else PlanChangeDecision.model_validate(semantic_decision)
            )
            has_long = self._is_valid(current_long_term_plan)
            has_short = self._is_valid(current_short_term_plan)
            long_action = decision.long_term_action
            short_action = decision.short_term_action
            daily_action = decision.daily_task_action
            requested_long_update = long_action == "update"
            requested_short_update = short_action == "update"
            # Missing layers are filled only when that layer is the requested
            # deliverable.  A first long-term plan must not accidentally force
            # empty short/daily contracts into the same model response.
            if not has_long and (
                requested_long_update or decision.replan_requested
            ):
                long_action = "update"
            if not has_short and requested_short_update:
                short_action = "update"
            # A parent change invalidates every child layer.  A short-term
            # change invalidates the current daily task.
            # A long-term *revision* is a parent-version change.  Initial
            # creation of a missing long-term plan is different: it must not
            # force the same response to invent short/daily layers.  Once a
            # parent exists, or Diagnosis explicitly marks this as a replan,
            # propagate invalidation through macro→meso→micro.
            if long_action == "update" and (has_long or decision.replan_requested):
                short_action = "update"
                daily_action = "update"
            elif short_action == "update":
                daily_action = "update"
            if single_performance_change:
                long_action = "reuse" if has_long else "update"
                short_action = "reuse" if has_short else "update"
                daily_action = "update"
            clarification = decision.requires_clarification
            clarification_questions = list(decision.clarification_questions)
            if decision.replan_requested and decision.changed_facts:
                clarification = False
                clarification_questions = []
            return decision.model_copy(
                update={
                    "long_term_action": long_action,
                    "short_term_action": short_action,
                    "daily_task_action": daily_action,
                    "replan_requested": bool(
                        decision.replan_requested
                        or long_action == "update"
                        or short_action == "update"
                    ),
                    "requires_clarification": clarification,
                    "clarification_questions": clarification_questions,
                }
            )
        # No semantic contract means the model did not provide enough
        # information to interpret the user's wording. Never infer intent from
        # Chinese keywords here: that was the source of false re-plans and
        # repeated clarification loops. Use only system-confirmed facts.
        if not allow_legacy_heuristics:
            has_long = self._is_valid(current_long_term_plan)
            has_short = self._is_valid(current_short_term_plan)
            long_update = bool(explicit_long_term_change or route_changed)
            short_update = bool(explicit_short_term_change or sustained_learning_change)
            if long_update:
                short_update = True
            if single_performance_change:
                long_update = False
                short_update = False
            return PlanChangeDecision(
                long_term_action="update" if long_update or not has_long else "reuse",
                short_term_action="update" if short_update or not has_short else "reuse",
                daily_task_action="update",
                reason=(
                    "仅依据系统已确认事实；用户语义需由 Diagnosis 合同提供。"
                    if not (long_update or short_update)
                    else "依据系统已确认的路线、学习节奏或计划变更事实。"
                ),
                replan_requested=bool(long_update or short_update),
            )

        text = "".join(str(user_request or "").split())
        has_long = self._is_valid(current_long_term_plan)
        has_short = self._is_valid(current_short_term_plan)
        mentions_long = any(word in text for word in self._LONG_WORDS)
        mentions_short = any(word in text for word in self._SHORT_WORDS)
        rejects_existing_plan = (
            any(word in text for word in ("不满意", "不合适", "不符合预期"))
            and any(word in text for word in ("计划", "规划"))
        )
        asks_replan = rejects_existing_plan or any(
            word in text for word in self._REPLAN_WORDS
        )
        gives_change = self._states_concrete_change(text)

        if asks_replan and not (
            explicit_long_term_change
            or explicit_short_term_change
            or route_changed
            or sustained_learning_change
            or (gives_change and (mentions_long or mentions_short))
        ):
            return PlanChangeDecision(
                long_term_action="reuse" if has_long else "update",
                short_term_action="reuse" if has_short else "update",
                daily_task_action="reuse",
                requires_clarification=True,
                clarification_questions=[
                    "需要调整长期计划、短期计划，还是两者？",
                    "目标或期限发生了什么具体变化？",
                    "当前每天或每周可用多少学习时间？",
                    "现有计划中希望保留和放弃哪些内容？",
                    "调整原因和期望结果是什么？",
                ],
                reason="重规划请求没有给出足以确定调整层级和内容的事实。",
                replan_requested=True,
            )

        long_update = explicit_long_term_change or route_changed or (
            asks_replan and gives_change and mentions_long
        )
        short_update = explicit_short_term_change or sustained_learning_change or (
            gives_change
            and mentions_short
            and any(
                word in text
                for word in ("调整", "修改", "更新", "重新规划", "重新计划")
            )
        )
        if long_update:
            # A short-term plan is an execution slice of the active long-term
            # route and must be regenerated when that parent route changes.
            short_update = True
        if single_performance_change:
            long_update = False
            short_update = False
        return PlanChangeDecision(
            long_term_action="update" if long_update or not has_long else "reuse",
            short_term_action="update" if short_update or not has_short else "reuse",
            daily_task_action="update",
            reason=(
                "只更新有明确变化事实的计划层级。"
                if long_update or short_update
                else "已有有效长短期计划默认原样复用；当日任务按今日事实更新。"
            ),
            replan_requested=bool(long_update or short_update),
        )

    @staticmethod
    def _is_valid(plan: Any) -> bool:
        if isinstance(plan, dict):
            content = plan.get("content")
            status = plan.get("status", "active")
        else:
            content = getattr(plan, "content", None)
            status = getattr(plan, "status", "active")
        return isinstance(content, str) and bool(content.strip()) and status not in {
            "invalid",
            "expired",
            "retired",
        }

    @staticmethod
    def _states_concrete_change(text: str) -> bool:
        change_markers = (
            "只有",
            "改为",
            "变成",
            "增加",
            "减少",
            "提前",
            "推迟",
            "每天",
            "每日",
            "每周",
            "分钟",
            "小时",
            "截止",
        )
        return any(marker in text for marker in change_markers)
