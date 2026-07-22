from __future__ import annotations

import re
from typing import Literal


PlanScope = Literal["long_term", "short_term", "daily_task", "unspecified"]


_DIRECT_DAILY_QUESTION = re.compile(
    r"(?:当日|今日|今天|今晚)[^，。；！？?]{0,18}"
    r"(?:学(?:习)?(?:些)?什么|学啥|要学|该学|看什么|看啥|做什么|做啥|复习什么|练什么)"
)
_EXPLICIT_DAILY_TASK = re.compile(
    r"(?:安排|制定|生成|更新|调整|给我|再给我|来一个)[^，。；！？?]{0,18}"
    r"(?:当日|今日|今天|今晚)(?:的)?(?:学习)?任务"
)
_SHORT_TERM_TARGET = re.compile(
    r"(?:制定|生成|调整|修改|重做|给我|来一份)?"
    r"[^，。；！？?]{0,18}(?:短期(?:学习)?(?:规划|计划)|本周(?:学习|复习)?(?:规划|计划|任务)|这周(?:学习|复习)?(?:规划|计划|任务))"
)
_LONG_TERM_TARGET = re.compile(
    r"(?:制定|生成|调整|修改|重做|给我|来一份)?"
    r"[^，。；！？?]{0,18}(?:长期(?:学习)?(?:规划|计划)|教材路线|阶段路线)"
)

_NON_PLANNING_TASK = re.compile(
    r"(?:讲解|解释|介绍|为什么|是什么|组卷|试卷|模拟卷|测试卷|"
    r"知识卡|学习卡|复习卡|出题|练习题|案例训练)"
)
_PLAN_CLARIFICATION_ANSWER = re.compile(
    r"(?:不对|更正|改成|其实|补充|目标(?:是|改为)?|"
    r"我(?:想|要|准备)考|零基础|学习基础|学过|专业|"
    r"每周|每天|一周|一天|小时|分钟|上午|下午|晚上|早晨)"
)


def infer_plan_scope(user_request: str) -> PlanScope | None:
    """Return a high-confidence scope hint without making the routing decision.

    Planner remains authoritative. This helper only supplies a deterministic
    fallback when model output is absent or malformed, so it intentionally
    recognizes explicit plan-layer wording and leaves vague learning requests
    unclassified.
    """

    request = str(user_request or "").strip()
    if not request:
        return None
    if _DIRECT_DAILY_QUESTION.search(request) or _EXPLICIT_DAILY_TASK.search(request):
        return "daily_task"
    planning_words = ("计划", "规划", "安排", "任务", "制定", "调整", "修改")
    if any(word in request for word in planning_words):
        has_short_term = any(
            word in request
            for word in ("短期", "本周", "这周", "下周", "近期", "未来一周", "未来两周")
        )
        has_long_term = any(
            word in request for word in ("长期", "教材路线", "阶段路线")
        )
        if has_short_term and has_long_term:
            long_term_is_parent = re.search(
                r"(?:根据|基于|按照|结合)[^，。；！？?]{0,20}"
                r"(?:长期(?:学习)?(?:规划|计划)|教材路线|阶段路线)",
                request,
            )
            if long_term_is_parent:
                return "short_term"
            return "unspecified"
        if has_short_term:
            return "short_term"
        if has_long_term:
            return "long_term"
    if _SHORT_TERM_TARGET.search(request):
        return "short_term"
    if _LONG_TERM_TARGET.search(request):
        return "long_term"
    return None


def infer_continued_plan_scope(
    user_request: str,
    messages: list[dict[str, str]],
) -> PlanScope | None:
    """Recover a plan scope for a terse clarification or correction turn.

    This is conversation-state recovery, not a general keyword router.  It is
    deliberately limited to profile/goal/time answers that follow an explicit
    planning turn.  A new explanation, paper, resource, or practice request is
    allowed to switch away from planning normally.
    """

    request = str(user_request or "").strip()
    if not request or infer_plan_scope(request) is not None:
        return None
    if _NON_PLANNING_TASK.search(request):
        return None
    if not _PLAN_CLARIFICATION_ANSWER.search(request):
        return None

    prior_messages = list(messages)
    if (
        prior_messages
        and str(prior_messages[-1].get("role", "")) == "user"
        and str(prior_messages[-1].get("content", "")).strip() == request
    ):
        prior_messages = prior_messages[:-1]

    for item in reversed(prior_messages[-10:]):
        content = str(item.get("content", "") or "").strip()
        scope = infer_plan_scope(content)
        if scope in {"long_term", "short_term", "daily_task"}:
            return scope
        if "长期规划已经" in content or "长期计划已经" in content:
            return "long_term"
        if "短期规划已经" in content or "短期计划已经" in content:
            return "short_term"
        if "当日任务已经" in content or "今日任务已经" in content:
            return "daily_task"
    return None
