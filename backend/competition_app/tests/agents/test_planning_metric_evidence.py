from copy import deepcopy

import pytest

from competition_app.agents.audit import AuditAgent
from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.runtime.tool_registry import ToolRegistry
from competition_app.services.planning_metrics import build_planning_metric_evidence
from competition_app.tests.agents.test_audit_resource import _short_plan_context
from competition_app.tests.agents.test_diagnosis_learning_plan import build_context, textbook_route_output
from competition_app.tests.services.test_planning_metrics import behavior


@pytest.mark.asyncio
async def test_authorized_tool_carries_metric_contract_without_changing_original():
    evidence = build_planning_metric_evidence(behavior())
    registry = ToolRegistry()
    registry.register(
        "get_learning_planning_context",
        lambda **_: {"source": "authorized_learning_planning_tools", "planning_metric_evidence": evidence},
        allowed_agents={"diagnosis_agent"},
    )
    context = {"learner_id": "L1", "plan_scope": "short_term", "tool_registry": registry}
    result = await DiagnosisAgent()._with_authorized_planning_context(context)
    assert result["planning_metric_evidence"] == evidence
    assert "planning_metric_evidence" not in context


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_planning_draft_uses_single_metric_source_without_changing_other_inputs(empty):
    class DraftReached(Exception):
        pass

    class Model:
        def __init__(self):
            self.calls = []

        async def complete_json(self, role, payload, **kwargs):
            self.calls.append((role, payload))
            raise DraftReached()

    raw = behavior()
    if empty:
        raw["learning_statistics"] = {}
    evidence = build_planning_metric_evidence(raw)
    context = build_context("diagnosis")
    context.update(
        task_type="learning_plan", plan_scope="long_term",
        system_data={**raw["system_data"], "completed_courses": ["中医诊断学"]},
        learning_profile={"question_accuracy": 0.95, "review_stability": 0.9,
                          "behavior_metrics": raw["system_data"]["behavior_window"]},
        dependency_outputs={"route_resolution": textbook_route_output()},
    )
    before = deepcopy(context)
    old_model, new_model = Model(), Model()
    with pytest.raises(DraftReached):
        await DiagnosisAgent(old_model).run(context)
    with pytest.raises(DraftReached):
        await DiagnosisAgent(new_model).run({**context, "planning_metric_evidence": evidence})
    assert len(old_model.calls) == len(new_model.calls) == 1
    old_payload = old_model.calls[0][1]["payload"]
    new_payload = new_model.calls[0][1]["payload"]
    assert new_payload["learning_evidence"]["metric_evidence"] == evidence
    summary = new_payload["learning_evidence"]["behavior_summary"]
    assert summary["question_accuracy"]["value"] == (None if empty else 0.9375)
    assert summary["review_stability"]["value"] is None
    portrait = new_payload["shared_context"]["user_profile"]["learning_profile"]
    assert "question_accuracy" not in portrait
    assert "review_stability" not in portrait
    # No change to route, parent constraints, budget, knowledge or output schema.
    for key in old_payload.keys() | new_payload.keys():
        if key not in {"learning_evidence", "shared_context"}:
            assert new_payload.get(key) == old_payload.get(key), key
    assert context == before


@pytest.mark.asyncio
async def test_audit_receives_same_metric_evidence_without_old_portrait_values():
    raw = behavior()
    evidence = build_planning_metric_evidence(raw)
    context = _short_plan_context()
    diagnosis = context["dependency_outputs"]["diagnosis"].payload
    diagnosis.audit_evidence = {"learning_evidence": {"metric_evidence": evidence}}
    context["learning_profile"] = {
        "question_accuracy": 0.95, "review_stability": 0.9,
        "behavior_metrics": raw["system_data"]["behavior_window"],
    }
    original_profile = deepcopy(context["learning_profile"])
    calls = []

    class Model:
        async def complete_json(self, role, payload, **kwargs):
            calls.append(role)
            data = payload["payload"]
            assert data["producer_evidence"]["learning_evidence"]["metric_evidence"] == evidence
            portrait = data["shared_context"]["user_profile"]["learning_profile"]
            assert "question_accuracy" not in portrait
            assert "review_stability" not in portrait
            return {"decision": "pass", "medical_safety": "safe", "findings": [],
                    "audit_report": "可执行的进度规划，未断言不可用当前指标。"}

    result = (await AuditAgent(Model()).run(context)).payload
    assert result.decision == "pass"
    assert calls == ["audit_agent"]
    assert context["learning_profile"] == original_profile
    assert diagnosis.audit_evidence["learning_evidence"]["metric_evidence"] == evidence