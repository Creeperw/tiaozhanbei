from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeModelCredentials:
    deepseek_api_key: str = ""
    siliconflow_api_key: str = ""
    mineru_api_token: str = ""


_MODEL_CREDENTIALS: ContextVar[RuntimeModelCredentials | None] = ContextVar(
    "competition_model_credentials",
    default=None,
)


def bind_model_credentials(
    credentials: RuntimeModelCredentials | None,
) -> Token:
    return _MODEL_CREDENTIALS.set(credentials)


def reset_model_credentials(token: Token) -> None:
    _MODEL_CREDENTIALS.reset(token)


def current_model_credentials() -> RuntimeModelCredentials | None:
    return _MODEL_CREDENTIALS.get()
