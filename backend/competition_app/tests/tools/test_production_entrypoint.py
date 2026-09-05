import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("serve_production", ROOT / "scripts/serve_production.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def settings(tmp_path):
    (tmp_path / "index.html").write_text("frontend", encoding="utf-8")
    return SimpleNamespace(
        mode="live", backend_handoff_enabled=True, auth_cookie_secure=True,
        jwt_secret_key="a" * 40, backend_handoff_secret_key="b" * 40,
        frontend_dist_root=tmp_path,
    )


def test_valid_production_settings(tmp_path):
    assert module.production_errors(settings(tmp_path)) == []


def test_production_rejects_stub(tmp_path):
    value = settings(tmp_path)
    value.mode = "stub"
    assert any("live" in error for error in module.production_errors(value))


def test_production_rejects_insecure_config_without_exposing_secrets(tmp_path):
    value = settings(tmp_path)
    value.auth_cookie_secure = False
    value.backend_handoff_enabled = False
    value.jwt_secret_key = "private-short"
    value.backend_handoff_secret_key = "competition-local-development-key"
    errors = module.production_errors(value)
    assert len(errors) == 4
    assert "private-short" not in str(errors)


def test_production_requires_built_frontend(tmp_path):
    value = settings(tmp_path)
    (tmp_path / "index.html").unlink()
    assert any("frontend" in error for error in module.production_errors(value))