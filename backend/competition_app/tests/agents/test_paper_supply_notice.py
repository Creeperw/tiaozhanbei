"""可用题目不足时，卷面必须如实说明题是怎么补足的。

单元范围内的正式题不够时，组卷会按缺口从「主知识点落在别的章节、但有次要
桥接命中本单元」的候选里补入题目。这些题多来自已经学过的知识点。学习者看到
卷子上有一部分题不属于本次指定的单元，必须能在卷面说明里找到原因，否则只会
觉得系统跑偏。

说明文案是系统确定性生成的卷面内容，只读候选池的结构化统计，不解析单元告警。
"""

from __future__ import annotations

from competition_app.agents.paper_assembly import PaperAssemblyAgent
from competition_app.contracts.paper import QuestionCandidatePool, UnitQuestionCandidates


def _candidate_pool(*borrowed_counts: int) -> QuestionCandidatePool:
    return QuestionCandidatePool(
        pool_id="POOL_1",
        blueprint_id="BLUEPRINT_1",
        units=[
            UnitQuestionCandidates(
                unit_id=f"UNIT_{index}",
                retrieval_query="《伤寒论》太阳病篇",
                requested_limit=50,
                required_question_count=40,
                borrowed_question_count=count,
            )
            for index, count in enumerate(borrowed_counts, start=1)
        ],
    )


def test_supply_notice_names_the_borrowed_questions() -> None:
    notice = PaperAssemblyAgent._build_supply_notice(
        candidate_pool=_candidate_pool(7, 0, 5)
    )

    assert "12" in notice
    assert "其他知识点" in notice
    assert "已经学过" in notice
    assert "单元内题目不足" in notice


def test_supply_notice_is_empty_when_nothing_was_borrowed() -> None:
    assert (
        PaperAssemblyAgent._build_supply_notice(candidate_pool=_candidate_pool(0, 0))
        == ""
    )


def test_candidate_pool_defaults_to_no_borrowed_questions() -> None:
    """旧调用方不传该字段时按 0 处理，不会凭空生成说明。"""

    unit = UnitQuestionCandidates(
        unit_id="UNIT_1",
        retrieval_query="《伤寒论》太阳病篇",
        requested_limit=50,
        required_question_count=40,
    )
    assert unit.borrowed_question_count == 0
    assert PaperAssemblyAgent._build_supply_notice(
        candidate_pool=QuestionCandidatePool(
            pool_id="POOL_1", blueprint_id="BLUEPRINT_1", units=[unit]
        )
    ) == ""
