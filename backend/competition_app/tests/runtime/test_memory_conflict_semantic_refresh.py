import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from competition_app.runtime.langgraph_orchestrator import LangGraphOrchestrator
from competition_app.runtime.agent_registry import AgentRegistry
from competition_app.runtime.trace import TraceRecorder
from competition_app.contracts.execution import ExecutionStep


@pytest.mark.parametrize("answer", [
    "不是原来的目标，我确认改成新的考试", "取消修改，保持原目标",
    "不要误解，我确认变更", "维持原来的目标", "是的",
])
def test_answer_invalidates_cached_route_without_classifying_intent(answer):
    assert LangGraphOrchestrator._memory_conflict_affects_route(
        {"interrupt_type": "memory_conflict"}, {"memory_conflict_answer": answer}
    )


@pytest.mark.parametrize("kind,answer", [("memory_conflict", ""), ("route_resolution", "确认修改")])
def test_only_answered_memory_interrupt_triggers_refresh(kind, answer):
    assert not LangGraphOrchestrator._memory_conflict_affects_route(
        {"interrupt_type": kind}, {"memory_conflict_answer": answer}
    )


@pytest.mark.asyncio
async def test_overlapping_resume_conditions_refresh_route_once():
    orchestrator = LangGraphOrchestrator(AgentRegistry())
    route = ExecutionStep(step_id="route_resolution", agent="default_route_resolver")
    diagnosis = ExecutionStep(step_id="diagnosis", agent="diagnosis_agent", depends_on=[route.step_id])
    output = SimpleNamespace(payload=SimpleNamespace(unknowns_to_confirm=["请确认目标"], textbook_route=None))
    orchestrator._run_step = AsyncMock(return_value=output)
    context = {"memory_conflict_answer": "不是原来的目标，我确认改成新的考试"}
    refreshed = await orchestrator._refresh_resume_dependencies(
        diagnosis, context, {route.step_id: output}, TraceRecorder(),
        {route.step_id: route, diagnosis.step_id: diagnosis},
        {"interrupt_type": "memory_conflict", "questions": ["请确认目标"]},
    )
    orchestrator._run_step.assert_awaited_once()
    assert orchestrator._run_step.call_args.args[1] == context
    assert refreshed[route.step_id] is output