import asyncio

import pytest
from pydantic import ValidationError
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
from competition_app.llm.schemas import KnowledgePaperUnitScopeModelOutput
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
        self.kp_queries: list[str] = []

    async def invoke(self, name, agent, **kwargs):
        if name == "get_kp_with_content":
            self.kp_queries.append(kwargs["query"])
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
                # 单元范围只解析出四君子汤这一个知识点；固冲汤题桥接到范围外
                # 的 KP，因此属于跑题候选，会被准入剔除并触发扩展检索。
                resolved_kp_ids=["KP_SIJUNZI"],
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
                resolved_kp_ids=["KP_SIJUNZI"],
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


class PaperUnitPlanModel(PaperRetrievalDecisionModel):
    """组卷单元：本智能体先撰写检索短语，再进入补充检索判定循环。"""

    def __init__(
        self,
        decisions: list[dict],
        plan: dict | None,
        *,
        raise_on_plan: bool = False,
    ) -> None:
        super().__init__(decisions)
        self.plan = plan
        self.raise_on_plan = raise_on_plan
        self.plan_payloads: list[dict] = []

    async def complete_json(self, role, payload, on_delta=None):
        phase = payload["payload"]["phase"]
        if phase == "plan_paper_unit_retrieval":
            self.phases.append(phase)
            self.plan_payloads.append(payload["payload"])
            if self.raise_on_plan:
                raise RuntimeError("planning unavailable")
            return self.plan
        return await super().complete_json(role, payload, on_delta=on_delta)


def _planning_blueprint(blueprint_id: str, *, required: int = 1) -> PaperBlueprint:
    return PaperBlueprint(
        blueprint_id=blueprint_id,
        title="测试卷",
        source_status="user_provided_unverified",
        scope_summary="四君子汤",
        units=[
            BlueprintUnit(
                unit_id="U1",
                sequence=1,
                knowledge_module="组成",
                learning_objective="识记组成",
                retrieval_query="检索四君子汤组成类简答题，正式题库优先，不足时按全局补题顺序补足",
                required_question_count=required,
                candidate_limit=4,
            )
        ],
    )


