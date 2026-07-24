from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, cast


PACKAGE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_ROOT.parent
REPOSITORY_ROOT = BACKEND_ROOT.parent

# The main backend remains authoritative for every model dependency. Values from
# config_new.py are deliberately not used as an alternative model stack.
CHAT_BASE_URL = (
    "https://llm-1nvjq1o5rj1bf5yi.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)
CHAT_MODEL = "qwen3.7-plus-2026-05-26"
EMBEDDING_BASE_URL = "https://api.siliconflow.cn/v1"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-4B"

DEFAULT_RUNTIME_ROOT = PACKAGE_ROOT / "runtime"
DEFAULT_FRONTEND_DIST_ROOT = REPOSITORY_ROOT / "frontend" / "llm" / "dist"
DEFAULT_QUESTION_VECTOR_STORE_ROOT = BACKEND_ROOT / "competition" / "vdb_store"
DEFAULT_KNOWLEDGE_VECTOR_STORE_ROOT = DEFAULT_QUESTION_VECTOR_STORE_ROOT
DEFAULT_KNOWLEDGE_HANDOFF_ROOT = (
    BACKEND_ROOT / "competition" / "知识星球视频知识库_前端交接包_2026-07-18"
)
DEFAULT_KNOWLEDGE_RUNTIME_ROOT = (
    DEFAULT_KNOWLEDGE_HANDOFF_ROOT / "知识库管理组件" / "runtime"
)
DEFAULT_BACKEND_HANDOFF_ROOT = (
    BACKEND_ROOT / "competition" / "backend-handoff-20260720"
)
DEFAULT_BACKEND_HANDOFF_RUNTIME_ROOT = DEFAULT_RUNTIME_ROOT / "frontend_backend"


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


def _environment_values(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    if environ is not None:
        return environ
    configured_file = os.environ.get("COMPETITION_ENV_FILE", "").strip()
    paths = (
        [Path(configured_file).expanduser()]
        if configured_file
        else [PACKAGE_ROOT / ".env", PACKAGE_ROOT / ".env.local"]
    )
    values: dict[str, str] = {}
    for path in paths:
        values.update(_load_dotenv(path))
    values.update(os.environ)
    return values


def _parse_bool(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw = values.get(name, "true" if default else "false").strip().lower()
    if raw in {"true", "1", "yes", "on"}:
        return True
    if raw in {"false", "0", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be a boolean")


def _parse_int(
    values: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int | None = None,
) -> int:
    try:
        value = int(values.get(name, str(default)))
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise SettingsError(f"{name} must be at least {minimum}")
    return value


def _parse_float(
    values: Mapping[str, str], name: str, default: float, *, minimum: float = 0
) -> float:
    try:
        value = float(values.get(name, str(default)))
    except ValueError as exc:
        raise SettingsError(f"{name} must be a number") from exc
    if value < minimum:
        raise SettingsError(f"{name} must be at least {minimum}")
    return value


def _parse_path(
    values: Mapping[str, str],
    name: str,
    default: Path,
    *,
    base: Path = BACKEND_ROOT,
) -> Path:
    path = Path(values.get(name, str(default))).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _parse_choice(
    values: Mapping[str, str], name: str, default: str, choices: set[str]
) -> str:
    value = values.get(name, default).strip().lower()
    if value not in choices:
        expected = ", ".join(sorted(choices))
        raise SettingsError(f"{name} must be one of: {expected}")
    return value


@dataclass(frozen=True)
class Settings:
    # Main application runtime.
    mode: Literal["stub", "live"] = "stub"
    execution_engine: Literal["langgraph", "legacy"] = "langgraph"
    api_host: str = "127.0.0.1"
    api_port: int = 7860
    runtime_root: Path = DEFAULT_RUNTIME_ROOT
    frontend_dist_root: Path = DEFAULT_FRONTEND_DIST_ROOT

    # Main model stack. These fields remain compatible with existing callers.
    chat_base_url: str = CHAT_BASE_URL
    chat_model: str = CHAT_MODEL
    embedding_base_url: str = EMBEDDING_BASE_URL
    embedding_model: str = EMBEDDING_MODEL
    embedding_mode: Literal["enabled", "disabled"] = "enabled"
    embedding_model_path: Path | None = None
    llm_timeout_seconds: float = 120.0

    # Knowledge and external assets.
    question_vector_store_root: Path = DEFAULT_QUESTION_VECTOR_STORE_ROOT
    knowledge_vector_store_root: Path = DEFAULT_KNOWLEDGE_VECTOR_STORE_ROOT
    knowledge_handoff_root: Path = DEFAULT_KNOWLEDGE_HANDOFF_ROOT
    knowledge_runtime_root: Path = DEFAULT_KNOWLEDGE_RUNTIME_ROOT
    knowledge_atlas_enabled: bool = True
    knowledge_atlas_asset_version: str = "2026-07-18"
    knowledge_atlas_data_root: Path = DEFAULT_KNOWLEDGE_HANDOFF_ROOT
    knowledge_atlas_video_root: Path = DEFAULT_KNOWLEDGE_HANDOFF_ROOT
    knowledge_atlas_contract_path: Path | None = None
    official_exam_data_dir: Path = DEFAULT_KNOWLEDGE_HANDOFF_ROOT

    # Transitional delivered-backend integration.
    backend_handoff_enabled: bool = False
    backend_handoff_root: Path = DEFAULT_BACKEND_HANDOFF_ROOT
    backend_handoff_runtime_root: Path = DEFAULT_BACKEND_HANDOFF_RUNTIME_ROOT
    backend_handoff_mysql_database: str = "competition_frontend"
    backend_handoff_secret_key: str = field(
        default="competition-local-development-key", repr=False
    )

    # Persistence and authentication. SQLite is parsed for the incoming feature
    # modules, but the main backend continues using its existing repositories in
    # this phase.
    use_sqlite: bool = False
    sqlite_path: Path = DEFAULT_RUNTIME_ROOT / "competition_app.sqlite3"
    database_url: str | None = field(default=None, repr=False)
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_database: str = "competition_app"
    auth_session_ttl_hours: int = 24 * 30
    auth_cookie_secure: bool = False
    admin_username: str = "admin"
    admin_email: str = "admin@sining.local"
    admin_default_password: str | None = field(default=None, repr=False)
    jwt_secret_key: str | None = field(default=None, repr=False)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 24 * 60

    # File, mail, image, and search configuration needed by selected incoming
    # business modules. Voice model configuration is intentionally omitted.
    upload_dir: Path = DEFAULT_RUNTIME_ROOT / "uploads"
    metadata_file: Path = DEFAULT_RUNTIME_ROOT / "file_metadata.json"
    markitdown_output_dir: Path = DEFAULT_RUNTIME_ROOT / "markitdown_output"
    markitdown_extract_timeout_seconds: int = 120
    max_text_length: int = 3000
    vision_api_base_url: str = ""
    vision_api_model: str = "qwen3-vl-flash"
    vision_api_timeout_seconds: int = 30
    mail_username: str = ""
    mail_from: str = "noreply@example.com"
    mail_port: int = 465
    mail_server: str = "smtp.qq.com"
    mail_starttls: bool = False
    mail_ssl_tls: bool = True
    exa_num_results: int = 3
    exa_content_char_limit: int = 500

    # Secrets are excluded from repr so errors and traces cannot leak them.
    dashscope_api_key: str | None = field(default=None, repr=False)
    siliconflow_api_key: str | None = field(default=None, repr=False)
    vision_api_key: str | None = field(default=None, repr=False)
    mail_password: str | None = field(default=None, repr=False)
    exa_api_key: str | None = field(default=None, repr=False)
    mineru_token: str | None = field(default=None, repr=False)
    mysql_password: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        values = _environment_values(environ)
        mode = _parse_choice(
            values, "COMPETITION_APP_MODE", "stub", {"stub", "live"}
        )
        execution_engine = _parse_choice(
            values,
            "COMPETITION_EXECUTION_ENGINE",
            "langgraph",
            {"langgraph", "legacy"},
        )
        if mode == "live":
            missing = [
                name
                for name in ("DASHSCOPE_API_KEY", "SILICONFLOW_API_KEY")
                if not values.get(name)
            ]
            if missing:
                raise SettingsError(
                    "Missing required environment variables: " + ", ".join(missing)
                )

        runtime_root = _parse_path(
            values, "RUNTIME_ROOT", DEFAULT_RUNTIME_ROOT, base=REPOSITORY_ROOT
        )
        knowledge_handoff_root = _parse_path(
            values,
            "KNOWLEDGE_HANDOFF_ROOT",
            DEFAULT_KNOWLEDGE_HANDOFF_ROOT,
        )
        knowledge_component = knowledge_handoff_root / "知识库管理组件"
        atlas_contract_raw = values.get("KNOWLEDGE_ATLAS_CONTRACT_PATH", "").strip()
        embedding_model_path_raw = values.get("EMBEDDING_MODEL_PATH", "").strip()

        return cls(
            mode=cast(Literal["stub", "live"], mode),
            execution_engine=cast(Literal["langgraph", "legacy"], execution_engine),
            api_host=values.get("API_HOST", "127.0.0.1"),
            api_port=_parse_int(values, "API_PORT", 7860, minimum=1),
            runtime_root=runtime_root,
            frontend_dist_root=_parse_path(
                values,
                "FRONTEND_DIST_ROOT",
                DEFAULT_FRONTEND_DIST_ROOT,
                base=REPOSITORY_ROOT,
            ),
            chat_base_url=values.get("CHAT_BASE_URL", CHAT_BASE_URL),
            chat_model=values.get("CHAT_MODEL", CHAT_MODEL),
            embedding_base_url=values.get("EMBEDDING_BASE_URL", EMBEDDING_BASE_URL),
            embedding_model=values.get("EMBEDDING_MODEL", EMBEDDING_MODEL),
            embedding_mode=cast(
                Literal["enabled", "disabled"],
                _parse_choice(
                    values,
                    "EMBEDDING_MODE",
                    "enabled",
                    {"enabled", "disabled"},
                ),
            ),
            embedding_model_path=(
                _parse_path(
                    {"EMBEDDING_MODEL_PATH": embedding_model_path_raw},
                    "EMBEDDING_MODEL_PATH",
                    Path(embedding_model_path_raw),
                )
                if embedding_model_path_raw
                else None
            ),
            llm_timeout_seconds=_parse_float(
                values, "LLM_TIMEOUT_SECONDS", 120.0, minimum=1.0
            ),
            question_vector_store_root=_parse_path(
                values,
                "QUESTION_VECTOR_STORE_ROOT",
                DEFAULT_QUESTION_VECTOR_STORE_ROOT,
            ),
            knowledge_vector_store_root=_parse_path(
                values,
                "KNOWLEDGE_VECTOR_STORE_ROOT",
                DEFAULT_KNOWLEDGE_VECTOR_STORE_ROOT,
            ),
            knowledge_handoff_root=knowledge_handoff_root,
            knowledge_runtime_root=_parse_path(
                values,
                "KNOWLEDGE_RUNTIME_ROOT",
                DEFAULT_KNOWLEDGE_RUNTIME_ROOT,
            ),
            knowledge_atlas_enabled=_parse_bool(
                values, "KNOWLEDGE_ATLAS_ENABLED", True
            ),
            knowledge_atlas_asset_version=values.get(
                "KNOWLEDGE_ATLAS_ASSET_VERSION", "2026-07-18"
            ),
            knowledge_atlas_data_root=_parse_path(
                values,
                "KNOWLEDGE_ATLAS_DATA_ROOT",
                knowledge_component / "data" / "backend_delivery",
            ),
            knowledge_atlas_video_root=_parse_path(
                values,
                "KNOWLEDGE_ATLAS_VIDEO_ROOT",
                knowledge_handoff_root / "bilibili_video_page" / "runtime",
            ),
            knowledge_atlas_contract_path=(
                _parse_path(
                    {"KNOWLEDGE_ATLAS_CONTRACT_PATH": atlas_contract_raw},
                    "KNOWLEDGE_ATLAS_CONTRACT_PATH",
                    Path(atlas_contract_raw),
                )
                if atlas_contract_raw
                else None
            ),
            official_exam_data_dir=_parse_path(
                values,
                "OFFICIAL_EXAM_DATA_DIR",
                knowledge_component
                / "data"
                / "backend_delivery"
                / "08_exam_learning_path_2025",
            ),
            backend_handoff_enabled=_parse_bool(
                values, "BACKEND_HANDOFF_ENABLED", False
            ),
            backend_handoff_root=_parse_path(
                values, "BACKEND_HANDOFF_ROOT", DEFAULT_BACKEND_HANDOFF_ROOT
            ),
            backend_handoff_runtime_root=_parse_path(
                values,
                "BACKEND_HANDOFF_RUNTIME_ROOT",
                DEFAULT_BACKEND_HANDOFF_RUNTIME_ROOT,
            ),
            backend_handoff_mysql_database=values.get(
                "BACKEND_HANDOFF_MYSQL_DATABASE", "competition_frontend"
            ),
            backend_handoff_secret_key=values.get(
                "BACKEND_HANDOFF_SECRET_KEY", "competition-local-development-key"
            ),
            use_sqlite=_parse_bool(values, "USE_SQLITE", False),
            sqlite_path=_parse_path(
                values,
                "SQLITE_PATH",
                runtime_root / "competition_app.sqlite3",
                base=runtime_root,
            ),
            database_url=values.get("DATABASE_URL") or None,
            mysql_host=values.get("MYSQL_HOST", "localhost"),
            mysql_port=_parse_int(values, "MYSQL_PORT", 3306, minimum=1),
            mysql_user=values.get("MYSQL_USER", "root"),
            mysql_database=values.get("MYSQL_DATABASE", "competition_app"),
            auth_session_ttl_hours=_parse_int(
                values, "AUTH_SESSION_TTL_HOURS", 24 * 30, minimum=1
            ),
            auth_cookie_secure=_parse_bool(values, "AUTH_COOKIE_SECURE", False),
            admin_username=values.get("ADMIN_USERNAME", "admin"),
            admin_email=values.get("ADMIN_EMAIL", "admin@sining.local"),
            admin_default_password=values.get("ADMIN_DEFAULT_PASSWORD") or None,
            jwt_secret_key=values.get("SECRET_KEY") or None,
            jwt_algorithm=values.get("ALGORITHM", "HS256"),
            access_token_expire_minutes=_parse_int(
                values, "ACCESS_TOKEN_EXPIRE_MINUTES", 24 * 60, minimum=1
            ),
            upload_dir=_parse_path(
                values, "UPLOAD_DIR", runtime_root / "uploads", base=runtime_root
            ),
            metadata_file=_parse_path(
                values,
                "METADATA_FILE",
                runtime_root / "file_metadata.json",
                base=runtime_root,
            ),
            markitdown_output_dir=_parse_path(
                values,
                "MARKITDOWN_OUTPUT_DIR",
                runtime_root / "markitdown_output",
                base=runtime_root,
            ),
            markitdown_extract_timeout_seconds=_parse_int(
                values, "MARKITDOWN_EXTRACT_TIMEOUT_SECONDS", 120, minimum=1
            ),
            max_text_length=_parse_int(values, "MAX_TEXT_LENGTH", 3000, minimum=1),
            vision_api_base_url=values.get("VISION_API_BASE_URL", ""),
            vision_api_model=values.get("VISION_API_MODEL", "qwen3-vl-flash"),
            vision_api_timeout_seconds=_parse_int(
                values, "VISION_API_TIMEOUT_SECONDS", 30, minimum=1
            ),
            mail_username=values.get("MAIL_USERNAME", ""),
            mail_from=values.get("MAIL_FROM", "noreply@example.com"),
            mail_port=_parse_int(values, "MAIL_PORT", 465, minimum=1),
            mail_server=values.get("MAIL_SERVER", "smtp.qq.com"),
            mail_starttls=_parse_bool(values, "MAIL_STARTTLS", False),
            mail_ssl_tls=_parse_bool(values, "MAIL_SSL_TLS", True),
            exa_num_results=_parse_int(values, "EXA_NUM_RESULTS", 3, minimum=1),
            exa_content_char_limit=_parse_int(
                values, "EXA_CONTENT_CHAR_LIMIT", 500, minimum=1
            ),
            dashscope_api_key=values.get("DASHSCOPE_API_KEY") or None,
            siliconflow_api_key=values.get("SILICONFLOW_API_KEY") or None,
            vision_api_key=values.get("VISION_API_KEY") or None,
            mail_password=values.get("MAIL_PASSWORD") or None,
            exa_api_key=values.get("EXA_API_KEY") or None,
            mineru_token=(
                values.get("MINERU_TOKEN")
                or values.get("MINERU_API_KEY")
                or None
            ),
            mysql_password=values.get("MYSQL_PASSWORD") or None,
        )
