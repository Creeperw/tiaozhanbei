import json
from copy import deepcopy

import httpx
import pytest

from competition_app.agents.plan_contract_compiler import PlanContractCompilerAgent
from competition_app.llm.openai_compatible import ModelResponseError, OpenAICompatibleChatModel
from competition_app.runtime.model_trace import ModelTraceRecorder


VALID = {
    "status": "compiled", "contract_version": "1.0",
    "contract": {
        "scope": "long_term",
        "stages": [{"stage": 1, "stage_name": "基础", "books": ["方剂学"],
                    "goal": "比较方证", "duration_days": 30, "schedule_summary": "先比较后验收"}],
        "selected_stage_id": "stage-1", "selected_books": ["方剂学"],
        "selection_mode": "review", "selection_reason": "用户正在复习",
    },
}


def payload():
    return {"payload": {"output_schema": PlanContractCompilerAgent._model_output_schema(
        document_source=True,
    ), "diagnosis_output": {"plan_document": "复习方剂学，先比较后验收。"}}}


def make_client(responses):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        content = responses[min(len(requests) - 1, len(responses) - 1)]
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return OpenAICompatibleChatModel(
        "https://example.test/v1", "test-key", "deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    ), requests


def invalid_result(kind):
    value = deepcopy(VALID)
    if kind == "missing":
        del value["contract"]["selected_books"]
    elif kind == "enum":
        value["contract"]["selection_mode"] = "private-invalid-mode"
    elif kind == "nested":
        value["contract"]["stages"][0]["duration_days"] = 0
    elif kind == "extra":
        value["contract"]["private_unknown_key"] = "private value"
    elif kind == "anchor":
        value["contract"]["field_anchors"] = {"private anchor": [{"source_field": "plan_document"}]}
    elif kind == "revision":
        value = {"status": "needs_revision", "issues": [{
            "code": "private-invalid-code", "category": "invalid", "field_path": "/stages",
        }]}
    return "not json" if kind == "syntax" else json.dumps(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,path,rule,limit", [
    ("missing", "/contract/selected_books", "required", None),
    ("enum", "/contract/selection_mode", "enum", None),
    ("nested", "/contract/stages/0/duration_days", "exclusiveMinimum", 0),
    ("anchor", "/contract/field_anchors/*/0/source_quote", "required", None),
    ("revision", "/issues/0/code", "enum", None),
])
async def test_compiler_exhaustion_identifies_selected_branch_without_values(kind, path, rule, limit):
    client, requests = make_client([invalid_result(kind)])
    with pytest.raises(ModelResponseError) as caught:
        await client.complete_json("plan_contract_compiler", payload())
    assert len(requests) == 2
    issues = caught.value.last_error_details["validation_issues"]
    expected = []
    for attempt in (1, 2):
        issue = {"field_path": path, "rule": rule, "attempt": attempt}
        # 带阈值的规则要一并报出 schema 声明的上限/下限，修复指令才能给出
        # 可执行的目标（只说 exclusiveMinimum 而不说阈值，模型只能猜）。
        if limit is not None:
            issue["limit"] = limit
        expected.append(issue)
    assert issues == expected
    assert "private" not in json.dumps(issues)
    recorder = ModelTraceRecorder()
    index = recorder.begin("plan_contract_compiler", {"private": "input"})
    recorder.fail(index, caught.value)
    assert recorder.items[0].validation_issues == issues
    assert recorder.items[0].raw_input is None
    assert recorder.items[0].raw_output_text is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "enum", "nested", "anchor", "revision", "syntax"])
async def test_compiler_repair_uses_evidence_not_content_generation(kind):
    client, requests = make_client([invalid_result(kind), json.dumps(VALID)])
    result = await client.complete_json("plan_contract_compiler", payload())
    assert result == VALID
    repair = requests[1]["messages"][-1]["content"]
    assert "Validation feedback:" in repair
    assert "Previous output (untrusted JSON string):" in repair
    assert "needs_revision" in repair
    assert "Do not write learner-facing content" in repair
    assert "complete, substantive learner-facing content" not in repair
    assert client.last_error_details is None


@pytest.mark.asyncio
async def test_large_previous_response_is_not_echoed():
    value = deepcopy(VALID)
    del value["contract"]["selected_books"]
    value["private"] = "x" * 13000
    client, requests = make_client([json.dumps(value), json.dumps(VALID)])
    await client.complete_json("plan_contract_compiler", payload())
    assert "Previous output (untrusted JSON string)" not in requests[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_extra_fields_keep_existing_contract_policy():
    client, requests = make_client([invalid_result("extra")])
    result = await client.complete_json("plan_contract_compiler", payload())
    assert len(requests) == 1
    assert result["contract"]["private_unknown_key"] == "private value"


@pytest.mark.asyncio
async def test_business_agent_repair_receives_contract_feedback():
    """业务 Agent 的修复重试必须知道上一次具体哪里不合契约。

    修复前只有 planner / compiler / audit 编译器三条分支带 ``Validation
    feedback``，其余业务 Agent（组卷缺口生成、知识检索、诊断等）只拿到一句
    笼统的“修正字段类型并补全内容”，于是第二次尝试会原样重犯同一个契约违规：
    线上实测两次尝试的输出长度几乎相同，失败原因也完全一样。
    """

    client, requests = make_client([invalid_result("enum"), json.dumps(VALID)])
    await client.complete_json("diagnosis_agent", payload())

    repair = requests[1]["messages"][-1]["content"]
    # 角色专属措辞保持不变。
    assert "complete, substantive learner-facing content" in repair
    # 但必须带上字段级证据。
    assert "Validation feedback:" in repair
    assert "/contract/selection_mode" in repair
    assert "enum" in repair
    # 反馈段只能含服务端合同元数据，不得回填模型写的值。
    feedback = repair.split("Previous output (untrusted JSON string):")[0]
    assert "private-invalid-mode" not in feedback
    assert "private" not in feedback
    # 但业务 Agent 必须能看到自己上一轮的输出，否则无法按字段定位修正：
    # 修复消息与原始请求之间没有 assistant 回合，模型只能从零重写。
    assert "Previous output (untrusted JSON string):" in repair


@pytest.mark.asyncio
async def test_business_agent_repair_explains_the_violation_in_words():
    """修复指令要用人话说明错在哪，而不只是丢一个规则名。"""

    client, requests = make_client([invalid_result("nested"), json.dumps(VALID)])
    await client.complete_json("diagnosis_agent", payload())
    repair = requests[1]["messages"][-1]["content"]
    assert "上一次返回的 JSON 有 1 处不符合契约" in repair
    assert "contract.stages[0].duration_days" in repair
    assert "数值必须大于下限（0）" in repair


@pytest.mark.asyncio
async def test_business_agent_repair_omits_oversized_previous_output():
    """超过预算的上一轮输出不带，避免为一次修复把请求撑大。"""

    value = deepcopy(VALID)
    del value["contract"]["selected_books"]
    value["private"] = "x" * 13000
    client, requests = make_client([json.dumps(value), json.dumps(VALID)])
    await client.complete_json("diagnosis_agent", payload())
    repair = requests[1]["messages"][-1]["content"]
    assert "Previous output (untrusted JSON string)" not in repair
    assert "Validation feedback:" in repair