import json

import httpx
import pytest

from competition_app.llm.openai_compatible import ModelResponseError, OpenAICompatibleChatModel
from competition_app.llm.failover import FailoverChatModel


@pytest.mark.asyncio
async def test_chat_client_uses_openai_compatible_request_shape() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"decision":"pass"}'}}]},
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )

    result = await client.complete_json("audit_agent", {"resource": "draft"})

    assert result == {"decision": "pass"}
    assert requests[0].url == "https://example.test/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer secret-value"
    assert b'"response_format":{"type":"json_object"}' in requests[0].content
    assert client.last_request_payload["body"]["messages"][0]["role"] == "system"
    assert client.last_request_payload["body"]["messages"][1]["role"] == "user"
    assert client.last_response_text == '{"decision":"pass"}'
    assert "authorization" not in client.last_request_payload


@pytest.mark.asyncio
async def test_deepseek_chat_client_uses_standard_structured_output_shape() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"decision":"pass"}'}}]},
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )

    assert await client.complete_json("audit_agent", {}) == {"decision": "pass"}
    assert "enable_thinking" not in json.loads(requests[0].content)


@pytest.mark.asyncio
async def test_qwen_2026_05_17_enables_required_thinking_mode() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "content": '{"decision":"pass"}',
                        "reasoning_content": "internal reasoning",
                    }
                }]
            },
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen3.7-max-2026-05-17",
        transport=httpx.MockTransport(handler),
    )

    assert await client.complete_json("audit_agent", {}) == {"decision": "pass"}
    assert json.loads(requests[0].content)["enable_thinking"] is True
    assert client.last_reasoning_text == "internal reasoning"


@pytest.mark.asyncio
async def test_chat_client_preserves_audit_findings_without_knowledge_aliases() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"decision":"pass","findings":["审核通过"]}'
                        }
                    }
                ]
            },
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )

    result = await client.complete_json("audit_agent", {})

    assert result == {"decision": "pass", "findings": ["审核通过"]}
    assert "quality_labels" not in result


@pytest.mark.asyncio
async def test_chat_client_normalizes_structured_audit_findings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "content": (
                            '{"decision":"revise","findings":['
                            '{"issue":"证据标注不足","detail":"对比结论未标来源",'
                            '"requirement":"补充来源标识"}]}'
                        )
                    }
                }]
            },
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )

    result = await client.complete_json("audit_agent", {})

    assert result == {
        "decision": "revise",
        "findings": [
            "问题：证据标注不足；说明：对比结论未标来源；修改要求：补充来源标识"
        ],
    }


@pytest.mark.asyncio
async def test_chat_client_normalizes_planner_fallback_policy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "content": (
                            '{"task_type":"paper_generation",'
                            '"selected_agents":["knowledge_base_agent","expert_agent","audit_agent"],'
                            '"routing_reason":"用户要求组卷",'
                            '"risk_level":"medium","requires_audit":true,'
                            '"fallback_policy":"若题库不足则停止组卷并提示用户"}'
                        )
                    }
                }]
            },
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )

    result = await client.complete_json("planner_agent", {})

    assert result["fallback_policy"] == "fail_closed"


@pytest.mark.asyncio
async def test_chat_client_preserves_route_reason_without_retrieval_alias() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"decision":"select",'
                                '"selected_route_id":"tcm_physician_standard_degree",'
                                '"confidence":0.95,"reason":"考试名称明确",'
                                '"clarification_question":null}'
                            )
                        }
                    }
                ]
            },
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )

    result = await client.complete_json("default_route_resolver", {})

    assert result["reason"] == "考试名称明确"
    assert "retrieval_reason" not in result


@pytest.mark.asyncio
async def test_chat_client_includes_nested_agent_schema_and_common_rules() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )

    await client.complete_json(
        "diagnosis_agent",
        {
            "purpose": "生成学情诊断",
            "task_instructions": "先分析学习状态，再输出有证据的建议。",
            "payload": {
                "task_instructions": ["详细说明诊断依据。"],
                "output_schema": {
                    "type": "object",
                    "required": ["summary"],
                    "properties": {"summary": {"type": "string", "description": "诊断摘要"}},
                    "additionalProperties": False,
                },
            },
            "permission_note": "不得生成系统状态。",
        },
    )

    body = json.loads(requests[0].content)
    system_prompt = body["messages"][0]["content"]
    user_message = body["messages"][1]["content"]
    assert "不得把推测写成事实" in system_prompt
    assert "先分析学习状态" in system_prompt
    assert "不得生成系统状态" in system_prompt
    assert "# 输出方式" in system_prompt
    assert "maxLength" not in system_prompt
    assert "additionalProperties" not in system_prompt
    assert "详细说明诊断依据。" not in user_message
    assert "payload" not in user_message
    assert "用户请求和相关资料：" in user_message
    assert "上下文标识" not in user_message
    assert "trace_id" not in user_message


