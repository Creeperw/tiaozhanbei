"""候选不足时按单元内的知识点回填网络题，并走同一套准入判定。

背景：正式题库里有一部分知识点根本挂不到题（全库 73,777 个知识点中 47,272
个没有任何题目链接），单元需要的题量只能靠现场生成。同时组卷时已经搜到过网络
练习题材料，但它们只被当作证据（截 2 条、每条 600 字），从未进入候选池。

这里锁住三件事：

1. 回填只在正式候选不足时触发，且只补单元准入范围内的知识点；
2. 网络题构造成与其他候选同形的 ``QuestionDetail``：来源标注为
   ``web_reference``、难度保持 None、桥接绑到该知识点的真实 ID；
3. 网络题之后走同一套准入判定，不享受特殊放行——范围判定失败时它与其他
   候选一样被拒。

另有一条同样重要的约束：**组卷不等灌题**。清洗实测 152～379 秒，而组卷只有
分钟级预算；在组卷内等待必然提前掐断清洗（线上该功能因此一道题都没进过卷）。
所以组卷只读已入库的题，灌题由 ``schedule_backfill`` 投到后台。
"""

from __future__ import annotations

from typing import Any

import pytest

from competition_app.agents.knowledge_base import KnowledgeBaseAgent
from competition_app.contracts.knowledge import EvidencePack, QuestionDetail
from competition_app.runtime.event_stream import bind_event_sink, reset_event_sink

pytestmark = pytest.mark.asyncio


def _unit(**overrides: Any) -> Any:
    values = {
        "unit_id": "U1",
        "knowledge_module": "伤寒论·太阳病篇",
        "learning_objective": "掌握太阳病辨证",
        "retrieval_query": "伤寒论 太阳病 辨证",
        "question_type_preferences": ["简答题"],
        "assessment_dimensions": [],
        "excluded_dimensions": [],
    }
    values.update(overrides)
    return type("_Unit", (), values)()


def _retrieved_item(question_id: str, kp_ids: list[str]) -> QuestionDetail:
    """一道正式候选：``source_metadata`` 带结构化知识点目录。"""
    return QuestionDetail.model_validate(
        {
            "question_id": question_id,
            "question_type": "简答题",
            "stem": f"{question_id} 的题干",
            "reference_answer": "答案",
            "analysis": None,
            "options": [],
            "origin": "retrieved",
            "source_tier": "textbook",
            "difficulty": None,
            "tags": [],
            "source_metadata": {
                "knowledge_points": [
                    {"kp": {"kp_id": kp_id, "kp_lv3": f"知识点{kp_id}"}}
                    for kp_id in kp_ids
                ]
            },
            "bridges": [
                {
                    "kp_id": kp_id,
                    "bridge_layer": "strict",
                    "relation": "primary",
                    "confidence": 1.0,
                    "rank": index,
                    "evidence_chunk_uid": "",
                    "match_method": "strict",
                }
                for index, kp_id in enumerate(kp_ids, start=1)
            ],
            "retrieval": {
                "channels": ["bridge"],
                "channel_scores": {"bridge": 1.0},
                "fusion_score": 1.0,
            },
        }
    )


class _FakeIngest:
    """假灌题服务：已入库的题按知识点名返回，并记录后台投递。

    组卷只读已入库的题（``web_questions_for``），灌题走 ``schedule_backfill``
    投到后台——组卷不等检索与清洗。
    """

    def __init__(
        self,
        rows_by_name: dict[str, list[dict[str, Any]]] | None = None,
        *,
        schedule_result: bool = True,
    ) -> None:
        self.rows_by_name = rows_by_name or {}
        self.schedule_result = schedule_result
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.reads: list[str] = []

    def web_questions_for(self, name: str) -> list[dict[str, Any]]:
        self.reads.append(name)
        return [dict(row) for row in self.rows_by_name.get(name, [])]

    def schedule_backfill(self, name: str, **kwargs: Any) -> bool:
        self.calls.append((name, tuple(kwargs.get("question_types") or ())))
        return self.schedule_result


