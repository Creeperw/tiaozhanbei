"""组卷缺口批次与步骤预算的回归。

两个线上失败都来自同一类问题：同一个量在多处各自写死，任一处漏改就静默丢内容
或提前掐断。

1. 缺口批次上限决定"一次要模型写几道题""契约允许几项""兜底解析保留几行"。
   三处此前都是 5，任一处漏改都会丢掉模型已经写对的题目。
2. 组卷步骤要串行跑多批缺口生成，但两条入口（planner 动态计划与 smart_paper
   固定计划）给了不同的预算，同一任务会因入口不同拿到不同时间。
"""

from __future__ import annotations

import math

from competition_app.agents.paper_assembly import PaperAssemblyAgent
from competition_app.agents.planner import PlannerAgent, PlannerDecision
from competition_app.contracts.execution import (
    PAPER_ASSEMBLY_STEP_TIMEOUT_SECONDS,
)
from competition_app.llm.schemas import (
    PAPER_GAP_BATCH_SIZE,
    PaperGapGenerationModelOutput,
)


def gap_item(index: int) -> dict:
    return {
        "question_type": "简答题",
        "stem": f"缺口题 {index}：试述太阳病提纲证的辨证要点。",
        "options": [],
        "reference_answer": "脉浮、头项强痛而恶寒。",
        "analysis": "提纲证概括太阳病的基本脉证。",
        "rationale": "补足蓝图单元缺口。",
        "evidence_nos": [],
    }


def paper_generation_decision() -> PlannerDecision:
    return PlannerDecision(
        task_type="paper_generation",
        selected_agents=["memory_agent", "knowledge_base_agent", "expert_agent", "audit_agent"],
        routing_reason="用户要求生成试卷蓝图。",
        risk_level="medium",
        requires_audit=True,
    )


def test_gap_batch_size_is_shared_by_contract_and_agent() -> None:
    """契约允许的项数与智能体请求的题量必须同源。"""
    schema = PaperGapGenerationModelOutput.model_json_schema()

    assert schema["properties"]["generated_items"]["maxItems"] == PAPER_GAP_BATCH_SIZE
    assert PaperAssemblyAgent._GAP_BATCH_SIZE == PAPER_GAP_BATCH_SIZE


def test_fallback_parser_keeps_as_many_items_as_the_contract_allows() -> None:
    """兜底解析的保留行数必须跟上契约，否则多写的题会被静默丢掉。"""
    over_limit = PAPER_GAP_BATCH_SIZE + 3

    output = PaperAssemblyAgent._normalize_gap_response(
        {"generated_items": [gap_item(index) for index in range(over_limit)]}
    )

    assert len(output.generated_items) == PAPER_GAP_BATCH_SIZE


def test_gap_batch_size_keeps_a_full_paper_within_five_batches() -> None:
    """整卷 50 题所需的批次数必须留在个位数。

    线上实测单批缺口生成均值 266.6s（2026-09-17 r2e，5 题/批）。批次数直接
    乘以单批耗时，批次过小会让组卷步骤必然超过预算。
    """
    assert math.ceil(50 / PAPER_GAP_BATCH_SIZE) <= 5


def test_both_paper_entry_points_share_the_assembly_budget() -> None:
    """两条组卷入口给 paper_assembly 的预算必须相同。"""
    from competition_app.services.smart_paper import build_smart_paper_execution_plan

    smart_plan = build_smart_paper_execution_plan()
    planner_plan = PlannerAgent.build_plan(paper_generation_decision())

    smart_step = next(
        step for step in smart_plan.steps if step.step_id == "paper_assembly"
    )
    planner_step = next(
        step for step in planner_plan.steps if step.step_id == "paper_assembly"
    )

    assert smart_step.timeout_seconds == PAPER_ASSEMBLY_STEP_TIMEOUT_SECONDS
    assert planner_step.timeout_seconds == PAPER_ASSEMBLY_STEP_TIMEOUT_SECONDS


def test_assembly_budget_leaves_room_beyond_a_single_provider_call() -> None:
    """组卷要跑多批，预算必须严格大于单次调用护栏。

    只按"单次调用 + 清理余量"推导（2100s）会让第二个批次起就没有预算，
    实测 9 批累计 2399.4s 撞上 2400s 步骤预算。
    """
    single_call_ceiling = 1800.0 + 300.0

    assert PAPER_ASSEMBLY_STEP_TIMEOUT_SECONDS > single_call_ceiling