@pytest.mark.asyncio
async def test_chat_client_includes_output_contract_in_system_prompt() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"summary":"已生成"}'}}]})

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )

    await client.complete_json(
        "diagnosis_agent",
        {
            "payload": {
                "output_schema": {
                    "type": "object",
                    "required": ["summary"],
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "当前学习状态摘要",
                        }
                    },
                }
            }
        },
    )

    system_prompt = json.loads(requests[0].content)["messages"][0]["content"]
    assert "# 输出契约" in system_prompt
    assert "summary" in system_prompt
    assert "必填" in system_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "payload", "expected_sections", "expected_facts"),
    [
        (
            "planner_agent",
            {
                "user_request": "请讲解四君子汤",
                "task_type": "knowledge_explanation",
                "existing_plan_state": {"has_long_term_plan": True},
                "hard_routing_rules": ["讲解任务需要知识、专家和审核智能体。"],
            },
            ["## 本次任务", "## 编排依据"],
            ["请讲解四君子汤", "has_long_term_plan：True"],
        ),
        (
            "knowledge_base_agent",
            {
                "phase": "plan_retrieval",
                "user_request": "检索补气类方剂",
                "retrieval_context": {"user_short_term_goal": "本周掌握补气类方剂"},
                "available_tools": {"get_kp_with_content": "检索教材内容"},
            },
            ["## 本次任务", "## 检索范围"],
            ["本周掌握补气类方剂", "检索教材内容"],
        ),
        (
            "diagnosis_agent",
            {
                "plan_scope": "short_term",
                "user_request": "结合长期规划制定短期计划",
                "learning_evidence": {
                    "current_status": {"status_name": "节奏恢复", "confidence": 0.82},
                    "behavior_summary": {
                        "task_completion_rate": {
                            "learning_task_completion_rate": {"value": 0.45},
                            "review_task_completion_rate": {"value": 0.4},
                        }
                    },
                },
                "default_route": {
                    "planning_status": "approved_route",
                    "textbook_route": {
                        "route_id": "textbook_tcm_physician",
                        "stages": [{
                            "stage_id": "stage-1",
                            "name": "中医基础与文化语言",
                            "books": ["《中医学基础》", "《医古文》"],
                        }],
                        "prerequisites": [{"course": "中医诊断学"}],
                    },
                },
                "existing_plans": {
                    "long_term": {
                        "plan_id": "LP_LONG_INTERNAL",
                        "content": "【最终目标】通过中医执业医师考试。",
                    }
                },
            },
            ["## 本次任务", "## 学习状态与证据", "## 已确认路线", "## 当前有效计划"],
            ["节奏恢复", "学习任务完成率：0.45", "《医古文》", "中医诊断学", "【最终目标】"],
        ),
        (
            "expert_agent",
            {
                "phase": "knowledge_explanation",
                "topic": "四君子汤",
                "semantic_evidence": [{"text": "四君子汤由人参、白术、茯苓、甘草组成。"}],
                "user_preference": {"communication_style": "任务清单式"},
            },
            ["## 本次任务", "## 证据材料", "## 学习者信息"],
            ["四君子汤", "人参、白术、茯苓、甘草", "任务清单式"],
        ),
        (
            "expert_agent",
            {
                "phase": "paper_blueprint",
                "user_request": "给我第三阶段的测试卷",
                "learning_scope": {
                    "requested_stage": 3,
                    "resolution": "已从当前长期规划正文解析",
                    "stage_description": "融合经典辨证体系与现代医学基础",
                },
                "planning_context": {
                    "long_term_plan": {
                        "content": "【能力路径与阶段】第三阶段融合经典辨证体系与现代医学基础。",
                    }
                },
            },
            ["## 本次任务", "## 试卷约束", "## 当前学习规划"],
            ["用户指定阶段：3", "经典辨证体系与现代医学基础", "当前长期规划"],
        ),
        (
            "audit_agent",
            {
                "semantic_resource": {"title": "四君子汤讲解", "content": "教学正文"},
                "semantic_evidence": [{"text": "教材证据"}],
                "acceptance_criteria": {"teaching_only": True},
            },
            ["## 审核对象", "## 证据材料", "## 审核依据"],
            ["四君子汤讲解", "教材证据", "teaching_only：True"],
        ),
    ],
)
async def test_chat_client_organizes_complete_material_for_each_agent(
    role, payload, expected_sections, expected_facts
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )
    await client.complete_json(role, {"payload": payload})
    user_message = json.loads(requests[0].content)["messages"][1]["content"]

    for section in expected_sections:
        assert section in user_message
    for fact in expected_facts:
        assert fact in user_message
    assert "LP_LONG_INTERNAL" not in user_message


