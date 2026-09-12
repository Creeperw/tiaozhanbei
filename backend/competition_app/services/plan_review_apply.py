"""规划复盘落地：把用户接受的复盘建议真正执行到计划里。

复盘体系原先只负责「规则/智能体生成建议 → 通知 → 记录决策」，用户点
「接受调整」后没有任何执行动作。本模块补齐执行端，按 proposal.operation
分派四类落地器：

- reduce_load          当日任务减负（同步）：削减今日 knowledge_practice
                       题数、删除非核心练习，保留视频与每日测验；
- add_review_window    短期级联（异步）：由协调器更新短期计划并重新物化
                       今日任务；无协调器的旧直接调用保留 recall fallback；
- replan_for_low_completion  触发多智能体重规划（异步）：复用聊天工作流，
                       由规划智能体强制重写短期计划与后续任务；
- slow_progress        异步短期级联：由协调器把低负载预期固化到短期与每日层。

所有落地器按 review_id 幂等（决策状态机 proposal_pending → accepted 单向），
失败不阻断反馈保存。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from competition_app.contracts.learning_plan import DailyTaskItemSpec
from competition_app.services.daily_task_addon import build_review_addon_items

# 减负比例：按连续低完成天数/执行率分级。
_REDUCE_RATIO_HEAVY = 0.5   # 连续 3+ 天低完成或执行率 < 0.25
_REDUCE_RATIO_NORMAL = 0.3  # 一般低完成

# 到期复习落地一项的预计耗时（分钟）。
REVIEW_WINDOW_ADDED_MINUTES = 15.0

_ITEM_SOURCE = "plan_review"

# 异步重规划启动器签名：
#   replan_starter(learner_id: str, review: dict) -> None
# 由调用方（app 层）实现，负责启动后台工作流并消费结果。
ReplanStarter = Callable[[str, dict[str, Any]], None]


def _focus_from_reason(primary: str, secondary: str = "") -> str:
    """从复盘文案提取知识点描述；与干预落地共用同一规则。"""
    for text in (str(primary or "").strip(), str(secondary or "").strip()):
        start = text.rfind("（")
        end = text.rfind("）")
        if 0 <= start < end:
            inside = text[start + 1 : end].strip()
            if inside and len(inside) <= 24:
                return inside
    return "到期复习知识点"


def _rebuild_task_text(task: Any, items: list[DailyTaskItemSpec]) -> tuple[str, str, str]:
    """按 items 重建 task_content / expected_output / completion_criteria。"""
    practice_items = [
        item for item in items if item.item_type == "knowledge_practice"
    ]
    exercise_items = [
        item for item in practice_items if not item.completion_policy.get("quiz")
    ]
    video_count = sum(item.item_type == "video_section" for item in items)
    question_count = sum(
        int(item.required_question_count or 0) for item in exercise_items
    )
    expected_parts = []
    if video_count:
        expected_parts.append(f"{video_count}条章节视频观看记录")
    if question_count:
        expected_parts.append(f"{question_count}道配套题提交记录")
    expected_output = "与".join(expected_parts) or str(task.expected_output or "")
    completion_criteria = (
        f"完成全部{len(items)}个原子任务"
        f"（{video_count}个章节视频、{len(exercise_items)}个知识点共"
        f"{question_count}道题）；以服务端记录全部完成为通过标准。"
    )
    executable_steps = "；".join(
        str(item.title or "").strip() for item in items
    )
    chapter = str(task.learning_chapter or "").strip()
    task_content = (
        f"今日围绕{chapter}学习：{executable_steps}。"
        if chapter
        else f"今日执行：{executable_steps}。"
    )
    return task_content, expected_output, completion_criteria


def apply_reduce_load(
    learning_plan_service: Any,
    learner_id: str,
    review: dict[str, Any],
) -> dict[str, Any]:
    current = learning_plan_service.get_current(learner_id)
    if current is None or current.learning_task is None:
        return {"applied": False, "reason": "当前没有进行中的每日任务，无法减负。", "summary": ""}
    task = current.learning_task
    intervention_id = str(review.get("intervention_id") or "").strip()
    intervention_marker = (
        f"[learning-intervention:{intervention_id}]" if intervention_id else ""
    )
    if intervention_marker and intervention_marker in str(task.task_content or ""):
        return {
            "applied": False,
            "already_applied": True,
            "reason": "该干预已经应用到今日任务。",
            "summary": "今日任务减负已经生效。",
        }
    if not task.items:
        return {"applied": False, "reason": "今日任务没有可削减的原子项。", "summary": ""}

    snapshot = review.get("input_snapshot") or {}
    dimensions = snapshot.get("dimensions") or {}
    streak = int(snapshot.get("low_completion_streak_days") or 0)
    execution = None
    if isinstance(dimensions, dict):
        execution = dimensions.get("execution")
    if isinstance(execution, bool) or not isinstance(execution, (int, float)):
        execution = None
    ratio = (
        _REDUCE_RATIO_HEAVY
        if streak >= 3 or (execution is not None and float(execution) < 0.25)
        else _REDUCE_RATIO_NORMAL
    )

    items: list[DailyTaskItemSpec] = []
    for item in task.items:
        if item.item_type == "knowledge_practice" and not item.completion_policy.get("quiz"):
            count = int(item.required_question_count or 0)
            reduced = max(1, int(round(count * (1 - ratio))))
            if reduced < count:
                items.append(
                    item.model_copy(
                        update={
                            "required_question_count": reduced,
                            "estimated_minutes": round(
                                item.estimated_minutes
                                * reduced
                                / max(1, count),
                                1,
                            ),
                        }
                    )
                )
            else:
                items.append(item)
        else:
            items.append(item)

    reduced_total = sum(
        int(item.required_question_count or 0)
        for item in items
        if item.item_type == "knowledge_practice"
        and not item.completion_policy.get("quiz")
    )
    original_total = sum(
        int(item.required_question_count or 0)
        for item in task.items
        if item.item_type == "knowledge_practice"
        and not item.completion_policy.get("quiz")
    )
    if reduced_total >= original_total:
        return {"applied": False, "reason": "今日任务已很精简，无需再削减。", "summary": ""}

    # 重排 ordinal 并重建文案。
    items = [
        item.model_copy(update={"ordinal": index + 1})
        for index, item in enumerate(items)
    ]
    task_content, expected_output, completion_criteria = _rebuild_task_text(task, items)
    note = (
        f"【今日减负】已按你的确认减少今日任务量"
        f"（配套题从{original_total}道减至{reduced_total}道）。"
    )
    if intervention_marker:
        note = f"{note}\n{intervention_marker}"
    task_content = f"{task_content}\n\n{note}"
    timestamp = datetime.now(timezone.utc)
    updated = task.model_copy(
        update={
            "items": items,
            "estimated_minutes": max(
                1.0, sum(item.estimated_minutes for item in items)
            ),
            "task_content": task_content,
            "expected_output": expected_output,
            "completion_criteria": completion_criteria,
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
        return {"applied": False, "reason": "任务刚被刷新，请重试。", "summary": ""}
    return {
        "applied": True,
        "reason": "",
        "summary": f"今日配套题已从{original_total}道减至{reduced_total}道，先完成核心内容。",
    }


def _apply_add_review_window(
    learning_plan_service: Any,
    learner_id: str,
    review: dict[str, Any],
) -> dict[str, Any]:
    current = learning_plan_service.get_current(learner_id)
    if current is None or current.learning_task is None:
        return {"applied": False, "reason": "当前没有进行中的每日任务，无法安排复习。", "summary": ""}
    task = current.learning_task

    review_id = review.get("review_id")
    for item in task.items:
        if str((item.resource_ref or {}).get("review_id") or "") == str(
            review_id or ""
        ) and (item.resource_ref or {}).get("source") == _ITEM_SOURCE:
            return {"applied": False, "reason": "该复盘建议已经安排进今日任务。", "summary": ""}

    focus = _focus_from_reason(
        review.get("summary") or "",
        " ".join(review.get("evidence") or []),
    )
    addon_items, unresolved = build_review_addon_items(
        resolver=getattr(learning_plan_service, "knowledge_point_resolver", None),
        focus_text=focus,
        learning_chapter=str(getattr(task, "learning_chapter", "") or ""),
        total_minutes=REVIEW_WINDOW_ADDED_MINUTES,
        item_id_prefix="ITM_REVIEW_",
        title_prefix="到期复习：",
        resource_ref={
            "review_id": str(review_id or ""),
            "source": _ITEM_SOURCE,
            "summary": str(review.get("summary") or "")[:200],
        },
        start_ordinal=len(task.items) + 1,
    )
    if not addon_items:
        # 与干预加练项同理：不加没有完成路径的占位项，如实告知用户。
        return {
            "applied": False,
            "reason": (
                "该复盘建议涉及的知识点当前没有可用的练习题，暂未安排进今日任务。"
                + (f"（未解析出：{'、'.join(unresolved)}）" if unresolved else "")
            ),
            "summary": "",
        }

    items = list(task.items) + addon_items
    titles = [item.title for item in addon_items]
    task_content, expected_output, completion_criteria = _rebuild_task_text(task, items)
    note = f"【今日加练】已按你的确认安排：{'、'.join(titles)}。"
    task_content = f"{task_content}\n\n{note}"
    timestamp = datetime.now(timezone.utc)
    updated = task.model_copy(
        update={
            "items": items,
            "estimated_minutes": task.estimated_minutes + REVIEW_WINDOW_ADDED_MINUTES,
            "task_content": task_content,
            "expected_output": expected_output,
            "completion_criteria": completion_criteria,
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
        return {"applied": False, "reason": "任务刚被刷新，请重试。", "summary": ""}
    return {
        "applied": True,
        "reason": "",
        "summary": f"已把到期复习「{'、'.join(titles)}」安排进今日任务。",
    }


def _apply_replan(
    learner_id: str,
    review: dict[str, Any],
    replan_starter: ReplanStarter | None,
) -> dict[str, Any]:
    if replan_starter is None:
        return {"applied": False, "reason": "重规划服务暂不可用，请稍后再试。", "summary": ""}
    try:
        replan_starter(learner_id, review)
    except Exception:
        return {"applied": False, "reason": "重规划启动失败，请稍后再试。", "summary": ""}
    return {
        "applied": True,
        "replan_started": True,
        "execution_status": "queued",
        "reason": "",
        "summary": "已启动多智能体重规划，完成后会通过通知提醒你。",
    }


def apply_accepted_plan_review(
    learning_plan_service: Any,
    learner_id: str,
    review: dict[str, Any],
    *,
    replan_starter: ReplanStarter | None = None,
) -> dict[str, Any]:
    """按复盘建议的 operation 分派落地器。

    返回 ``{"applied": bool, "reason": str, "summary": str}``。
    """
    proposal = review.get("proposal") or {}
    operation = str(proposal.get("operation") or "").strip()
    target_layer = str(proposal.get("target_layer") or "").strip()
    allowed_pairs = {
        ("daily_task", "reduce_load"),
        ("short_term", "add_review_window"),
        ("short_term", "replan_for_low_completion"),
        ("short_term", "slow_progress"),
    }
    if operation and (target_layer, operation) not in allowed_pairs:
        return {
            "applied": False,
            "reason": "复盘建议的层级与操作不一致，已阻止执行。",
            "summary": "",
        }
    if operation == "reduce_load":
        return apply_reduce_load(learning_plan_service, learner_id, review)
    if operation == "add_review_window":
        if replan_starter is not None:
            return _apply_replan(learner_id, review, replan_starter)
        # Direct service callers from older integrations do not provide the
        # coordinator. Keep the original deterministic fallback; the API
        # always supplies a starter for the real short-term cascade.
        return _apply_add_review_window(learning_plan_service, learner_id, review)
    if operation == "replan_for_low_completion":
        return _apply_replan(learner_id, review, replan_starter)
    if operation == "slow_progress":
        if replan_starter is None:
            return apply_reduce_load(learning_plan_service, learner_id, review)
        return _apply_replan(learner_id, review, replan_starter)
    return {"applied": False, "reason": "该建议无需额外落地，已记录。", "summary": ""}
