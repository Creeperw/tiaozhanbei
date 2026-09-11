from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel


# 内部标识符（编译器/服务端自己生成的句柄）：对学习者没有意义，任何面向浏览器
# 的投影都必须去掉。这里放唯一一份定义，prose 与 JSON 两条边界共用——否则会
# 出现「自然语言已隐藏、结构化产出仍原样透出」的漏洞。
INTERNAL_HANDLE_PREFIXES = (
    "EVID",
    "ART",
    "EXEC",
    "TRACE",
    "REQ",
    "CASE",
    "KP",
    "RULE",
    "THREAD",
    "UNIT",
    "MODEL_CALL",
    "DRAFT",
    "EP",
    "AUDIT",
    "USER",
    "GENERATED",
)

# 三种形态：
#  1) 任意前缀 + uuid4().hex —— 不依赖前缀名单，新增的 ``SOMETHING_<uuid>``
#     句柄会被自动覆盖（USER_/EP_/DRAFT_/AUDIT_/C_ 等都属于这一形态）；
#  2) 名单内的前缀 + ASCII 后缀（KP_…/THREAD_…/EP_SCOPE_…/RULE_…）；
#  3) 证据句柄，后缀含中文，例如 E_CHUNK_中医学基础_clean:00011。
INTERNAL_HANDLE_PATTERN = (
    r"[A-Z][A-Z0-9]*_[0-9a-f]{32,}"
    rf"|(?:{'|'.join(INTERNAL_HANDLE_PREFIXES)})_[A-Za-z0-9_.:\-]+"
    r"|E_(?:CHUNK|VECTOR|EXA|WEB|CONCEPT)_[A-Za-z0-9_.:\-\u4e00-\u9fff]+"
)

INTERNAL_HANDLE = re.compile(INTERNAL_HANDLE_PATTERN)


SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:"
    r"api[_-]?key|password|passwd|authorization|dsn|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"client[_-]?secret|jwt[_-]?secret(?:[_-]?key)?|private[_-]?key|"
    r"backend[_-]?handoff[_-]?secret(?:[_-]?key)?|cookie|session[_-]?id"
    r")(?:$|[_-])",
    re.IGNORECASE,
)
AUTHORIZATION_VALUE = re.compile(
    r"(?i)((?:authorization|proxy-authorization)\s*:\s*(?:bearer|basic)\s+)[^\s\"']+"
)
DATABASE_URL_VALUE = re.compile(
    r"(?i)(mysql(?:\+pymysql)?://[^:/\s]+:)[^@/\s]+(@[^\s\"']+)"
)
API_TOKEN_VALUE = re.compile(r"(?i)\b(?:sk|dash|sf)-[a-z0-9_-]{20,}\b")
COOKIE_VALUE = re.compile(r"(?i)(cookie\s*:\s*[^=\s;]+\s*=)[^;\s\"']+")
JWT_VALUE = re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b")
GENERIC_SECRET_VALUE = re.compile(
    r"(?i)((?:access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"jwt[_-]?secret(?:[_-]?key)?|private[_-]?key|password)\s*[=:]\s*)"
    r"[^\s,;\"']+"
)
PHONE_VALUE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
IDENTITY_VALUE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")


def _sanitize_string(value: str) -> str:
    value = AUTHORIZATION_VALUE.sub(r"\1[REDACTED]", value)
    value = DATABASE_URL_VALUE.sub(r"\1[REDACTED]\2", value)
    value = API_TOKEN_VALUE.sub("[REDACTED]", value)
    value = COOKIE_VALUE.sub(r"\1[REDACTED]", value)
    value = JWT_VALUE.sub("[REDACTED]", value)
    value = GENERIC_SECRET_VALUE.sub(r"\1[REDACTED]", value)
    value = PHONE_VALUE.sub("[REDACTED_PHONE]", value)
    return IDENTITY_VALUE.sub("[REDACTED_ID]", value)


def _is_sensitive_key(value: object) -> bool:
    key = str(value)
    if SENSITIVE_KEY.search(key):
        return True
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    markers = (
        "apikey",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "clientsecret",
        "jwtsecret",
        "privatekey",
        "password",
        "passwd",
        "authorization",
        "cookie",
        "sessionid",
    )
    return (
        normalized in {"token", "secret", "dsn"}
        or normalized.endswith("token")
        or any(marker in normalized for marker in markers)
    )


def _sanitize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _sanitize(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_sensitive_key(key) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize(item) for item in value)
    if isinstance(value, set):
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