@pytest.mark.asyncio
async def test_chat_client_repairs_invalid_json_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = "not-json" if calls == 1 else '{"fixed":true}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )

    assert await client.complete_json("expert_agent", {}) == {"fixed": True}
    assert calls == 2


@pytest.mark.asyncio
async def test_chat_client_extracts_json_object_from_incidental_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "content": '分析结束。\n```json\n{"status":"ok","detail":{"value":1}}\n```'
                    }
                }]
            },
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )

    assert await client.complete_json("planner_agent", {}) == {
        "status": "ok",
        "detail": {"value": 1},
    }


@pytest.mark.asyncio
async def test_chat_client_error_never_exposes_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("request timed out", request=request)

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelResponseError) as exc_info:
        await client.complete_json("planner_agent", {})

    assert "secret-value" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_chat_client_retries_one_transient_http_status() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"message": "temporary unavailable"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"status":"ok"}'}}]},
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen3.7-plus-2026-05-26",
        transport=httpx.MockTransport(handler),
    )

    assert await client.complete_json("planner_agent", {}) == {"status": "ok"}
    assert calls == 2


@pytest.mark.asyncio
async def test_chat_client_does_not_retry_non_transient_http_status() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"message": "invalid request"})

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen3.7-plus-2026-05-26",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelResponseError, match="HTTP 400"):
        await client.complete_json("planner_agent", {})
    assert calls == 1


@pytest.mark.asyncio
async def test_chat_client_uses_bounded_backoff_for_rate_limit(monkeypatch) -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"status":"ok"}'}}]},
        )

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("competition_app.llm.openai_compatible.asyncio.sleep", fake_sleep)
    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen3.7-plus-2026-05-26",
        transport=httpx.MockTransport(handler),
    )

    assert await client.complete_json("planner_agent", {}) == {"status": "ok"}
    assert calls == 3
    assert delays == [2.0, 4.0]


@pytest.mark.asyncio
async def test_chat_client_streams_incremental_content() -> None:
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert b'"stream":true' in request.content
        body = (
            'data: {"choices":[{"delta":{"content":"{\\"status\\": "}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"\\"ok\\"}"}}]}\n\n'
            'data: [DONE]\n\n'
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )

    result = await client.complete_json("planner_agent", {}, on_delta=observed.append)

    assert result == {"status": "ok"}
    assert observed == ['{"status": ', '"ok"}']
    assert client.last_request_payload["body"]["stream"] is True
    assert client.last_response_text == '{"status": "ok"}'


@pytest.mark.asyncio
async def test_chat_client_streams_reasoning_content_when_present() -> None:
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"choices":[{"delta":{"reasoning_content":"分析中..."}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"{\\"status\\":\\"ok\\"}"}}]}\n\n'
            'data: [DONE]\n\n'
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )

    assert await client.complete_json("planner_agent", {}, on_delta=observed.append) == {"status": "ok"}
    assert observed == ["分析中...", '{"status":"ok"}']
    assert client.last_reasoning_text == "分析中..."
    assert client.last_response_text == '{"status":"ok"}'


@pytest.mark.asyncio
async def test_chat_client_ignores_stream_usage_event_without_choices() -> None:
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"choices":[{"delta":{"content":"{\\"status\\":\\"ok\\"}"}}]}\n\n'
            'data: {"choices":[],"usage":{"total_tokens":42}}\n\n'
            'data: [DONE]\n\n'
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )

    assert await client.complete_json("planner_agent", {}, on_delta=observed.append) == {
        "status": "ok"
    }
    assert observed == ['{"status":"ok"}']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": None}}]},
    ],
)
async def test_chat_client_rejects_empty_success_response(body) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen3.7-flash",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelResponseError) as exc_info:
        await client.complete_json("planner_agent", {})
    assert exc_info.value.failover_eligible is True


@pytest.mark.asyncio
async def test_chat_client_rejects_stream_without_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='data: {"choices":[],"usage":{"total_tokens":1}}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen3.7-flash",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelResponseError, match="no content"):
        await client.complete_json("planner_agent", {}, on_delta=lambda _: None)


@pytest.mark.asyncio
async def test_chat_client_hides_invalid_json_repair_stream() -> None:
    responses = iter(["not-json", '{"status":"ok"}'])

    def handler(request: httpx.Request) -> httpx.Response:
        content = next(responses)
        event = json.dumps({"choices": [{"delta": {"content": content}}]})
        return httpx.Response(
            200,
            text=f"data: {event}\n\ndata: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen3.7-flash",
        transport=httpx.MockTransport(handler),
    )
    observed: list[str] = []

    assert await client.complete_json(
        "planner_agent", {}, on_delta=observed.append
    ) == {"status": "ok"}
    assert observed == ['{"status":"ok"}']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "reason"),
    [
        (402, "quota_exhausted"),
        (403, "model_access_denied"),
        (404, "model_unavailable"),
        (503, "transient_provider_error"),
    ],
)
async def test_chat_client_classifies_failover_http_errors(
    status_code, reason, monkeypatch
) -> None:
    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("competition_app.llm.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"message": "unavailable"})

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="qwen3.7-flash",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelResponseError) as exc_info:
        await client.complete_json("planner_agent", {})
    assert exc_info.value.reason == reason
    assert exc_info.value.failover_eligible is True


