from unittest.mock import patch

import httpx
import pytest

from APP.backend import config
from APP.backend.health_llm import (
    LLMClient,
    ModelUnavailableError,
    is_model_unavailable_error,
)


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "OK"}}]}


class _Client:
    def __init__(self):
        self.payload = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def post(self, _url, *, json, headers):
        self.payload = json
        self.headers = headers
        return _Response()


def test_all_configured_token_budgets_are_integers() -> None:
    for name in (
        "MAX_TOKENS",
        "CONTEXT_MANAGER_MAX_TOKENS",
        "COMPRESSION_MAX_TOKENS",
        "INFO_REFINER_MAX_TOKENS",
        "PLANNER_MAX_TOKENS",
        "EXECUTOR_MAX_TOKENS",
        "REVIEWER_MAX_TOKENS",
        "SESSION_TITLE_MAX_TOKENS",
    ):
        assert isinstance(getattr(config, name), int), name


def test_openai_compatible_client_normalizes_max_tokens_to_integer() -> None:
    transport = _Client()
    client = LLMClient("https://model.example/v1", "model", mode="local")

    with patch("APP.backend.health_llm.httpx.Client", return_value=transport):
        message = client.chat_message(
            [{"role": "user", "content": "hello"}],
            max_tokens=2048.0,
        )

    assert message["content"] == "OK"
    assert transport.payload["max_tokens"] == 2048
    assert isinstance(transport.payload["max_tokens"], int)


def test_upload_client_preserves_provider_session_across_calls():
    transport = _Client()
    client = LLMClient("https://opencode.ai/zen/go/v1", "model", api_key="test", mode="local")
    with patch("APP.backend.health_llm.httpx.Client", return_value=transport):
        client.chat([])
        session = transport.headers["x-opencode-session"]
        client.chat([])
    assert transport.headers["x-opencode-session"] == session
    assert transport.headers["Authorization"] == "Bearer test"
    assert LLMClient("https://example.com/v1", "model", mode="local")._openai_headers() == {}


class _UnavailableResponse:
    def __init__(self, status_code):
        request = httpx.Request("POST", "https://model.example/v1/chat/completions")
        self._response = httpx.Response(status_code, request=request)

    def raise_for_status(self):
        self._response.raise_for_status()

    def json(self):
        return {}


class _UnavailableClient(_Client):
    def __init__(self, status_code):
        super().__init__()
        self.status_code = status_code

    def post(self, _url, *, json, headers):
        return _UnavailableResponse(self.status_code)


@pytest.mark.parametrize("status_code", [429, 503])
def test_rate_limited_or_gateway_failure_becomes_model_unavailable(status_code):
    client = LLMClient("https://model.example/v1", "model", api_key="test", mode="local")
    transport = _UnavailableClient(status_code)

    with patch("APP.backend.health_llm.httpx.Client", return_value=transport):
        with pytest.raises(ModelUnavailableError) as error:
            client.chat([{"role": "user", "content": "hello"}])

    assert error.value.status_code == status_code


def test_business_status_error_is_not_reported_as_model_unavailable():
    client = LLMClient("https://model.example/v1", "model", api_key="test", mode="local")
    transport = _UnavailableClient(400)

    with patch("APP.backend.health_llm.httpx.Client", return_value=transport):
        with pytest.raises(httpx.HTTPStatusError) as error:
            client.chat([{"role": "user", "content": "hello"}])

    assert not isinstance(error.value, ModelUnavailableError)


def test_transport_failures_keep_their_type_so_callers_can_retry():
    """连接失败与超时不能被翻译：调用方依赖 httpx 类型决定是否重试。"""
    client = LLMClient("https://model.example/v1", "model", api_key="test", mode="local")

    class _TimeoutClient(_Client):
        def post(self, _url, *, json, headers):
            raise httpx.ConnectTimeout("timed out")

    with patch("APP.backend.health_llm.httpx.Client", return_value=_TimeoutClient()):
        with pytest.raises(httpx.ConnectTimeout):
            client.chat([{"role": "user", "content": "hello"}])


def test_model_unavailability_classifier_covers_transport_and_gateway_failures():
    request = httpx.Request("POST", "https://model.example/v1/chat/completions")

    assert is_model_unavailable_error(
        ModelUnavailableError("gateway", status_code=429)
    )
    assert is_model_unavailable_error(
        httpx.HTTPStatusError(
            "HTTP 429", request=request, response=httpx.Response(429, request=request)
        )
    )
    assert is_model_unavailable_error(httpx.ConnectTimeout("timed out"))
    assert is_model_unavailable_error(httpx.ConnectError("refused"))
    assert not is_model_unavailable_error(
        httpx.HTTPStatusError(
            "HTTP 400", request=request, response=httpx.Response(400, request=request)
        )
    )
    assert not is_model_unavailable_error(ValueError("bad content"))


def test_streaming_model_outage_becomes_model_unavailable():
    client = LLMClient("https://model.example/v1", "model", api_key="test", mode="local")

    class _UnavailableStream:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def raise_for_status(self):
            request = httpx.Request("POST", "https://model.example/v1/chat/completions")
            raise httpx.HTTPStatusError(
                "HTTP 429",
                request=request,
                response=httpx.Response(429, request=request),
            )

        def iter_lines(self):
            return iter(())

    class _StreamClient(_Client):
        def stream(self, _method, _url, *, json, headers):
            return _UnavailableStream()

    with patch("APP.backend.health_llm.httpx.Client", return_value=_StreamClient()):
        with pytest.raises(ModelUnavailableError):
            list(client.chat_stream([{"role": "user", "content": "hello"}]))

