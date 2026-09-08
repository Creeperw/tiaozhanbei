from copy import deepcopy

import pytest
from jsonschema import validate

from competition_app.agents.plan_contract_compiler import PlanContractCompilerAgent
from competition_app.contracts.route_binding import binding_schema, bind_stages


def fixture():
    stages = [
        {"stage_id": "stage-1", "name": "基础", "books": ["《基础》"], "goal": "建立基础和阅读能力。"},
        {"stage_id": "stage-2", "name": "进阶", "books": ["《方剂学》"], "goal": "建立辨析能力。"},
    ]
    doc = "stage-1 《基础》 建立基础与阅读能力\n阶段1共10天，阅读基础并产出卡片，通过闭卷说明验收。\nstage-2 《方剂学》\n阶段2共20天，比较方证并产出对比表，通过案例辨析验收。\n当前stage-1，《基础》，诊断。选择依据：先验证基础。"
    doc = "【最终目标】形成辨析能力\n【能力路径与阶段】\n" + doc + "\n【阶段里程碑】闭卷说明、案例辨析\n【资源预算】每天50分钟，保留机动\n【重规划条件】连续未达标调整\n【保温底线】每周回顾卡片"
    raw = {"status": "compiled", "contract": {
        "scope": "long_term", "selected_stage_id": "stage-1", "selected_books": ["《基础》"],
        "selection_mode": "diagnostic", "selection_reason": "先验证基础。",
        "stages": [
            {"stage_id": "stage-1", "duration_days": 10, "schedule_summary": "阅读基础并产出卡片，通过闭卷说明验收。", "acceptance": []},
            {"stage_id": "stage-2", "duration_days": 20, "schedule_summary": "比较方证并产出对比表，通过案例辨析验收。", "acceptance": []},
        ],
        "field_anchors": {
            "/stages/0/stage_id": [{"source_field": "plan_document", "source_quote": "stage-1 《基础》 建立基础与阅读能力\n阶段1共10天，阅读基础并产出卡片，通过闭卷说明验收。"}],
            "/stages/1/stage_id": [{"source_field": "plan_document", "source_quote": "stage-2 《方剂学》\n阶段2共20天，比较方证并产出对比表，通过案例辨析验收。"}],
            "/stages/0/duration_days": [{"source_field": "plan_document", "source_quote": "阶段1共10天"}],
            "/stages/1/duration_days": [{"source_field": "plan_document", "source_quote": "阶段2共20天"}],
            **{f"/stages/{i}/schedule_summary": [{"source_field": "plan_document", "source_quote": quote}] for i, quote in enumerate([
                "阅读基础并产出卡片，通过闭卷说明验收。", "比较方证并产出对比表，通过案例辨析验收。",
            ])},
        },
    }}
    return doc, raw, {"binding_mode": "fixed_route_v1", "stages": stages}


class Model:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def complete_json(self, role, context):
        self.calls.append(context)
        validate(self.response, context["payload"]["output_schema"])
        return deepcopy(self.response)


async def compile_case(doc, raw, route):
    model = Model(raw)
    result = await PlanContractCompilerAgent(model).compile(
        {"trace_id": "TRACE_TEST", "request_id": "REQUEST_TEST", "learner_id": "USER_TEST"},
        plan_scope="long_term", diagnosis_output={"plan_document": doc},
        trusted_route=route, parent_plan_constraints={},
    )
    return result, model


@pytest.mark.asyncio
async def test_route_owned_goal_does_not_require_rewriting_prose():
    doc, raw, route = fixture()
    result, model = await compile_case(doc, raw, route)
    assert result.result.status == "compiled", result
    contract = result.result.contract
    assert contract.stages[0].goal == "建立基础和阅读能力。"
    assert contract.long_term_plan_content == doc
    assert contract.total_duration_days == 30
    assert contract.field_anchors["/stages/0/goal"][0].source_field == "trusted_route_binding"
    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_stage", "reordered", "missing_stage_reference", "missing_book", "missing_duration_anchor", "invented_acceptance", "invented_schedule", "route_source_for_decision"])
async def test_binding_never_completes_missing_decisions(fault):
    doc, raw, route = fixture()
    if fault == "missing_stage":
        raw["contract"]["stages"].pop()
    elif fault == "reordered":
        raw["contract"]["stages"].reverse()
    elif fault == "missing_stage_reference":
        doc = doc.replace("stage-2", "阶段二")
    elif fault == "missing_book":
        doc = doc.replace("《方剂学》", "相关教材")
    elif fault == "missing_duration_anchor":
        raw["contract"]["field_anchors"].pop("/stages/0/duration_days")
    elif fault == "invented_acceptance":
        raw["contract"]["stages"][0]["acceptance"] = ["不存在的验收条件"]
    elif fault == "invented_schedule":
        raw["contract"]["stages"][0]["schedule_summary"] = "伪造学习安排"
    else:
        raw["contract"]["selection_reason"] = route["stages"][0]["goal"]
        raw["contract"]["field_anchors"]["/selection_reason"] = [{
            "source_field": "trusted_route_binding", "source_quote": route["stages"][0]["goal"],
        }]
    result, _ = await compile_case(doc, raw, route)
    assert result.result.status == "needs_revision"