def _planning_envelope(blueprint: PaperBlueprint, artifact_id: str) -> AgentEnvelope:
    return AgentEnvelope(
        artifact_id=artifact_id,
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
    # 解析是交付条件，不是题目本身的完整性：本题库绝大多数正式题没有解析，
    # 是否要求逐题解析由蓝图合同（requires_explanation）在组卷阶段判定。
    assert KnowledgeBaseAgent._question_is_complete(
        valid_single.model_copy(update={"analysis": None})
    )
    assert KnowledgeBaseAgent._question_is_complete(
        valid_single.model_copy(update={"analysis": ""})
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

    assert KnowledgeBaseAgent._candidate_admission(eligible, unit, ["KP_1"])[0] == "eligible"
    assert KnowledgeBaseAgent._candidate_admission(uncertain, unit, ["KP_1"])[0] == "uncertain"
    assert KnowledgeBaseAgent._candidate_admission(rejected, unit, ["KP_1"]) == (
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


def test_question_scope_accepts_every_kp_inside_the_judged_scope() -> None:
    """单元范围是判定出的完整知识点集合，不是排序首位。

    范围集合由知识库智能体按单元声明的范围判定（见
    ``_resolve_unit_scope_kp_ids``），它不是检索命中的排序结果：集合内任何
    一个知识点都要放行，集合外才判跑题。
    """
    unit = BlueprintUnit(
        unit_id="U_ANCHOR", sequence=1,
        knowledge_module="方剂学·补益剂·四君子汤",
        learning_objective="掌握四君子汤", retrieval_query="四君子汤 组成 功效",
        required_question_count=1,
    )
    scope = ["002463", "002812", "005390", "005391", "012252"]

    # 挂四君子汤 KP 的题应 eligible（即使集合首位 KP 是补中益气汤）
    sijunzi = _scope_question("Q_SIJUNZI", "005390")
    assert KnowledgeBaseAgent._candidate_admission(sijunzi, unit, scope) == (
        "eligible", "topic_entity_match"
    )

    # 桥接 KP 完全不在判定出的集合内，才判为跑题
    outside = _scope_question("Q_OUTSIDE", "099999")
    assert KnowledgeBaseAgent._candidate_admission(outside, unit, scope) == (
        "rejected", "topic_entity_mismatch"
    )


def test_question_scope_rejects_candidates_whose_primary_kp_is_outside() -> None:
    """主知识点落在范围外的候选不放行，即使次要桥接命中范围内知识点。

    线上实测的越界题都长这样：一道中医学概论的题顺带桥接到本单元的
    “麻黄汤证”知识点。只看“任意一个桥接命中”会给这类题开后门，所以只有
    主知识点落在范围内才算 eligible；仅次要桥接命中属于证据不足，记为
    uncertain，只在正式候选不足时按缺口降级提供。
    """
    unit = BlueprintUnit(
        unit_id="U_PRIMARY", sequence=1,
        knowledge_module="《伤寒论》太阳病篇",
        learning_objective="检验并巩固太阳病篇核心知识",
        retrieval_query="《伤寒论》太阳病篇",
        required_question_count=1,
    )
    scope = ["033712", "052357"]

    secondary_only = QuestionDetail(
        question_id="Q_SECONDARY_ONLY",
        question_type="单项选择题",
        stem="方剂的组成变化有哪些形式？",
        reference_answer="A",
        analysis="解析",
        options=["A. 药味加减", "B. 无关项"],
        tags=[],
        source_metadata={},
        bridges=[
            QuestionBridge(
                kp_id="005353", bridge_layer="strict", relation="primary",
                confidence=1.0, rank=1,
                evidence_chunk_uid="中医学概论:方剂的组成",
                match_method="question_kp_ids",
            ),
            QuestionBridge(
                kp_id="052357", bridge_layer="strict", relation="secondary",
                confidence=1.0, rank=2,
                evidence_chunk_uid="本草典籍选读:麻黄汤证",
                match_method="question_kp_ids",
            ),
        ],
        retrieval=QuestionRetrievalMetadata(
            channels=["bridge"], channel_scores={"bridge": 1.0}, fusion_score=1.0,
        ),
    )
    assert KnowledgeBaseAgent._candidate_admission(
        secondary_only, unit, scope
    ) == ("uncertain", "primary_kp_scope_unverified")

    # 主、次桥接都在范围外才是跑题
    outside = secondary_only.model_copy(update={
        "bridges": [
            bridge.model_copy(update={"kp_id": "099998"})
            for bridge in secondary_only.bridges
        ]
    })
    assert KnowledgeBaseAgent._candidate_admission(outside, unit, scope) == (
        "rejected", "topic_entity_mismatch"
    )


def test_question_scope_ignores_the_knowledge_module_wording() -> None:
    """准入结果不得取决于模型怎么写 knowledge_module。

    线上场景（r2c / r2d 实测）：单元的 knowledge_module 是章节式标签
    （“桂枝汤类方证治”“太阳病变证，含误治变证、蓄水证……”）。这类标签不是
    任何知识点的名字，拿它做子串或前缀匹配会反向失效——标签越长越具体，
    反而只与最泛化的知识点（“太阳病证”）共享前缀，把真正对口的 KP 全部判成
    跑题，该单元的正式候选归零。

    范围改由结构化 KP 集合决定后，标签措辞不应再影响任何一条准入结论。
    """
    scope = ["004988", "033736", "033762", "033817", "052355", "069685"]
    relevant = ("033736", "033762", "033817", "052355", "069685")

    # 同一份判定范围，三种写法完全不同的标签
    labels = (
        "桂枝汤类方证治",
        "伤寒论·太阳病兼证（兼项背强、兼喘等）及合病、并病",
        "太阳病变证，含误治变证、蓄水证、蓄血证、结胸证、痞证等",
    )
    for index, label in enumerate(labels):
        unit = BlueprintUnit(
            unit_id=f"U_CHAPTER_{index}", sequence=1,
            knowledge_module=label,
            learning_objective="掌握桂枝汤类方", retrieval_query="桂枝汤类方证治",
            required_question_count=1,
        )
        for kp_id in relevant:
            assert KnowledgeBaseAgent._candidate_admission(
                _scope_question(f"Q_{kp_id}", kp_id), unit, scope
            ) == ("eligible", "topic_entity_match"), (label, kp_id)
        # 桥接 KP 不在本单元判定出的集合内 → 仍被拒
        assert KnowledgeBaseAgent._candidate_admission(
            _scope_question("Q_OUT", "099999"), unit, scope
        ) == ("rejected", "topic_entity_mismatch"), label


def test_question_scope_uses_only_structured_kp_identity() -> None:
    """范围判断是结构化标识的集合运算，不比对任何自然语言文本。

    同一份判定范围下，只要候选的主知识点在集合内就放行、不在就拒绝；把
    knowledge_module 换成任意字符串都不改变结论。
    """
    unit_kwargs = dict(
        unit_id="U_STRUCT", sequence=1,
        learning_objective="掌握四君子汤",
        retrieval_query="四君子汤 功效 主治",
        required_question_count=1,
    )
    scope = ["KP_SIJUNZI"]

    for label in ("四君子汤功效主治", "方剂学·补益剂·四君子汤", "补益剂", "完全无关的标签"):
        unit = BlueprintUnit(knowledge_module=label, **unit_kwargs)
        assert KnowledgeBaseAgent._candidate_admission(
            _scope_question("Q_SIJUNZI", "KP_SIJUNZI"), unit, scope
        ) == ("eligible", "topic_entity_match"), label
        # 固冲汤题：名称与标签毫无关系，但真正起作用的是 KP 不在集合内
        assert KnowledgeBaseAgent._candidate_admission(
            _scope_question("Q_GUCHONG", "KP_GUCHONG"), unit, scope
        ) == ("rejected", "topic_entity_mismatch"), label


def test_question_scope_keeps_unbridged_candidates_uncertain() -> None:
    """候选没有桥接 KP 时范围无法判定——这是 uncertain 的合法来源。"""
    unit = BlueprintUnit(
        unit_id="U_UNCERTAIN", sequence=1,
        knowledge_module="四君子汤功效主治",
        learning_objective="掌握四君子汤功效主治",
        retrieval_query="四君子汤 功效 主治",
        required_question_count=1,
    )
    unbridged = _scope_question("Q_NO_BRIDGE", "KP_SIJUNZI").model_copy(
        update={"bridges": []}
    )
    assert KnowledgeBaseAgent._question_scope_status(
        unbridged, unit, ["KP_SIJUNZI"]
    ) == "uncertain"


def test_empty_scope_rejects_candidates_instead_of_marking_them_uncertain() -> None:
    """范围判定结论为空时候选全部越界，不得降级成「不确定」。

    空范围的含义是「判定成立，且候选目录里没有任何知识点属于本单元」，不是
    「判定缺失」。此前它返回 uncertain，等于把全部越界候选放进按缺口借题的
    降级池：线上实测一个需要 20 题的单元里 50 道候选全部被判 uncertain，
    系统按缺口借满整份配额，最终 13 题里 10 题来自其他知识点。范围**不可用**
    （模型调用失败）才走 uncertain，两者的处置相反，不能混。
    """
    unit = BlueprintUnit(
        unit_id="U_EMPTY_SCOPE", sequence=1,
        knowledge_module="太阳中风证",
        learning_objective="掌握太阳中风证的病因病机与桂枝汤运用",
        retrieval_query="太阳中风证",
        required_question_count=20,
    )
    for kp_id in ("033710", "099999"):
        assert KnowledgeBaseAgent._question_scope_status(
            _scope_question(f"Q_{kp_id}", kp_id), unit, []
        ) == "rejected", kp_id
    # 结论必须体现在准入结果上：范围为空 → 越界，不再作为借题来源
    assert KnowledgeBaseAgent._candidate_admission(
        _scope_question("Q_EMPTY_SCOPE", "033710"), unit, []
    ) == ("rejected", "topic_entity_mismatch")


def test_unit_scope_catalog_reads_textbook_chapter_and_point_names() -> None:
    """目录取候选自己桥接到的知识点，带教材/章节/知识点三级名称。

    目录只覆盖候选桥接到的知识点：准入是候选桥接集合与判定范围的交集，
    不在任何候选桥接里的知识点不可能影响准入结果。
    """
    item = _scope_question("Q_CATALOG", "033713").model_copy(update={
        "source_metadata": {
            "knowledge_points": [
                {
                    "kp": {
                        "kp_id": "033713",
                        "kp_lv1": "伤寒论选读",
                        "kp_lv2": "第二节 太阳病辨证纲要",
                        "kp_lv3": "太阳中风证",
                    }
                },
                {
                    "kp": {
                        "kp_id": "050313",
                        "kp_lv1": "方剂学",
                        "kp_lv2": "第一节 活血祛瘀剂",
                        "kp_lv3": "下焦蓄血证",
                    }
                },
            ]
        },
        "bridges": [
            QuestionBridge(
                kp_id="033713", bridge_layer="strict", relation="primary",
                confidence=1.0, rank=1, evidence_chunk_uid="教材:太阳中风证",
                match_method="question_kp_ids",
            ),
            QuestionBridge(
                kp_id="050313", bridge_layer="strict", relation="secondary",
                confidence=1.0, rank=2, evidence_chunk_uid="教材:下焦蓄血证",
                match_method="question_kp_ids",
            ),
        ],
    })
    assert KnowledgeBaseAgent._unit_scope_catalog([item]) == [
        {
            "kp_id": "033713", "kp_lv1": "伤寒论选读",
            "kp_lv2": "第二节 太阳病辨证纲要", "kp_lv3": "太阳中风证",
        },
        {
            "kp_id": "050313", "kp_lv1": "方剂学",
            "kp_lv2": "第一节 活血祛瘀剂", "kp_lv3": "下焦蓄血证",
        },
    ]


class _ScopeJudgementModel(FixedModel):
    """按 phase 返回范围判定结果，并记录调用次数。

    ``raise_on_scope`` 为整数时，前 N 次判定调用抛传输异常，之后返回
    ``judgement``，用于验证失败重试。
    """

    def __init__(
        self,
        judgement: dict | None,
        *,
        raise_on_scope: bool | int = False,
    ) -> None:
        super().__init__({})
        self.judgement = judgement
        self.raise_on_scope = raise_on_scope
        self.scope_calls = 0
        self.scope_payloads: list[dict] = []

    async def complete_json(self, role, payload, on_delta=None):
        business = payload["payload"]
        if business.get("phase") != "decide_paper_unit_scope":
            return await super().complete_json(role, payload, on_delta=on_delta)
        self.scope_calls += 1
        self.scope_payloads.append(business)
        if self.raise_on_scope is True:
            raise RuntimeError("scope judgement unavailable")
        if isinstance(self.raise_on_scope, int) and self.scope_calls <= self.raise_on_scope:
            raise RuntimeError("scope judgement unavailable")
        return self.judgement


def _scope_agent(model: FixedModel) -> KnowledgeBaseAgent:
    return KnowledgeBaseAgent(None, model)


@pytest.mark.asyncio
async def test_unit_scope_judgement_is_the_admission_boundary() -> None:
    """准入边界取模型判定的范围，不取检索命中的知识点列表。

    线上实测的失效方式：检索命中列表含无关教材的知识点（方剂学“下焦蓄血证”）
    又漏掉同章节的知识点（同书同章节的“太阳中风证”）。判定返回的是范围内的
    知识点，系统只认这个集合。
    """
    unit = BlueprintUnit(
        unit_id="U_SCOPE", sequence=1,
        knowledge_module="《伤寒论》太阳病篇",
        learning_objective="检验并巩固太阳病篇核心知识",
        retrieval_query="《伤寒论》太阳病篇",
        required_question_count=1,
    )
    item = _scope_question("Q_TAIYANG", "033713").model_copy(update={
        "source_metadata": {
            "knowledge_points": [
                {
                    "kp": {
                        "kp_id": "033713", "kp_lv1": "伤寒论选读",
                        "kp_lv2": "第二节 太阳病辨证纲要",
                        "kp_lv3": "太阳中风证",
                    }
                }
            ]
        }
    })
    evidence = EvidencePack(
        evidence_pack_id="EP_SCOPE", query="《伤寒论》太阳病篇",
        resolved_kp_ids=["050313", "019040"],
        evidence_items=[],
    )
    model = _ScopeJudgementModel({
        "in_scope_kp_ids": ["033713", "099999"],
        "scope_reason": "伤寒论选读的太阳病篇各章节属于本单元。",
    })
    scope, failure = await _scope_agent(model)._resolve_unit_scope_kp_ids(
        context=context("组卷"), unit=unit, items=[item], evidence_pack=evidence,
        judged_keys=set(),
    )
    # 目录里没有的 ID 被丢弃，只保留目录内的判定结果
    assert scope == ["033713"]
    assert failure == ""
    assert model.scope_payloads[0]["knowledge_point_catalog"] == [
        {
            "kp_id": "033713", "kp_lv1": "伤寒论选读",
            "kp_lv2": "第二节 太阳病辨证纲要", "kp_lv3": "太阳中风证",
        }
    ]
    assert KnowledgeBaseAgent._question_scope_status(item, unit, scope) == "eligible"


def test_scope_judgement_contract_requires_the_kp_id_field() -> None:
    """契约必须区分「结论为空」与「没有给出结论」。

    ``in_scope_kp_ids`` 曾带 ``default_factory=list``：模型漏掉这个键时校验
    静默通过、补成空数组，而 ``scope_reason`` 是必填，所以整体校验照样通过。
    下游于是把「模型没给结论」当成「范围内没有知识点」，既按缺口借满跑题题，
    又静默跳过网络题回填。字段缺失在契约层就必须是校验失败，由既有重试与
    兜底路径处理。
    """
    with pytest.raises(ValidationError):
        KnowledgePaperUnitScopeModelOutput.model_validate(
            {"scope_reason": "目录内均不属于本单元"}
        )
    # 显式的空数组是有效结论，必须继续被接受
    empty = KnowledgePaperUnitScopeModelOutput.model_validate(
        {"in_scope_kp_ids": [], "scope_reason": "目录内均不属于本单元"}
    )
    assert empty.in_scope_kp_ids == []


@pytest.mark.asyncio
async def test_empty_scope_judgement_is_reported_as_an_empty_conclusion() -> None:
    """判定成立但结论为空时失败说明必须非空，且不得退回命中列表。

    空范围是有效结论，但调用方必须知道本单元没有任何候选通过准入——它决定
    单元告警怎么写、题量缺口怎么补。此前这里返回空串，线上既没有事件也没有
    告警，只能靠排除法反推。
    """
    unit = BlueprintUnit(
        unit_id="U_EMPTY_CONCLUSION", sequence=1,
        knowledge_module="太阳中风证",
        learning_objective="掌握太阳中风证的病因病机与桂枝汤运用",
        retrieval_query="太阳中风证",
        required_question_count=20,
    )
    item = _scope_question("Q_TAIYANG_EMPTY", "033713")
    evidence = EvidencePack(
        evidence_pack_id="EP_EMPTY_CONCLUSION", query="太阳中风证",
        resolved_kp_ids=["050313"], evidence_items=[],
    )
    model = _ScopeJudgementModel({
        "in_scope_kp_ids": [],
        "scope_reason": "目录内全部教材章节都不属于本单元。",
    })
    scope, failure = await _scope_agent(model)._resolve_unit_scope_kp_ids(
        context=context("组卷"), unit=unit, items=[item],
        evidence_pack=evidence, judged_keys=set(),
    )
    assert scope == []
    assert failure, "结论为空必须留下失败说明，不能静默"
    # 结论为空 ≠ 判定不可用：不得退回按名称召回的命中列表
    assert evidence.resolved_kp_ids == ["050313"]


@pytest.mark.asyncio
async def test_scope_judgement_outside_the_catalog_is_not_silently_emptied() -> None:
    """模型给出的知识点一个都不在候选目录内时，按判定不可用处理。

    此前这种返回被 ``if kp_id in known`` 静默过滤成空范围，失败说明还是空串，
    下游无法与「判定成立且结论为空」区分。两者后果相反：一个是判定与目录
    对不上，一个是本单元候选全部越界。
    """
    unit = BlueprintUnit(
        unit_id="U_OFF_CATALOG", sequence=1,
        knowledge_module="太阳中风证",
        learning_objective="掌握太阳中风证的病因病机与桂枝汤运用",
        retrieval_query="太阳中风证",
        required_question_count=20,
    )
    item = _scope_question("Q_TAIYANG_OFF_CATALOG", "033713")
    evidence = EvidencePack(
        evidence_pack_id="EP_OFF_CATALOG", query="太阳中风证",
        resolved_kp_ids=["050313"], evidence_items=[],
    )
    model = _ScopeJudgementModel({
        "in_scope_kp_ids": ["070101", "070102"],
        "scope_reason": "目录内这些章节属于本单元。",
    })
    scope, failure = await _scope_agent(model)._resolve_unit_scope_kp_ids(
        context=context("组卷"), unit=unit, items=[item],
        evidence_pack=evidence, judged_keys=set(),
    )
    assert scope == ["050313"], "判定与目录对不上时退回命中列表，不能归零"
    assert "都不在候选知识点目录内" in failure


@pytest.mark.asyncio
async def test_unit_scope_judgement_falls_back_to_retrieval_hits() -> None:
    """判定不可用时退回检索命中列表，并把失败原因交给调用方。

    退回本身不能静默：退回后的准入边界与修复前的命中列表完全一样，产出的
    试卷也会一样偏离。失败原因必须回到调用方，写进单元告警与运行事件。
    """
    unit = BlueprintUnit(
        unit_id="U_FALLBACK", sequence=1, knowledge_module="组成",
        learning_objective="识记组成", retrieval_query="四君子汤 组成",
        required_question_count=1,
    )
    item = _scope_question("Q_FALLBACK", "KP_1")
    evidence = EvidencePack(
        evidence_pack_id="EP_FALLBACK", query="四君子汤",
        resolved_kp_ids=["KP_1"], evidence_items=[],
    )
    model = _ScopeJudgementModel(None, raise_on_scope=True)
    scope, failure = await _scope_agent(model)._resolve_unit_scope_kp_ids(
        context=context("组卷"), unit=unit, items=[item], evidence_pack=evidence,
        judged_keys=set(),
    )
    assert scope == ["KP_1"]
    assert failure.startswith("RuntimeError")
    # 失败会重试一次，两次都不行才退回
    assert model.scope_calls == 2

    # 模型输出不符合契约（缺 scope_reason）时同样退回并留下失败说明
    scope, failure = await _scope_agent(
        _ScopeJudgementModel({"in_scope_kp_ids": ["KP_1"]})
    )._resolve_unit_scope_kp_ids(
        context=context("组卷"), unit=unit, items=[item], evidence_pack=evidence,
        judged_keys=set(),
    )
    assert scope == ["KP_1"]
    assert failure.startswith("ValidationError")


@pytest.mark.asyncio
async def test_unit_scope_judgement_retries_once_before_falling_back() -> None:
    """传输失败重试一次；重试成功就不退回命中列表。"""
    unit = BlueprintUnit(
        unit_id="U_RETRY", sequence=1, knowledge_module="组成",
        learning_objective="识记组成", retrieval_query="四君子汤 组成",
        required_question_count=1,
    )
    item = _scope_question("Q_RETRY", "KP_1")
    evidence = EvidencePack(
        evidence_pack_id="EP_RETRY", query="四君子汤",
        resolved_kp_ids=["KP_OTHER"], evidence_items=[],
    )
    model = _ScopeJudgementModel(
        {"in_scope_kp_ids": ["KP_1"], "scope_reason": "属于本单元。"},
        raise_on_scope=1,
    )
    scope, failure = await _scope_agent(model)._resolve_unit_scope_kp_ids(
        context=context("组卷"), unit=unit, items=[item], evidence_pack=evidence,
        judged_keys=set(),
    )
    assert scope == ["KP_1"]
    assert failure == ""
    assert model.scope_calls == 2


@pytest.mark.asyncio
async def test_unit_scope_judgement_is_reused_when_catalog_is_unchanged() -> None:
    """目录没有新增知识点时不重复调用模型。"""
    unit = BlueprintUnit(
        unit_id="U_CACHE", sequence=1, knowledge_module="组成",
        learning_objective="识记组成", retrieval_query="四君子汤 组成",
        required_question_count=1,
    )
    item = _scope_question("Q_CACHE", "KP_1")
    evidence = EvidencePack(
        evidence_pack_id="EP_CACHE", query="四君子汤", evidence_items=[]
    )
    model = _ScopeJudgementModel({
        "in_scope_kp_ids": ["KP_1"], "scope_reason": "属于本单元。",
    })
    agent = _scope_agent(model)
    judged_keys: set[tuple[str, ...]] = set()
    for _ in range(3):
        scope, failure = await agent._resolve_unit_scope_kp_ids(
            context=context("组卷"), unit=unit, items=[item], evidence_pack=evidence,
            judged_keys=judged_keys,
        )
        assert scope == ["KP_1"]
        assert failure == ""
    assert model.scope_calls == 1


def _bridged_question(
    question_id: str,
    *bridges: tuple[str, str, str, str],
) -> QuestionDetail:
    """按 (kp_id, 教材, 章节, 知识点) 构造带结构化桥接的候选。

    第一个桥接是主知识点，其余是次要桥接；教材/章节/知识点名称写进
    ``source_metadata["knowledge_points"]``，这是三级名称的唯一载体。
    """
    return QuestionDetail(
        question_id=question_id,
        question_type="单项选择题",
        stem=f"{question_id}题干",
        reference_answer="A",
        analysis="解析",
        options=["A. 正确项", "B. 干扰项"],
        tags=[],
        source_metadata={
            "knowledge_points": [
                {
                    "kp": {
                        "kp_id": kp_id, "kp_lv1": textbook,
                        "kp_lv2": chapter, "kp_lv3": point,
                    }
                }
                for kp_id, textbook, chapter, point in bridges
            ]
        },
        bridges=[
            QuestionBridge(
                kp_id=kp_id,
                bridge_layer="strict",
                relation="primary" if index == 1 else "secondary",
                confidence=1.0,
                rank=index,
                evidence_chunk_uid=f"{textbook}:{point}",
                match_method="question_kp_ids",
            )
            for index, (kp_id, textbook, chapter, point) in enumerate(
                bridges, start=1
            )
        ],
        retrieval=QuestionRetrievalMetadata(
            channels=["bridge"], channel_scores={"bridge": 1.0}, fusion_score=1.0,
        ),
    )


class _PollutedScopeRegistry:
    """返回线上实测的候选集：检索命中列表既多收又漏收。

    多收：方剂学"下焦蓄血证"、金匮要略"水气"、中医学概论"方剂的组成"、
    中药药剂学"鉴别"这类同名或泛化的知识点被名称召回进来。
    漏收：同一章节里没被名称命中的"太阳中风证"不在命中列表里。
    """

    def __init__(self, items: list[QuestionDetail]) -> None:
        self.items = items
        self.question_queries: list[str] = []

    async def invoke(self, name, agent, **kwargs):
        if name == "get_kp_with_content":
            return EvidencePack(
                evidence_pack_id="EP_POLLUTED",
                query=kwargs["query"],
                resolved_kp_ids=["005353", "033712", "050313", "052357", "068300"],
                evidence_items=[
                    EvidenceItem(
                        evidence_id="E_1", source_id="S_1",
                        content_summary="《伤寒论》太阳病篇原文与释义",
                        authority_level="textbook", confidence=0.9,
                    )
                ],
            )
        assert name == "get_question_with_content"
        self.question_queries.append(kwargs["query"])
        return QuestionSearchResult(
            query=kwargs["query"],
            resolved_kp_ids=["005353", "033712", "050313", "052357", "068300"],
            embedding_model="stub",
            vector_index_path="stub",
            items=list(self.items),
        )


class _ScopedPaperModel(FixedModel):
    """按阶段返回组卷检索规划与单元范围判定，并记录两次调用。"""

    def __init__(
        self,
        *,
        plan: dict,
        judgement: dict,
        raise_on_scope: bool = False,
    ) -> None:
        super().__init__({})
        self.plan = plan
        self.judgement = judgement
        self.raise_on_scope = raise_on_scope
        self.phases: list[str] = []
        self.scope_payloads: list[dict] = []
        self.scope_calls = 0

    async def complete_json(self, role, payload, on_delta=None):
        business = payload["payload"]
        phase = business.get("phase")
        self.phases.append(phase)
        if phase == "plan_paper_unit_retrieval":
            return self.plan
        if phase == "decide_paper_unit_scope":
            self.scope_calls += 1
            self.scope_payloads.append(business)
            if self.raise_on_scope:
                raise RuntimeError("scope judgement unavailable")
            return self.judgement
        if phase == "paper_retrieval_decision":
            return {
                "decision": "enough",
                "reason": "首轮正式候选已经满足本单元题量。",
                "supplemental_queries": [],
                "missing_requirements": [],
            }
        return await super().complete_json(role, payload, on_delta=on_delta)


@pytest.mark.asyncio
async def test_paper_unit_pool_excludes_candidates_outside_the_judged_scope() -> None:
    """端到端：组卷候选池只留判定范围内的题目，检索命中列表不再当范围用。

    线上失效现场（09-18 04:43 那次）：单元声明《伤寒论》太阳病篇，检索命中
    列表里混进方剂学"下焦蓄血证"、金匮要略"水气"、中医学概论"方剂的组成"，
    同时漏掉同章节的"太阳中风证"。准入边界取的是命中列表，于是越界题进池、
    范围内题被挤掉。这里复现同一批候选，断言最终候选池只含范围内题目。
    """
    blueprint = PaperBlueprint(
        blueprint_id="BP_SCOPE",
        title="测试卷",
        source_status="user_provided_unverified",
        scope_summary="《伤寒论》太阳病篇",
        units=[
            BlueprintUnit(
                unit_id="U_SCOPE", sequence=1,
                knowledge_module="《伤寒论》太阳病篇",
                learning_objective="检验并巩固太阳病篇核心知识",
                retrieval_query="《伤寒论》太阳病篇",
                required_question_count=2, candidate_limit=10,
            )
        ],
    )
    candidates = [
        _bridged_question(
            "Q_TIGANG", ("033712", "伤寒论选读", "第二节 太阳病辨证纲要", "太阳病提纲")
        ),
        _bridged_question(
            "Q_ZHONGFENG", ("033713", "伤寒论选读", "第二节 太阳病辨证纲要", "太阳中风证")
        ),
        _bridged_question(
            "Q_TAOHECHENGQI",
            ("033784", "伤寒论选读", "第三节 太阳病变证", "桃核承气汤证"),
        ),
        # 越界：主知识点是中医学概论的方剂组成，只顺带桥接到本草典籍选读
        _bridged_question(
            "Q_FANGJI_ZUCHENG",
            ("005353", "中医学概论", "方剂", "方剂的组成"),
            ("052357", "本草典籍选读", "麻黄汤", "麻黄汤证"),
        ),
        # 越界：方剂学的下焦蓄血证与伤寒论的下焦蓄血同名异书
        _bridged_question(
            "Q_XIAJIAO",
            ("050313", "方剂学", "理血剂", "下焦蓄血证"),
            ("029486", "中西医结合耳鼻咽喉科学", "鼻科", "鼻衄"),
        ),
        # 越界：金匮要略的水气与伤寒论的水气同名异书
        _bridged_question(
            "Q_SHUIQI", ("068300", "金匮要略", "【原文】", "水气")
        ),
    ]
    registry = _PollutedScopeRegistry(candidates)
    model = _ScopedPaperModel(
        plan={
            "kp_query": "太阳病提纲 中风 伤寒",
            "question_query": "太阳病提纲证 中风 伤寒 简答题",
            "retrieval_reason": "覆盖太阳病篇提纲证与辨证纲要。",
        },
        judgement={
            "in_scope_kp_ids": ["033712", "033713", "033784"],
            "scope_reason": (
                "伤寒论选读第二节太阳病辨证纲要、第三节太阳病变证属于本单元；"
                "中医学概论、方剂学、金匮要略的同名知识点是不同教材的独立条目，"
                "本草典籍选读的麻黄汤证属于药物篇章，均不属于本单元。"
            ),
        },
    )

    result = await KnowledgeBaseAgent(None, model).run({
        **context("组卷"),
        "task_type": "paper_generation",
        "dependency_outputs": {
            "paper_blueprint": AgentEnvelope(
                artifact_id="A_SCOPE", artifact_type="paper_blueprint",
                case_id="C1", trace_id="T1", request_id="R1", execution_id="E1",
                step_id="paper_blueprint", producer="expert_agent",
                task_type="paper_generation", learner_id="L1", payload=blueprint,
            )
        },
        "tool_registry": registry,
    })

    unit = result.payload.units[0]
    # 判定范围被持久化，与检索命中列表分开保存
    assert unit.scope_kp_ids == ["033712", "033713", "033784"]
    assert unit.resolved_kp_ids == ["005353", "033712", "050313", "052357", "068300"]

    pool_ids = [item.question_id for item in unit.items]
    # 候选池只剩判定范围内的题目；越界题一道都不进池
    assert pool_ids == ["Q_TIGANG", "Q_ZHONGFENG", "Q_TAOHECHENGQI"]
    assert unit.eligible_count == 3
    assert unit.uncertain_count == 0
    assert unit.rejected_count == 3
    assert "topic_entity_mismatch" in unit.admission_notes

    # 模型看到的是候选实际桥接到的知识点目录，含教材/章节/知识点三级名称
    catalog = model.scope_payloads[0]["knowledge_point_catalog"]
    assert {entry["kp_id"]: entry["kp_lv1"] for entry in catalog} == {
        "005353": "中医学概论",
        "029486": "中西医结合耳鼻咽喉科学",
        "033712": "伤寒论选读",
        "033713": "伤寒论选读",
        "033784": "伤寒论选读",
        "050313": "方剂学",
        "052357": "本草典籍选读",
        "068300": "金匮要略",
    }
    assert model.scope_payloads[0]["blueprint_unit"]["knowledge_module"] == (
        "《伤寒论》太阳病篇"
    )
    # 判定成功时不留“退回命中列表”的告警
    assert not [w for w in unit.warnings if "准入范围判定不可用" in w]


@pytest.mark.asyncio
async def test_paper_unit_warns_when_scope_judgement_is_unavailable() -> None:
    blueprint = PaperBlueprint(
        blueprint_id="BP_SCOPE_FAIL",
        title="测试卷",
        source_status="user_provided_unverified",
        scope_summary="《伤寒论》太阳病篇",
        units=[
            BlueprintUnit(
                unit_id="U_SCOPE_FAIL", sequence=1,
                knowledge_module="《伤寒论》太阳病篇",
                learning_objective="检验并巩固太阳病篇核心知识",
                retrieval_query="《伤寒论》太阳病篇",
                required_question_count=1, candidate_limit=10,
            )
        ],
    )
    candidates = [
        _bridged_question(
            "Q_TIGANG", ("033712", "伤寒论选读", "第二节 太阳病辨证纲要", "太阳病提纲")
        ),
        _bridged_question(
            "Q_SHUIQI", ("068300", "金匮要略", "【原文】", "水气")
        ),
    ]
    registry = _PollutedScopeRegistry(candidates)
    model = _ScopedPaperModel(
        plan={
            "kp_query": "太阳病提纲",
            "question_query": "太阳病提纲证 简答题",
            "retrieval_reason": "覆盖太阳病篇提纲证。",
        },
        judgement={"in_scope_kp_ids": ["033712"], "scope_reason": "属于本单元。"},
        raise_on_scope=True,
    )

    result = await KnowledgeBaseAgent(None, model).run({
        **context("组卷"),
        "task_type": "paper_generation",
        "dependency_outputs": {
            "paper_blueprint": AgentEnvelope(
                artifact_id="A_SCOPE_FAIL", artifact_type="paper_blueprint",
                case_id="C1", trace_id="T1", request_id="R1", execution_id="E1",
                step_id="paper_blueprint", producer="expert_agent",
                task_type="paper_generation", learner_id="L1", payload=blueprint,
            )
        },
        "tool_registry": registry,
    })

    unit = result.payload.units[0]
    # 退回检索命中列表：金匮要略“水气”的主知识点在命中列表里，于是越界题被放行
    assert unit.scope_kp_ids == [
        "005353", "033712", "050313", "052357", "068300",
    ]
    assert [item.question_id for item in unit.items] == ["Q_TIGANG", "Q_SHUIQI"]
    notice = [w for w in unit.warnings if "准入范围判定不可用" in w]
    assert len(notice) == 1
    assert "RuntimeError" in notice[0]
    # 失败重试一次后才退回
    assert model.scope_calls == 2


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

    assert model.phases == ["plan_paper_unit_retrieval", "paper_retrieval_decision"]
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

    assert model.phases == [
        "plan_paper_unit_retrieval",
        "paper_retrieval_decision",
        "paper_retrieval_decision",
    ]
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


def test_question_type_preferences_recognise_platform_english_enums() -> None:
    """网络题写成平台英文枚举时也必须匹配上题型配额。

    线上失效现场（2026-09-18「太阳中风证练习试卷」）：8 道已入库的网络题全部
    写成 ``single_choice``，而别名表只认中文，``_matches_question_type`` 对
    每一道都返回 False，整批被判成「题型不一致」丢弃。对照实验：同一批题只把
    题型改成「单选题」，8 道里有 4 道立刻通过准入与题型过滤。
    """

    for actual in ("single_choice", "Single Choice", "singlechoice", "单选题"):
        assert KnowledgeBaseAgent._matches_question_type(
            actual, ["单项选择题"]
        ), actual
    assert KnowledgeBaseAgent._matches_question_type("multiple_choice", ["选择题"])
    assert KnowledgeBaseAgent._matches_question_type("true_false", ["判断题"])
    assert KnowledgeBaseAgent._matches_question_type("short_answer", ["简答题"])
    assert KnowledgeBaseAgent._matches_question_type("case_quiz", ["简答题"])
    # 归一化只翻译词汇，不会把选择题算成简答题。
    assert not KnowledgeBaseAgent._matches_question_type(
        "single_choice", ["简答题"]
    )


@pytest.mark.asyncio
async def test_paper_unit_retrieval_searches_the_query_the_agent_wrote() -> None:
    """组卷首轮检索词由知识库智能体撰写，不直接拿蓝图的范围原文去检索。

    蓝图里的 `retrieval_query` 是给人读的范围要求（含"正式题库优先，不足时按全局
    补题顺序补足"这类执行要求）。直接当检索词会把执行要求也变成检索词：线上实测
    BM25 词数 7→37、候选集 3.2%→30.9%、Top-10 主题相关 8/10→3/10。
    """

    blueprint = _planning_blueprint("BP_AGENT_QUERY")
    registry = BlueprintToolRegistry()
    model = PaperUnitPlanModel(
        [
            {
                "decision": "enough",
                "reason": "首轮正式候选已经满足本单元题量。",
                "supplemental_queries": [],
                "missing_requirements": [],
            }
        ],
        {
            "kp_query": "太阳病提纲证 病机",
            "question_query": "太阳病提纲证 中风 伤寒 温病 鉴别 简答题",
            "retrieval_reason": "覆盖本单元提纲证与三类表证鉴别范围。",
        },
    )
    await KnowledgeBaseAgent(None, model).run(
        {
            **context("组卷"),
            "task_type": "paper_generation",
            "dependency_outputs": {
                "paper_blueprint": _planning_envelope(blueprint, "A_AGENT_QUERY")
            },
            "tool_registry": registry,
        }
    )

    assert model.phases == ["plan_paper_unit_retrieval", "paper_retrieval_decision"]
    # 检索用的是模型写的聚焦短语，不是蓝图的范围原文。
    assert [call["query"] for call in registry.calls] == [
        "太阳病提纲证 中风 伤寒 温病 鉴别 简答题"
    ]
    assert registry.kp_queries == ["太阳病提纲证 病机"]
    assert blueprint.units[0].retrieval_query not in registry.kp_queries
    # 模型看到的是范围要求本身，以及系统声明它能执行的两类检索。
    plan_payload = model.plan_payloads[0]
    assert plan_payload["blueprint_unit"]["retrieval_requirement"] == (
        blueprint.units[0].retrieval_query
    )
    assert plan_payload["blueprint_unit"]["required_question_count"] == 1
    assert set(plan_payload["output_schema"]["properties"]) == {
        "kp_query",
        "question_query",
        "retrieval_reason",
    }


@pytest.mark.asyncio
async def test_paper_unit_retrieval_falls_back_to_the_blueprint_requirement() -> None:
    """规划失败时退回范围原文，并留下可见告警，不用系统自造的查询掩盖失败。"""

    blueprint = _planning_blueprint("BP_PLAN_FALLBACK")
    registry = BlueprintToolRegistry()
    model = PaperUnitPlanModel(
        [
            {
                "decision": "enough",
                "reason": "首轮正式候选已经满足本单元题量。",
                "supplemental_queries": [],
                "missing_requirements": [],
            }
        ],
        {"question_query": ""},  # 不合契约，规划视为失败
    )
    result = await KnowledgeBaseAgent(None, model).run(
        {
            **context("组卷"),
            "task_type": "paper_generation",
            "dependency_outputs": {
                "paper_blueprint": _planning_envelope(blueprint, "A_PLAN_FALLBACK")
            },
            "tool_registry": registry,
        }
    )

    requirement = blueprint.units[0].retrieval_query
    assert [call["query"] for call in registry.calls] == [requirement]
    assert registry.kp_queries == [requirement]
    assert any("检索计划暂不可用" in item for item in result.payload.units[0].warnings)


@pytest.mark.asyncio
async def test_paper_unit_planning_error_does_not_abort_the_unit() -> None:
    """规划调用抛错等同于规划不可用，不得让整个单元检索失败。"""

    blueprint = _planning_blueprint("BP_PLAN_RAISED")
    registry = BlueprintToolRegistry()
    model = PaperUnitPlanModel(
        [
            {
                "decision": "enough",
                "reason": "首轮正式候选已经满足本单元题量。",
                "supplemental_queries": [],
                "missing_requirements": [],
            }
        ],
        None,
        raise_on_plan=True,
    )
    result = await KnowledgeBaseAgent(None, model).run(
        {
            **context("组卷"),
            "task_type": "paper_generation",
            "dependency_outputs": {
                "paper_blueprint": _planning_envelope(blueprint, "A_PLAN_RAISED")
            },
            "tool_registry": registry,
        }
    )

    assert result.payload.units[0].items
    assert [call["query"] for call in registry.calls] == [
        blueprint.units[0].retrieval_query
    ]


@pytest.mark.asyncio
async def test_planned_query_is_not_repeated_as_a_supplement() -> None:
    """已执行的首轮查询进入去重集合，不会被当成补充检索重复执行。"""

    blueprint = _planning_blueprint("BP_PLAN_DEDUPE")
    registry = BlueprintToolRegistry()
    planned = "四君子汤 组成 功效 简答题"
    model = PaperUnitPlanModel(
        [
            {
                "decision": "continue",
                "reason": "候选不足，需要补充。",
                "supplemental_queries": [planned],
                "missing_requirements": ["还缺少正式候选题"],
            }
        ],
        {
            "kp_query": "四君子汤 组成",
            "question_query": planned,
            "retrieval_reason": "覆盖组成范围。",
        },
    )
    result = await KnowledgeBaseAgent(None, model).run(
        {
            **context("组卷"),
            "task_type": "paper_generation",
            "dependency_outputs": {
                "paper_blueprint": _planning_envelope(blueprint, "A_PLAN_DEDUPE")
            },
            "tool_registry": registry,
        }
    )

    assert [call["query"] for call in registry.calls] == [planned]
    assert any(
        "未提供可执行的新查询" in item for item in result.payload.units[0].warnings
    )


@pytest.mark.asyncio
async def test_paper_unit_retrieval_event_reports_both_requirement_and_query() -> None:
    """运行事件同时给出蓝图范围要求与实际执行的检索语句，便于线上归因。"""

    blueprint = _planning_blueprint("BP_EVENT_QUERY")
    planned = "四君子汤 组成 简答题"
    model = PaperUnitPlanModel(
        [
            {
                "decision": "enough",
                "reason": "首轮正式候选已经满足本单元题量。",
                "supplemental_queries": [],
                "missing_requirements": [],
            }
        ],
        {
            "kp_query": None,
            "question_query": planned,
            "retrieval_reason": "覆盖组成范围。",
        },
    )
    events: list[dict] = []
    token = bind_event_sink(events.append)
    try:
        await KnowledgeBaseAgent(None, model).run(
            {
                **context("组卷"),
                "task_type": "paper_generation",
                "dependency_outputs": {
                    "paper_blueprint": _planning_envelope(blueprint, "A_EVENT_QUERY")
                },
                "tool_registry": BlueprintToolRegistry(),
            }
        )
    finally:
        reset_event_sink(token)

    retrieval_event = next(
        item for item in events if item["event"] == "paper_unit_retrieval"
    )
    assert retrieval_event["query"] == planned
    assert retrieval_event["retrieval_requirement"] == blueprint.units[0].retrieval_query

    plan_event = next(
        item for item in events if item["event"] == "paper_unit_retrieval_plan"
    )
    assert plan_event["question_query"] == planned
    assert plan_event["kp_query"] is None
    assert plan_event["retrieval_requirement"] == blueprint.units[0].retrieval_query
    assert plan_event["retrieval_reason"] == "覆盖组成范围。"


@pytest.mark.asyncio
async def test_paper_unit_does_not_borrow_when_the_scope_judgement_is_empty() -> None:
    """端到端：范围判定结论为空时一道题都不借，而不是借满整份配额。

    复现线上失效现场：单元准入范围判定返回空数组。修复前空范围与「判定不可
    用」共用 ``uncertain`` 这一档，于是 6 道候选全部进入降级借题池，按
    ``required_question_count`` 补满，卷面变成「本卷有 N 道题来自其他知识
    点」。修复后空范围按越界处理：候选一道不进池，借题数为 0，缺口交给现场
    生成与网络题回填补足，并在单元告警里如实说明是判定结论为空。
    """
    blueprint = PaperBlueprint(
        blueprint_id="BP_EMPTY_SCOPE",
        title="测试卷",
        source_status="user_provided_unverified",
        scope_summary="《伤寒论》太阳病篇",
        units=[
            BlueprintUnit(
                unit_id="U_EMPTY_SCOPE", sequence=1,
                knowledge_module="《伤寒论》太阳病篇",
                learning_objective="检验并巩固太阳病篇核心知识",
                retrieval_query="《伤寒论》太阳病篇",
                required_question_count=2, candidate_limit=10,
            )
        ],
    )
    candidates = [
        _bridged_question(
            "Q_TIGANG", ("033712", "伤寒论选读", "第二节 太阳病辨证纲要", "太阳病提纲")
        ),
        _bridged_question(
            "Q_ZHONGFENG", ("033713", "伤寒论选读", "第二节 太阳病辨证纲要", "太阳中风证")
        ),
        _bridged_question(
            "Q_TAOHECHENGQI",
            ("033784", "伤寒论选读", "第三节 太阳病变证", "桃核承气汤证"),
        ),
        _bridged_question(
            "Q_FANGJI_ZUCHENG",
            ("005353", "中医学概论", "方剂", "方剂的组成"),
            ("052357", "本草典籍选读", "麻黄汤", "麻黄汤证"),
        ),
        _bridged_question(
            "Q_XIAJIAO",
            ("050313", "方剂学", "理血剂", "下焦蓄血证"),
            ("029486", "中西医结合耳鼻咽喉科学", "鼻科", "鼻衄"),
        ),
        _bridged_question("Q_SHUIQI", ("068300", "金匮要略", "【原文】", "水气")),
    ]
    registry = _PollutedScopeRegistry(candidates)
    model = _ScopedPaperModel(
        plan={
            "kp_query": "太阳病提纲 中风 伤寒",
            "question_query": "太阳病提纲证 中风 伤寒 简答题",
            "retrieval_reason": "覆盖太阳病篇提纲证与辨证纲要。",
        },
        judgement={
            "in_scope_kp_ids": [],
            "scope_reason": "候选目录里的知识点均不属于本单元。",
        },
    )

    result = await KnowledgeBaseAgent(None, model).run({
        **context("组卷"),
        "task_type": "paper_generation",
        "dependency_outputs": {
            "paper_blueprint": AgentEnvelope(
                artifact_id="A_EMPTY_SCOPE", artifact_type="paper_blueprint",
                case_id="C1", trace_id="T1", request_id="R1", execution_id="E1",
                step_id="paper_blueprint", producer="expert_agent",
                task_type="paper_generation", learner_id="L1", payload=blueprint,
            )
        },
        "tool_registry": registry,
    })

    unit = result.payload.units[0]
    assert unit.scope_kp_ids == []
    # 全部候选判为越界，而不是「不确定」
    assert unit.eligible_count == 0
    assert unit.uncertain_count == 0
    assert unit.rejected_count == 6
    # 关键用户可见差异：借题数为 0，卷面不会出现其他知识点的题
    assert unit.borrowed_question_count == 0
    assert unit.items == []
    assert [w for w in unit.warnings if "准入范围判定结论为空" in w]
    assert not [w for w in unit.warnings if "准入范围判定不可用" in w]


class _RecordingIngest:
    """假灌题服务：记录回填目标与题型，只读已入库的题（这里一道都没有）。"""

    def __init__(self) -> None:
        self.reads: list[str] = []
        self.scheduled: list[tuple[str, tuple[str, ...]]] = []

    def web_questions_for(self, name: str) -> list[dict]:
        self.reads.append(name)
        return []

    def schedule_backfill(self, name: str, **kwargs) -> bool:
        self.scheduled.append((name, tuple(kwargs.get("question_types") or ())))
        return True


@pytest.mark.asyncio
async def test_web_backfill_triggers_on_the_effective_pool_size() -> None:
    """端到端：触发回填的判定必须与建池同口径，读池子的实际大小。

    复现线上失效现场（2026-09-18「太阳中风证」）：该单元 eligible 25 道、
    uncertain 3 道、需要 20 题。eligible 总数已经够全局题量，缺口为 0，建池时
    uncertain 整批被丢弃；而题型过滤后池子只剩 18 道。旧触发条件用「未被拒绝
    的候选数」（eligible + uncertain，题型过滤后 21 道）判定题量已够，跳过
    网络题回填，最终 20 题里 9 道靠现场生成，``paper_unit_web_backfill``
    事件一条都没有。

    这里把同一结构缩到 3 题：eligible 3 道（题型过滤后 2 道）、uncertain 2 道
    （题型匹配），需要 3 题。旧口径看到 4 道 → 不回填；新口径看到 2 道 → 回填。
    """
    scope = ["033710", "033712", "033713", "033784"]
    blueprint = PaperBlueprint(
        blueprint_id="BP_POOL_GAP",
        title="测试卷",
        source_status="user_provided_unverified",
        scope_summary="《伤寒论》太阳病篇",
        units=[
            BlueprintUnit(
                unit_id="U_POOL_GAP", sequence=1,
                knowledge_module="《伤寒论》太阳病篇",
                learning_objective="检验并巩固太阳病篇核心知识",
                retrieval_query="《伤寒论》太阳病篇",
                required_question_count=3, candidate_limit=10,
                question_type_preferences=["单项选择题"],
            )
        ],
    )
    candidates = [
        _bridged_question(
            "Q_TIGANG", ("033712", "伤寒论选读", "第二节 太阳病辨证纲要", "太阳病提纲")
        ),
        _bridged_question(
            "Q_ZHONGFENG", ("033713", "伤寒论选读", "第二节 太阳病辨证纲要", "太阳中风证")
        ),
        # 范围内但题型不匹配：本单元只要单项选择题
        _bridged_question(
            "Q_TAOHECHENGQI",
            ("033784", "伤寒论选读", "第三节 太阳病变证", "桃核承气汤证"),
        ).model_copy(update={"question_type": "简答题"}),
        # 只有次要桥接落在范围内 → uncertain
        _bridged_question(
            "Q_XIAJIAO",
            ("050313", "方剂学", "理血剂", "下焦蓄血证"),
            ("033710", "伤寒论选读", "第二节 太阳病辨证纲要", "太阳伤寒证"),
        ),
        _bridged_question(
            "Q_SHUIQI",
            ("068300", "金匮要略", "【原文】", "水气"),
            ("033713", "伤寒论选读", "第二节 太阳病辨证纲要", "太阳中风证"),
        ),
    ]
    unit_contract = blueprint.units[0]
    # 两种口径确实不同：未被拒绝、题型匹配的候选 4 道，池子只有 2 道。
    assert len(
        KnowledgeBaseAgent._admissible_unit_candidates(
            candidates, unit_contract, scope
        )
    ) == 4
    assert len(
        KnowledgeBaseAgent._unit_candidate_pool(
            candidates, unit_contract, scope
        ).pool
    ) == 2

    registry = _PollutedScopeRegistry(candidates)
    ingest = _RecordingIngest()
    model = _ScopedPaperModel(
        plan={
            "kp_query": "太阳病提纲 中风 伤寒",
            "question_query": "太阳病提纲证 中风 伤寒 单项选择题",
            "retrieval_reason": "覆盖太阳病篇提纲证与辨证纲要。",
        },
        judgement={
            "in_scope_kp_ids": list(scope),
            "scope_reason": "伤寒论选读第二、三节里的这些知识点属于本单元。",
        },
    )
    agent = KnowledgeBaseAgent(
        type("_Tool", (), {"delivery_backend": object()})(),
        model,
        web_question_ingest=ingest,
    )

    result = await agent.run({
        **context("组卷"),
        "task_type": "paper_generation",
        "dependency_outputs": {
            "paper_blueprint": AgentEnvelope(
                artifact_id="A_POOL_GAP", artifact_type="paper_blueprint",
                case_id="C1", trace_id="T1", request_id="R1", execution_id="E1",
                step_id="paper_blueprint", producer="expert_agent",
                task_type="paper_generation", learner_id="L1", payload=blueprint,
            )
        },
        "tool_registry": registry,
    })

    unit = result.payload.units[0]
    assert unit.eligible_count == 3
    assert unit.uncertain_count == 2
    assert len(unit.items) == 2, "题型过滤后池子只有 2 道"
    # 池子不足 3 道 → 网络题回填必须被触发
    assert ingest.scheduled, (
        "池子实际不足时必须触发回填；按「未被拒绝的候选数」判定会误以为题量已够"
    )
    assert all(types == ("单项选择题",) for _, types in ingest.scheduled)
    # 回填目标必须是本单元准入范围内的知识点
    scope_names = {"太阳伤寒证", "太阳病提纲", "太阳中风证", "桃核承气汤证"}
    assert {name for name, _ in ingest.scheduled} <= scope_names

