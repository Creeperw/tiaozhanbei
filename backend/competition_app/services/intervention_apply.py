"""干预落地：把用户已接受的干预建议物化为当前每日任务中的原子任务项。

干预体系原先只负责「生成建议 → 通知 → 记录反馈」，用户点了「接受建议」
之后没有任何执行动作，建议从未变成真实任务。本模块补齐执行端：接受
建议时，把可物化的干预（如「安排错题复盘」）追加为今日任务的一个
原子任务项，并同步更新任务预算、内容描述与版本号。

不可物化的建议（如「保持当前计划」）不落地，反馈仅记录状态。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from competition_app.contracts.learning_plan import (
    LEARNING_INTERVENTION_ITEM_SOURCE,
    DailyTaskItemSpec,
)
from competition_app.services.plan_review_apply import apply_reduce_load

# 展示文案与执行操作必须解耦。规则候选提供确定性 execution_operation，
# LLM 仅决定 adjust/keep 并润色文案；旧生命周期仍兼容历史 action。
_REVIEW_OPERATIONS = {
    "add_mistake_review",
    "安排错题复盘",
    "复习薄弱知识点",
}
_REDUCE_LOAD_OPERATION = "reduce_load"

# 落地一项错题复盘的预计耗时（分钟），并同步增加父任务预算。
INTERVENTION_ADDED_MINUTES = 15.0

_ITEM_SOURCE = LEARNING_INTERVENTION_ITEM_SOURCE


def _focus_from_reason(primary: str, secondary: str = "") -> str:
    """从干预文案中尝试提取知识点描述，用于任务标题。

    文案通常形如「…重复出现的薄弱知识点（中医诊断学·舌诊、四君子汤）…」。
    优先在 summary 与 reason 中取括号内容，取不到则回退为通用表述。
    """
    for text in (str(primary or "").strip(), str(secondary or "").strip()):
        start = text.rfind("（")
        end = text.rfind("）")
        if 0 <= start < end:
            inside = text[start + 1 : end].strip()
            if inside and len(inside) <= 24:
                return inside
    return "近期薄弱知识点"


def _execution_operation(intervention: dict[str, Any]) -> str:
    snapshot = intervention.get("trigger_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    recommendation = snapshot.get("final_recommendation")
    recommendation = recommendation if isinstance(recommendation, dict) else {}
    candidate = snapshot.get("rule_candidate")
    candidate = candidate if isinstance(candidate, dict) else {}
    decision = snapshot.get("agent_decision")
    decision = decision if isinstance(decision, dict) else {}
    return str(
        recommendation.get("execution_operation")
        or candidate.get("execution_operation")
        or decision.get("operation")
        or intervention.get("action")
        or ""
    ).strip()


def _decision_summary(intervention: dict[str, Any]) -> str:
    snapshot = intervention.get("trigger_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    decision = snapshot.get("agent_decision")
    decision = decision if isinstance(decision, dict) else {}
    legacy_adjustment = decision.get("adjustment")
    legacy_adjustment = (
        legacy_adjustment if isinstance(legacy_adjustment, dict) else {}
    )
    recommendation = snapshot.get("final_recommendation")
    recommendation = recommendation if isinstance(recommendation, dict) else {}
    return str(
        decision.get("summary")
        or legacy_adjustment.get("summary")
        or recommendation.get("message")
        or decision.get("reason")
        or intervention.get("reason")
        or ""
    ).strip()


def apply_accepted_intervention(
    learning_plan_service: Any,
    learner_id: str,
    intervention: dict[str, Any],
) -> dict[str, Any]:
    """把已接受的干预建议落地到当前每日任务。

    ``intervention`` 为干预序列化结果（含 action / reason / trigger_snapshot）。

    返回 ``{"applied": bool, "reason": str, "summary": str, "title": str}``：
    - applied=True：已把干预物化为今日任务项，summary 为给用户看的文案；
    - applied=False：不可物化 / 已落地过 / 任务不可用，reason 说明原因。
    """
    current = learning_plan_service.get_current(learner_id)
    if current is None or current.learning_task is None:
        return {
            "applied": False,
            "already_applied": False,
            "retryable": True,
            "reason": "当前没有进行中的每日任务，无法安排。",
            "summary": "",
            "title": "",
        }
    task = current.learning_task
    operation = _execution_operation(intervention)
    if operation == _REDUCE_LOAD_OPERATION:
        result = apply_reduce_load(
            learning_plan_service,
            learner_id,
            {
                "intervention_id": intervention.get("intervention_id"),
                "input_snapshot": {},
            },
        )
        return {
            **result,
            "already_applied": bool(result.get("already_applied")),
            "retryable": bool(
                not result.get("applied")
                and not result.get("already_applied")
                and "请重试" in str(result.get("reason") or "")
            ),
            "title": (
                "今日任务减负"
                if result.get("applied") or result.get("already_applied")
                else ""
            ),
        }
    if operation not in _REVIEW_OPERATIONS:
        return {
            "applied": False,
            "already_applied": False,
            "retryable": False,
            "reason": "该建议当前没有可执行的任务变更，未标记为已接受。",
            "summary": "",
            "title": "",
        }

    intervention_id = intervention.get("intervention_id")
    for item in task.items:
        if str((item.resource_ref or {}).get("intervention_id") or "") == str(
            intervention_id or ""
        ) and (item.resource_ref or {}).get("source") == _ITEM_SOURCE:
            return {
                "applied": False,
                "already_applied": True,
                "retryable": False,
                "reason": "该干预已经安排进今日任务。",
                "summary": _decision_summary(intervention),
                "title": item.title,
            }

    summary = _decision_summary(intervention)
    focus = _focus_from_reason(summary, intervention.get("reason") or "")
    title = f"错题复盘：{focus}"

    new_item = DailyTaskItemSpec(
        task_item_id=f"ITM_INTERV_{uuid4().hex[:12]}",
        ordinal=len(task.items) + 1,
        item_type="recall",
        title=title,
        estimated_minutes=INTERVENTION_ADDED_MINUTES,
        knowledge_point_name=None,
        kp_id=None,
        required_question_count=None,
        resource_ref={
            "intervention_id": str(intervention_id or ""),
            "source": _ITEM_SOURCE,
            "summary": summary[:200],
        },
        completion_policy={},
    )
    items = list(task.items) + [new_item]
    timestamp = datetime.now(timezone.utc)
    note = f"【今日加练】已按你的确认安排：{title}。"
    task_content = f"{task.task_content}\n\n{note}"
    updated = task.model_copy(
        update={
            "items": items,
            "estimated_minutes": task.estimated_minutes + INTERVENTION_ADDED_MINUTES,
            "task_content": task_content,
            "version": task.version + 1,
            "updated_at": timestamp,
        }
    )
    saved = learning_plan_service.plan_repository.save_current(
        learner_id,
        current.model_copy(update={"learning_task": updated}),
        expected_task_id=task.task_id,
        expected_task_version=task.version,
    )
    if not saved:
        return {
            "applied": False,
            "already_applied": False,
            "retryable": True,
            "reason": "任务刚被刷新，请重试。",
            "summary": "",
            "title": "",
        }
    return {
        "applied": True,
        "already_applied": False,
        "retryable": False,
        "reason": "",
        "summary": summary,
        "title": title,
    }
