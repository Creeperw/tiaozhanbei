import asyncio
import json

import httpx
import pytest

from competition_app.llm.openai_compatible import OpenAICompatibleChatModel, ModelResponseError
from competition_app.llm.provider_session import (
    bind_provider_session, current_provider_session, reset_provider_session,
)


def model(handler, base_url="https://opencode.ai/zen/go/v1"):
    return OpenAICompatibleChatModel(
        base_url, "secret-value", "deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_provider_header_stable_across_agents_and_retries(stream):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, json={"error": {"type": "overloaded_error"}})
        if stream:
            chunk = json.dumps({"choices": [{"delta": {"content": "ok"}}]})
            return httpx.Response(200, text=f"data: {chunk}\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = model(handler)
    token = bind_provider_session("THREAD_private_identity")
    try:
        for role in ("knowledge_base_agent", "expert_agent"):
            assert await client.complete_text(role, {}, on_delta=(lambda _: None) if stream else None) == "ok"
    finally:
        reset_provider_session(token)
    ids = {r.headers["x-opencode-session"] for r in requests}
    assert len(ids) == 1
    assert "private_identity" not in next(iter(ids))
    assert all(r.headers["user-agent"].startswith("ShizhenTrainingAssistant/") for r in requests)
    assert current_provider_session() is None


@pytest.mark.asyncio
async def test_concurrent_sessions_and_resume_identity():
    async def handler(request):
        await asyncio.sleep(0)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": request.headers["x-opencode-session"],
        }}]})

    client = model(handler)

    async def run(thread):
        token = bind_provider_session(thread)
        try:
            return await client.complete_text("expert_agent", {})
        finally:
            reset_provider_session(token)

    a, b = await asyncio.gather(run("thread-a"), run("thread-b"))
    assert a != b
    assert await run("thread-a") == a
    assert current_provider_session() is None


@pytest.mark.asyncio
async def test_unbound_calls_not_shared_and_other_hosts_unchanged():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = model(handler)
    await client.complete_text("expert_agent", {})
    await client.complete_text("expert_agent", {})
    assert requests[0].headers["x-opencode-session"] != requests[1].headers["x-opencode-session"]
    other = model(handler, "https://opencode.ai.example.test/v1")
    await other.complete_text("expert_agent", {})
    assert "x-opencode-session" not in requests[-1].headers
    assert requests[-1].headers["user-agent"].startswith("Mozilla/")


@pytest.mark.asyncio
async def test_missing_session_error_safe_and_no_schema_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {
            "type": "MissingSessionID", "message": "echo secret-value private prompt",
        }})

    client = model(handler)
    with pytest.raises(ModelResponseError) as caught:
        await client.complete_json("planner_agent", {"output_schema": {
            "type": "object", "properties": {"ok": {"type": "boolean"}},
        }})
    assert caught.value.reason == "missing_provider_session"
    assert "MissingSessionID" in str(caught.value)
    assert "secret-value" not in str(caught.value)
    assert "private prompt" not in str(client.last_error_details)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [[], {"error": []}, {"error": {"type": {}}},
                                     {"error": {"type": "secret-value"}}])
def test_untrusted_error_codes_are_not_retained(body):
    assert OpenAICompatibleChatModel._provider_error_code(httpx.Response(400, json=body)) is None