import pytest

from competition_app.config import (
    BACKEND_ROOT,
    CHAT_BASE_URL,
    Settings,
    SettingsError,
    _load_dotenv,
)


def test_stub_mode_does_not_require_external_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DASHSCOPE_API_KEY", "SILICONFLOW_API_KEY", "MYSQL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("COMPETITION_APP_MODE", "stub")

    settings = Settings.from_env()

    assert settings.mode == "stub"
    assert settings.chat_model == "deepseek-v4-flash"
    assert settings.embedding_model == "Qwen/Qwen3-Embedding-4B"
    assert settings.execution_engine == "langgraph"


def test_live_mode_reports_missing_variable_names_without_secret_values(
) -> None:
    values = {
        "COMPETITION_APP_MODE": "live",
        "MYSQL_PASSWORD": "top-secret-password",
    }

    with pytest.raises(SettingsError) as exc_info:
        Settings.from_env(values)

    message = str(exc_info.value)
    assert "DASHSCOPE_API_KEY" in message
    assert "SILICONFLOW_API_KEY" in message
    assert "top-secret-password" not in message


def test_settings_repr_redacts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPETITION_APP_MODE", "live")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-secret")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "silicon-secret")
    monkeypatch.setenv("MYSQL_PASSWORD", "mysql-secret")

    rendered = repr(Settings.from_env())

    assert "dash-secret" not in rendered
    assert "silicon-secret" not in rendered
    assert "mysql-secret" not in rendered


def test_settings_defaults_to_competition_vector_store_root() -> None:
    settings = Settings.from_env({"COMPETITION_APP_MODE": "stub"})

    assert settings.question_vector_store_root.name == "vdb_store"


def test_settings_accepts_question_vector_store_root_override() -> None:
    settings = Settings.from_env(
        {
            "COMPETITION_APP_MODE": "stub",
            "QUESTION_VECTOR_STORE_ROOT": "/tmp/question-vector-store",
        }
    )

    assert str(settings.question_vector_store_root) == "/tmp/question-vector-store"


def test_settings_accepts_legacy_execution_engine() -> None:
    settings = Settings.from_env(
        {
            "COMPETITION_APP_MODE": "stub",
            "COMPETITION_EXECUTION_ENGINE": "legacy",
        }
    )

    assert settings.execution_engine == "legacy"


def test_settings_rejects_unknown_execution_engine() -> None:
    with pytest.raises(SettingsError, match="COMPETITION_EXECUTION_ENGINE"):
        Settings.from_env(
            {
                "COMPETITION_APP_MODE": "stub",
                "COMPETITION_EXECUTION_ENGINE": "unknown",
            }
        )


def test_dotenv_loader_parses_comments_quotes_and_empty_values(tmp_path) -> None:
    dotenv = tmp_path / ".env.example"
    dotenv.write_text(
        "# comment\nA=plain\nB='quoted value'\nC=\nINVALID\n",
        encoding="utf-8",
    )

    assert _load_dotenv(dotenv) == {"A": "plain", "B": "quoted value", "C": ""}


def test_settings_parse_unified_server_and_runtime_configuration() -> None:
    settings = Settings.from_env(
        {
            "COMPETITION_APP_MODE": "stub",
            "API_HOST": "0.0.0.0",
            "API_PORT": "7860",
            "RUNTIME_ROOT": "/tmp/tiaozhanbei-runtime",
            "FRONTEND_DIST_ROOT": "/tmp/tiaozhanbei-dist",
            "LLM_TIMEOUT_SECONDS": "180",
        }
    )

    assert settings.api_host == "0.0.0.0"
    assert settings.api_port == 7860
    assert str(settings.runtime_root) == "/tmp/tiaozhanbei-runtime"
    assert str(settings.frontend_dist_root) == "/tmp/tiaozhanbei-dist"
    assert settings.llm_timeout_seconds == 180


def test_incoming_model_configuration_cannot_replace_main_model_stack() -> None:
    settings = Settings.from_env(
        {
            "COMPETITION_APP_MODE": "stub",
            "LLM_API_BASE_URL": "https://untrusted-model.example/anthropic",
            "LLM_API_MODEL": "other-model",
            "PLANNER_EXECUTOR_MODEL": "other-planner",
            "MANAGER_REVIEWER_MODEL": "other-reviewer",
        }
    )

    assert settings.chat_base_url == CHAT_BASE_URL
    assert settings.chat_model == "deepseek-v4-flash"
    assert not hasattr(settings, "voice_model_path")


def test_relative_external_asset_paths_resolve_from_backend_root() -> None:
    settings = Settings.from_env(
        {
            "COMPETITION_APP_MODE": "stub",
            "QUESTION_VECTOR_STORE_ROOT": "competition/vdb_store",
            "KNOWLEDGE_HANDOFF_ROOT": "competition/knowledge-delivery",
        }
    )

    assert settings.question_vector_store_root == (
        BACKEND_ROOT / "competition" / "vdb_store"
    ).resolve()
    assert settings.knowledge_handoff_root == (
        BACKEND_ROOT / "competition" / "knowledge-delivery"
    ).resolve()


def test_unified_settings_repr_redacts_all_new_secret_fields() -> None:
    settings = Settings.from_env(
        {
            "COMPETITION_APP_MODE": "stub",
            "DASHSCOPE_API_KEY": "dash-private",
            "SILICONFLOW_API_KEY": "embedding-private",
            "MYSQL_PASSWORD": "mysql-private",
            "VISION_API_KEY": "vision-private",
            "MAIL_PASSWORD": "mail-private",
            "SECRET_KEY": "jwt-private",
        }
    )

    rendered = repr(settings)
    for secret in (
        "dash-private",
        "embedding-private",
        "mysql-private",
        "vision-private",
        "mail-private",
        "jwt-private",
    ):
        assert secret not in rendered


def test_repository_env_example_contains_no_secret_values() -> None:
    env_example = BACKEND_ROOT / "competition_app" / ".env.example"
    values = _load_dotenv(env_example)

    for name in (
        "DASHSCOPE_API_KEY",
        "SILICONFLOW_API_KEY",
        "MYSQL_PASSWORD",
        "EXA_API_KEY",
        "VISION_API_KEY",
        "MAIL_PASSWORD",
        "SECRET_KEY",
        "BACKEND_HANDOFF_SECRET_KEY",
    ):
        assert values[name] == ""

    settings = Settings.from_env(values)
    assert settings.mode == "stub"
    assert settings.api_port == 7860
    assert settings.runtime_root == (
        BACKEND_ROOT.parent / "backend" / "competition_app" / "runtime"
    ).resolve()
    assert settings.frontend_dist_root == (
        BACKEND_ROOT.parent / "frontend" / "llm" / "dist"
    ).resolve()


def test_compatibility_config_delegates_models_to_main_settings(monkeypatch) -> None:
    import importlib

    from competition_app import config_new

    monkeypatch.setenv("CHAT_BASE_URL", "https://main-config.example/v1")
    monkeypatch.setenv("CHAT_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-4B")
    reloaded = importlib.reload(config_new)

    assert reloaded.LLM_API_BASE_URL == "https://main-config.example/v1"
    assert reloaded.LLM_API_MODEL == "deepseek-v4-flash"
    assert reloaded.PLANNER_EXECUTOR_MODEL == "deepseek-v4-flash"
    assert reloaded.MANAGER_REVIEWER_MODEL == "deepseek-v4-flash"
    assert reloaded.EmbeddingConfig.EMBEDDING_MODEL_ID == "Qwen/Qwen3-Embedding-4B"
    assert reloaded.VOICE_MODE == "disabled"