def _agent(ingest: Any) -> KnowledgeBaseAgent:
    retrieval_tool = type(
        "_Tool", (), {"delivery_backend": object()}
    )()
    return KnowledgeBaseAgent(retrieval_tool, web_question_ingest=ingest)  # type: ignore[arg-type]


def _web_row(question_id: str, stem: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "question_id": question_id,
        "stem": stem,
        "question_type": "简答题",
        "answer": "参考答案",
        "analysis": "解析",
        "options": [],
        "source_urls": ["https://example.com/quiz"],
        "source_ref": "web://知识点KP_A",
    }
    row.update(overrides)
    return row


async def _backfill(agent: KnowledgeBaseAgent, **kwargs: Any):
    defaults: dict[str, Any] = {
        "unit": _unit(),
        "items": [_retrieved_item("Q1", ["KP_A"])],
        "evidence_pack": EvidencePack(
            evidence_pack_id="EP_TEST",
            query="测试",
            resolved_kp_names={"KP_A": "知识点KP_A", "KP_B": "知识点KP_B"},
        ),
        "scope_kp_ids": ["KP_A", "KP_B"],
        "existing_ids": {"Q1"},
    }
    defaults.update(kwargs)
    return await agent._backfill_web_question_candidates(**defaults)


async def test_backfill_targets_the_scarcest_knowledge_points_in_scope():
    """候选最少的知识点优先补：它们正是题库挂不到题的位置。"""
    ingest = _FakeIngest(
        {
            "知识点KP_A": [_web_row("WEBQ_A", "A 的网络题")],
            "知识点KP_B": [_web_row("WEBQ_B", "B 的网络题")],
        }
    )
    agent = _agent(ingest)

    # KP_A 已有 1 道候选，KP_B 没有 → KP_B 排前面。
    candidates, notes = await _backfill(agent)

    assert [name for name, _ in ingest.calls] == ["知识点KP_B", "知识点KP_A"]
    assert {item.question_id for item in candidates} == {"WEBQ_A", "WEBQ_B"}
    assert notes


async def test_backfill_never_leaves_the_unit_scope():
    """回填只针对单元准入范围内的知识点，不会越过单元边界。"""
    ingest = _FakeIngest({"知识点KP_C": [_web_row("WEBQ_C", "范围外网络题")]})
    agent = _agent(ingest)

    candidates, _ = await _backfill(
        agent,
        scope_kp_ids=["KP_C"],
        evidence_pack=EvidencePack(
            evidence_pack_id="EP_TEST",
            query="测试",
            resolved_kp_names={"KP_C": "知识点KP_C"},
        ),
    )

    assert [name for name, _ in ingest.calls] == ["知识点KP_C"]
    assert [item.question_id for item in candidates] == ["WEBQ_C"]


async def test_backfill_can_name_knowledge_points_without_any_candidate():
    """没有任何候选的知识点也要能回填。

    范围判定失败时准入范围退回按名称召回的命中列表，里面包含完全没有候选的
    知识点；只看候选自带的目录会拿不到它们的名字，恰好漏掉最需要补的位置。
    """
    ingest = _FakeIngest({"知识点KP_Z": [_web_row("WEBQ_Z", "Z 的网络题")]})
    agent = _agent(ingest)

    candidates, _ = await _backfill(
        agent,
        scope_kp_ids=["KP_Z"],
        evidence_pack=EvidencePack(
            evidence_pack_id="EP_TEST",
            query="测试",
            resolved_kp_names={"KP_Z": "知识点KP_Z"},
        ),
    )

    assert [name for name, _ in ingest.calls] == ["知识点KP_Z"]
    assert [item.question_id for item in candidates] == ["WEBQ_Z"]


