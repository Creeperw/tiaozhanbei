import pytest

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.llm.stub import StubChatModel
from competition_app.llm.stub_planning import fixed_route_document
from competition_app.tests.agents.test_diagnosis_learning_plan import build_context, textbook_route_output


@pytest.mark.asyncio
@pytest.mark.parametrize("with_judgment", [True, False])
async def test_same_diagnosis_call_owns_prerequisite_judgment(with_judgment):
    class Model(StubChatModel):
        roles = []

        async def complete_json(self, role, payload, on_delta=None):
            self.roles.append(role)
            if role != "diagnosis_agent":
                return await super().complete_json(role, payload, on_delta)
            data = payload["payload"]
            assert data["prerequisite_sources"]["user_request"] == "中医诊断学不是已经掌握，未知前置先诊断。"
            assert "prerequisite_judgments" in data["output_schema"]["properties"]
            assert data["route_conditions"]
            response = {"plan_document": fixed_route_document(data["default_route"]["textbook_route"])}
            if with_judgment:
                response["prerequisite_judgments"] = [{
                    "course": "中医诊断学", "status": "unknown", "source_ref": "user_request",
                    "source_quote": "不是已经掌握", "rationale": "否定掌握陈述不构成能力通过证据，先诊断。",
                }]
            return response

    context = build_context("diagnosis")
    context.update(plan_scope="long_term", user_request="中医诊断学不是已经掌握，未知前置先诊断。")
    context["planning_request_scope"] = {
        "mode": "route", "objects": [], "source_quote": context["user_request"],
        "clarification_question": None,
    }
    context["dependency_outputs"] = {"route_resolution": textbook_route_output()}
    context["path_candidates"] = {"eligible": [], "blocked": [], "prerequisite_evidence": {"route_id": ""}}
    model = Model()
    result = (await DiagnosisAgent(model).run(context)).payload
    assert result.learning_plan_proposal is not None
    assessment = result.audit_evidence["prerequisite_assessment"]
    assert assessment["route_id"] == "textbook_formula"
    assert assessment["satisfied_courses"] == []
    assert assessment["unknown_courses"] == ["中医诊断学"]
    assert len(assessment["judgments"]) == int(with_judgment)
    assert result.trusted_plan_route["prerequisites"]
    assert model.roles.count("diagnosis_agent") == 1
    assert set(model.roles) == {"diagnosis_agent", "plan_contract_compiler"}


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_valid", [True, False])
async def test_invalid_prerequisite_quote_has_one_bounded_author_repair(repair_valid):
    from competition_app.llm.openai_compatible import ModelResponseError

    class Model(StubChatModel):
        def __init__(self):
            self.roles = []

        async def complete_json(self, role, payload, on_delta=None):
            self.roles.append(role)
            if role != "diagnosis_agent":
                return await super().complete_json(role, payload, on_delta)
            data = payload["payload"]
            repairing = self.roles.count(role) == 2
            if repairing:
                assert data["previous_plan_document"]
                assert "逐字连续" in data["revision_instruction"]
            return {
                "plan_document": fixed_route_document(data["default_route"]["textbook_route"]),
                "prerequisite_judgments": [{
                    "course": "中医诊断学", "status": "unknown", "source_ref": "user_request",
                    "source_quote": "未知前置先诊断" if repairing and repair_valid else "虚构的原文",
                    "rationale": "未取得前置通过证据。",
                }],
            }

    context = build_context("diagnosis")
    context.update(plan_scope="long_term", user_request="未知前置先诊断")
    context["planning_request_scope"] = {"mode": "route", "objects": [], "source_quote": context["user_request"], "clarification_question": None}
    context["dependency_outputs"] = {"route_resolution": textbook_route_output()}
    model = Model()
    if repair_valid:
        result = (await DiagnosisAgent(model).run(context)).payload
        assert result.audit_evidence["prerequisite_assessment"]["unknown_courses"] == ["中医诊断学"]
    else:
        with pytest.raises(ModelResponseError, match="一次来源修订"):
            await DiagnosisAgent(model).run(context)
        assert "plan_contract_compiler" not in model.roles
    assert model.roles.count("diagnosis_agent") == 2