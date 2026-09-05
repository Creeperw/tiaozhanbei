from __future__ import annotations

import os
from pathlib import Path


def load_project_env(path: Path | None = None) -> None:
    env_path = path or Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) > 1 and value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


load_project_env()