async def test_web_candidates_are_labeled_and_bound_to_the_real_knowledge_point():
    """网络题的来源、难度与知识点绑定必须如实标注。"""
    ingest = _FakeIngest({"知识点KP_B": [_web_row("WEBQ_B", "B 的网络题")]})
    agent = _agent(ingest)

    candidates, _ = await _backfill(agent)

    candidate = candidates[0]
    assert candidate.source_tier == "web_reference"
    assert candidate.origin == "retrieved"
    # 网络题没有真实难度标注，不得推断出难度值冒充指定难度。
    assert candidate.difficulty is None
    # 绑到真实知识点 ID，不是 WEB_ 之类的合成 ID。
    assert [bridge.kp_id for bridge in candidate.bridges] == ["KP_B"]
    assert candidate.bridges[0].relation == "primary"
    assert candidate.source_metadata["knowledge_points"][0]["kp"]["kp_id"] == "KP_B"


async def test_backfill_carries_the_unit_question_types_into_the_search():
    """单元需要的题型要进入检索词（线上实测：需要简答题却搜到选择题试卷）。"""
    ingest = _FakeIngest({"知识点KP_B": [_web_row("WEBQ_B", "B 的网络题")]})
    agent = _agent(ingest)

    await _backfill(agent)

    assert ingest.calls[0][1] == ("简答题",)


async def test_backfill_skips_questions_already_in_the_candidate_pool():
    """同一道网络题不会因重复检索而重复入卷。"""
    ingest = _FakeIngest({"知识点KP_B": [_web_row("WEBQ_B", "B 的网络题")]})
    agent = _agent(ingest)

    candidates, _ = await _backfill(agent, existing_ids={"WEBQ_B"})

    assert candidates == []


async def test_backfill_is_disabled_without_the_ingest_service():
    """未配置回填服务时组卷按原路径走，不会因此失败，但仍要可诊断。"""
    agent = KnowledgeBaseAgent(type("_Tool", (), {"delivery_backend": object()})())  # type: ignore[arg-type]
    events: list[dict[str, Any]] = []
    token = bind_event_sink(events.append)
    try:
        candidates, notes = await _backfill(agent)
    finally:
        reset_event_sink(token)

    assert candidates == []
    assert notes == []
    skipped = [event for event in events if event["event"] == "paper_unit_web_backfill"]
    assert skipped and skipped[0]["skipped"] == "ingest_unavailable"


async def test_web_candidate_is_admitted_by_the_same_scope_rules():
    """网络题走同一套准入判定：范围外一样被拒，不享受特殊放行。"""
    ingest = _FakeIngest({"知识点KP_B": [_web_row("WEBQ_B", "B 的网络题")]})
    agent = _agent(ingest)
    unit = _unit()

    candidates, _ = await _backfill(agent)
    candidate = candidates[0]

    # 本单元范围含 KP_B → 与正式候选同判 eligible。
    assert agent._candidate_admission(candidate, unit, ["KP_B"])[0] == "eligible"
    # 范围不含 KP_B → 同样被拒，网络题不因来源而放行。
    assert agent._candidate_admission(candidate, unit, ["KP_OTHER"])[0] == "rejected"


async def test_backfill_reports_nothing_when_the_scope_is_empty():
    """范围为空且单元主题没有召回知识点时，如实报告跳过，不静默返回。

    这条路径此前既不写告警也不发事件，线上因此无法区分「本单元没有缺口」和
    「回填被跳过」——一次完整组卷里 ``paper_unit_web_backfill`` 出现 0 次，
    只能靠排除法反推空范围。跳过原因必须同时进单元告警与运行事件。
    """
    ingest = _FakeIngest({"知识点KP_B": [_web_row("WEBQ_B", "B 的网络题")]})
    agent = _agent(ingest)
    events: list[dict[str, Any]] = []
    token = bind_event_sink(events.append)
    try:
        candidates, notes = await _backfill(agent, scope_kp_ids=[])
    finally:
        reset_event_sink(token)

    assert candidates == []
    assert ingest.calls == []
    assert notes, "跳过回填必须如实告知学习者"
    skipped = [event for event in events if event["event"] == "paper_unit_web_backfill"]
    assert skipped and skipped[0]["skipped"] == "empty_scope"


