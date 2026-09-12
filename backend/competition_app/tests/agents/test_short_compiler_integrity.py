from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from competition_app.agents.plan_contract_compiler import PlanContractCompilerAgent as Compiler
from competition_app.tests.agents.test_plan_contract_compiler import DocumentCompilerModel, compiler_context

DOCUMENT = "当前阶段stage-1，未来14天使用《方剂学》。先完成教材核对；再完成闭卷比较。预期产出：一张类方比较表。完成标准：能够闭卷比较代表方剂。用途：复习。"


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["verbatim", "indexed", "rewritten_total", "rewritten_indexed", "newline", "wrong_total"])
async def test_node_integrity_independent_of_anchor_shape(variant):
    class Model:
        async def complete_json(self, role, context):
            raw = await DocumentCompilerModel().complete_json(role, context)
            contract = raw["contract"]
            if variant in {"indexed", "rewritten_indexed"}:
                anchors = contract["field_anchors"].pop("/progression_nodes")
                for index, anchor in enumerate(anchors):
                    contract["field_anchors"][f"/progression_nodes/{index}"] = [anchor]
            if variant.startswith("rewritten"):
                contract["progression_nodes"][1] = "随后进行闭卷比较"
            if variant == "newline":
                contract["progression_nodes"][1] = "再完成\n闭卷比较"
            if variant == "wrong_total":
                contract["field_anchors"]["/progression_nodes"] = [{"source_field": "plan_document", "source_quote": "用途：复习。"}]
            return raw

    result = await Compiler(Model()).compile(compiler_context(), plan_scope="short_term", diagnosis_output={"plan_document": DOCUMENT}, trusted_route={}, parent_plan_constraints={})
    if variant in {"verbatim", "indexed"}:
        assert result.result.status == "compiled"
        assert result.repair_owner is None
    else:
        assert result.result.status == "needs_revision"
        assert result.failure_origin == "backend"
        assert result.repair_owner == "compiler"
        assert any(issue.field_path == "/progression_nodes/1" for issue in result.result.issues)


@pytest.mark.asyncio
@pytest.mark.parametrize("code,owner", [("missing_required_field", "author"), ("parent_plan_conflict", "author"), ("source_anchor_invalid", "compiler"), ("schema_invalid", "compiler")])
async def test_model_fixed_issue_codes_route_without_prose_matching(code, owner):
    class Model:
        async def complete_json(self, role, context):
            return {"status": "needs_revision", "issues": [{"code": code, "category": "invalid", "field_path": "/progression_nodes"}]}

    result = await Compiler(Model()).compile(compiler_context(), plan_scope="short_term", diagnosis_output={"plan_document": DOCUMENT}, trusted_route={}, parent_plan_constraints={})
    assert result.failure_origin == "model"
    assert result.repair_owner == owner


def test_short_request_requires_explicit_nonempty_anchor_paths():
    schema = Compiler._model_output_schema(document_source=True, plan_scope="short_term")
    definition = schema["$defs"]["CompiledShortTermContract"]
    assert "field_anchors" in definition["required"]
    anchors = definition["properties"]["field_anchors"]
    validator = Draft202012Validator({**anchors, "$defs": schema["$defs"]})
    valid = {path: [{"source_field": "plan_document", "source_quote": "原文"}] for path in anchors["required"]}
    assert not list(validator.iter_errors(valid))
    for path in anchors["required"]:
        missing = deepcopy(valid)
        missing.pop(path)
        assert list(validator.iter_errors(missing))
        missing[path] = []
        assert list(validator.iter_errors(missing))
    for scope in ("long_term", "daily_task"):
        unchanged = Compiler._model_output_schema(document_source=True, plan_scope=scope)
        assert "field_anchors" not in unchanged["$defs"]["CompiledShortTermContract"]["required"]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,retry_error", [("compiler", None), ("compiler", "invalid_json"), ("compiler", "business_schema_invalid"), ("author", None)])
async def test_short_extraction_budget_and_author_boundary(owner, retry_error, monkeypatch):
    from competition_app.agents.diagnosis import DiagnosisAgent
    from competition_app.contracts.plan_compilation import PlanCompilationEnvelope, PlanCompilationError
    from competition_app.llm.openai_compatible import ModelResponseError
    from competition_app.tests.agents.test_diagnosis_learning_plan import build_context, build_knowledge, textbook_route_output

    class AuthorReached(Exception):
        pass

    class Model:
        calls = 0

        async def complete_json(self, role, context):
            if "plan_document" in context["payload"].get("output_schema", {}).get("properties", {}):
                self.calls += 1
            if "compiler_revision_issues" in context["payload"]:
                raise AuthorReached()
            return {"plan_document": DOCUMENT}

    class FailedCompiler:
        calls = 0

        async def compile(self, *args, **kwargs):
            self.calls += 1
            assert kwargs["diagnosis_output"]["plan_document"] == DOCUMENT
            if self.calls == 2:
                assert kwargs["extraction_feedback"]
                if retry_error:
                    raise ModelResponseError("retry failed", reason=retry_error)
            return PlanCompilationEnvelope.model_validate({
                "source_digest": "a" * 64, "failure_origin": "model" if owner == "author" else "backend", "repair_owner": owner,
                "result": {"status": "needs_revision", "issues": [{"code": "missing_required_field" if owner == "author" else "source_value_not_verbatim", "category": "invalid", "field_path": "/progression_nodes/1"}]},
            })

    context = build_context("diagnosis")
    context["plan_scope"] = "short_term"
    context["current_long_term_plan"] = {"plan_id": "LP1", "version": 2, "content": "长期计划", "stages": []}
    context["dependency_outputs"] = {"knowledge": await build_knowledge(build_context("knowledge")), "route_resolution": textbook_route_output()}
    model = Model()
    agent = DiagnosisAgent(model)
    monkeypatch.setattr(agent, "_compiler_route_context", lambda *args: {})
    agent.plan_contract_compiler = FailedCompiler()
    with pytest.raises(AuthorReached if owner == "author" else PlanCompilationError):
        await agent.run(context)
    assert agent.plan_contract_compiler.calls == 2
    assert model.calls == (2 if owner == "author" else 1)


@pytest.mark.asyncio
async def test_real_prompt_boundary_has_required_anchors_and_short_repair_rules():
    from competition_app.llm.openai_compatible import OpenAICompatibleChatModel

    class Model:
        async def complete_json(self, role, context):
            client = OpenAICompatibleChatModel(base_url="https://example.test/v1", api_key="test", model="test")
            system, user = [message["content"] for message in client._build_messages(role, context, strict_json=True)]
            short_contract = system.split("类型说明 CompiledShortTermContract，", 1)[1].split("类型说明 CompiledDailyTaskContract，", 1)[0]
            assert "field_anchors（object，必填）" in short_contract
            assert "/progression_nodes（array，必填）" in short_contract
            assert "短期正文提取与纠错" in system
            assert "字段名没有出现在正文中，不等于业务内容缺失" in system
            assert DOCUMENT in user and DOCUMENT not in system
            assert context["payload"]["extraction_feedback"]
            return {"status": "needs_revision", "issues": [{"code": "source_anchor_invalid", "category": "invalid", "field_path": "/progression_nodes/1"}]}

    result = await Compiler(Model()).compile(compiler_context(), plan_scope="short_term", diagnosis_output={"plan_document": DOCUMENT}, trusted_route={}, parent_plan_constraints={}, extraction_feedback=[{"code": "source_value_not_verbatim", "field_path": "/progression_nodes/1"}])
    assert result.repair_owner == "compiler"