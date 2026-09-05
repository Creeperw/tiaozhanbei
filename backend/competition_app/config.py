from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, cast

from competition_app.asset_layout import AssetLayout, resolve_platform_backend_root
from competition_app.runtime.debug_trace import (
    DEFAULT_DEBUG_TRACE_MAX_BYTES,
    DEFAULT_DEBUG_TRACE_ROTATE_BYTES,
    parse_debug_trace_run_ids,
)
from competition_app.contracts.execution import DEFAULT_PROVIDER_TIMEOUT_SECONDS


PACKAGE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_ROOT.parent
REPOSITORY_ROOT = BACKEND_ROOT.parent

# The main backend remains authoritative for every model dependency. Values from
# config_new.py are deliberately not used as an alternative model stack.
CHAT_BASE_URL = (
    "https://llm-298mleun258tyc3o.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)
CHAT_MODELS = (
    "qwen3.7-flash",
    "qwen3.7-max-preview",
    "glm-5.2",
    "qwen3.7-flash-2026-07-15",
    "qwen-plus",
)
CHAT_MODEL = CHAT_MODELS[0]
EMBEDDING_BASE_URL = "https://api.siliconflow.cn/v1"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-4B"

DEFAULT_RUNTIME_ROOT = PACKAGE_ROOT / "runtime"
DEFAULT_FRONTEND_DIST_ROOT = REPOSITORY_ROOT / "frontend" / "llm" / "dist"
DEFAULT_QUESTION_VECTOR_STORE_ROOT = BACKEND_ROOT / "competition" / "vdb_store"
DEFAULT_KNOWLEDGE_VECTOR_STORE_ROOT = DEFAULT_QUESTION_VECTOR_STORE_ROOT
DEFAULT_TEXTBOOK_PDF_ROOT = BACKEND_ROOT / "competition" / "textbook_pdfs"
DEFAULT_TREEKG_ROOT = REPOSITORY_ROOT / "TreeKG-main"
DEFAULT_TEXTBOOK_PDF_CATALOG_PATH = (
    PACKAGE_ROOT / "data" / "textbook_pdfs" / "catalog.v1.json"
)
DEFAULT_KNOWLEDGE_HANDOFF_ROOT = (
    REPOSITORY_ROOT / "assets" / "knowledge" / "releases" / "2026-07-18"
)
DEFAULT_KNOWLEDGE_RUNTIME_ROOT = (
    DEFAULT_RUNTIME_ROOT / "knowledge"
)
DEFAULT_BACKEND_HANDOFF_ROOT = (
    BACKEND_ROOT / "platform_backend"
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
    # An explicitly exported legacy CHAT_MODEL must not be shadowed by a
    # CHAT_MODELS value loaded only from a dotenv file.
    if "CHAT_MODEL" in os.environ and "CHAT_MODELS" not in os.environ:
        values.pop("CHAT_MODELS", None)
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
    configured = values.get(name)
    raw_value = (
        str(default)
        if configured is None or not str(configured).strip()
        else str(configured).strip()
    )
    # Keep POSIX absolute paths stable when configuration tests or deployment
    # tooling inspect them from Windows.
    if raw_value.startswith("/") and os.name == "nt":
        return Path(raw_value)
    path = Path(raw_value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _parse_choice(
    values: Mapping[str, str], name: str, default: str, choices: set[str]
) -> str:
    value = values.get(name, default).strip().lower()
    if value not in choices:
        expected = ", ".join(sorted(choices))
        raise SettingsError(f"{name} must be one of: {expected}")
    return value


def _parse_chat_models(values: Mapping[str, str]) -> tuple[str, ...]:
    raw = values.get("CHAT_MODELS", "").strip()
    if not raw:
        configured_model = values.get("CHAT_MODEL", "").strip()
        return (configured_model,) if configured_model else CHAT_MODELS
    models = tuple(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))
    if not models:
        raise SettingsError("CHAT_MODELS must contain at least one model name")
    return models


def _parse_llm_api_keys(values: Mapping[str, str]) -> tuple[str, ...]:
    """Return the primary key followed by unique configured fallback keys.

    The unified LLM_API_KEY is authoritative. Legacy per-provider variables
    (SILICONFLOW_API_KEY / DASHSCOPE_API_KEY / DEEPSEEK_API_KEY) are honoured
    only as a fallback so existing deployments keep working unchanged.
    """

    primary = (
        values.get("LLM_API_KEY", "").strip()
        or values.get("SILICONFLOW_API_KEY", "").strip()
        or values.get("DASHSCOPE_API_KEY", "").strip()
        or values.get("DEEPSEEK_API_KEY", "").strip()
    )
    fallback_raw = (
        values.get("LLM_API_KEYS", "").strip()
        or values.get("SILICONFLOW_API_KEYS", "").strip()
    )
    candidates = [primary, *re.split(r"[,;\s]+", fallback_raw)]
    return tuple(dict.fromkeys(item.strip() for item in candidates if item.strip()))


def _required_chat_api_key_name() -> str:
    """The unified chat API key variable name used in live-mode validation."""

    return "LLM_API_KEY"


@dataclass(frozen=True)
class Settings:
    # Main application runtime.
    mode: Literal["stub", "live"] = "stub"
    execution_engine: Literal["langgraph", "legacy"] = "langgraph"
    api_host: str = "127.0.0.1"
    api_port: int = 7860
    runtime_root: Path = DEFAULT_RUNTIME_ROOT
    frontend_dist_root: Path = DEFAULT_FRONTEND_DIST_ROOT
    asset_root: Path = REPOSITORY_ROOT / "assets"
    asset_manifest_path: Path | None = None
    asset_release_id: str = "2026-07-18"

    # Main model stack. These fields remain compatible with existing callers.
    chat_base_url: str = CHAT_BASE_URL
    chat_model: str = CHAT_MODEL
    chat_models: tuple[str, ...] = CHAT_MODELS
    embedding_base_url: str = EMBEDDING_BASE_URL
    embedding_model: str = EMBEDDING_MODEL
    embedding_mode: Literal["enabled", "disabled"] = "enabled"
    embedding_model_path: Path | None = None
    # 单次模型调用总时长护栏（思考模式模型持续推流时相邻读取不超时，
    # 此护栏防单次调用无限挂起）。live 模式由 LLM_TIMEOUT_SECONDS 覆盖，
    # 未配置时回退 1800s（与 .env.local 一致）。
    llm_timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS

    # Server-local diagnostic capture.  It is opt-in, bounded, and deliberately
    # separate from public SSE, snapshots, and business persistence.
    debug_trace_enabled: bool = False
    debug_trace_root: Path = DEFAULT_RUNTIME_ROOT / "debug_traces"
    debug_trace_max_bytes: int = DEFAULT_DEBUG_TRACE_MAX_BYTES
    debug_trace_rotate_bytes: int = DEFAULT_DEBUG_TRACE_ROTATE_BYTES
    debug_trace_flush: bool = True
    debug_trace_run_ids: tuple[str, ...] = ()
    debug_trace_allow_live: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.question_rerank_reject_threshold <= 1.0:
            raise SettingsError("question_rerank_reject_threshold must be in [0, 1]")
        if not 0.0 <= self.question_rerank_eligible_threshold <= 1.0:
            raise SettingsError("question_rerank_eligible_threshold must be in [0, 1]")
        if self.question_rerank_reject_threshold > self.question_rerank_eligible_threshold:
            raise SettingsError(
                "question_rerank_reject_threshold must not exceed eligible threshold"
            )
        if self.debug_trace_max_bytes < 1:
            raise SettingsError("debug_trace_max_bytes must be positive")
        if self.debug_trace_rotate_bytes < 1:
            raise SettingsError("debug_trace_rotate_bytes must be positive")
        if self.debug_trace_rotate_bytes > self.debug_trace_max_bytes:
            raise SettingsError(
                "debug_trace_rotate_bytes must not exceed debug_trace_max_bytes"
            )
        normalized_models = tuple(
            dict.fromkeys(str(item).strip() for item in self.chat_models if str(item).strip())
        )
        if not normalized_models:
            raise SettingsError("chat_models must contain at least one model name")
        if self.chat_model != normalized_models[0]:
            if self.chat_models == CHAT_MODELS:
                compatibility_model = str(self.chat_model).strip()
                if not compatibility_model:
                    raise SettingsError("chat_model must contain a model name")
                normalized_models = (compatibility_model,)
            object.__setattr__(self, "chat_models", normalized_models)
            object.__setattr__(self, "chat_model", normalized_models[0])
        elif normalized_models != self.chat_models:
            object.__setattr__(self, "chat_models", normalized_models)

    @property
    def knowledge_release_root(self) -> Path:
        """Canonical read-only knowledge asset release root."""

        return self.knowledge_handoff_root

    @property
    def platform_backend_root(self) -> Path:
        """Canonical integrated business backend root."""

        return self.backend_handoff_root

    # Knowledge and external assets.
    question_vector_store_root: Path = DEFAULT_QUESTION_VECTOR_STORE_ROOT
    knowledge_vector_store_root: Path = DEFAULT_KNOWLEDGE_VECTOR_STORE_ROOT
    knowledge_handoff_root: Path = DEFAULT_KNOWLEDGE_HANDOFF_ROOT
    knowledge_runtime_root: Path = DEFAULT_KNOWLEDGE_RUNTIME_ROOT
    knowledge_atlas_enabled: bool = True
    knowledge_atlas_asset_version: str = "2026-07-18"
    knowledge_atlas_data_root: Path = DEFAULT_KNOWLEDGE_HANDOFF_ROOT
    knowledge_atlas_video_root: Path = DEFAULT_KNOWLEDGE_HANDOFF_ROOT
    knowledge_atlas_chapter_root: Path = REPOSITORY_ROOT / "assets" / "knowledge-atlas" / "chapters" / "releases" / "2026-07-22"
    knowledge_atlas_contract_path: Path | None = None
    official_exam_data_dir: Path = DEFAULT_KNOWLEDGE_HANDOFF_ROOT
    textbook_pdf_root: Path = DEFAULT_TEXTBOOK_PDF_ROOT
    textbook_pdf_catalog_path: Path = DEFAULT_TEXTBOOK_PDF_CATALOG_PATH
    treekg_root: Path = DEFAULT_TREEKG_ROOT
    treekg_python: str = ""
    treekg_api_key: str | None = field(default=None, repr=False)
    treekg_api_base: str = "https://api.deepseek.com"
    treekg_model_name: str = "deepseek-v4-flash"

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
    # Explicitly disabled evaluation-only Agent-output fault injection.  The
    # token is never forwarded to model context or learner-facing SSE.
    accountability_evaluation_enabled: bool = False
    accountability_evaluation_token: str | None = field(default=None, repr=False)
    # Failure-driven evolution is isolated from the learner workflow by
    # default. Governance may be enabled without activating runtime rules.
    evolution_enabled: bool = False
    evolution_rules_enabled: bool = False
    evolution_data_root: Path = DEFAULT_RUNTIME_ROOT / "evolution"
    evolution_min_cases: int = 3
    evolution_min_executions: int = 2
    evolution_min_high_trust: int = 2
    preference_training_enabled: bool = False
    preference_training_allow_trl_dpo: bool = False
    admin_username: str = "admin"
    admin_email: str = "admin@sining.local"
    admin_default_password: str | None = field(default=None, repr=False)
    jwt_secret_key: str | None = field(default=None, repr=False)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 24 * 60

    # File, mail, image, and search configuration needed by selected incoming
    # business modules. Voice model configuration is intentionally omitted.
    upload_dir: Path = DEFAULT_RUNTIME_ROOT / "uploads"
    avatar_dir: Path = DEFAULT_RUNTIME_ROOT / "profile_avatars"
    metadata_file: Path = DEFAULT_RUNTIME_ROOT / "file_metadata.json"
    markitdown_output_dir: Path = DEFAULT_RUNTIME_ROOT / "markitdown_output"
    markitdown_extract_timeout_seconds: int = 120
    max_text_length: int = 3000
    vision_api_base_url: str = ""
    vision_api_model: str = "Qwen/Qwen3-VL-8B-Instruct"
    vision_api_timeout_seconds: int = 30
    mail_username: str = ""
    mail_from: str = "noreply@example.com"
    mail_port: int = 465
    mail_server: str = "smtp.qq.com"
    mail_starttls: bool = False
    mail_ssl_tls: bool = True
    exa_num_results: int = 3
    exa_content_char_limit: int = 500

    # Knowledge base retrieval tuning. All values are overridable via env vars
    # so retrieval behavior can be tuned without code changes.
    knowledge_supplement_max_rounds: int = 2
    knowledge_supplement_max_queries: int = 3
    knowledge_supplement_query_max_length: int = 200
    knowledge_retrieval_exa_limit: int = 3
    knowledge_retrieval_textbook_limit: int = 5
    question_fusion_strategy: Literal["legacy_max", "rrf_v1"] = "rrf_v1"
    question_rrf_k: int = 60
    question_rerank_mode: Literal["disabled", "shadow", "sort", "gate"] = "disabled"
    question_rerank_base_url: str = ""
    question_rerank_model: str = "Qwen/Qwen3-Reranker-8B"
    question_rerank_top_n: int = 30
    question_rerank_batch_size: int = 30
    question_rerank_timeout_seconds: float = 30.0
    question_rerank_eligible_threshold: float = 0.65
    question_rerank_reject_threshold: float = 0.30

    # Secrets are excluded from repr so errors and traces cannot leak them.
    llm_api_key: str | None = field(default=None, repr=False)
    llm_api_keys: tuple[str, ...] = field(default=(), repr=False)
    embedding_api_key: str | None = field(default=None, repr=False)
    question_rerank_api_key: str | None = field(default=None, repr=False)
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
            chat_key_name = _required_chat_api_key_name()
            # chat key：LLM_API_KEY 优先；旧部署回退到 SILICONFLOW_API_KEY。
            # embedding key：EMBEDDING_API_KEY 优先；旧部署回退到 SILICONFLOW_API_KEY。
            missing = []
            if not values.get(chat_key_name) and not values.get("SILICONFLOW_API_KEY"):
                missing.append(chat_key_name)
            if not values.get("EMBEDDING_API_KEY") and not values.get("SILICONFLOW_API_KEY"):
                missing.append("EMBEDDING_API_KEY")
            if missing:
                raise SettingsError(
                    "Missing required environment variables: "
                    + ", ".join(missing)
                )

        debug_trace_enabled = _parse_bool(
            values, "COMPETITION_DEBUG_TRACE_ENABLED", False
        )
        debug_trace_allow_live = _parse_bool(
            values, "COMPETITION_DEBUG_TRACE_ALLOW_LIVE", False
        )
        if mode == "live" and debug_trace_enabled and not debug_trace_allow_live:
            raise SettingsError(
                "COMPETITION_DEBUG_TRACE_ALLOW_LIVE must be true when "
                "COMPETITION_DEBUG_TRACE_ENABLED is true in live mode"
            )

        asset_layout = AssetLayout.resolve(
            values,
            repository_root=REPOSITORY_ROOT,
            backend_root=BACKEND_ROOT,
            package_runtime_root=DEFAULT_RUNTIME_ROOT,
        )
        runtime_root = asset_layout.runtime_root
        knowledge_handoff_root = asset_layout.knowledge_release_root
        knowledge_component = asset_layout.knowledge_component_root
        atlas_contract_raw = values.get("KNOWLEDGE_ATLAS_CONTRACT_PATH", "").strip()
        embedding_model_path_raw = values.get("EMBEDDING_MODEL_PATH", "").strip()

        chat_models = _parse_chat_models(values)
        llm_api_keys = _parse_llm_api_keys(values)
        return cls(
            mode=cast(Literal["stub", "live"], mode),
            execution_engine=cast(Literal["langgraph", "legacy"], execution_engine),
            api_host=values.get("API_HOST", "127.0.0.1"),
            api_port=_parse_int(values, "API_PORT", 7860, minimum=1),
            runtime_root=runtime_root,
            asset_root=asset_layout.asset_root,
            asset_manifest_path=asset_layout.manifest_path,
            asset_release_id=asset_layout.release_id,
            frontend_dist_root=_parse_path(
                values,
                "FRONTEND_DIST_ROOT",
                DEFAULT_FRONTEND_DIST_ROOT,
                base=REPOSITORY_ROOT,
            ),
            chat_base_url=values.get("CHAT_BASE_URL", CHAT_BASE_URL),
            chat_model=chat_models[0],
            chat_models=chat_models,
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
                values,
                "LLM_TIMEOUT_SECONDS",
                DEFAULT_PROVIDER_TIMEOUT_SECONDS,
                minimum=1.0,
            ),
            debug_trace_enabled=debug_trace_enabled,
            debug_trace_root=_parse_path(
                values,
                "COMPETITION_DEBUG_TRACE_ROOT",
                runtime_root / "debug_traces",
                base=runtime_root,
            ),
            debug_trace_max_bytes=_parse_int(
                values,
                "COMPETITION_DEBUG_TRACE_MAX_BYTES",
                DEFAULT_DEBUG_TRACE_MAX_BYTES,
                minimum=1,
            ),
            debug_trace_rotate_bytes=_parse_int(
                values,
                "COMPETITION_DEBUG_TRACE_ROTATE_BYTES",
                DEFAULT_DEBUG_TRACE_ROTATE_BYTES,
                minimum=1,
            ),
            debug_trace_flush=_parse_bool(
                values, "COMPETITION_DEBUG_TRACE_FLUSH", True
            ),
            debug_trace_run_ids=parse_debug_trace_run_ids(
                values.get("COMPETITION_DEBUG_TRACE_RUN_IDS")
            ),
            debug_trace_allow_live=debug_trace_allow_live,
            question_vector_store_root=asset_layout.question_vector_store_root,
            knowledge_vector_store_root=asset_layout.knowledge_vector_store_root,
            knowledge_handoff_root=knowledge_handoff_root,
            knowledge_runtime_root=asset_layout.knowledge_runtime_root,
            knowledge_atlas_enabled=_parse_bool(
                values, "KNOWLEDGE_ATLAS_ENABLED", True
            ),
            knowledge_atlas_asset_version=values.get(
                "KNOWLEDGE_ATLAS_ASSET_VERSION", "2026-07-18"
            ),
            knowledge_atlas_data_root=_parse_path(
                values,
                "KNOWLEDGE_ATLAS_DATA_ROOT",
                asset_layout.knowledge_data_root,
            ),
            knowledge_atlas_video_root=_parse_path(
                values,
                "KNOWLEDGE_ATLAS_VIDEO_ROOT",
                asset_layout.knowledge_video_root,
            ),
            knowledge_atlas_chapter_root=asset_layout.knowledge_atlas_chapter_root,
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
            textbook_pdf_root=asset_layout.textbook_pdf_root,
            textbook_pdf_catalog_path=_parse_path(
                values,
                "TEXTBOOK_PDF_CATALOG_PATH",
                DEFAULT_TEXTBOOK_PDF_CATALOG_PATH,
                base=REPOSITORY_ROOT,
            ),
            treekg_root=_parse_path(
                values,
                "TREEKG_ROOT",
                DEFAULT_TREEKG_ROOT,
                base=REPOSITORY_ROOT,
            ),
            treekg_python=values.get("TREEKG_PYTHON", "").strip(),
            treekg_api_key=values.get("TREEKG_API_KEY") or None,
            treekg_api_base=values.get(
                "TREEKG_API_BASE", "https://api.deepseek.com"
            ).strip(),
            treekg_model_name=values.get(
                "TREEKG_MODEL_NAME", "deepseek-v4-flash"
            ).strip(),
            backend_handoff_enabled=_parse_bool(
                values, "BACKEND_HANDOFF_ENABLED", False
            ),
            backend_handoff_root=resolve_platform_backend_root(
                values, backend_root=BACKEND_ROOT
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
            accountability_evaluation_enabled=_parse_bool(
                values, "ACCOUNTABILITY_EVALUATION_ENABLED", False
            ),
            accountability_evaluation_token=(
                values.get("ACCOUNTABILITY_EVALUATION_TOKEN") or None
            ),
            evolution_enabled=_parse_bool(values, "EVOLUTION_ENABLED", False),
            evolution_rules_enabled=_parse_bool(
                values, "EVOLUTION_RULES_ENABLED", False
            ),
            evolution_data_root=_parse_path(
                values,
                "EVOLUTION_DATA_ROOT",
                runtime_root / "evolution",
                base=runtime_root,
            ),
            evolution_min_cases=_parse_int(
                values, "EVOLUTION_MIN_CASES", 3, minimum=1
            ),
            evolution_min_executions=_parse_int(
                values, "EVOLUTION_MIN_EXECUTIONS", 2, minimum=1
            ),
            evolution_min_high_trust=_parse_int(
                values, "EVOLUTION_MIN_HIGH_TRUST", 2, minimum=1
            ),
            preference_training_enabled=_parse_bool(
                values, "PREFERENCE_TRAINING_ENABLED", False
            ),
            preference_training_allow_trl_dpo=_parse_bool(
                values, "PREFERENCE_TRAINING_ALLOW_TRL_DPO", False
            ),
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
            avatar_dir=_parse_path(
                values,
                "AVATAR_DIR",
                runtime_root / "profile_avatars",
                base=runtime_root,
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
            vision_api_model=values.get(
                "VISION_API_MODEL", "Qwen/Qwen3-VL-8B-Instruct"
            ),
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
            knowledge_supplement_max_rounds=_parse_int(
                values, "KNOWLEDGE_SUPPLEMENT_MAX_ROUNDS", 2, minimum=0
            ),
            knowledge_supplement_max_queries=_parse_int(
                values, "KNOWLEDGE_SUPPLEMENT_MAX_QUERIES", 3, minimum=1
            ),
            knowledge_supplement_query_max_length=_parse_int(
                values, "KNOWLEDGE_SUPPLEMENT_QUERY_MAX_LENGTH", 200, minimum=1
            ),
            knowledge_retrieval_exa_limit=_parse_int(
                values, "KNOWLEDGE_RETRIEVAL_EXA_LIMIT", 3, minimum=1
            ),
            knowledge_retrieval_textbook_limit=_parse_int(
                values, "KNOWLEDGE_RETRIEVAL_TEXTBOOK_LIMIT", 5, minimum=1
            ),
            question_fusion_strategy=cast(
                Literal["legacy_max", "rrf_v1"],
                _parse_choice(
                    values,
                    "QUESTION_FUSION_STRATEGY",
                    "rrf_v1",
                    {"legacy_max", "rrf_v1"},
                ),
            ),
            question_rrf_k=_parse_int(values, "QUESTION_RRF_K", 60, minimum=1),
            question_rerank_mode=cast(
                Literal["disabled", "shadow", "sort", "gate"],
                _parse_choice(
                    values,
                    "QUESTION_RERANK_MODE",
                    "disabled",
                    {"disabled", "shadow", "sort", "gate"},
                ),
            ),
            question_rerank_base_url=values.get(
                "QUESTION_RERANK_BASE_URL", values.get("CHAT_BASE_URL", CHAT_BASE_URL)
            ),
            question_rerank_model=values.get(
                "QUESTION_RERANK_MODEL", "Qwen/Qwen3-Reranker-8B"
            ),
            question_rerank_top_n=_parse_int(
                values, "QUESTION_RERANK_TOP_N", 30, minimum=1
            ),
            question_rerank_batch_size=_parse_int(
                values, "QUESTION_RERANK_BATCH_SIZE", 30, minimum=1
            ),
            question_rerank_timeout_seconds=_parse_float(
                values, "QUESTION_RERANK_TIMEOUT_SECONDS", 30.0, minimum=1.0
            ),
            question_rerank_eligible_threshold=_parse_float(
                values, "QUESTION_RERANK_ELIGIBLE_THRESHOLD", 0.65, minimum=0.0
            ),
            question_rerank_reject_threshold=_parse_float(
                values, "QUESTION_RERANK_REJECT_THRESHOLD", 0.30, minimum=0.0
            ),
            llm_api_key=(
                values.get("LLM_API_KEY")
                or values.get("SILICONFLOW_API_KEY")
                or values.get("DASHSCOPE_API_KEY")
                or values.get("DEEPSEEK_API_KEY")
                or None
            ),
            llm_api_keys=llm_api_keys,
            embedding_api_key=(
                values.get("EMBEDDING_API_KEY")
                or values.get("SILICONFLOW_API_KEY")
                or None
            ),
            question_rerank_api_key=(
                values.get("QUESTION_RERANK_API_KEY")
                or values.get("SILICONFLOW_API_KEY")
                or None
            ),
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