@pytest.mark.asyncio
async def test_chat_client_does_not_failover_on_invalid_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid api key"})

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="invalid-value",
        model="qwen3.7-flash",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelResponseError) as exc_info:
        await client.complete_json("planner_agent", {})
    assert exc_info.value.reason == "http_error"
    assert exc_info.value.failover_eligible is False


@pytest.mark.asyncio
async def test_failover_switches_after_model_access_denied() -> None:
    calls: list[str] = []

    def first_handler(request: httpx.Request) -> httpx.Response:
        calls.append("qwen3.7-flash")
        return httpx.Response(403, json={"message": "model access denied"})

    def second_handler(request: httpx.Request) -> httpx.Response:
        calls.append("qwen3.7-max-preview")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"status":"ok"}'}}]},
        )

    failover = FailoverChatModel(
        [
            OpenAICompatibleChatModel(
                "https://example.test/v1", "secret", "qwen3.7-flash",
                transport=httpx.MockTransport(first_handler),
            ),
            OpenAICompatibleChatModel(
                "https://example.test/v1", "secret", "qwen3.7-max-preview",
                transport=httpx.MockTransport(second_handler),
            ),
        ]
    )

    assert await failover.complete_json("planner_agent", {}) == {"status": "ok"}
    assert calls == ["qwen3.7-flash", "qwen3.7-max-preview"]
    assert failover.failover_history[-1]["reason"] == "model_access_denied"


@pytest.mark.asyncio
async def test_failover_model_switches_in_configured_order_and_stays_on_success(
    monkeypatch,
) -> None:
    calls: list[str] = []

    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("competition_app.llm.openai_compatible.asyncio.sleep", fake_sleep)

    def first_handler(request: httpx.Request) -> httpx.Response:
        calls.append("qwen3.7-flash")
        return httpx.Response(402, json={"message": "quota exhausted"})

    def second_handler(request: httpx.Request) -> httpx.Response:
        calls.append("qwen3.7-max-preview")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"status":"ok"}'}}]},
        )

    failover = FailoverChatModel(
        [
            OpenAICompatibleChatModel(
                "https://example.test/v1", "secret", "qwen3.7-flash",
                transport=httpx.MockTransport(first_handler),
            ),
            OpenAICompatibleChatModel(
                "https://example.test/v1", "secret", "qwen3.7-max-preview",
                transport=httpx.MockTransport(second_handler),
            ),
        ]
    )

    assert await failover.complete_json("planner_agent", {}) == {"status": "ok"}
    assert await failover.complete_json("planner_agent", {}) == {"status": "ok"}
    assert calls == ["qwen3.7-flash", "qwen3.7-max-preview", "qwen3.7-max-preview"]
    assert failover.model == "qwen3.7-max-preview"
    assert failover.failover_history == [
        {
            "from_model": "qwen3.7-flash",
            "to_model": "qwen3.7-max-preview",
            "reason": "quota_exhausted",
            "status_code": 402,
        }
    ]


@pytest.mark.asyncio
async def test_failover_retries_business_invalid_json_and_hides_failed_stream() -> None:
    observed: list[str] = []

    def invalid_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"{\\"status\\":\\"bad\\"}"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    def valid_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"{\\"status\\":\\"ok\\"}"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    failover = FailoverChatModel(
        [
            OpenAICompatibleChatModel(
                "https://example.test/v1", "secret", "first",
                transport=httpx.MockTransport(invalid_handler),
            ),
            OpenAICompatibleChatModel(
                "https://example.test/v1", "secret", "second",
                transport=httpx.MockTransport(valid_handler),
            ),
        ]
    )
    payload = {
        "_result_validator": lambda result: (
            result
            if result.get("status") == "ok"
            else (_ for _ in ()).throw(ValueError("invalid status"))
        )
    }

    assert await failover.complete_json(
        "planner_agent", payload, on_delta=observed.append
    ) == {"status": "ok"}
    assert observed == ['{"status":"ok"}']
    assert failover.last_request_payload["failovers"][-1]["reason"] == (
        "business_schema_invalid"
    )

    assert await failover.complete_json(
        "planner_agent", payload, on_delta=lambda _: None
    ) == {"status": "ok"}
    assert "failovers" not in failover.last_request_payload
