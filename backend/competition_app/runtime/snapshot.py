from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel


SENSITIVE_KEY = re.compile(r"(api[_-]?key|password|authorization|dsn)", re.IGNORECASE)
AUTHORIZATION_VALUE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s\"']+")
DATABASE_URL_VALUE = re.compile(
    r"(?i)(mysql(?:\+pymysql)?://[^:/\s]+:)[^@/\s]+(@[^\s\"']+)"
)
API_TOKEN_VALUE = re.compile(r"(?i)\b(?:sk|dash|sf)-[a-z0-9_-]{20,}\b")
COOKIE_VALUE = re.compile(r"(?i)(cookie\s*:\s*[^=\s;]+\s*=)[^;\s\"']+")
PHONE_VALUE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
IDENTITY_VALUE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")


def _sanitize_string(value: str) -> str:
    value = AUTHORIZATION_VALUE.sub(r"\1[REDACTED]", value)
    value = DATABASE_URL_VALUE.sub(r"\1[REDACTED]\2", value)
    value = API_TOKEN_VALUE.sub("[REDACTED]", value)
    value = COOKIE_VALUE.sub(r"\1[REDACTED]", value)
    value = PHONE_VALUE.sub("[REDACTED_PHONE]", value)
    return IDENTITY_VALUE.sub("[REDACTED_ID]", value)


def _sanitize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _sanitize(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {key: "[REDACTED]" if SENSITIVE_KEY.search(key) else _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value)
    return value


class SnapshotExporter:
    def __init__(self, root: Path) -> None:
        self.root = root

    def export(self, case_id: str, execution_id: str, payload: dict[str, Any]) -> Path:
        path = self.root / case_id / f"{execution_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_sanitize(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        return path
