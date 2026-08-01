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


@pytest.mark.asyncio
async def test_stub_planner_treats_existing_plan_lookup_as_read_only_query() -> None:
    model = StubChatModel()

    result = await model.complete_json(
        "planner_agent",
        {
            "payload": {
                "user_request": "给我看看我的长期学习计划",
                "existing_plan_state": {
                    "has_long_term_plan": True,
                    "has_short_term_plan": True,
                },
            }
        },
    )

    assert result["task_type"] == "learner_data_query"
    assert result["query_kind"] == "plan_progress"
    assert result["plan_scope"] is None
    assert result["plan_action"] is None
    assert result["selected_agents"] == ["memory_agent", "diagnosis_agent"]
