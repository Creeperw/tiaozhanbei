"""Tests for the json_schema strict-mode structured output path.

The production code flattens a pydantic ``model_json_schema()`` into a
provider strict-mode compatible schema (no ``$defs``, every field required,
``additionalProperties: false``) and sends it via
``response_format.type = "json_schema"`` so the provider enforces field
names, types and required-ness at the source instead of relying on the loose
``json_object`` mode plus post-hoc normalization.
"""

import json

import httpx
import pytest
from pydantic import ValidationError

from competition_app.agents.planner import PlannerAgent
from competition_app.llm.schemas import PlannerLearningPlanOutput
from competition_app.llm.openai_compatible import (
    AmbiguousJSONObjectError,
    ModelResponseError,
    OpenAICompatibleChatModel,
    _flatten_schema_for_strict_mode,
    _parse_json_object,
)


def _nested_schema() -> dict:
    """A schema with a $defs reference, optional fields and anyOf branches."""
    return {
        "$defs": {
            "Conflict": {
                "type": "object",
                "title": "Conflict",
                "required": ["memory_id", "reason"],
                "properties": {
                    "memory_id": {"type": "integer", "title": "Memory Id"},
                    "reason": {"type": "string", "title": "Reason"},
                },
                "additionalProperties": False,
            }
        },
        "type": "object",
        "title": "Root",
        "required": ["notes", "candidates", "conflicts"],
        "properties": {
            "notes": {"type": "string", "title": "Notes", "description": "说明"},
            "candidates": {
                "type": "array",
                "title": "Candidates",
                "items": {"type": "string"},
            },
            "conflicts": {
                "type": "array",
                "title": "Conflicts",
                "items": {"$ref": "#/$defs/Conflict"},
            },
            "optional_flag": {
                "anyOf": [{"type": "null"}, {"type": "boolean"}],
                "title": "Optional Flag",
            },
        },
        "additionalProperties": False,
    }


def test_flatten_removes_defs_and_refs() -> None:
    flat = _flatten_schema_for_strict_mode(_nested_schema())
    assert flat is not None
    assert "$defs" not in flat
    assert "$ref" not in json.dumps(flat)
    # Every object gets additionalProperties: false and a full required list.
    assert flat["additionalProperties"] is False
    assert set(flat["required"]) == {"notes", "candidates", "conflicts", "optional_flag"}
    conflict_items = flat["properties"]["conflicts"]["items"]
    assert conflict_items["additionalProperties"] is False
    assert set(conflict_items["required"]) == {"memory_id", "reason"}
    # Nullable fields keep the null branch as type: ["<base>", "null"] so the
    # model may still emit null for optional fields (dropping it forced models
    # to fill optional fields, corrupting routing).
    assert flat["properties"]["optional_flag"] == {"type": ["boolean", "null"]}


def test_flatten_keeps_field_descriptions_out_of_schema() -> None:
    flat = _flatten_schema_for_strict_mode(_nested_schema())
    assert "description" not in json.dumps(flat)
    assert "title" not in json.dumps(flat)


def test_flatten_rejects_non_object_schemas() -> None:
    assert _flatten_schema_for_strict_mode(None) is None
    assert _flatten_schema_for_strict_mode("not-a-schema") is None
    assert _flatten_schema_for_strict_mode({}) is None
    assert _flatten_schema_for_strict_mode({"type": "string"}) is None


def test_flatten_rejects_nested_non_nullable_union_without_losing_branch() -> None:
    schema = {
        "type": "object",
        "properties": {
            "value": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "integer"},
                ]
            }
        },
        "required": ["value"],
    }

    assert _flatten_schema_for_strict_mode(schema) is None


def test_parse_json_rejects_multiple_different_top_level_objects() -> None:
    with pytest.raises(AmbiguousJSONObjectError):
        _parse_json_object(
            '示例：{"status":"draft","payload":{"large":true}} '
            '正式：{"status":"ok"}'
        )


def test_parse_json_accepts_one_wrapped_object_with_nested_fields() -> None:
    assert _parse_json_object(
        '```json\n{"status":"ok","payload":{"nested":true}}\n```'
    ) == {"status": "ok", "payload": {"nested": True}}


