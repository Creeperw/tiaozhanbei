"""加练原子项物化的回归测试。

背景：接受「安排错题复盘」后系统曾生成 ``item_type="recall"`` 的原子项，
该项没有完成路径（点不开），又因发布是整版原子校验而拖死当天所有任务项。
现在改为解析出正式知识点 ID 后生成知识点练习项；解析不出就不加项。
"""

from __future__ import annotations

from competition_app.services.daily_task_addon import (
    ADDON_QUESTION_COUNT,
    build_review_addon_items,
    focus_candidates,
)


def _resolver(mapping: dict[str, str]):
    def resolve(name: str, chapter: str = "") -> str | None:
        return mapping.get(name)

    return resolve


def test_focus_candidates_splits_common_separators() -> None:
    assert focus_candidates("中医诊断学·舌诊、四君子汤") == [
        "中医诊断学·舌诊",
        "四君子汤",
    ]
    assert focus_candidates("舌诊，四君子汤；补气剂/归脾汤") == [
        "舌诊",
        "四君子汤",
        "补气剂",
        "归脾汤",
    ]


def test_focus_candidates_skips_generic_placeholder_and_duplicates() -> None:
    assert focus_candidates("") == []
    assert focus_candidates("近期薄弱知识点") == []
    assert focus_candidates("四君子汤、四君子汤") == ["四君子汤"]


def test_build_review_addon_items_returns_nothing_when_nothing_resolves() -> None:
    items, unresolved = build_review_addon_items(
        resolver=_resolver({}),
        focus_text="中医诊断学·舌诊",
        learning_chapter="《方剂学》补益剂",
        total_minutes=15.0,
        item_id_prefix="ITM_INTERV_",
        title_prefix="错题复盘：",
        resource_ref={"source": "learning_intervention"},
        start_ordinal=1,
    )

    assert items == []
    assert unresolved == ["中医诊断学·舌诊"]


def test_build_review_addon_items_creates_executable_practice_items() -> None:
    items, unresolved = build_review_addon_items(
        resolver=_resolver({"中医诊断学·舌诊": "KP_TONGUE", "四君子汤": "KP_SIJUNZI"}),
        focus_text="中医诊断学·舌诊、四君子汤",
        learning_chapter="《方剂学》补益剂",
        total_minutes=15.0,
        item_id_prefix="ITM_INTERV_",
        title_prefix="错题复盘：",
        resource_ref={"source": "learning_intervention", "intervention_id": "13"},
        start_ordinal=3,
    )

    assert unresolved == []
    assert [item.item_type for item in items] == ["knowledge_practice", "knowledge_practice"]
    assert [item.kp_id for item in items] == ["KP_TONGUE", "KP_SIJUNZI"]
    assert [item.ordinal for item in items] == [3, 4]
    assert [item.title for item in items] == [
        "错题复盘：中医诊断学·舌诊",
        "错题复盘：四君子汤",
    ]
    assert all(item.required_question_count == ADDON_QUESTION_COUNT for item in items)
    assert all(
        item.completion_policy == {"policy": "frozen_question_set"} for item in items
    )
    assert sum(item.estimated_minutes for item in items) == 15.0
    assert all(item.task_item_id.startswith("ITM_INTERV_") for item in items)
    assert all(
        item.resource_ref == {"source": "learning_intervention", "intervention_id": "13"}
        for item in items
    )


def test_build_review_addon_items_deduplicates_the_same_knowledge_point() -> None:
    items, unresolved = build_review_addon_items(
        resolver=_resolver({"舌诊": "KP_TONGUE", "中医诊断学·舌诊": "KP_TONGUE"}),
        focus_text="舌诊、中医诊断学·舌诊",
        learning_chapter="",
        total_minutes=10.0,
        item_id_prefix="ITM_REVIEW_",
        title_prefix="到期复习：",
        resource_ref={},
        start_ordinal=1,
    )

    assert unresolved == []
    assert len(items) == 1
    assert items[0].kp_id == "KP_TONGUE"
    assert items[0].estimated_minutes == 10.0


def test_build_review_addon_items_keeps_only_resolvable_names() -> None:
    items, unresolved = build_review_addon_items(
        resolver=_resolver({"四君子汤": "KP_SIJUNZI"}),
        focus_text="中医诊断学·舌诊、四君子汤",
        learning_chapter="",
        total_minutes=12.0,
        item_id_prefix="ITM_INTERV_",
        title_prefix="错题复盘：",
        resource_ref={},
        start_ordinal=1,
    )

    assert unresolved == ["中医诊断学·舌诊"]
    assert [item.kp_id for item in items] == ["KP_SIJUNZI"]
    assert items[0].estimated_minutes == 12.0


def test_build_review_addon_items_without_resolver_adds_nothing() -> None:
    items, unresolved = build_review_addon_items(
        resolver=None,
        focus_text="四君子汤",
        learning_chapter="",
        total_minutes=10.0,
        item_id_prefix="ITM_INTERV_",
        title_prefix="错题复盘：",
        resource_ref={},
        start_ordinal=1,
    )

    assert items == []
    assert unresolved == ["四君子汤"]
