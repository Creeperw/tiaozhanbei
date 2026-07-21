import pytest

from competition_app.config import Settings, SettingsError


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
    from competition_app.config import _load_dotenv

    dotenv = tmp_path / ".env.example"
    dotenv.write_text(
        "# comment\nA=plain\nB='quoted value'\nC=\nINVALID\n",
        encoding="utf-8",
    )

    assert _load_dotenv(dotenv) == {"A": "plain", "B": "quoted value", "C": ""}