def test_flatten_preserves_root_union_statuses() -> None:
    schema = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "status": {"const": "compiled", "type": "string"},
                    "contract": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                        "required": ["title"],
                    },
                },
                "required": ["status", "contract"],
            },
            {
                "type": "object",
                "properties": {
                    "status": {"const": "needs_revision", "type": "string"},
                    "issues": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["status", "issues"],
            },
        ]
    }

    flat = _flatten_schema_for_strict_mode(schema)
    assert flat is not None
    assert flat["type"] == "object"
    assert "oneOf" not in flat
    assert flat["properties"]["status"]["enum"] == [
        "compiled",
        "needs_revision",
    ]
    assert flat["properties"]["contract"]["type"] == ["object", "null"]
    assert flat["properties"]["issues"]["type"] == ["array", "null"]
    assert set(flat["required"]) == {"status", "contract", "issues"}


@pytest.mark.asyncio
async def test_complete_json_uses_json_schema_strict_mode_when_schema_provided() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"notes":"n","candidates":[],"conflicts":[],"optional_flag":true}'}}]},
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )

    result = await client.complete_json(
        "memory_agent",
        {"payload": {"output_schema": _nested_schema()}},
    )

    assert result["notes"] == "n"
    body = json.loads(requests[0].content)
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert "$defs" not in schema
    assert set(schema["required"]) == {"notes", "candidates", "conflicts", "optional_flag"}


@pytest.mark.asyncio
async def test_complete_json_sends_both_root_union_statuses() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"needs_revision","contract":null,"issues":[]}'
                        }
                    }
                ]
            },
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )
    result = await client.complete_json(
        "paper_assembly_compiler",
        {
            "payload": {
                "output_schema": {
                    "oneOf": [
                        {
                            "type": "object",
                            "properties": {
                                "status": {"const": "compiled", "type": "string"},
                                "contract": {"type": "object"},
                            },
                            "required": ["status", "contract"],
                        },
                        {
                            "type": "object",
                            "properties": {
                                "status": {
                                    "const": "needs_revision",
                                    "type": "string",
                                },
                                "issues": {"type": "array"},
                            },
                            "required": ["status", "issues"],
                        },
                    ]
                }
            }
        },
    )

    assert result["status"] == "needs_revision"
    schema = json.loads(requests[0].content)["response_format"]["json_schema"][
        "schema"
    ]
    assert schema["properties"]["status"]["enum"] == [
        "compiled",
        "needs_revision",
    ]


@pytest.mark.asyncio
async def test_complete_json_falls_back_to_loose_mode_without_schema() -> None:
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

    await client.complete_json("audit_agent", {})
    body = json.loads(requests[0].content)
    # No output_schema -> no response_format at all (loose mode).
    assert "response_format" not in body


