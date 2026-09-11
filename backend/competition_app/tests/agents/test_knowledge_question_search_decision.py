import asyncio

import pytest

from competition_app.agents.knowledge_base import KnowledgeBaseAgent
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import (
    EvidenceItem,
    EvidencePack,
    QuestionBridge,
    QuestionDetail,
    QuestionRetrievalMetadata,
    QuestionSearchResult,
)
from competition_app.contracts.paper import BlueprintUnit, PaperBlueprint
from competition_app.runtime.event_stream import bind_event_sink, reset_event_sink


class FakeToolRegistry:
    def __init__(self, *, model_result: dict, question_result: QuestionSearchResult | None = None) -> None:
        self.calls: list[str] = []
        self.question_query_calls: list[str] = []
        self.model_result = model_result
        self.question_result = question_result

    async def invoke(self, name, agent, **kwargs):
        self.calls.append(name)
        if name == "get_kp_with_content":
            return EvidencePack(
                evidence_pack_id="EP_1", query=kwargs["query"], resolved_kp_ids=["KP_1"],
                evidence_items=[EvidenceItem(evidence_id="E_1", source_id="C_1", content_summary="教材证据", authority_level="textbook", confidence=0.9)],
            )
        if name == "get_question_with_content":
            self.question_query_calls.append(kwargs["query"])
            return self.question_result
        raise KeyError(name)


class BlueprintToolRegistry:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def invoke(self, name, agent, **kwargs):
        if name == "get_kp_with_content":
            return EvidencePack(
                evidence_pack_id=f"EP_{len(self.calls) + 1}",
                query=kwargs["query"],
                resolved_kp_ids=["KP_1"],
                evidence_items=[
                    EvidenceItem(
                        evidence_id="E_1", source_id="S_1",
                        content_summary="教材证据", authority_level="textbook",
                        confidence=0.9,
                    )
                ],
            )
        assert name == "get_question_with_content"
        self.calls.append(kwargs)
        query = kwargs["query"]
        return QuestionSearchResult(
            query=query,
            resolved_kp_ids=["KP_1"],
            embedding_model="stub",
            vector_index_path="stub",
            items=[
                QuestionDetail(
                    question_id=f"Q_{len(self.calls)}",
                    question_type="单项选择题",
                    stem=f"{query}题干",
                    reference_answer="A",
                    analysis="解析",
                    options=["A. 正确项", "B. 干扰项"],
                    tags=[],
                    source_metadata={},
                    bridges=[],
                    retrieval=QuestionRetrievalMetadata(
                        channels=["vector"],
                        channel_scores={"vector": 1.0},
                        fusion_score=1.0,
                    ),
                )
            ],
        )