def test_schema_no_longer_requires_model_to_copy_fixed_facts():
    _, _, route = fixture()
    schema = binding_schema(PlanContractCompilerAgent._model_output_schema(document_source=True), route["stages"])
    properties = schema["$defs"]["CompiledLongTermStage"]["properties"]
    assert set(properties) == {"stage_id", "duration_days", "schedule_summary", "acceptance"}


def test_binder_does_not_mutate_model_response():
    _, raw, route = fixture()
    before = deepcopy(raw)
    bind_stages(raw, route["stages"])
    assert raw == before


@pytest.mark.asyncio
async def test_summary_only_revision_is_rejected_before_compiler_model():
    _, raw, route = fixture()
    result, model = await compile_case("【最终目标】" + "只有目标的摘要。" * 35, raw, route)
    assert result.result.status == "needs_revision"
    assert not model.calls
    assert "/plan_document/stages" in {issue.field_path for issue in result.result.issues}


@pytest.mark.asyncio
async def test_route_digest_changes_with_version_but_document_digest_does_not():
    doc, raw, route = fixture()
    first, _ = await compile_case(doc, raw, route)
    second, _ = await compile_case(doc, raw, {**route, "route_version": 2})
    assert first.source_digest == second.source_digest
    assert first.route_source_digest != second.route_source_digest


@pytest.mark.asyncio
@pytest.mark.parametrize("feedback_key", ["compiler_revision_issues", "revision_issues"])
async def test_both_revisions_receive_complete_document_and_preservation_policy(feedback_key):
    from competition_app.agents.diagnosis import DiagnosisAgent
    from competition_app.llm.prompt_skills import prompt_skill_registry

    class Draft:
        async def complete_json(self, role, context):
            payload = context["payload"]
            assert payload["previous_plan_document"] == document
            assert "保留未受影响的全部栏目" in payload["revision_instruction"]
            assert feedback_key in payload
            return {"plan_document": document}

    document = "【最终目标】\n" + "完整规划安排。" * 600 + "\n【保温底线】保留尾部"
    result = await DiagnosisAgent(Draft())._complete_plan_draft(
        {"trace_id": "TRACE", "request_id": "REQ", "learner_id": "USER"},
        {"previous_plan_document": document, feedback_key: []},
        prompt_skill_registry.load("diagnosis_agent", "learning_plan"),
        permission_note="修订完整文档",
    )
    assert result["plan_document"] == document


def test_planning_diagnostics_are_bounded_and_attached_to_draft():
    from competition_app.runtime.model_trace import ModelTraceRecorder

    recorder = ModelTraceRecorder()
    index = recorder.begin("diagnosis_agent", {})
    recorder.succeed(index, {"plan_document": "草稿"})
    recorder.begin("plan_contract_compiler", {})
    recorder.record_planning_validation([
        {"code": "route_goal_mismatch", "field_path": "/stages/*/goal", "secret": "PRIVATE"},
        {"code": "PRIVATE", "field_path": "/PRIVATE"},
    ], attempt=1)
    items = recorder.items
    assert items[0].planning_validation_issues == [{
        "code": "route_goal_mismatch", "field_path": "/stages/*/goal", "attempt": 1,
    }]
    assert items[1].planning_validation_issues is None


@pytest.mark.parametrize("mode", ["new_learning", "review", "diagnostic"])
def test_unknown_prerequisite_cannot_be_bypassed_by_selection_mode(mode):
    from competition_app.services.planning_validator import PlanningValidator
    from competition_app.tests.services.test_planning_validator import output, textbook_bound_route

    value = output(selected_textbook_route_id="textbook_formula", selected_stage_id="stage-2",
                   selected_books=["《方剂学》"], selection_reason="练习方证", selection_mode=mode)
    result = PlanningValidator().validate(value, textbook_bound_route(), active_scope="long_term")
    assert {"code": "prerequisite_unconfirmed", "field_path": "/selected_books"} in result.diagnostics
    prerequisite = value.model_copy(update={"selected_books": ["《中医诊断学》"], "selection_mode": "diagnostic"})
    result = PlanningValidator().validate(prerequisite, textbook_bound_route(), active_scope="long_term")
    assert not any(item["code"] == "prerequisite_unconfirmed" for item in result.diagnostics)