@pytest.mark.asyncio
async def test_complete_json_degrades_to_json_object_when_provider_rejects_schema() -> None:
    """A 400 from the provider (json_schema unsupported) retries in json_object mode."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        response_format = body.get("response_format")
        if response_format and response_format.get("type") == "json_schema":
            return httpx.Response(
                400,
                json={"error": {"message": "json_schema not supported"}},
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"notes":"n","candidates":[],"conflicts":[],"optional_flag":true}'}}]},
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )

    result = await client.complete_json(
        "memory_agent",
        {"payload": {"output_schema": _nested_schema()}},
    )

    assert result["notes"] == "n"
    assert len(requests) == 2
    first_body = json.loads(requests[0].content)
    second_body = json.loads(requests[1].content)
    assert first_body["response_format"]["type"] == "json_schema"
    assert second_body["response_format"]["type"] == "json_object"
    assert client.last_error_details is None


@pytest.mark.asyncio
async def test_schema_fallback_still_rejects_business_invalid_json() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        response_format = body.get("response_format") or {}
        if response_format.get("type") == "json_schema":
            return httpx.Response(400, json={"error": {"message": "unsupported"}})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"notes":123}'}}]},
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelResponseError) as exc_info:
        await client.complete_json(
            "memory_agent",
            {"payload": {"output_schema": _nested_schema()}},
        )

    assert exc_info.value.reason == "business_schema_invalid"
    assert client.last_error_details == {
        "error_type": "ModelResponseError",
        "reason": "business_schema_invalid",
        "attempt_count": 2,
        "attempt_failures": [
            "business_schema_invalid",
            "business_schema_invalid",
        ],
        "response_lengths": [13, 13],
    }


@pytest.mark.asyncio
async def test_direct_client_executes_internal_result_validator() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        assert "_result_validator" not in json.dumps(body, ensure_ascii=False)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"status":"bad"}'}}]},
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelResponseError) as exc_info:
        await client.complete_json(
            "planner_agent",
            {
                "_result_validator": lambda result: (
                    result
                    if result.get("status") == "ok"
                    else (_ for _ in ()).throw(ValueError("bad status"))
                )
            },
        )

    assert exc_info.value.reason == "business_schema_invalid"
    assert len(requests) == 2
    repair_message = json.loads(requests[1].content)["messages"][-1]["content"]
    assert "Repair only the current Planner decision JSON" in repair_message
    assert "Do not write learner-facing content" in repair_message


@pytest.mark.asyncio
async def test_planner_branch_raw_validation_repairs_cross_task_pollution() -> None:
    requests: list[httpx.Request] = []
    polluted = {
        "task_type": "knowledge_explanation",
        "current_turn_available_minutes": None,
        "current_turn_available_minutes_source_quote": None,
        "current_turn_available_minutes_scope": None,
        "question_explanation_request": False,
        "routing_reason": "错误地混入复习调整字段。",
        "review_adjustment": "reduce_capacity",
        "review_adjustment_source_quote": "减少每日复习任务数量",
    }
    repaired = {
        "task_type": "knowledge_explanation",
        "current_turn_available_minutes": None,
        "current_turn_available_minutes_source_quote": None,
        "current_turn_available_minutes_scope": None,
        "question_explanation_request": False,
        "routing_reason": "用户要求结合教材证据讲解知识点。",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        content = polluted if len(requests) == 1 else repaired
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(content, ensure_ascii=False)}}
                ]
            },
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )
    user_request = "请结合教材证据讲解阴阳学说，并给我一道练习题。"
    schema = PlannerAgent._branch_output_schema("knowledge_explanation")

    result = await client.complete_json(
        "planner_agent",
        {
            "prompt_skill_id": "planner.route_knowledge_explanation",
            "payload": {
                "frozen_task_type": "knowledge_explanation",
                "user_request": user_request,
                "output_schema": schema,
            },
            "_result_validator": lambda value: PlannerAgent._validate_frozen_branch_raw(
                value,
                {"user_request": user_request},
                "knowledge_explanation",
            ),
        },
    )

    assert result == repaired
    assert len(requests) == 2
    first_body = json.loads(requests[0].content)
    strict_schema = first_body["response_format"]["json_schema"]["schema"]
    assert strict_schema["properties"]["task_type"]["enum"] == [
        "knowledge_explanation"
    ]
    assert "review_adjustment" not in strict_schema["properties"]
    first_prompt = json.dumps(first_body["messages"], ensure_ascii=False)
    assert "减少每日复习任务数量" not in first_prompt
    assert "review_adjustment" not in first_prompt
    repair_message = json.loads(requests[1].content)["messages"][-1]["content"]
    assert "trusted business validation" in repair_message
    assert "Previous routing fields" not in repair_message
    assert client.last_response_text == json.dumps(repaired, ensure_ascii=False)
    assert client.last_error_details is None


def test_planner_branch_clears_orphan_budget_tuple_without_relaxing_contract() -> None:
    request = "今天学习阴阳学说"
    orphan = {
        "planning_request_scope": {"mode": "explicit_focus", "objects": ["阴阳学说"],
                                   "source_quote": "学习阴阳学说", "clarification_question": None},
        "task_type": "learning_plan",
        "plan_scope": "daily_task",
        "plan_action": "create_or_update",
        "current_turn_available_minutes": None,
        "current_turn_available_minutes_source_quote": None,
        "current_turn_available_minutes_scope": "daily_recurring",
        "requires_clarification": False,
        "clarification_question": None,
        "requires_knowledge_support": False,
        "routing_reason": "用户要求制定当日学习任务。",
    }

    normalized = PlannerAgent._validate_frozen_branch_raw(
        orphan,
        {"user_request": request},
        "learning_plan",
    )

    assert normalized["current_turn_available_minutes"] is None
    assert normalized["current_turn_available_minutes_source_quote"] is None
    assert normalized["current_turn_available_minutes_scope"] is None
    with pytest.raises(ValidationError):
        PlannerLearningPlanOutput.model_validate(orphan)


@pytest.mark.parametrize("scope", ["today_only", "daily_recurring"])
def test_planner_branch_preserves_complete_anchored_budget_tuple(scope: str) -> None:
    request = "我今天有 30 分钟学习阴阳学说"
    complete = {
        "planning_request_scope": {"mode": "explicit_focus", "objects": ["阴阳学说"],
                                   "source_quote": "学习阴阳学说", "clarification_question": None},
        "task_type": "learning_plan",
        "plan_scope": "daily_task",
        "plan_action": "create_or_update",
        "current_turn_available_minutes": 30,
        "current_turn_available_minutes_source_quote": "30 分钟",
        "current_turn_available_minutes_scope": scope,
        "requires_clarification": False,
        "clarification_question": None,
        "requires_knowledge_support": False,
        "routing_reason": "用户要求按预算安排当日学习任务。",
    }

    normalized = PlannerAgent._validate_frozen_branch_raw(
        complete,
        {"user_request": request},
        "learning_plan",
    )

    assert normalized["current_turn_available_minutes"] == 30
    assert normalized["current_turn_available_minutes_source_quote"] == "30 分钟"
    assert normalized["current_turn_available_minutes_scope"] == scope


@pytest.mark.parametrize(
    "overrides",
    [
        {"current_turn_available_minutes_source_quote": "两小时"},
        {"current_turn_available_minutes_scope": "unsupported"},
        {"current_turn_available_minutes": None},
    ],
)
def test_planner_branch_clears_untrusted_partial_budget_tuple(
    overrides: dict[str, object],
) -> None:
    result = {
        "planning_request_scope": {"mode": "explicit_focus", "objects": ["阴阳学说"],
                                   "source_quote": "学习阴阳学说", "clarification_question": None},
        "task_type": "learning_plan",
        "plan_scope": "daily_task",
        "plan_action": "create_or_update",
        "current_turn_available_minutes": 30,
        "current_turn_available_minutes_source_quote": "30 分钟",
        "current_turn_available_minutes_scope": "today_only",
        "requires_clarification": False,
        "clarification_question": None,
        "requires_knowledge_support": False,
        "routing_reason": "用户要求按预算安排当日学习任务。",
    }
    result.update(overrides)

    normalized = PlannerAgent._validate_frozen_branch_raw(
        result,
        {"user_request": "我今天有 30 分钟学习阴阳学说"},
        "learning_plan",
    )

    assert normalized["current_turn_available_minutes"] is None
    assert normalized["current_turn_available_minutes_source_quote"] is None
    assert normalized["current_turn_available_minutes_scope"] is None


@pytest.mark.asyncio
async def test_orphan_budget_tuple_does_not_trigger_provider_repair() -> None:
    requests: list[httpx.Request] = []
    orphan = {
        "planning_request_scope": {"mode": "explicit_focus", "objects": ["阴阳学说"],
                                   "source_quote": "学习阴阳学说", "clarification_question": None},
        "task_type": "learning_plan",
        "plan_scope": "daily_task",
        "plan_action": "create_or_update",
        "current_turn_available_minutes": None,
        "current_turn_available_minutes_source_quote": None,
        "current_turn_available_minutes_scope": "daily_recurring",
        "requires_clarification": False,
        "clarification_question": None,
        "requires_knowledge_support": False,
        "routing_reason": "用户要求制定当日学习任务。",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(orphan, ensure_ascii=False)}}
                ]
            },
        )

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )
    user_request = "今天学习阴阳学说"

    result = await client.complete_json(
        "planner_agent",
        {
            "prompt_skill_id": "planner.route_learning_plan",
            "payload": {
                "frozen_task_type": "learning_plan",
                "user_request": user_request,
                "output_schema": PlannerAgent._branch_output_schema("learning_plan"),
            },
            "_result_validator": lambda value: PlannerAgent._validate_frozen_branch_raw(
                value,
                {"user_request": user_request},
                "learning_plan",
            ),
        },
    )

    assert len(requests) == 1
    assert result["current_turn_available_minutes"] is None
    assert result["current_turn_available_minutes_source_quote"] is None
    assert result["current_turn_available_minutes_scope"] is None
    assert client.last_error_details is None


@pytest.mark.asyncio
async def test_scope_resolver_repair_does_not_preserve_false_plan_route() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            content = '{"task_type":"learning_plan","plan_scope":"unspecified"}'
        else:
            content = '{"task_type":"knowledge_explanation","plan_scope":null}'
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
        )

    def validate(result):
        if result.get("task_type") == "learning_plan":
            raise ValueError("false plan premise")
        return result

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )

    result = await client.complete_json(
        "planner_agent",
        {
            "prompt_skill_id": "planner.resolve_plan_scope",
            "_result_validator": validate,
        },
    )

    assert result["task_type"] == "knowledge_explanation"
    repair_message = json.loads(requests[1].content)["messages"][-1]["content"]
    assert "Previous routing fields" not in repair_message
    assert "MUST be preserved exactly" not in repair_message


@pytest.mark.asyncio
async def test_complete_json_does_not_retry_other_4xx_in_loose_mode() -> None:
    """A 400 without json_schema active is not retried as a schema fallback."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelResponseError) as exc_info:
        await client.complete_json("audit_agent", {})
    assert exc_info.value.reason == "provider_incompatible"
    assert len(requests) == 1
