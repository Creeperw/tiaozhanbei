from copy import deepcopy

import pytest

from competition_app.agents.plan_contract_compiler import PlanContractCompilerAgent as Compiler
from competition_app.contracts.plan_compilation import PlanCompilationError
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.runtime.agent_registry import AgentRegistry
from competition_app.runtime.orchestrator import Orchestrator


def test_document_schema_requires_current_selection_without_breaking_legacy():
    legacy = Compiler._model_output_schema()["$defs"]["CompiledLongTermContract"]
    current = Compiler._model_output_schema(document_source=True)["$defs"]["CompiledLongTermContract"]
    for name in ("selected_stage_id", "selected_books", "selection_reason", "selection_mode"):
        assert name not in legacy["required"]
        assert name in current["required"]
        assert "anyOf" not in current["properties"][name]
    assert "total_duration_days" not in current["required"]
    assert "long_term_plan_content" not in current["properties"]


def test_valid_partial_quote_is_reanchored_to_actual_selection_reason():
    reason = "阶段内先诊断基础，再推进未学内容。"
    document = "选择依据：" + reason
    raw = {"status": "compiled", "contract": {
        "selection_reason": reason,
        "field_anchors": {"/selection_reason": [
            {"source_field": "plan_document", "source_quote": "先诊断基础"}
        ]},
    }}
    result = Compiler._backfill_anchors(raw, {"plan_document": document}, "long_term")
    assert result["contract"]["field_anchors"]["/selection_reason"][0]["source_quote"] == reason
    assert raw["contract"]["field_anchors"]["/selection_reason"][0]["source_quote"] == "先诊断基础"


def duration_case():
    # Exact stage durations from the failed 2026-09-07 Live document; no total.
    days = [35, 30, 40, 20, 30]
    quotes = [f"阶段{i + 1}，时长 {day} 天。" for i, day in enumerate(days)]
    return {"plan_document": "\n".join(quotes)}, {"status": "compiled", "contract": {
        "stages": [{"duration_days": day} for day in days],
        "field_anchors": {
            f"/stages/{i}/duration_days": [{"source_field": "plan_document", "source_quote": quote}]
            for i, quote in enumerate(quotes)
        },
    }}


def test_derive_total_from_individually_grounded_stage_durations():
    doc, raw = duration_case()
    result = Compiler._inject_system_fields(raw, doc, "long_term")["contract"]
    assert result["total_duration_days"] == 155
    assert len(result["field_anchors"]["/total_duration_days"]) == 5
    assert result["long_term_plan_content"] == doc["plan_document"]
    assert "total_duration_days" not in raw["contract"]


@pytest.mark.parametrize("invalid", ["missing_anchor", "wrong_value", "fabricated_quote", "conflicting_total"])
def test_total_derivation_does_not_conceal_missing_or_conflicting_evidence(invalid):
    doc, original = duration_case()
    raw = deepcopy(original)
    if invalid == "missing_anchor":
        del raw["contract"]["field_anchors"]["/stages/0/duration_days"]
    elif invalid == "wrong_value":
        raw["contract"]["stages"][0]["duration_days"] = 99
    elif invalid == "fabricated_quote":
        raw["contract"]["field_anchors"]["/stages/0/duration_days"][0]["source_quote"] = "不存在的35天"
    else:
        raw["contract"]["total_duration_days"] = 999
    result = Compiler._inject_system_fields(raw, doc, "long_term")["contract"]
    assert result.get("total_duration_days") == (999 if invalid == "conflicting_total" else None)


@pytest.mark.asyncio
async def test_exhausted_compilation_does_not_restart_entire_diagnosis():
    class FailingDiagnosis:
        calls = 0

        async def run(self, context):
            self.calls += 1
            raise PlanCompilationError("bounded compilation repair exhausted")

    agent = FailingDiagnosis()
    registry = AgentRegistry()
    registry.register("diagnosis_agent", agent)
    plan = ExecutionPlan(plan_id="P_COMPILE", task_type="learning_plan", steps=[
        ExecutionStep(step_id="diagnosis", agent="diagnosis_agent", max_retries=1)
    ])
    result = await Orchestrator(registry).execute(plan, {})
    assert result.status == "failed"
    assert agent.calls == 1
    assert result.error_type == "PlanCompilationError"