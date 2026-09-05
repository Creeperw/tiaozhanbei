#!/usr/bin/env python3
"""Validate production configuration, then replace this process with Uvicorn."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def production_errors(settings) -> list[str]:
    errors = []
    if settings.mode != "live":
        errors.append("COMPETITION_APP_MODE must explicitly be live")
    if not settings.backend_handoff_enabled:
        errors.append("BACKEND_HANDOFF_ENABLED must be true")
    if not settings.auth_cookie_secure:
        errors.append("AUTH_COOKIE_SECURE must be true; serve through HTTPS")
    for name, secret in (
        ("SECRET_KEY", settings.jwt_secret_key),
        ("BACKEND_HANDOFF_SECRET_KEY", settings.backend_handoff_secret_key),
    ):
        if len(secret or "") < 32 or secret == "competition-local-development-key":
            errors.append(f"{name} must be a private random secret of at least 32 characters")
    if not (settings.frontend_dist_root / "index.html").is_file():
        errors.append("Build the frontend before starting production")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate only; do not start a server")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "backend"))
    from competition_app.config import Settings, SettingsError

    try:
        settings = Settings.from_env()
    except (SettingsError, ValueError):
        print("Invalid application configuration; check the configured environment file", file=sys.stderr)
        return 1
    errors = production_errors(settings)
    if errors:
        print("Production preflight failed:\n- " + "\n- ".join(errors), file=sys.stderr)
        return 1
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_deployment_layout.py"), "--require-live-assets"],
        cwd=ROOT,
        check=False,
    )
    if result.returncode:
        return result.returncode
    if args.check:
        print("Production preflight passed; no server started")
        return 0
    os.chdir(ROOT / "backend")
    os.execv(sys.executable, [sys.executable, "-m", "competition_app.cli.app", "serve"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())