from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping


CHAT_BASE_URL = (
    "https://llm-1nvjq1o5rj1bf5yi.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)
CHAT_MODEL = "deepseek-v4-flash"
EMBEDDING_BASE_URL = "https://api.siliconflow.cn/v1"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-4B"
DEFAULT_QUESTION_VECTOR_STORE_ROOT = Path(__file__).resolve().parents[1] / "competition" / "vdb_store"
DEFAULT_KNOWLEDGE_VECTOR_STORE_ROOT = DEFAULT_QUESTION_VECTOR_STORE_ROOT
DEFAULT_KNOWLEDGE_HANDOFF_ROOT = (
    Path(__file__).resolve().parents[1]
    / "competition"
    / "知识星球视频知识库_前端交接包_2026-07-18"
)
DEFAULT_KNOWLEDGE_RUNTIME_ROOT = (
    DEFAULT_KNOWLEDGE_HANDOFF_ROOT / "知识库管理组件" / "runtime"
)
DEFAULT_BACKEND_HANDOFF_ROOT = (
    Path(__file__).resolve().parents[1]
    / "competition"
    / "backend-handoff-20260720"
)
DEFAULT_BACKEND_HANDOFF_RUNTIME_ROOT = (
    Path(__file__).resolve().parent / "runtime" / "frontend_backend"
)


class SettingsError(ValueError):
    """Raised when required application configuration is missing or invalid."""


def _load_dotenv(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE entries without exporting them to os.environ."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True)
class Settings:
    mode: Literal["stub", "live"] = "stub"
    execution_engine: Literal["langgraph", "legacy"] = "langgraph"
    chat_base_url: str = CHAT_BASE_URL
    chat_model: str = CHAT_MODEL
    embedding_base_url: str = EMBEDDING_BASE_URL
    embedding_model: str = EMBEDDING_MODEL
    question_vector_store_root: Path = DEFAULT_QUESTION_VECTOR_STORE_ROOT
    knowledge_vector_store_root: Path = DEFAULT_KNOWLEDGE_VECTOR_STORE_ROOT
    knowledge_handoff_root: Path = DEFAULT_KNOWLEDGE_HANDOFF_ROOT
    knowledge_runtime_root: Path = DEFAULT_KNOWLEDGE_RUNTIME_ROOT
    backend_handoff_enabled: bool = False
    backend_handoff_root: Path = DEFAULT_BACKEND_HANDOFF_ROOT
    backend_handoff_runtime_root: Path = DEFAULT_BACKEND_HANDOFF_RUNTIME_ROOT
    backend_handoff_mysql_database: str = "competition_frontend"
    backend_handoff_secret_key: str = "competition-local-development-key"
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_database: str = "competition_app"
    auth_session_ttl_hours: int = 24 * 30
    auth_cookie_secure: bool = False
    dashscope_api_key: str | None = field(default=None, repr=False)
    siliconflow_api_key: str | None = field(default=None, repr=False)
    exa_api_key: str | None = field(default=None, repr=False)
    mysql_password: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        if environ is None:
            dotenv_values = _load_dotenv(Path(__file__).with_name(".env.example"))
            values = {**dotenv_values, **os.environ}
        else:
            values = environ
        mode = values.get("COMPETITION_APP_MODE", "stub").strip().lower()
        if mode not in {"stub", "live"}:
            raise SettingsError("COMPETITION_APP_MODE must be 'stub' or 'live'")
        execution_engine = values.get(
            "COMPETITION_EXECUTION_ENGINE", "langgraph"
        ).strip().lower()
        if execution_engine not in {"langgraph", "legacy"}:
            raise SettingsError(
                "COMPETITION_EXECUTION_ENGINE must be 'langgraph' or 'legacy'"
            )

        required = []
        if mode == "live":
            required.extend(("DASHSCOPE_API_KEY", "SILICONFLOW_API_KEY"))
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise SettingsError(f"Missing required environment variables: {', '.join(missing)}")

        try:
            mysql_port = int(values.get("MYSQL_PORT", "3306"))
        except ValueError as exc:
            raise SettingsError("MYSQL_PORT must be an integer") from exc
        try:
            auth_session_ttl_hours = int(
                values.get("AUTH_SESSION_TTL_HOURS", str(24 * 30))
            )
        except ValueError as exc:
            raise SettingsError("AUTH_SESSION_TTL_HOURS must be an integer") from exc
        if auth_session_ttl_hours <= 0:
            raise SettingsError("AUTH_SESSION_TTL_HOURS must be positive")
        auth_cookie_secure = values.get("AUTH_COOKIE_SECURE", "false").strip().lower()
        if auth_cookie_secure not in {"true", "false", "1", "0", "yes", "no"}:
            raise SettingsError("AUTH_COOKIE_SECURE must be a boolean")
        backend_handoff_enabled = values.get(
            "BACKEND_HANDOFF_ENABLED", "false"
        ).strip().lower()
        if backend_handoff_enabled not in {
            "true", "false", "1", "0", "yes", "no"
        }:
            raise SettingsError("BACKEND_HANDOFF_ENABLED must be a boolean")

        return cls(
            mode=mode,
            execution_engine=execution_engine,
            chat_base_url=values.get("CHAT_BASE_URL", CHAT_BASE_URL),
            chat_model=values.get("CHAT_MODEL", CHAT_MODEL),
            embedding_base_url=values.get("EMBEDDING_BASE_URL", EMBEDDING_BASE_URL),
            embedding_model=values.get("EMBEDDING_MODEL", EMBEDDING_MODEL),
            question_vector_store_root=Path(
                values.get("QUESTION_VECTOR_STORE_ROOT", str(DEFAULT_QUESTION_VECTOR_STORE_ROOT))
            ),
            knowledge_vector_store_root=Path(
                values.get("KNOWLEDGE_VECTOR_STORE_ROOT", str(DEFAULT_KNOWLEDGE_VECTOR_STORE_ROOT))
            ),
            knowledge_handoff_root=Path(
                values.get("KNOWLEDGE_HANDOFF_ROOT", str(DEFAULT_KNOWLEDGE_HANDOFF_ROOT))
            ),
            knowledge_runtime_root=Path(
                values.get("KNOWLEDGE_RUNTIME_ROOT", str(DEFAULT_KNOWLEDGE_RUNTIME_ROOT))
            ),
            backend_handoff_enabled=backend_handoff_enabled in {"true", "1", "yes"},
            backend_handoff_root=Path(
                values.get("BACKEND_HANDOFF_ROOT", str(DEFAULT_BACKEND_HANDOFF_ROOT))
            ),
            backend_handoff_runtime_root=Path(
                values.get(
                    "BACKEND_HANDOFF_RUNTIME_ROOT",
                    str(DEFAULT_BACKEND_HANDOFF_RUNTIME_ROOT),
                )
            ),
            backend_handoff_mysql_database=values.get(
                "BACKEND_HANDOFF_MYSQL_DATABASE", "competition_frontend"
            ),
            backend_handoff_secret_key=values.get(
                "BACKEND_HANDOFF_SECRET_KEY", "competition-local-development-key"
            ),
            mysql_host=values.get("MYSQL_HOST", "localhost"),
            mysql_port=mysql_port,
            mysql_user=values.get("MYSQL_USER", "root"),
            mysql_database=values.get("MYSQL_DATABASE", "competition_app"),
            auth_session_ttl_hours=auth_session_ttl_hours,
            auth_cookie_secure=auth_cookie_secure in {"true", "1", "yes"},
            dashscope_api_key=values.get("DASHSCOPE_API_KEY"),
            siliconflow_api_key=values.get("SILICONFLOW_API_KEY"),
            exa_api_key=values.get("EXA_API_KEY"),
            mysql_password=values.get("MYSQL_PASSWORD"),
        )