async def test_backfill_falls_back_to_the_unit_name_recall_when_scope_is_empty():
    """范围为空时改取单元主题召回的知识点，并标注目标来源。

    范围判定结论为空说明本轮候选全部越界，但「本单元正式题库没有题」恰恰是
    回填要处理的情形，不能因此静默跳过。目标改取单元主题按名称召回的结构化
    知识点（是检索结果，不是对自然语言的模式匹配），来源写进运行事件。
    """
    ingest = _FakeIngest({"知识点KP_Z": [_web_row("WEBQ_Z", "Z 的网络题")]})
    agent = _agent(ingest)
    events: list[dict[str, Any]] = []
    token = bind_event_sink(events.append)
    try:
        candidates, _ = await _backfill(
            agent,
            scope_kp_ids=[],
            evidence_pack=EvidencePack(
                evidence_pack_id="EP_RECALL",
                query="测试",
                resolved_kp_ids=["KP_Z"],
                resolved_kp_names={"KP_Z": "知识点KP_Z"},
            ),
        )
    finally:
        reset_event_sink(token)

    assert [name for name, _ in ingest.calls] == ["知识点KP_Z"]
    assert [item.question_id for item in candidates] == ["WEBQ_Z"]
    filled = [event for event in events if event["event"] == "paper_unit_web_backfill"]
    assert filled and filled[0]["target_source"] == "recall_fallback"
    assert filled[0]["skipped"] == ""


async def test_backfill_emits_an_event_when_the_delivery_backend_is_missing():
    """交付后端缺失时回填不执行，同样要留下可诊断的事件。"""
    ingest = _FakeIngest({"知识点KP_B": [_web_row("WEBQ_B", "B 的网络题")]})
    agent = KnowledgeBaseAgent(  # type: ignore[arg-type]
        type("_Tool", (), {"delivery_backend": None})(), web_question_ingest=ingest
    )
    events: list[dict[str, Any]] = []
    token = bind_event_sink(events.append)
    try:
        candidates, notes = await _backfill(agent)
    finally:
        reset_event_sink(token)

    assert candidates == []
    assert notes == []
    skipped = [event for event in events if event["event"] == "paper_unit_web_backfill"]
    assert skipped and skipped[0]["skipped"] == "delivery_unavailable"


async def test_backfill_reads_ingested_rows_without_waiting_for_ingestion():
    """组卷只读已入库的题，不等检索与清洗。

    实测单次清洗 152～379 秒，组卷只有分钟级预算；在组卷内等待必然提前
    掐断清洗（线上该功能因此一道题都没进过卷）。本次没有已入库的题时，
    组卷按现有候选继续，同时把灌题投到后台并如实告知。
    """
    ingest = _FakeIngest({})  # 还没有任何已入库的网络题
    agent = _agent(ingest)

    candidates, notes = await _backfill(agent)

    assert candidates == []
    # 读的还是同一批知识点，只是还没有已入库的题。
    assert ingest.reads == ["知识点KP_B", "知识点KP_A"]
    # 后台灌题已经投出去，最缺题的知识点排前面。
    assert [name for name, _ in ingest.calls] == ["知识点KP_B", "知识点KP_A"]
    assert all("后台" in note for note in notes)


async def test_no_background_notice_when_the_task_is_not_scheduled():
    """后台任务已在跑（或没有事件循环）时不重复投递，也不重复告知。"""
    ingest = _FakeIngest({}, schedule_result=False)
    agent = _agent(ingest)

    candidates, notes = await _backfill(agent)

    assert candidates == []
    assert notes == []


