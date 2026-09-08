from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError

from competition_app.agents.knowledge_base import KnowledgeBaseAgent
from competition_app.contracts.knowledge import EvidencePack, QuestionSearchResult
from competition_app.llm.schemas import KnowledgeModelOutput, KnowledgeRetrievalPlanModelOutput
from competition_app.llm.openai_compatible import ModelResponseError
from competition_app.runtime.tool_registry import ToolRegistry
from competition_app.tools.knowledge_retrieval import KnowledgeRetrievalTool


def context(flag=False):
    return {
        "case_id": "CASE_autonomy", "trace_id": "TRACE_autonomy",
        "request_id": "REQ_autonomy", "execution_id": "EXE_autonomy",
        "step_id": "knowledge", "learner_id": "L1",
        "task_type": "learning_plan", "external_information_request": flag,
        "user_request": "制定计划，考试日期未定，不要查询考试时间。".ljust(542, "复"),
        "planning_request_scope": {"mode": "route", "objects": [],
                                   "source_quote": "制定计划", "clarification_question": None},
        "dependency_outputs": {},
    }


def tool():
    return Mock(
        get_kp_with_content=AsyncMock(return_value=EvidencePack(evidence_pack_id="EP_local", query="复习")),
        get_question_with_content=AsyncMock(return_value=QuestionSearchResult(
            query="复习题", resolved_kp_ids=[], embedding_model="test", vector_index_path="", items=[])),
        search_web_resources=AsyncMock(return_value=[]),
        search_video_resources=AsyncMock(return_value=[]),
        search_reference_resources=AsyncMock(return_value=[]),
        search_question_resources=AsyncMock(return_value=[]),
    )


def plan(**kwargs):
    return {"kp_query": None, "question_query": None, "kp_concepts": [],
            "external_queries": [], "retrieval_reason": "按任务语义决定查询", **kwargs}


async def run_plan(t, output, flag=False, registry=None):
    model = Mock(complete_json=AsyncMock(return_value=output))
    agent = KnowledgeBaseAgent(t, model)
    ctx = context(flag)
    if registry is not None:
        ctx["tool_registry"] = registry
    with patch.object(agent, "_summarize_retrieved_content", new=AsyncMock(return_value=KnowledgeModelOutput())):
        result = await agent.run(ctx)
    model.complete_json.assert_awaited_once()
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [False, True])
async def test_long_date_constraint_can_select_no_queries_even_with_upstream_hint(flag):
    t = tool()
    result = await run_plan(t, plan(), flag)
    assert t.mock_calls == []
    assert result.payload.evidence_items == []
    assert not result.payload.question_search_decision.final_question_search_needed


@pytest.mark.asyncio
async def test_agent_selects_local_and_questions_without_implicit_web():
    t = tool()
    await run_plan(t, plan(kp_query="中医学基础 复习诊断", question_query="中医学基础 分阶测试"))
    t.get_kp_with_content.assert_awaited_once_with("中医学基础 复习诊断", concepts=[], local_only=True)
    t.get_question_with_content.assert_awaited_once()
    t.search_web_resources.assert_not_awaited()
    t.search_video_resources.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["web", "video", "reference", "question"])
@pytest.mark.parametrize("registered", [False, True])
async def test_only_agent_selected_external_source_and_query_execute(source, registered):
    t = tool()
    registry = ToolRegistry() if registered else None
    if registry:
        for name in ("web", "video", "reference", "question"):
            async def handler(query, limit=3, selected=name):
                return await getattr(t, f"search_{selected}_resources")(query, limit=limit)
            registry.register(f"search_{name}_resources", handler,
                              allowed_agents={"knowledge_base_agent"})
    await run_plan(t, plan(external_queries=[{"source": source, "query": "2026年执业药师考试 官方公告"}]),
                   registry=registry)
    getattr(t, f"search_{source}_resources").assert_awaited_once_with("2026年执业药师考试 官方公告", limit=3)
    assert len(t.mock_calls) == 1


@pytest.mark.asyncio
async def test_model_failure_never_generates_system_fallback_query():
    t = tool()
    model = Mock(complete_json=AsyncMock(side_effect=ModelResponseError("unavailable")))
    with pytest.raises(ModelResponseError):
        await KnowledgeBaseAgent(t, model).run(context())
    assert t.mock_calls == []


@pytest.mark.asyncio
async def test_invalid_query_reports_only_field_and_type_without_input():
    t = tool()
    model = Mock(complete_json=AsyncMock(return_value=plan(kp_query="private-input" * 50)))
    with pytest.raises(ValueError, match="kp_query: string_too_long") as error:
        await KnowledgeBaseAgent(t, model).run(context())
    assert "private-input" not in str(error.value)
    assert t.mock_calls == []


@pytest.mark.asyncio
async def test_external_only_supplement_uses_selected_source():
    t = tool()
    t.search_web_resources.return_value = [SimpleNamespace(
        source_id="official", title="官方说明", summary="真实来源摘要", url="https://example.org",
        score=0.9, resource_type="web")]
    model = Mock(complete_json=AsyncMock(return_value=plan()))
    agent = KnowledgeBaseAgent(t, model)
    with patch.object(agent, "_summarize_retrieved_content", new=AsyncMock(side_effect=[
        KnowledgeModelOutput(need_more_retrieval=True, supplemental_external_queries=[
            {"source": "web", "query": "官方考试公告"}]),
        KnowledgeModelOutput(),
    ])):
        result = await agent.run(context())
    t.search_web_resources.assert_awaited_once_with("官方考试公告", limit=3)
    assert len(t.mock_calls) == 1
    assert len(result.payload.evidence_items) == 1


@pytest.mark.asyncio
async def test_local_tool_never_invokes_shared_web_provider_including_concepts():
    exa = Mock()
    delivery = Mock(build_local_evidence_pack=AsyncMock(return_value=EvidencePack(
        evidence_pack_id="EP_test", query="复习")))
    t = KnowledgeRetrievalTool(Mock(), Mock(), exa_retriever=exa, delivery_backend=delivery)
    await t.get_kp_with_content("复习", concepts=["如何辨证"], local_only=True)
    assert exa.mock_calls == []
    assert t.exa_retriever is exa
    assert delivery.build_local_evidence_pack.await_count == 2


@pytest.mark.asyncio
async def test_selected_web_query_is_not_rewritten_as_medical_concept_search():
    exa = Mock(search_web=AsyncMock(return_value=[]))
    t = KnowledgeRetrievalTool(Mock(), Mock(), exa_retriever=exa)
    await t.search_web_resources("官方考试日期", limit=2)
    exa.search_web.assert_awaited_once_with("官方考试日期", limit=2)
    assert len(exa.mock_calls) == 1


def test_query_contract_limits_sources_and_requires_explicit_reason():
    with pytest.raises(ValidationError):
        KnowledgeRetrievalPlanModelOutput.model_validate({})
    with pytest.raises(ValidationError):
        KnowledgeRetrievalPlanModelOutput.model_validate(plan(external_queries=[
            {"source": "arbitrary_tool", "query": "test"}]))
    with pytest.raises(ValidationError):
        KnowledgeModelOutput(supplemental_external_queries=[{"source": "web", "query": "test"}])