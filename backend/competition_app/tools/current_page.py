from __future__ import annotations

import re
from typing import Any


ALL_PAGE_CONTEXT_AGENTS = frozenset(
    {
        "planner_agent",
        "paper_blueprint_agent",
        "paper_assembly_agent",
        "knowledge_explanation_agent",
        "memory_agent",
        "knowledge_base_agent",
        "default_route_resolver",
        "diagnosis_agent",
        "learning_plan_service",
        "review_scheduler",
        "expert_agent",
        "audit_agent",
    }
)


class CurrentPageReadTool:
    """Validate and bound a browser-produced semantic page snapshot.

    Browser content is always untrusted data.  This tool is deliberately
    read-only and never exposes DOM, cookies, hidden fields, or navigation
    operations to an agent.
    """

    max_text_chars = 8_000
    max_items = 40
    max_semantic_depth = 4

    _sensitive_key = re.compile(
        r"password|passwd|token|secret|authorization|cookie|session[_-]?id|csrf",
        re.IGNORECASE,
    )
    _control_chars = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
    _redaction_patterns = (
        (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I), "[已脱敏凭据]", "credential"),
        (
            re.compile(r"\b(access[_-]?token|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]{6,}", re.I),
            r"\1=[已脱敏凭据]",
            "credential",
        ),
        (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[已脱敏手机号]", "phone"),
        (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[已脱敏证件号]", "identity_number"),
        (
            re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I),
            "[已脱敏邮箱]",
            "email",
        ),
        (
            re.compile(r"(?:[A-Z]:\\|/(?:home|Users)/)[^\s]+", re.I),
            "[已脱敏路径]",
            "file_path",
        ),
    )
    _prompt_injection = re.compile(
        r"ignore\s+(?:all\s+)?previous|system\s+prompt|developer\s+message|"
        r"忽略.{0,12}(?:要求|指令|规则)|系统提示词|覆盖.{0,8}(?:规则|指令)",
        re.IGNORECASE,
    )

    def read(self, *, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(snapshot, dict) or not snapshot:
            return {
                "schema_version": "1.0",
                "tool_name": "read_current_page",
                "source": "current_browser_page",
                "trust_level": "untrusted_page_content",
                "available": False,
                "reason": "page_snapshot_unavailable",
                "redactions": [],
                "security_flags": [],
            }

        redactions = {
            str(item)[:80]
            for item in snapshot.get("redactions", [])
            if isinstance(item, str) and item.strip()
        }
        security_flags: set[str] = set()

        def clean_text(value: Any, limit: int) -> str:
            text = self._control_chars.sub("", str(value or ""))
            text = re.sub(r"[ \t\f\v]+", " ", text)
            text = re.sub(r"\s*\n\s*", "\n", text).strip()
            for pattern, replacement, category in self._redaction_patterns:
                if pattern.search(text):
                    redactions.add(category)
                    text = pattern.sub(replacement, text)
            if self._prompt_injection.search(text):
                security_flags.add("potential_prompt_injection")
            return text[:limit]

        def clean_list(value: Any, *, limit: int = 500) -> list[str]:
            if not isinstance(value, list):
                return []
            seen: set[str] = set()
            result: list[str] = []
            for item in value[: self.max_items]:
                text = clean_text(item, limit)
                if not text or text in seen:
                    continue
                seen.add(text)
                result.append(text)
            return result

        def clean_semantic(value: Any, depth: int = 0) -> Any:
            if value is None or depth > self.max_semantic_depth:
                return None
            if isinstance(value, str):
                return clean_text(value, 2_000)
            if isinstance(value, (bool, int, float)):
                return value
            if isinstance(value, list):
                return [
                    clean_semantic(item, depth + 1)
                    for item in value[: self.max_items]
                ]
            if isinstance(value, dict):
                result: dict[str, Any] = {}
                for key, item in list(value.items())[: self.max_items]:
                    key_text = str(key)[:80]
                    if self._sensitive_key.search(key_text):
                        redactions.add("sensitive_field")
                        continue
                    result[key_text] = clean_semantic(item, depth + 1)
                return result
            return None

        form_state: list[dict[str, Any]] = []
        for raw in snapshot.get("form_state", [])[: self.max_items]:
            if not isinstance(raw, dict):
                continue
            if self._sensitive_key.search(
                " ".join(str(raw.get(key, "")) for key in ("label", "name", "type"))
            ):
                redactions.add("sensitive_form_field")
                continue
            item = {
                "label": clean_text(raw.get("label"), 160),
                "type": clean_text(raw.get("type"), 40),
                "disabled": bool(raw.get("disabled")),
            }
            if "checked" in raw:
                item["checked"] = bool(raw.get("checked"))
            if "value" in raw:
                item["value"] = clean_text(raw.get("value"), 500)
            form_state.append(item)

        regions: list[dict[str, Any]] = []
        raw_regions = snapshot.get("regions")
        if isinstance(raw_regions, list):
            for raw in raw_regions[:20]:
                if not isinstance(raw, dict):
                    continue
                label = clean_text(raw.get("label"), 200)
                if not label:
                    continue
                regions.append(
                    {
                        "label": label,
                        "text": clean_text(raw.get("text"), 2_000),
                        "actions": clean_list(raw.get("actions"), limit=300)[:20],
                    }
                )

        visible_text = clean_text(snapshot.get("visible_text"), self.max_text_chars)
        # The browser contract sends pathname only. Re-apply that boundary on
        # the server so a forged snapshot cannot smuggle query credentials or
        # fragments into model context.
        url_path = clean_text(snapshot.get("url_path") or "/", 300)
        url_path = re.split(r"[?#]", url_path, maxsplit=1)[0] or "/"
        return {
            "schema_version": "1.0",
            "tool_name": "read_current_page",
            "source": "current_browser_page",
            "trust_level": "untrusted_page_content",
            "available": bool(snapshot.get("available", True)),
            "page_type": clean_text(snapshot.get("page_type") or "unknown", 80),
            "page_title": clean_text(snapshot.get("page_title"), 200),
            "url_path": url_path,
            "captured_at": clean_text(snapshot.get("captured_at"), 80),
            "visible_text": visible_text,
            "headings": clean_list(snapshot.get("headings"), limit=200),
            "selected_items": clean_list(snapshot.get("selected_items")),
            "form_state": form_state,
            "regions": regions,
            "available_actions": clean_list(snapshot.get("available_actions"), limit=200),
            "alerts": clean_list(snapshot.get("alerts")),
            "semantic_context": clean_semantic(snapshot.get("semantic_context")),
            "redactions": sorted(redactions),
            "security_flags": sorted(security_flags),
            "truncated": bool(snapshot.get("truncated"))
            or len(str(snapshot.get("visible_text") or "")) > self.max_text_chars,
        }
