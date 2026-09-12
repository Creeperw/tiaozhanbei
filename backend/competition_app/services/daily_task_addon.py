"""干预 / 复盘加练项的物化。

用户接受「安排错题复盘」这类建议后，系统要在今日任务里补一项可执行内容。
历史实现直接生成 ``item_type="recall"`` 的原子项，而执行层只为
``knowledge_practice`` / ``video_section`` 提供完成入口：该项本身点不开，
又因为发布是整版原子校验，会把同版本里其它完全可执行的项一起拖死——用户
看到的是「今日任务全部点不开」。

这里改为把建议文案里的薄弱知识点解析成正式知识点 ID，生成真正的知识点
练习原子项。解析不出可执行知识点时宁可不加项，也不生成没有完成路径的
占位项：占位项对用户毫无价值，却会连累当天整份任务。
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from competition_app.contracts.learning_plan import DailyTaskItemSpec
from competition_app.services.learning_plan import resolve_executable_knowledge_point

# 加练项的题量。与知识点解析器保证的可执行题量一致，否则执行层会因为
# 冻结题量不足而丢弃该项。
ADDON_QUESTION_COUNT = 3

# 建议文案里多个知识点常用的罗列分隔符。这里只做分词，解析由知识点
# 解析器按正式 ID 完成，不做任何关键词层面的业务判断。
_FOCUS_SEPARATORS = ("、", "，", ",", "；", ";", "/")

# 文案提取不到具体知识点时的通用表述，本身不是知识点名称。
_GENERIC_FOCUS = "近期薄弱知识点"


def focus_candidates(focus_text: str) -> list[str]:
    """把建议文案里的知识点表述拆成候选名称。"""

    text = str(focus_text or "").strip()
    if not text:
        return []
    for separator in _FOCUS_SEPARATORS:
        text = text.replace(separator, "\n")
    candidates: list[str] = []
    for raw in text.split("\n"):
        name = raw.strip()
        if not name or name == _GENERIC_FOCUS or name in candidates:
            continue
        candidates.append(name)
    return candidates


def build_review_addon_items(
    *,
    resolver: Callable[..., str | None] | None,
    focus_text: str,
    learning_chapter: str,
    total_minutes: float,
    item_id_prefix: str,
    title_prefix: str,
    resource_ref: dict[str, Any],
    start_ordinal: int,
) -> tuple[list[DailyTaskItemSpec], list[str]]:
    """生成可执行的加练原子项。

    返回 ``(items, unresolved_names)``：``items`` 为空表示文案里的知识点
    一个都没解析成可执行知识点，调用方应当不加项并如实告知用户。
    """

    resolved: list[tuple[str, str]] = []
    unresolved: list[str] = []
    for name in focus_candidates(focus_text):
        kp_id = resolve_executable_knowledge_point(
            name, resolver, learning_chapter=learning_chapter
        )
        if not kp_id:
            unresolved.append(name)
            continue
        if any(kp_id == existing for _, existing in resolved):
            continue
        resolved.append((name, kp_id))
    if not resolved:
        return [], unresolved

    per_item_minutes = total_minutes / len(resolved)
    items: list[DailyTaskItemSpec] = []
    for offset, (name, kp_id) in enumerate(resolved):
        minutes = (
            total_minutes - per_item_minutes * (len(resolved) - 1)
            if offset == len(resolved) - 1
            else per_item_minutes
        )
        items.append(
            DailyTaskItemSpec(
                task_item_id=f"{item_id_prefix}{uuid4().hex[:12]}",
                ordinal=start_ordinal + offset,
                item_type="knowledge_practice",
                title=f"{title_prefix}{name}",
                estimated_minutes=max(minutes, 1.0),
                knowledge_point_name=name,
                kp_id=kp_id,
                required_question_count=ADDON_QUESTION_COUNT,
                resource_ref=dict(resource_ref),
                completion_policy={"policy": "frozen_question_set"},
            )
        )
    return items, unresolved
