from copy import deepcopy

import pytest

from competition_app.agents.plan_contract_compiler import PlanContractCompilerAgent as Compiler
from competition_app.contracts.route_binding import quote_within_stage
from competition_app.tests.agents.test_fixed_route_binding import fixture, compile_case


def section_case():
    doc, raw, route = fixture()
    for i in range(2):
        entry = raw["contract"]["field_anchors"][f"/stages/{i}/stage_id"][0]
        old = entry["source_quote"]
        new = "### " + old
        if i == 0:
            new += "\n验收通过后再进入 stage-2，不代表跳过验收。"
        doc = doc.replace(old, new)
        entry["source_quote"] = new
    return doc, raw, route


@pytest.mark.asyncio
async def test_stage_reference_in_own_exit_sentence_is_not_cross_section():
    doc, raw, route = section_case()
    result, model = await compile_case(doc, raw, route)
    assert result.result.status == "compiled"
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_quote_spanning_two_stage_sections_is_rejected():
    doc, raw, route = section_case()
    first = raw["contract"]["field_anchors"]["/stages/0/stage_id"][0]
    second = raw["contract"]["field_anchors"]["/stages/1/stage_id"][0]
    start = doc.index(first["source_quote"])
    end = doc.index(second["source_quote"]) + len(second["source_quote"])
    first["source_quote"] = doc[start:end]
    result, model = await compile_case(doc, raw, route)
    assert result.result.status == "needs_revision"
    assert len(model.calls) == 2
    assert not Compiler.document_revision_required(result, doc)


@pytest.mark.parametrize("fault", ["duplicate", "missing", "reordered", "outside"])
def test_ambiguous_or_outside_section_never_authorizes(fault):
    doc, raw, route = section_case()
    quote = raw["contract"]["field_anchors"]["/stages/0/stage_id"][0]["source_quote"]
    if fault == "duplicate":
        doc += "\n### stage-1 重复阶段\n"
    elif fault == "missing":
        doc = doc.replace("### stage-2", "段落 stage-2")
    elif fault == "reordered":
        doc = doc.replace("### stage-1", "### tmp").replace("### stage-2", "### stage-1").replace("### tmp", "### stage-2")
    else:
        quote = doc
    assert not quote_within_stage(doc, quote, "stage-1", route["stages"])


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["schedule_summary", "acceptance"])
async def test_nonverbatim_value_reextracted_from_identical_document(field):
    doc, valid, route = section_case()
    invalid = deepcopy(valid)
    invalid["contract"]["stages"][0][field] = ["改写验收"] if field == "acceptance" else "改写摘要"
    calls = []

    class Model:
        async def complete_json(self, role, context):
            calls.append(context)
            assert context["payload"]["diagnosis_output"]["plan_document"] == doc
            if len(calls) == 2:
                assert context["payload"]["extraction_feedback"]
            return deepcopy(invalid if len(calls) == 1 else valid)

    result = await Compiler(Model()).compile(
        {"trace_id": "TRACE_TEST", "request_id": "REQUEST_TEST", "learner_id": "USER_TEST"},
        plan_scope="long_term", diagnosis_output={"plan_document": doc},
        trusted_route=route, parent_plan_constraints={},
    )
    assert result.result.status == "compiled"
    assert result.revision_count == 1
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_extraction_exhaustion_cannot_request_author_rewrite():
    doc, raw, route = section_case()
    raw["contract"]["stages"][0]["acceptance"] = ["改写验收"]
    result, model = await compile_case(doc, raw, route)
    assert len(model.calls) == 2
    assert result.revision_count == 1
    assert not Compiler.document_revision_required(result, doc)


@pytest.mark.asyncio
async def test_missing_document_section_still_requests_author_revision():
    doc, raw, route = section_case()
    result, model = await compile_case(doc.replace("【最终目标】", "【当前主目标】"), raw, route)
    assert not model.calls
    assert Compiler.document_revision_required(result, doc.replace("【最终目标】", "【当前主目标】"))


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["invalid_json", "business_schema_invalid", "transport_error"])
async def test_retry_error_preserves_extraction_failure_without_more_calls(reason):
    from competition_app.llm.openai_compatible import ModelResponseError

    doc, raw, route = fixture()
    raw["contract"]["stages"][0]["acceptance"] = ["不在正文中"]

    class Model:
        calls = 0

        async def complete_json(self, role, context):
            self.calls += 1
            if self.calls == 2:
                raise ModelResponseError("retry error", reason=reason)
            return deepcopy(raw)

    model = Model()
    call = Compiler(model).compile(
        {"trace_id": "TRACE_TEST", "request_id": "REQUEST_TEST", "learner_id": "USER_TEST"},
        plan_scope="long_term", diagnosis_output={"plan_document": doc},
        trusted_route=route, parent_plan_constraints={},
    )
    if reason == "transport_error":
        with pytest.raises(ModelResponseError):
            await call
    else:
        result = await call
        assert result.result.status == "needs_revision"
        assert result.revision_count == 1
        assert not Compiler.document_revision_required(result, doc)
    assert model.calls == 2


def test_last_stage_cannot_borrow_next_column():
    doc, raw, route = section_case()
    quote = raw["contract"]["field_anchors"]["/stages/1/stage_id"][0]["source_quote"]
    assert not quote_within_stage(doc, doc[doc.index(quote):], "stage-2", route["stages"])


def test_stage_id_token_does_not_collide_with_stage_ten():
    route = [{"stage_id": "stage-1"}, {"stage_id": "stage-10"}]
    quote = "### stage-1 基础\n通过后进入 stage-10。\n"
    doc = quote + "### stage-10 巩固\n阶段十安排"
    assert quote_within_stage(doc, quote, "stage-1", route)
    assert not quote_within_stage(doc, doc, "stage-1", route)