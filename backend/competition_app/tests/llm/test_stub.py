import pytest

from competition_app.llm.stub import StubChatModel


@pytest.mark.asyncio
async def test_stub_chat_model_returns_role_specific_json() -> None:
    model = StubChatModel()

    result = await model.complete_json("memory_agent", {"messages": []})

    assert result["summary"]
    assert set(result) == {
        "summary",
        "preserved_facts",
        "unresolved_questions",
        "temporary_constraints",
        "memory_candidates",
    }
