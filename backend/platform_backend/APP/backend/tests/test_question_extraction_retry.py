import json
from unittest.mock import Mock, patch

import httpx
import pytest

from APP.backend import question_workspace_service as service
from APP.backend.health_llm import LLMClient


def payload(stem="测试题"):
    return json.dumps({"items": [{"stem": stem, "answer": "A", "options": ["A. 望诊"]}]})


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ConnectTimeout])
def test_retries_connection_failure_with_identical_provider_request(error_type):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            raise error_type("test connection failure", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": payload()}}]})

    client = LLMClient("https://opencode.ai/zen/v1", "model", api_key="test-only", mode="local")
    http_client = httpx.Client
    with patch.object(service, "build_llm_client", return_value=client) as build, \
         patch("APP.backend.config.LLM_MODE", "local"), \
         patch("APP.backend.health_llm.httpx.Client", side_effect=lambda **kw: http_client(
             transport=httpx.MockTransport(handler), **kw)), \
         patch("time.sleep") as pause:
        rows = service._llm_extract_questions("测试材料")
    assert len(rows) == 1
    assert build.call_count == 1
    assert len(requests) == 3
    assert pause.call_count == 2
    assert len({str(r.url) for r in requests}) == 1
    assert len({r.content for r in requests}) == 1
    assert len({r.headers["x-opencode-session"] for r in requests}) == 1
    assert all(r.headers["authorization"] == "Bearer test-only" for r in requests)


def test_exhausted_connection_failure_is_not_swallowed():
    error = httpx.ConnectError("test-only")
    client = Mock()
    client.chat.side_effect = error
    with patch.object(service, "build_llm_client", return_value=client), \
         patch("APP.backend.config.LLM_MODE", "local"), \
         patch("time.sleep") as pause:
        with pytest.raises(httpx.ConnectError) as raised:
            service._llm_extract_questions("测试材料")
    assert raised.value is error
    assert client.chat.call_count == 3
    assert pause.call_count == 2


@pytest.mark.parametrize("error", [
    httpx.ReadTimeout("test-only"),
    httpx.WriteError("test-only"),
    httpx.PoolTimeout("test-only"),
    httpx.HTTPStatusError("test-only", request=httpx.Request("POST", "https://example.com"),
                          response=httpx.Response(401)),
    httpx.HTTPStatusError("test-only", request=httpx.Request("POST", "https://example.com"),
                          response=httpx.Response(503)),
])
def test_non_connection_failures_are_not_retried(error):
    client = Mock()
    client.chat.side_effect = error
    with patch.object(service, "build_llm_client", return_value=client), \
         patch("APP.backend.config.LLM_MODE", "local"):
        with pytest.raises(type(error)):
            service._llm_extract_questions("测试材料")
    assert client.chat.call_count == 1


def test_invalid_json_does_not_repeat_model_request():
    client = Mock()
    client.chat.return_value = "not json"
    with patch.object(service, "build_llm_client", return_value=client), \
         patch("APP.backend.config.LLM_MODE", "local"):
        with pytest.raises(ValueError):
            service._llm_extract_questions("测试材料")
    assert client.chat.call_count == 1


def test_retry_only_current_chunk_not_completed_chunks():
    client = Mock()
    client.chat.side_effect = [payload("第一题"), httpx.ConnectError("test-only"), payload("第二题")]
    with patch.object(service, "build_llm_client", return_value=client), \
         patch("APP.backend.config.LLM_MODE", "local"), \
         patch("time.sleep"):
        rows = service._llm_extract_questions("a" * 12000 + "\n\n第二部分")
    assert [r["stem"] for r in rows] == ["第一题", "第二题"]
    calls = client.chat.call_args_list
    assert calls[0].args[0][1]["content"] == "a" * 12000
    assert calls[1] == calls[2]
