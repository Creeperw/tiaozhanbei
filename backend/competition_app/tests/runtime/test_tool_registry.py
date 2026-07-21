import pytest

from competition_app.runtime.tool_registry import ToolPermissionError, ToolRegistry
from competition_app.runtime.trace import TraceRecorder


@pytest.mark.asyncio
async def test_tool_registry_enforces_agent_permissions() -> None:
    registry = ToolRegistry()

    async def search(query: str) -> dict[str, str]:
        return {"query": query}

    registry.register("search_knowledge", search, allowed_agents={"knowledge_base_agent"})

    assert await registry.invoke("search_knowledge", "knowledge_base_agent", query="四君子汤") == {
        "query": "四君子汤"
    }
    with pytest.raises(ToolPermissionError):
        await registry.invoke("search_knowledge", "expert_agent", query="四君子汤")


def test_tool_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry()
    registry.register("search", lambda: None, allowed_agents={"knowledge_base_agent"})

    with pytest.raises(ValueError, match="already registered"):
        registry.register("search", lambda: None, allowed_agents={"knowledge_base_agent"})


@pytest.mark.asyncio
async def test_tool_registry_records_safe_success_summary() -> None:
    registry = ToolRegistry()
    recorder = TraceRecorder()
    registry.register("search", lambda query: {"count": 2}, allowed_agents={"knowledge_base_agent"})

    await registry.invoke(
        "search",
        "knowledge_base_agent",
        trace_recorder=recorder,
        safe_input_summary={"query_length": 4},
        safe_output_summary_factory=lambda _: {"candidate_count": 2},
        query="四君子汤",
    )

    assert recorder.tool_items[0].safe_output_summary == {"candidate_count": 2}


@pytest.mark.asyncio
async def test_tool_registry_records_error_type_without_raw_inputs() -> None:
    registry = ToolRegistry()
    recorder = TraceRecorder()

    def fail(query: str) -> None:
        raise LookupError("answer=secret")

    registry.register("search", fail, allowed_agents={"knowledge_base_agent"})

    with pytest.raises(LookupError):
        await registry.invoke(
            "search",
            "knowledge_base_agent",
            trace_recorder=recorder,
            safe_input_summary={"query_length": 4},
            query="四君子汤",
        )

    assert recorder.tool_items[0].error_type == "LookupError"
    assert "secret" not in recorder.tool_items[0].model_dump_json()