async def test_ingested_rows_are_used_in_the_same_run_they_are_read():
    """已经入库的题在本次组卷就能用，不必等下一轮。"""
    ingest = _FakeIngest({"知识点KP_B": [_web_row("WEBQ_B", "B 的网络题")]})
    agent = _agent(ingest)

    candidates, notes = await _backfill(agent)

    assert [item.question_id for item in candidates] == ["WEBQ_B"]
    assert any("知识点KP_B" in note and "补充" in note for note in notes)


async def test_english_typed_web_questions_survive_admission_and_type_filter():
    """英文题型的网络题必须能通过准入与题型过滤进入候选池。

    线上失效现场（2026-09-18「太阳中风证练习试卷」）：8 道已入库的网络题全部
    写成 ``single_choice``，准入的题型过滤只认中文名，整批被判成「题型不一致」
    丢弃，最终 20 题里 11 道靠现场生成；卷面却写着「检索到网络参考题 8 条」。
    对照实验：同一批题只把题型改成「单选题」，立刻通过准入与题型过滤。
    """

    ingest = _FakeIngest(
        {
            "知识点KP_A": [
                _web_row(
                    "WEBQ_EN",
                    "英文题型网络题",
                    question_type="single_choice",
                    options=["A. 人参", "B. 白术"],
                    answer="A",
                )
            ]
        }
    )
    agent = _agent(ingest)
    candidates, _ = await _backfill(agent, scope_kp_ids=["KP_A"])

    assert [item.question_id for item in candidates] == ["WEBQ_EN"]
    # 候选保留检索侧的原始写法，归一化发生在判定处：存量行因此不必重写。
    assert candidates[0].question_type == "single_choice"

    unit = _unit(
        question_type_preferences=["单项选择题"],
        required_question_count=1,
        candidate_limit=4,
    )
    pool = KnowledgeBaseAgent._unit_candidate_pool(
        [*[_retrieved_item("Q1", ["KP_A"])], *candidates],
        unit,
        ["KP_A"],
    )

    assert [item.question_id for item in pool.pool] == ["WEBQ_EN"]
    # 中文简答题不匹配「单项选择题」，仍要在题型过滤处被挡下。
    assert "Q1" not in {item.question_id for item in pool.matching}


async def test_backfill_outcome_names_the_gate_that_dropped_each_candidate():
    """回填结果事件必须说清每道候选被哪一道闸门拦下。

    线上失效现场里 ``accepted=8``、最终入池 0 道，事件完全看不出是准入拒绝
    还是题型不一致，只能离线重放才能定位。逐类计数把这件事留在运行事件里。
    """

    ingest = _FakeIngest(
        {
            "知识点KP_A": [
                # 没有标准答案：准入的完整性判定会拒。
                _web_row("WEBQ_NO_ANSWER", "无答案网络题", answer=""),
                # 英文选择题：准入通过，但单元只要简答题 → 题型过滤丢弃。
                _web_row(
                    "WEBQ_EN",
                    "英文选择题",
                    question_type="single_choice",
                    options=["A. 人参", "B. 白术"],
                    answer="A",
                ),
                # 英文简答题：通过全部闸门。
                _web_row("WEBQ_OK", "英文简答题", question_type="short_answer"),
            ]
        }
    )
    agent = _agent(ingest)
    candidates, _ = await _backfill(agent, scope_kp_ids=["KP_A"])
    assert len(candidates) == 3

    unit = _unit(
        question_type_preferences=["简答题"],
        required_question_count=1,
        candidate_limit=4,
    )
    pool = KnowledgeBaseAgent._unit_candidate_pool(candidates, unit, ["KP_A"])
    outcome = KnowledgeBaseAgent._web_backfill_outcome(
        candidates, unit, ["KP_A"], pool
    )

    assert outcome["pooled"] == 1
    assert outcome["admission_invalid_question_delivery"] == 1
    assert outcome["question_type_mismatch"] == 1
    # 一道题只计入它撞上的第一道闸门，各项之和等于候选总数。
    assert sum(outcome.values()) == len(candidates)
