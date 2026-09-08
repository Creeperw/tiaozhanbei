from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

from competition_app.contracts.exam_scope import ExamWorkspaceContext
from competition_app.contracts.review import ReviewMemoryUnit
from competition_app.exam_scope import bind_exam_workspace_context, reset_exam_workspace
from competition_app.repositories.learning_plan import InMemoryLearningPlanRepository
from competition_app.services.current_learning_state import CurrentLearningStateReader, model_learning_state
from competition_app.runtime.tool_registry import ToolRegistry, ToolPermissionError
from competition_app.agents.learning_plan_service import LearningPlanServiceAdapter, LearningStateChangedError


@pytest.fixture
def bound():
    token = bind_exam_workspace_context(ExamWorkspaceContext(learner_id="A", exam_track_id="EXAM_A"))
    yield
    reset_exam_workspace(token)


def reader(facts=None, review=None):
    return CurrentLearningStateReader(InMemoryLearningPlanRepository(),
        facts or (lambda *args, **kwargs: {"availability": "available", "books": [], "record_version": []}),
        review or SimpleNamespace(get_queue=lambda *args, **kwargs: SimpleNamespace(entries=[])))


def test_requires_bound_scope_and_disallows_caller_identity():
    with pytest.raises(PermissionError):
        reader().read()
    with pytest.raises(TypeError):
        reader().read(learner_id="B")


@pytest.mark.parametrize("kwargs", [{"limit": 21}, {"limit": True}, {"cursor": -1},
                                    {"view": "arbitrary"}, {"view": "section"}])
def test_query_bounds(bound, kwargs):
    with pytest.raises(ValueError):
        reader().read(**kwargs)


def test_snapshot_stable_until_record_changes_and_read_failure_unknown(bound):
    records = []
    service = reader(lambda *args, **kwargs: {"availability": "available", "books": [], "record_version": records})
    before = service.read()
    assert before["source_version"] == service.read()["source_version"]
    records.append({"record_id": 1, "is_demo": True})
    assert before["source_version"] != service.read()["source_version"]
    def fail(*args, **kwargs):
        raise OSError("private connection details")
    failed = reader(fail).read()
    assert failed["facts_availability"] == "unavailable"
    assert "private connection" not in str(failed)
    assert "未知" in failed["summary"]


def test_ten_due_items_are_not_ten_scheduled_tasks(bound):
    now = datetime.now(timezone.utc)
    entries = [SimpleNamespace(
        memory_unit=ReviewMemoryUnit(memory_unit_id=f"M{i}", learner_id="A", kp_id=f"KP{i}",
            prompt_abstract=f"知识{i}", mastery_score=50, lambda_per_day=.1,
            next_review_at=now-timedelta(days=1), created_at=now, updated_at=now),
        is_due=True, task=None, resource=None,
    ) for i in range(10)]
    state = reader(review=SimpleNamespace(get_queue=lambda *args, **kwargs: SimpleNamespace(entries=entries))).read()
    assert state["review"]["due_count"] == 10
    assert state["review"]["scheduled_today"] == []
    assert state["today"]["items"] == []
    assert state["today"]["actual_minutes"] is None
    assert state["review"]["pending_pool"][0]["kp_id"] == "KP0"


@pytest.mark.asyncio
async def test_freshness_gate_rejects_changes(bound):
    service = reader()
    snapshot = service.read()
    tools = ToolRegistry()
    tools.register("get_current_learning_state", lambda: service.read(), allowed_agents={"learning_plan_service"})
    context = {"learner_id": "A", "current_learning_state": snapshot, "tool_registry": tools}
    await LearningPlanServiceAdapter._validate_learning_state(context)
    context["current_learning_state"] = {**snapshot, "source_version": "old"}
    with pytest.raises(LearningStateChangedError):
        await LearningPlanServiceAdapter._validate_learning_state(context)


@pytest.mark.asyncio
async def test_permissions_and_identity_arguments(bound):
    tools = ToolRegistry()
    tools.register("get_current_learning_state", reader().read, allowed_agents={"planner_agent"})
    with pytest.raises(ToolPermissionError):
        await tools.invoke("get_current_learning_state", "plan_contract_compiler")
    with pytest.raises(TypeError):
        await tools.invoke("get_current_learning_state", "planner_agent", learner_id="B")
    assert (await tools.invoke("get_current_learning_state", "planner_agent"))["learner_id"] == "A"


def test_model_projection_preserves_ids_and_filters_video_transcript():
    value = model_learning_state({"books": [{"sections": [{"section_id": "SEC_14", "chapter_id": "CH_1",
        "completion_evidence": [{"is_demo": True}], "knowledge_points": [],
        "resources": {"section_videos": [{"bvid": "BV1", "transcript": "do not expose"}]}}]}]})
    section = value["books"][0]["sections"][0]
    assert section["section_id"] == "SEC_14"
    assert section["completion_evidence"][0]["is_demo"] is True
    assert "transcript" not in str(value)


