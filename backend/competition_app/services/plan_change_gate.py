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
    ) -> PlanChangeDecision:
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
