"""Bounded structured-validation diagnostics, without model values or prompts."""

from typing import Any

from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError


_RULES = frozenset({
    "required", "additionalProperties", "type", "enum", "const", "anyOf",
    "oneOf", "minLength", "maxLength", "minimum", "maximum", "minItems",
    "exclusiveMinimum", "exclusiveMaximum",
    "maxItems", "uniqueItems", "pattern", "schema_invalid", "business_rule",
    "current_message_quote", "focus_user_anchor", "budget_fields_together",
    "frozen_task_type", "scope_objects", "scope_question", "plan_clarification",
})
_BUSINESS_RULES = {
    "route source quote is not in the current user message": ("task_source_quote", "current_message_quote"),
    "planning scope requires an exact current-message quote": ("planning_request_scope.source_quote", "current_message_quote"),
    "requested focus object has no user-message anchor": ("planning_request_scope.objects", "focus_user_anchor"),
    "current-turn available-minutes source quote is not in current user message": ("current_turn_available_minutes_source_quote", "current_message_quote"),
    "review task adjustment requires an exact current-message mutation quote": ("review_adjustment_source_quote", "current_message_quote"),
    "current-turn minutes and source quote must be provided together": ("current_turn_available_minutes", "budget_fields_together"),
    "current-turn minutes and scope must be provided together": ("current_turn_available_minutes", "budget_fields_together"),
    "planner branch attempted to change the frozen task type": ("task_type", "frozen_task_type"),
    "explicit focus requires bounded nonempty objects": ("planning_request_scope.objects", "scope_objects"),
    "duplicate requested focus objects": ("planning_request_scope.objects", "scope_objects"),
    "only explicit focus may contain requested objects": ("planning_request_scope.objects", "scope_objects"),
    "ambiguous request requires a specific question": ("planning_request_scope.clarification_question", "scope_question"),
    "resolved request must not contain a question": ("planning_request_scope.clarification_question", "scope_question"),
    "an unspecified or clarify plan requires clarification": ("requires_clarification", "plan_clarification"),
    "an executable plan must not include clarification fields": ("clarification_question", "plan_clarification"),
}


def validation_issues(error: BaseException, schema: Any) -> list[dict[str, str]]:
    """Only schema-owned names may appear in paths; never copy error messages."""
    names: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            names.update((node.get("properties") or {}).keys())
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(schema)

    def issue(path: Any, rule: str) -> dict[str, str]:
        parts = [str(part) if str(part) in names else "*" for part in list(path)[:12]]
        return {"field_path": "/" + "/".join(parts), "rule": rule if rule in _RULES else "business_rule"}

    cause = error.__cause__
    if isinstance(cause, JsonSchemaValidationError):
        error = cause
    if isinstance(error, JsonSchemaValidationError):
        def schema_issues(current: JsonSchemaValidationError, depth: int = 0) -> list[dict[str, str]]:
            # Follow only a schema-declared discriminator branch, never guess
            # from free text or mix errors from unrelated plan scopes.
            if depth < 12 and current.validator in {"oneOf", "anyOf"} and current.context:
                discriminator = current.schema.get("discriminator", {})
                field = discriminator.get("propertyName")
                selected = current.instance.get(field) if isinstance(current.instance, dict) else None
                mapping = discriminator.get("mapping", {})
                target = mapping.get(selected) if isinstance(selected, str) else None
                branches = current.schema.get(current.validator, [])
                if target:
                    for index, branch in enumerate(branches):
                        if branch.get("$ref") != target:
                            continue
                        result = []
                        for child in current.context:
                            if list(child.schema_path)[:1] == [index]:
                                result.extend(schema_issues(child, depth + 1))
                            if len(result) >= 8:
                                break
                        if result:
                            return result[:8]
            path = list(current.absolute_path)
            if current.validator == "required" and isinstance(current.instance, dict):
                return [issue([*path, name], "required") for name in current.validator_value
                        if name not in current.instance][:8]
            return [issue(path, str(current.validator))]

        return schema_issues(error)
    if isinstance(error, ValidationError):
        result = []
        for detail in error.errors(include_input=False, include_url=False)[:8]:
            message = detail.get("msg", "").removeprefix("Value error, ")
            if message in _BUSINESS_RULES:
                path, rule = _BUSINESS_RULES[message]
                result.append(issue(path.split("."), rule))
            else:
                rule = {"missing": "required", "extra_forbidden": "additionalProperties",
                        "literal_error": "enum", "string_too_long": "maxLength",
                        "string_too_short": "minLength"}.get(detail["type"], "business_rule")
                result.append(issue(detail.get("loc", ()), rule))
        return result
    if str(error) in _BUSINESS_RULES:
        path, rule = _BUSINESS_RULES[str(error)]
        return [issue(path.split("."), rule)]
    return [issue((), "business_rule")]


def safe_validation_issues(value: Any) -> list[dict[str, Any]]:
    """Copy only bounded diagnostic fields at each persistence boundary."""
    if not isinstance(value, list):
        return []
    result = []
    for entry in value[:16]:
        if not isinstance(entry, dict) or entry.get("rule") not in _RULES:
            continue
        path = entry.get("field_path")
        if not isinstance(path, str) or len(path) > 240 or not path.startswith("/"):
            continue
        if not all(c.isascii() and (c.isalnum() or c in "/_*") for c in path):
            continue
        item = {"field_path": path, "rule": entry["rule"]}
        if type(entry.get("attempt")) is int and entry["attempt"] in (1, 2):
            item["attempt"] = entry["attempt"]
        result.append(item)
    return result