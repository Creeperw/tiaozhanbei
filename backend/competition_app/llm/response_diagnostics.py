"""Bounded response metadata; never retain prompt text or provider reasoning."""

import hashlib
import json
import re
from typing import Any

from competition_app.llm.validation_diagnostics import safe_validation_issues

PLAN_HEADINGS = ("最终目标", "能力路径与阶段", "阶段里程碑", "资源预算", "重规划条件", "保温底线")
FINISH_REASONS = frozenset({"stop", "length", "tool_calls", "function_call", "content_filter"})
PROVIDER_ERROR_CODES = frozenset({
    "MissingSessionID", "invalid_request_error", "authentication_error", "permission_error",
    "rate_limit_error", "insufficient_quota", "insufficient_balance", "invalid_api_key", "overloaded_error",
})
PROVIDER_ERROR_MESSAGES = frozenset({
    "This response_format type is unavailable now",
    "Error from provider (Console Go): Upstream request failed: [invalid_request_error] This response_format type is unavailable now",
    "json_schema not supported",
})


def provider_error_diagnostics(raw: bytes) -> dict:
    """Unknown provider prose is hashed, never persisted (may echo PII/secrets)."""
    result = {"provider_error_digest": hashlib.sha256(raw).hexdigest()}
    if len(raw) > 16384:
        return result
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        return result
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return result
    for key in ("type", "code"):
        if isinstance(error.get(key), str) and error[key] in PROVIDER_ERROR_CODES:
            result["provider_error_" + key] = error[key]
    message = error.get("message")
    if isinstance(message, str) and message in PROVIDER_ERROR_MESSAGES:
        result["provider_error_message"] = message
    if error.get("param") in ("response_format", "response_format.type", "response_format.json_schema"):
        result["provider_error_param"] = error["param"]
    return result


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def update_response_metadata(target: dict, event: Any) -> None:
    if not isinstance(event, dict):
        return
    usage = event.get("usage")
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if type(value) is int and 0 <= value <= 100_000_000:
                target[key] = value
    choices = event.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        reason = choices[0].get("finish_reason")
        if reason is not None:
            target["finish_reason"] = reason if isinstance(reason, str) and reason in FINISH_REASONS else "other"


def safe_response_diagnostics(value: Any) -> dict:
    """Revalidate at persistence boundaries, even for non-HTTP model adapters."""
    if not isinstance(value, dict):
        return {}
    safe: dict = {}
    for key in ("skill_digest", "system_digest", "messages_digest", "schema_digest", "provider_error_digest"):
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item):
            safe[key] = item
    if value.get("skill_id") == "diagnosis.create_learning_plan":
        safe["skill_id"] = value["skill_id"]
        version = value.get("skill_version")
        if isinstance(version, str) and re.fullmatch(r"\d{1,5}\.\d{1,5}\.\d{1,5}", version):
            safe["skill_version"] = version
    for key in ("skill_in_system", "stream", "done_received", "body_read_completed", "max_tokens_configured", "max_completion_tokens_configured", "stop_configured"):
        if type(value.get(key)) is bool:
            safe[key] = value[key]
    if isinstance(value.get("required_headings_present"), dict):
        safe["required_headings_present"] = {
            key: item for key, item in value["required_headings_present"].items()
            if key in PLAN_HEADINGS and type(item) is bool
        }
    for key in ("attempt", "retry_count", "content_chars", "prompt_tokens", "completion_tokens", "total_tokens", "http_status", "max_tokens", "max_completion_tokens"):
        item = value.get(key)
        if type(item) is int and 0 <= item <= 100_000_000:
            safe[key] = item
    for key, allowed in (
        ("finish_reason", FINISH_REASONS | {"other"}),
        ("response_format", {"json_schema", "json_object", "text"}),
        ("thinking", {"enabled", "disabled", "unspecified"}),
        ("structured_output_mode", {"json_schema", "json_object"}),
        ("provider_error_type", PROVIDER_ERROR_CODES),
        ("provider_error_code", PROVIDER_ERROR_CODES),
        ("provider_error_message", PROVIDER_ERROR_MESSAGES),
        ("provider_error_param", {"response_format", "response_format.type", "response_format.json_schema"}),
    ):
        item = value.get(key)
        if isinstance(item, str) and item in allowed:
            safe[key] = item
    attempts = value.get("attempts")
    if isinstance(attempts, list):
        safe["attempts"] = [
            safe_response_diagnostics({k: v for k, v in item.items() if k not in {"attempts", "structured_attempts"}})
            for item in attempts[:20] if isinstance(item, dict)
        ]
    structured = value.get("structured_attempts")
    if isinstance(structured, list):
        safe["structured_attempts"] = []
        for item in structured[:2]:
            if not isinstance(item, dict):
                continue
            if type(item.get("attempt")) is not int or item["attempt"] not in (1, 2):
                continue
            if item.get("status") not in (
                "succeeded", "invalid_json", "validation_failed", "transport_failed",
            ):
                continue
            record = {"attempt": item["attempt"], "status": item["status"]}
            if item.get("failure_reason") in (
                "invalid_json", "ambiguous_json", "business_schema_invalid",
            ):
                record["failure_reason"] = item["failure_reason"]
            issues = safe_validation_issues(item.get("validation_issues"))
            if issues:
                record["validation_issues"] = issues
            safe["structured_attempts"].append(record)
    return safe