@pytest.mark.asyncio
async def test_concurrent_agents_share_one_snapshot_and_no_task_is_persisted(bound):
    import asyncio
    from competition_app.runtime.agent_registry import AgentRegistry
    from competition_app.runtime.orchestrator import Orchestrator
    from competition_app.runtime.trace import TraceRecorder
    from competition_app.contracts.execution import ExecutionStep

    calls = []
    seen = []
    ready = asyncio.Event()
    async def load():
        calls.append(1)
        await ready.wait()
        return reader().read()
    class Agent:
        async def run(self, context):
            seen.append(context["current_learning_state"])
            assert "_learning_state_read" not in context
            return {"ok": True}
    agents = AgentRegistry()
    tools = ToolRegistry()
    tools.register("get_current_learning_state", load, allowed_agents={"diagnosis_agent", "knowledge_base_agent"})
    for name in ("diagnosis_agent", "knowledge_base_agent"):
        agents.register(name, Agent())
    orchestrator = Orchestrator(agents, tool_registry=tools)
    context = {"learner_id": "A", "task_type": "learning_plan"}
    tasks = [asyncio.create_task(orchestrator._run_step(
        ExecutionStep(step_id=name, agent=name), context, {}, TraceRecorder())) for name in (
            "diagnosis_agent", "knowledge_base_agent")]
    ready.set()
    await asyncio.gather(*tasks)
    assert len(calls) == 1
    assert seen[0] is seen[1]
    assert "_learning_state_read" not in context


def test_compilers_do_not_receive_state_but_business_agents_share_it():
    from competition_app.contracts.agent_context import build_model_context
    from competition_app.llm.prompt_skills import prompt_skill_registry
    context = {"learner_id": "A", "trace_id": "TRACE", "request_id": "REQ",
               "current_learning_state": {"snapshot_id": "ONE", "books": []}}
    skill = prompt_skill_registry.load("diagnosis_agent", "learning_plan")
    for target in ("planner_agent", "diagnosis_agent", "knowledge_base_agent", "audit_agent", "plan_contract_compiler"):
        value = build_model_context(context, target_agent=target, prompt_skill=skill,
                                    payload={}, permission_note="只读")
        assert ("current_learning_state" in value["payload"]) == (target != "plan_contract_compiler")


@pytest.mark.asyncio
async def test_container_registers_bound_tool_without_identity_override(tmp_path, bound):
    from competition_app.application.container import ApplicationContainer
    from competition_app.config import Settings
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    tools = container.review_card_use_case.orchestrator.tool_registry
    for agent in ("planner_agent", "diagnosis_agent", "knowledge_base_agent", "audit_agent"):
        state = await tools.invoke("get_current_learning_state", agent)
        assert state["learner_id"] == "A"
        assert state["exam_track_id"] == "EXAM_A"
        assert state["facts_availability"] == "unavailable"
    with pytest.raises(TypeError):
        await tools.invoke("get_current_learning_state", "planner_agent", exam_track_id="EXAM_B")


def test_scope_and_catalog_versions_change_snapshot(bound):
    facts = {"availability": "available", "books": [{"catalog_version": "CAT1"}],
             "target_version": {"syllabus_version": "V1"}}
    service = reader(lambda *args, **kwargs: facts)
    initial = service.read()["source_version"]
    facts["target_version"]["syllabus_version"] = "V2"
    assert initial != service.read()["source_version"]
    before_catalog = service.read()["source_version"]
    facts["books"][0]["catalog_version"] = "CAT2"
    assert before_catalog != service.read()["source_version"]
    assert service.read(view="today")["source_version"] == service.read()["source_version"]
    assert service.read(view="today")["books"] == []


def test_review_read_failure_remains_unknown_and_permissions_propagate(bound):
    def broken(*args, **kwargs):
        raise OSError("private")
    result = reader(review=SimpleNamespace(get_queue=broken)).read()
    assert result["review"]["due_count"] is None
    assert result["review"]["availability"] == "unavailable"
    def forbidden(*args, **kwargs):
        raise PermissionError("exam mismatch")
    with pytest.raises(PermissionError):
        reader(review=SimpleNamespace(get_queue=forbidden)).read()


def test_plan_changed_during_read_is_stale(bound):
    calls = []
    class Plans:
        def get_current(self, learner):
            calls.append(1)
            task = None if len(calls) == 1 else SimpleNamespace(task_id="NEW", version=2)
            return SimpleNamespace(long_term_plan=None, short_term_plan=None, learning_task=task)
    service = reader()
    service.plans = Plans()
    assert service.read()["availability"] == "stale"