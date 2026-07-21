from unittest.mock import patch

from APP.backend import config
from APP.backend.health_llm import LLMClient


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