class ConcurrentBlueprintToolRegistry(BlueprintToolRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.started_queries: set[str] = set()
        self.all_units_started = asyncio.Event()

    async def invoke(self, name, agent, **kwargs):
        if name == "get_kp_with_content":
            query = kwargs["query"]
            self.started_queries.add(query)
            if len(self.started_queries) == 2:
                self.all_units_started.set()
            await asyncio.wait_for(self.all_units_started.wait(), timeout=0.5)
        return await super().invoke(name, agent, **kwargs)


class TypeFilteringToolRegistry:
    def __init__(self) -> None:
        self.question_limits: list[int] = []

    @staticmethod
    def _question(question_id: str, question_type: str, channels: list[str]) -> QuestionDetail:
        is_choice = "选择" in question_type
        return QuestionDetail(
            question_id=question_id,
            question_type=question_type,
            stem=f"{question_type}题干",
            reference_answer="A" if is_choice else "答案",
            analysis="解析",
            options=["A. 正确项", "B. 干扰项"] if is_choice else [],
            tags=["四君子汤"],
            source_metadata={},
            bridges=[
                QuestionBridge(
                    kp_id="KP_1",
                    bridge_layer="strict",
                    relation="primary",
                    confidence=1.0,
                    rank=1,
                    evidence_chunk_uid="教材:四君子汤",
                    match_method="question_kp_ids",
                )
            ],
            retrieval=QuestionRetrievalMetadata(
                channels=channels,
                channel_scores={channel: 1.0 for channel in channels},
                fusion_score=1.0,
            ),
        )

    async def invoke(self, name, agent, **kwargs):
        if name == "get_kp_with_content":
            return EvidencePack(
                evidence_pack_id="EP_FILTER",
                query=kwargs["query"],
                resolved_kp_ids=["KP_1"],
                evidence_items=[
                    EvidenceItem(
                        evidence_id="E_TEXTBOOK",
                        source_id="教材:四君子汤",
                        content_summary="四君子汤教材证据。",
                        authority_level="textbook",
                        confidence=0.9,
                    ),
                    EvidenceItem(
                        evidence_id="E_WEB_QUESTION",
                        source_id="EXA_QUESTION_1",
                        content_summary="四君子汤外部练习题线索",
                        authority_level="web_question",
                        confidence=0.7,
                        bridge_layer="external",
                        source_url="https://example.test/question",
                        resource_type="question",
                    ),
                ],
            )
        if name == "get_question_with_content":
            self.question_limits.append(kwargs["limit"])
            questions = [
                self._question("Q_1", "判断题", ["bridge"]),
                self._question("Q_2", "填空题", ["bm25"]),
                self._question("Q_3", "问答题", ["vector"]),
                self._question("Q_4", "单项选择题", ["bridge", "bm25", "vector"]),
            ]
            return QuestionSearchResult(
                query=kwargs["query"],
                resolved_kp_ids=["KP_1"],
                embedding_model="stub",
                vector_index_path="stub",
                items=questions[: kwargs["limit"]],
            )
        raise KeyError(name)


class ExpandedRetrievalTimeoutRegistry:
    def __init__(self) -> None:
        self.question_calls = 0

    async def invoke(self, name, agent, **kwargs):
        if name == "get_kp_with_content":
            if "变式题" in kwargs["query"]:
                raise TimeoutError("external expansion timed out")
            return EvidencePack(
                evidence_pack_id="EP_TIMEOUT",
                query=kwargs["query"],
                resolved_kp_ids=["KP_1"],
                evidence_items=[],
            )
        if name == "get_question_with_content":
            self.question_calls += 1
            return QuestionSearchResult(
                query=kwargs["query"],
                resolved_kp_ids=["KP_1"],
                embedding_model="stub",
                vector_index_path="stub",
                items=[TypeFilteringToolRegistry._question(
                    "Q_LOCAL", "单项选择题", ["bridge"]
                )],
            )
        raise KeyError(name)


class TopicFilteringExpansionRegistry:
    def __init__(self) -> None:
        self.question_calls = 0

    @staticmethod
    def _question(question_id: str, topic: str, kp_id: str) -> QuestionDetail:
        return QuestionDetail(
            question_id=question_id,
            question_type="填空题",
            stem=f"{topic}的功效为____。",
            reference_answer="测试答案",
            analysis=f"本题考查{topic}。",
            tags=[topic],
            source_metadata={},
            bridges=[
                QuestionBridge(
                    kp_id=kp_id,
                    bridge_layer="strict",
                    relation="primary",
                    confidence=1.0,
                    rank=1,
                    evidence_chunk_uid=f"教材:{topic}",
                    match_method="question_kp_ids",
                )
            ],
            retrieval=QuestionRetrievalMetadata(
                channels=["bridge"],
                channel_scores={"bridge": 1.0},
                fusion_score=1.0,
            ),
        )

    async def invoke(self, name, agent, **kwargs):
        if name == "get_kp_with_content":
            return EvidencePack(
                evidence_pack_id=f"EP_TOPIC_{self.question_calls}",
                query="四君子汤",
                resolved_kp_ids=["KP_SIJUNZI", "KP_GUCHONG"],
                evidence_items=[
                    EvidenceItem(
                        evidence_id="E_TOPIC",
                        source_id="教材:四君子汤",
                        content_summary="四君子汤由人参、白术、茯苓、炙甘草组成。",
                        authority_level="textbook",
                        confidence=1.0,
                    )
                ],
            )
        if name == "get_question_with_content":
            self.question_calls += 1
            items = [
                self._question("Q_SIJUNZI_1", "四君子汤", "KP_SIJUNZI"),
                self._question("Q_GUCHONG", "固冲汤", "KP_GUCHONG"),
            ]
            if self.question_calls > 1:
                items.append(
                    self._question("Q_SIJUNZI_2", "四君子汤", "KP_SIJUNZI")
                )
            return QuestionSearchResult(
                query=kwargs["query"],
                resolved_kp_ids=["KP_SIJUNZI", "KP_GUCHONG"],
                embedding_model="stub",
                vector_index_path="stub",
                items=items,
            )
        raise KeyError(name)


class FixedModel:
    def __init__(self, result: dict) -> None:
        self.result = result

    async def complete_json(self, role, payload, on_delta=None):
        business_payload = payload["payload"]
        if business_payload["phase"] == "plan_retrieval":
            return {
                "kp_query": "四君子汤",
                "question_query": "四君子汤练习题",
                "retrieval_reason": "按用户诉求同时检索知识点内容和题目内容。",
            }
        return self.result


class PaperRetrievalDecisionModel(FixedModel):
    def __init__(self, decisions: list[dict]) -> None:
        super().__init__({})
        self.decisions = iter(decisions)
        self.phases: list[str] = []

    async def complete_json(self, role, payload, on_delta=None):
        phase = payload["payload"]["phase"]
        self.phases.append(phase)
        if phase == "paper_retrieval_decision":
            return next(self.decisions)
        return await super().complete_json(role, payload, on_delta=on_delta)


class UnmappedEvidenceToolRegistry:
    async def invoke(self, name, agent, **kwargs):
        if name == "get_kp_with_content":
            return EvidencePack(
                evidence_pack_id="EP_UNMAPPED",
                query=kwargs["query"],
                resolved_kp_ids=[],
                evidence_items=[
                    EvidenceItem(
                        evidence_id="E_1",
                        source_id="教材:理中丸",
                        content_summary="理中丸由人参、干姜、白术、炙甘草组成。",
                        authority_level="textbook",
                        confidence=0.9,
                    )
                ],
            )
        if name == "get_question_with_content":
            return QuestionSearchResult(
                query=kwargs["query"],
                resolved_kp_ids=[],
                embedding_model="stub",
                vector_index_path="stub",
                items=[
                    QuestionDetail(
                        question_id="Q_LIZHONG",
                        question_type="单项选择题",
                        stem="理中丸的功用是？",
                        reference_answer="温中祛寒，补气健脾",
                        analysis="教材解析",
                        tags=["理中丸"],
                        source_metadata={},
                        bridges=[
                            QuestionBridge(
                                kp_id="KP_LIZHONG",
                                bridge_layer="strict",
                                relation="primary",
                                confidence=1.0,
                                rank=1,
                                evidence_chunk_uid="教材:理中丸",
                                match_method="question_kp_ids",
                            )
                        ],
                        retrieval=QuestionRetrievalMetadata(
                            channels=["bridge"],
                            channel_scores={"bridge": 1.0},
                            fusion_score=1.0,
                        ),
                    )
                ],
            )
        raise KeyError(name)


def context(topic: str) -> dict:
    return {
        "case_id": "CASE_1", "trace_id": "TRACE_1", "request_id": "REQ_1",
        "execution_id": "EXE_1", "step_id": "knowledge", "learner_id": "L1",
        "topic": topic, "task_type": "personalized_review_card", "dependency_outputs": {},
    }


@pytest.mark.asyncio
async def test_every_knowledge_task_invokes_both_content_tools() -> None:
    registry = FakeToolRegistry(model_result={"quality_labels": [], "uncertainty": []}, question_result=None)
    with pytest.raises(ValueError, match="question search result"):
        await KnowledgeBaseAgent(None, FixedModel(registry.model_result)).run({**context("四君子汤"), "tool_registry": registry})

    assert registry.calls == ["get_kp_with_content", "get_question_with_content"]


@pytest.mark.parametrize(
    ("request_text",),
    [
        ("给我讲讲感冒的知识点",),
        ("四君子汤的组成",),
        ("出三道练习题",),
        # Even a pasted question keeps the agent-owned query: the model is
        # responsible for turning a stem into the retrieval statement, the
        # agent must not override it with hard-coded extraction.
        ("讲解一下这道题：多发性硬化的临床表现错误的是（ ）",),
        ("帮我解析这道题：五行中“木”的特性是（ ）",),
    ],
)
@pytest.mark.asyncio
async def test_model_question_query_is_passed_through_unchanged(request_text: str) -> None:
    registry = FakeToolRegistry(
        model_result={"quality_labels": [], "uncertainty": []},
        question_result=QuestionSearchResult(
            query="ignored",
            resolved_kp_ids=["KP_1"],
            embedding_model="stub",
            vector_index_path="stub",
            items=[],
        ),
    )
    result = await KnowledgeBaseAgent(None, FixedModel(registry.model_result)).run(
        {**context("测试"), "user_request": request_text, "tool_registry": registry}
    )

    # The agent passes the model-generated question_query through untouched;
    # stem-aware retrieval is the model's job via the retrieval-plan prompt.
    assert registry.question_query_calls == ["四君子汤练习题"]


@pytest.mark.asyncio
async def test_learning_plan_task_also_invokes_both_content_tools() -> None:
    registry = FakeToolRegistry(model_result={"quality_labels": [], "uncertainty": []}, question_result=None)
    plan_context = {**context("四君子汤学习计划"), "task_type": "learning_plan", "tool_registry": registry}
    plan_context["planning_request_scope"] = {
        "mode": "route", "objects": [], "source_quote": "四君子汤学习计划",
        "clarification_question": None,
    }
    with pytest.raises(ValueError, match="question search result"):
        await KnowledgeBaseAgent(None, FixedModel(registry.model_result)).run(plan_context)

    assert registry.calls == ["get_kp_with_content", "get_question_with_content"]


@pytest.mark.asyncio
async def test_model_generated_question_query_triggers_question_content_tool() -> None:
    registry = FakeToolRegistry(model_result={"quality_labels": [], "uncertainty": []}, question_result=None)
    with pytest.raises(ValueError, match="question search result"):
        await KnowledgeBaseAgent(
            None, FixedModel(registry.model_result)
        ).run({**context("四君子汤，出三道练习题"), "tool_registry": registry})

    assert registry.calls == ["get_kp_with_content", "get_question_with_content"]


@pytest.mark.asyncio
async def test_model_cannot_skip_question_content_tool() -> None:
    registry = FakeToolRegistry(model_result={
        "quality_labels": [], "uncertainty": [], "question_search_needed": True,
        "question_search_reason": "需要练习候选。",
    }, question_result=None)
    with pytest.raises(ValueError, match="question search result"):
        await KnowledgeBaseAgent(None, FixedModel({"quality_labels": [], "uncertainty": []})).run({**context("四君子汤"), "tool_registry": registry})

    assert registry.calls == ["get_kp_with_content", "get_question_with_content"]


@pytest.mark.asyncio
async def test_question_bridges_fill_kp_ids_when_textbook_chunks_are_unmapped() -> None:
    result = await KnowledgeBaseAgent(
        None,
        FixedModel({"quality_labels": [], "uncertainty": []}),
    ).run({**context("理中丸"), "tool_registry": UnmappedEvidenceToolRegistry()})

    assert result.payload.resolved_kp_ids == ["KP_LIZHONG"]
    assert any("题库 Bridge" in note for note in result.payload.risk_notes)


@pytest.mark.asyncio
async def test_paper_retrieval_runs_each_blueprint_unit_without_difficulty_filter() -> None:
    blueprint = PaperBlueprint(
        blueprint_id="BP_1",
        title="测试卷",
        source_status="user_provided_unverified",
        scope_summary="四君子汤",
        units=[
            BlueprintUnit(
                unit_id="U1", sequence=1, knowledge_module="组成",
                learning_objective="识记组成", retrieval_query="四君子汤 组成",
                required_question_count=1, candidate_limit=4,
            ),
            BlueprintUnit(
                unit_id="U2", sequence=2, knowledge_module="配伍",
                learning_objective="理解配伍", retrieval_query="四君子汤 配伍",
                required_question_count=1, candidate_limit=5,
            ),
        ],
    )
    envelope = AgentEnvelope(
        artifact_id="A1", artifact_type="paper_blueprint", case_id="C1",
        trace_id="T1", request_id="R1", execution_id="E1",
        step_id="paper_blueprint", producer="expert_agent",
        task_type="paper_generation", learner_id="L1", payload=blueprint,
    )
    registry = BlueprintToolRegistry()
    paper_context = {
        **context("组卷"),
        "task_type": "paper_generation",
        "dependency_outputs": {"paper_blueprint": envelope},
        "tool_registry": registry,
    }

    result = await KnowledgeBaseAgent(None, FixedModel({})).run(paper_context)

    assert [unit.unit_id for unit in result.payload.units] == ["U1", "U2"]
    assert [call["limit"] for call in registry.calls] == [20, 25]
    assert all(
        call["limit"] > unit.candidate_limit
        for call, unit in zip(registry.calls, blueprint.units)
    )
    assert all("difficulty" not in call for call in registry.calls)


@pytest.mark.asyncio
async def test_paper_retrieval_starts_units_concurrently_and_preserves_order() -> None:
    blueprint = PaperBlueprint(
        blueprint_id="BP_CONCURRENT",
        title="并发测试卷",
        source_status="user_provided_unverified",
        scope_summary="四君子汤",
        units=[
            BlueprintUnit(
                unit_id="U1", sequence=1, knowledge_module="组成",
                learning_objective="识记组成", retrieval_query="四君子汤 组成",
                required_question_count=1,
            ),
            BlueprintUnit(
                unit_id="U2", sequence=2, knowledge_module="配伍",
                learning_objective="理解配伍", retrieval_query="四君子汤 配伍",
                required_question_count=1,
            ),
        ],
    )
    blueprint_envelope = AgentEnvelope(
        artifact_id="A_CONCURRENT", artifact_type="paper_blueprint", case_id="C1",
        trace_id="T1", request_id="R1", execution_id="E1",
        step_id="paper_blueprint", producer="expert_agent",
        task_type="paper_generation", learner_id="L1", payload=blueprint,
    )
    registry = ConcurrentBlueprintToolRegistry()

    result = await KnowledgeBaseAgent(None, FixedModel({})).run({
        **context("组卷"),
        "task_type": "paper_generation",
        "dependency_outputs": {"paper_blueprint": blueprint_envelope},
        "tool_registry": registry,
    })

    assert registry.started_queries == {"四君子汤 组成", "四君子汤 配伍"}
    assert [unit.unit_id for unit in result.payload.units] == ["U1", "U2"]


def test_question_completeness_rejects_invalid_choice_delivery() -> None:
    valid_single = TypeFilteringToolRegistry._question(
        "Q_VALID", "单项选择题", ["vector"]
    ).model_copy(update={
        "options": ["A. 人参", "B. 白术", "C. 茯苓", "D. 甘草"],
        "reference_answer": "四君子汤",
        "analysis": "答案说明",
    })
    assert not KnowledgeBaseAgent._question_is_complete(valid_single)

    valid_single = valid_single.model_copy(update={"reference_answer": "人参"})
    assert KnowledgeBaseAgent._question_is_complete(valid_single)
    assert not KnowledgeBaseAgent._question_is_complete(
        valid_single.model_copy(update={"reference_answer": "C7"})
    )
    assert not KnowledgeBaseAgent._question_is_complete(
        valid_single.model_copy(update={"analysis": None})
    )
    assert not KnowledgeBaseAgent._question_is_complete(
        valid_single.model_copy(update={"options": []})
    )

    valid_multiple = valid_single.model_copy(update={
        "question_type": "多项选择题",
        "reference_answer": "AC",
    })
    assert KnowledgeBaseAgent._question_is_complete(valid_multiple)
    assert not KnowledgeBaseAgent._question_is_complete(
        valid_multiple.model_copy(update={"reference_answer": "A"})
    )


def test_candidate_admission_uses_structured_assessment_dimensions() -> None:
    base = TypeFilteringToolRegistry._question(
        "Q_DIM", "单项选择题", ["vector"]
    ).model_copy(update={
        "options": ["A. 人参", "B. 白术", "C. 茯苓", "D. 甘草"],
        "reference_answer": "A",
    })
    unit = BlueprintUnit(
        unit_id="U_DIM", sequence=1, knowledge_module="组成",
        learning_objective="识记组成", retrieval_query="四君子汤组成",
        required_question_count=1,
        assessment_dimensions=["composition"],
        excluded_dimensions=["comparison_differentiation"],
    )
    evidence = EvidencePack(
        evidence_pack_id="EP_DIM", query="四君子汤组成",
        resolved_kp_ids=["KP_1"], evidence_items=[],
    )

    eligible = base.model_copy(update={
        "source_metadata": {"assessment_dimensions": ["composition"]}
    })
    uncertain = base.model_copy(update={"source_metadata": {}})
    rejected = base.model_copy(update={
        "source_metadata": {
            "assessment_dimensions": ["comparison_differentiation"]
        }
    })

    assert KnowledgeBaseAgent._candidate_admission(eligible, unit, evidence)[0] == "eligible"
    assert KnowledgeBaseAgent._candidate_admission(uncertain, unit, evidence)[0] == "uncertain"
    assert KnowledgeBaseAgent._candidate_admission(rejected, unit, evidence) == (
        "rejected", "excluded_dimension_conflict"
    )


def _scope_question(question_id: str, kp_id: str) -> QuestionDetail:
    return QuestionDetail(
        question_id=question_id,
        question_type="单项选择题",
        stem=f"{kp_id}相关题目",
        reference_answer="A",
        analysis="解析",
        options=["A. 选项一", "B. 选项二"],
        tags=[],
        source_metadata={},
        bridges=[
            QuestionBridge(
                kp_id=kp_id,
                bridge_layer="strict",
                relation="primary",
                confidence=1.0,
                rank=1,
                evidence_chunk_uid=f"教材:{kp_id}",
                match_method="question_kp_ids",
            )
        ],
        retrieval=QuestionRetrievalMetadata(
            channels=["bridge"],
            channel_scores={"bridge": 1.0},
            fusion_score=1.0,
        ),
    )


def test_question_scope_uses_knowledge_module_anchor() -> None:
    """主题锚点：knowledge_module 末段匹配 resolved_kp_names 的 KP 视为合法主题。

    线上场景：长查询导致 resolve_topic 排序不稳定，真正的主题 KP（四君子汤
    005390）被挤出 resolved_kp_ids 首位（首位是补中益气汤 002463）。主题锚点
    应让挂四君子汤 KP 的题 eligible，而不是被误判 off-topic。
    """
    unit = BlueprintUnit(
        unit_id="U_ANCHOR", sequence=1,
        knowledge_module="方剂学·补益剂·四君子汤",
        learning_objective="掌握四君子汤", retrieval_query="四君子汤 组成 功效",
        required_question_count=1,
    )
    evidence = EvidencePack(
        evidence_pack_id="EP_ANCHOR", query="四君子汤",
        resolved_kp_ids=["002463", "002812", "005390", "005391", "012252"],
        resolved_kp_names={
            "002463": "补中益气汤",
            "002812": "补中益气汤",
            "005390": "四君子汤",
            "005391": "参苓白术散",
            "012252": "参苓白术散",
        },
        evidence_items=[],
    )

    # 挂四君子汤 KP 的题应 eligible（即使首位 KP 是补中益气汤）
    sijunzi = _scope_question("Q_SIJUNZI", "005390")
    assert KnowledgeBaseAgent._candidate_admission(sijunzi, unit, evidence) == (
        "eligible", "topic_entity_match"
    )

    # 挂补中益气汤 KP 的题应 rejected（不属于四君子汤主题）
    buzhong = _scope_question("Q_BUZHONG", "002463")
    assert KnowledgeBaseAgent._candidate_admission(buzhong, unit, evidence) == (
        "rejected", "topic_entity_mismatch"
    )


def test_question_scope_falls_back_to_first_kp_without_names() -> None:
    """无 resolved_kp_names 时回退到 resolved_kp_ids 首位（保持原有语义）。

    测试语义：resolved_kp_ids=[KP_SIJUNZI, KP_GUCHONG]，固冲汤题（挂
    KP_GUCHONG）应被拒，四君子汤题（挂 KP_SIJUNZI）应接受。
    """
    unit = BlueprintUnit(
        unit_id="U_FALLBACK", sequence=1,
        knowledge_module="四君子汤功效主治",
        learning_objective="掌握四君子汤功效主治",
        retrieval_query="四君子汤 功效 主治",
        required_question_count=1,
    )
    evidence = EvidencePack(
        evidence_pack_id="EP_FALLBACK", query="四君子汤",
        resolved_kp_ids=["KP_SIJUNZI", "KP_GUCHONG"],
        evidence_items=[],
    )

    sijunzi = _scope_question("Q_SIJUNZI", "KP_SIJUNZI")
    assert KnowledgeBaseAgent._candidate_admission(sijunzi, unit, evidence) == (
        "eligible", "topic_entity_match"
    )

    guchong = _scope_question("Q_GUCHONG", "KP_GUCHONG")
    assert KnowledgeBaseAgent._candidate_admission(guchong, unit, evidence) == (
        "rejected", "topic_entity_mismatch"
    )


def test_question_scope_anchor_dimension_only_falls_back() -> None:
    """knowledge_module 末段是纯维度词（如“组成”）时无法作锚点，回退首位。"""
    unit = BlueprintUnit(
        unit_id="U_DIM_ANCHOR", sequence=1,
        knowledge_module="组成",
        learning_objective="识记组成", retrieval_query="四君子汤组成",
        required_question_count=1,
    )
    evidence = EvidencePack(
        evidence_pack_id="EP_DIM_ANCHOR", query="四君子汤",
        resolved_kp_ids=["KP_SIJUNZI", "KP_GUCHONG"],
        resolved_kp_names={"KP_SIJUNZI": "四君子汤", "KP_GUCHONG": "固冲汤"},
        evidence_items=[],
    )

    # 锚点“组成”是维度词，回退到首位 KP_SIJUNZI
    sijunzi = _scope_question("Q_SIJUNZI", "KP_SIJUNZI")
    assert KnowledgeBaseAgent._candidate_admission(sijunzi, unit, evidence) == (
        "eligible", "topic_entity_match"
    )
    guchong = _scope_question("Q_GUCHONG", "KP_GUCHONG")
    assert KnowledgeBaseAgent._candidate_admission(guchong, unit, evidence) == (
        "rejected", "topic_entity_mismatch"
    )


@pytest.mark.asyncio
async def test_knowledge_agent_decides_enough_and_skips_supplemental_search() -> None:
    blueprint = PaperBlueprint(
        blueprint_id="BP_DECISION_ENOUGH",
        title="测试卷",
        source_status="user_provided_unverified",
        scope_summary="四君子汤",
        units=[
            BlueprintUnit(
                unit_id="U1", sequence=1, knowledge_module="组成",
                learning_objective="识记组成", retrieval_query="四君子汤 组成",
                required_question_count=1, candidate_limit=4,
            )
        ],
    )
    registry = BlueprintToolRegistry()
    model = PaperRetrievalDecisionModel([
        {
            "decision": "enough",
            "reason": "首轮正式候选已经满足本单元题量。",
            "supplemental_queries": [],
            "missing_requirements": [],
        }
    ])
    result = await KnowledgeBaseAgent(None, model).run({
        **context("组卷"),
        "task_type": "paper_generation",
        "dependency_outputs": {
            "paper_blueprint": AgentEnvelope(
                artifact_id="A_DECISION_ENOUGH", artifact_type="paper_blueprint",
                case_id="C1", trace_id="T1", request_id="R1", execution_id="E1",
                step_id="paper_blueprint", producer="expert_agent",
                task_type="paper_generation", learner_id="L1", payload=blueprint,
            )
        },
        "tool_registry": registry,
    })

    assert model.phases == ["paper_retrieval_decision"]
    assert len(registry.calls) == 1
    assert len(result.payload.units[0].items) == 1


@pytest.mark.asyncio
async def test_knowledge_agent_decides_supplemental_query_and_rechecks_candidates() -> None:
    blueprint = PaperBlueprint(
        blueprint_id="BP_DECISION_MORE",
        title="测试卷",
        source_status="user_provided_unverified",
        scope_summary="四君子汤",
        units=[
            BlueprintUnit(
                unit_id="U1", sequence=1, knowledge_module="组成",
                learning_objective="识记组成", retrieval_query="四君子汤 组成",
                required_question_count=2, candidate_limit=4,
            )
        ],
    )
    registry = BlueprintToolRegistry()
    model = PaperRetrievalDecisionModel([
        {
            "decision": "continue",
            "reason": "首轮候选不足。",
            "supplemental_queries": ["四君子汤 组成 补充练习题"],
            "missing_requirements": ["还需要补充正式候选题"],
        },
        {
            "decision": "enough",
            "reason": "补充检索后候选已足够。",
            "supplemental_queries": [],
            "missing_requirements": [],
        },
    ])
    result = await KnowledgeBaseAgent(None, model).run({
        **context("组卷"),
        "task_type": "paper_generation",
        "dependency_outputs": {
            "paper_blueprint": AgentEnvelope(
                artifact_id="A_DECISION_MORE", artifact_type="paper_blueprint",
                case_id="C1", trace_id="T1", request_id="R1", execution_id="E1",
                step_id="paper_blueprint", producer="expert_agent",
                task_type="paper_generation", learner_id="L1", payload=blueprint,
            )
        },
        "tool_registry": registry,
    })

    assert model.phases == ["paper_retrieval_decision", "paper_retrieval_decision"]
    assert [call["query"] for call in registry.calls] == [
        "四君子汤 组成", "四君子汤 组成 补充练习题"
    ]
    assert result.payload.units[0].items


@pytest.mark.asyncio
async def test_paper_retrieval_overfetches_before_question_type_filter() -> None:
    blueprint = PaperBlueprint(
        blueprint_id="BP_FILTER",
        title="测试卷",
        source_status="user_provided_unverified",
        scope_summary="四君子汤",
        units=[
            BlueprintUnit(
                unit_id="U1",
                sequence=1,
                knowledge_module="组成",
                learning_objective="识记组成",
                retrieval_query="四君子汤组成",
                question_type_preferences=["单项选择题"],
                required_question_count=1,
                candidate_limit=1,
            )
        ],
    )
    blueprint_envelope = AgentEnvelope(
        artifact_id="A_FILTER",
        artifact_type="paper_blueprint",
        case_id="C1",
        trace_id="T1",
        request_id="R1",
        execution_id="E1",
        step_id="paper_blueprint",
        producer="expert_agent",
        task_type="paper_generation",
        learner_id="L1",
        payload=blueprint,
    )
    registry = TypeFilteringToolRegistry()

    result = await KnowledgeBaseAgent(None, FixedModel({})).run(
        {
            **context("组卷"),
            "task_type": "paper_generation",
            "dependency_outputs": {"paper_blueprint": blueprint_envelope},
            "tool_registry": registry,
        }
    )

    assert registry.question_limits[0] > 1
    assert [item.question_id for item in result.payload.units[0].items] == ["Q_4"]


@pytest.mark.asyncio
async def test_paper_retrieval_expands_after_discarding_off_topic_candidates() -> None:
    blueprint = PaperBlueprint(
        blueprint_id="BP_TOPIC",
        title="四君子汤填空卷",
        source_status="user_provided_unverified",
        scope_summary="四君子汤组成、功效主治和配伍意义",
        required_total_question_count=2,
        question_count_is_hard_constraint=True,
        units=[
            BlueprintUnit(
                unit_id="U1",
                sequence=1,
                knowledge_module="四君子汤功效主治",
                learning_objective="掌握四君子汤功效主治",
                retrieval_query="四君子汤 功效 主治",
                question_type_preferences=["填空题"],
                required_question_count=2,
                candidate_limit=4,
            )
        ],
    )
    registry = TopicFilteringExpansionRegistry()
    result = await KnowledgeBaseAgent(None, FixedModel({})).run(
        {
            **context("组卷"),
            "task_type": "paper_generation",
            "dependency_outputs": {
                "paper_blueprint": AgentEnvelope(
                    artifact_id="A_TOPIC",
                    artifact_type="paper_blueprint",
                    case_id="C1",
                    trace_id="T1",
                    request_id="R1",
                    execution_id="E1",
                    step_id="paper_blueprint",
                    producer="expert_agent",
                    task_type="paper_generation",
                    learner_id="L1",
                    payload=blueprint,
                )
            },
            "tool_registry": registry,
        }
    )

    unit = result.payload.units[0]
    assert registry.question_calls == 2
    assert [item.question_id for item in unit.items] == [
        "Q_SIJUNZI_1",
        "Q_SIJUNZI_2",
    ]
    assert all("固冲汤" not in item.stem for item in unit.items)
    assert any("主题不一致" in warning for warning in unit.warnings)


@pytest.mark.asyncio
async def test_paper_retrieval_event_reports_filter_channels_and_web_clues() -> None:
    blueprint = PaperBlueprint(
        blueprint_id="BP_EVENT",
        title="测试卷",
        source_status="user_provided_unverified",
        scope_summary="四君子汤",
        units=[
            BlueprintUnit(
                unit_id="U1",
                sequence=1,
                knowledge_module="组成",
                learning_objective="识记组成",
                retrieval_query="四君子汤组成",
                question_type_preferences=["单项选择题"],
                required_question_count=1,
                candidate_limit=1,
            )
        ],
    )
    blueprint_envelope = AgentEnvelope(
        artifact_id="A_EVENT",
        artifact_type="paper_blueprint",
        case_id="C1",
        trace_id="T1",
        request_id="R1",
        execution_id="E1",
        step_id="paper_blueprint",
        producer="expert_agent",
        task_type="paper_generation",
        learner_id="L1",
        payload=blueprint,
    )
    events: list[dict] = []
    token = bind_event_sink(events.append)
    try:
        await KnowledgeBaseAgent(None, FixedModel({})).run(
            {
                **context("组卷"),
                "task_type": "paper_generation",
                "dependency_outputs": {"paper_blueprint": blueprint_envelope},
                "tool_registry": TypeFilteringToolRegistry(),
            }
        )
    finally:
        reset_event_sink(token)

    event = next(item for item in events if item["event"] == "paper_unit_retrieval")
    assert event["raw_candidate_count"] == 4
    assert event["candidate_count"] == 1
    assert event["filtered_out_count"] == 3
    assert event["channel_counts"] == {"bridge": 2, "bm25": 2, "vector": 2}
    assert event["external_question_references"] == [
        {
            "source_id": "EXA_QUESTION_1",
            "content": "四君子汤外部练习题线索",
            "source_url": "https://example.test/question",
            "confidence": 0.7,
        }
    ]


@pytest.mark.asyncio
async def test_paper_retrieval_keeps_local_candidates_when_expansion_times_out() -> None:
    blueprint = PaperBlueprint(
        blueprint_id="BP_TIMEOUT",
        title="测试卷",
        source_status="user_provided_unverified",
        scope_summary="第三阶段",
        units=[
            BlueprintUnit(
                unit_id="U1",
                sequence=1,
                knowledge_module="经典辨证",
                learning_objective="完成综合辨析",
                retrieval_query="伤寒论 辨证",
                question_type_preferences=["单项选择题"],
                required_question_count=5,
                candidate_limit=8,
            )
        ],
    )
    result = await KnowledgeBaseAgent(None, FixedModel({})).run({
        **context("组卷"),
        "task_type": "paper_generation",
        "dependency_outputs": {
            "paper_blueprint": AgentEnvelope(
                artifact_id="A_TIMEOUT",
                artifact_type="paper_blueprint",
                case_id="C1",
                trace_id="T1",
                request_id="R1",
                execution_id="E1",
                step_id="paper_blueprint",
                producer="expert_agent",
                task_type="paper_generation",
                learner_id="L1",
                payload=blueprint,
            )
        },
        "tool_registry": ExpandedRetrievalTimeoutRegistry(),
    })

    unit = result.payload.units[0]
    assert [item.question_id for item in unit.items] == ["Q_LOCAL"]
    assert any("已保留首轮正式题库候选" in warning for warning in unit.warnings)


def test_question_type_preferences_normalize_choice_aliases() -> None:
    assert KnowledgeBaseAgent._matches_question_type("单项选择题", ["单选题"])
    assert KnowledgeBaseAgent._matches_question_type("多项选择题", ["选择题"])
    assert not KnowledgeBaseAgent._matches_question_type("判断题", ["选择题"])


@pytest.mark.asyncio
async def test_paper_retrieval_requires_structured_primary_kp_scope() -> None:
    """Difficulty is a constraint, not evidence that a question belongs to a unit."""

    class DifficultyScopeRegistry:
        async def invoke(self, name, agent, **kwargs):
            if name == "get_kp_with_content":
                return EvidencePack(
                    evidence_pack_id="EP_DIFF",
                    query=kwargs["query"],
                    resolved_kp_ids=["KP_COURSE_BIANZHENG"],
                    evidence_items=[
                        EvidenceItem(
                            evidence_id="E_1", source_id="S_1",
                            content_summary="辨证论治教材证据", authority_level="textbook",
                            confidence=0.9,
                        )
                    ],
                )
            assert name == "get_question_with_content"
            return QuestionSearchResult(
                query=kwargs["query"],
                resolved_kp_ids=["KP_COURSE_BIANZHENG"],
                embedding_model="stub",
                vector_index_path="stub",
                items=[
                    QuestionDetail(
                        question_id="Q_EXAM_SYLLABUS",
                        question_type="单项选择题",
                        stem="同病异治、异病同治的根本依据在于____。",
                        reference_answer="A",
                        analysis="解析",
                        options=["A. 证", "B. 病"],
                        tags=["辨证论治"],
                        source_metadata={},
                        difficulty=2,
                        difficulty_source="qa_stress_label_2026-08-05",
                        bridges=[
                            QuestionBridge(
                                kp_id="KP_COURSE_BIANZHENG",
                                bridge_layer="strict",
                                relation="covered",
                                confidence=0.8,
                                rank=1,
                                evidence_chunk_uid="教材:辨证论治",
                                match_method="question_kp_ids",
                            )
                        ],
                        retrieval=QuestionRetrievalMetadata(
                            channels=["bm25"],
                            channel_scores={"bm25": 1.0},
                            fusion_score=1.0,
                        ),
                    )
                ],
            )

    blueprint = PaperBlueprint(
        blueprint_id="BP_DIFF",
        title="辨证论治2星卷",
        source_status="user_provided_unverified",
        scope_summary="辨证论治",
        required_total_question_count=1,
        question_count_is_hard_constraint=True,
        units=[
            BlueprintUnit(
                unit_id="U1",
                sequence=1,
                knowledge_module="辨证论治",
                learning_objective="掌握同病异治异病同治",
                retrieval_query="辨证论治 同病异治 异病同治",
                question_type_preferences=["单项选择题"],
                target_difficulty=2,
                difficulty_is_hard_constraint=True,
                required_question_count=1,
                candidate_limit=4,
            )
        ],
    )
    result = await KnowledgeBaseAgent(None, FixedModel({})).run(
        {
            **context("组卷"),
            "task_type": "paper_generation",
            "dependency_outputs": {
                "paper_blueprint": AgentEnvelope(
                    artifact_id="A_DIFF",
                    artifact_type="paper_blueprint",
                    case_id="C1",
                    trace_id="T1",
                    request_id="R1",
                    execution_id="E1",
                    step_id="paper_blueprint",
                    producer="expert_agent",
                    task_type="paper_generation",
                    learner_id="L1",
                    payload=blueprint,
                )
            },
            "tool_registry": DifficultyScopeRegistry(),
        }
    )

    unit = result.payload.units[0]
    assert [item.question_id for item in unit.items] == ["Q_EXAM_SYLLABUS"]
    assert not any("主题不一致" in warning for warning in unit.warnings)


def test_question_type_preferences_group_open_response_aliases() -> None:
    for actual in ("问答题", "临床案例问答", "病例分析/实践技能"):
        assert KnowledgeBaseAgent._matches_question_type(actual, ["简答题"])
