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

# 规则名到修复说明的固定映射。键全部取自 _RULES，取值是服务端写死的文案，
# 不含任何模型文本。修复重试必须让模型知道“错在哪”：只给规则名和阈值，模型
# 仍要自己翻译；business_rule 这类兜底规则更是没有任何信息量。
_RULE_DESCRIPTIONS = {
    "required": "缺少必填字段",
    "additionalProperties": "包含契约不允许的字段",
    "type": "字段类型不符合契约",
    "enum": "取值不在契约允许的范围内",
    "const": "取值必须是契约规定的固定值",
    "anyOf": "不符合契约允许的任一分支",
    "oneOf": "不符合契约允许的任一分支",
    "minLength": "内容长度低于下限",
    "maxLength": "内容长度超过上限",
    "minimum": "数值低于下限",
    "maximum": "数值超过上限",
    "minItems": "条目数量低于下限",
    "maxItems": "条目数量超过上限",
    "exclusiveMinimum": "数值必须大于下限",
    "exclusiveMaximum": "数值必须小于上限",
    "uniqueItems": "条目存在重复",
    "pattern": "格式不符合契约",
    "schema_invalid": "输出结构不符合契约",
    "business_rule": "违反业务契约",
    "current_message_quote": "引文必须是本轮用户消息的原文",
    "focus_user_anchor": "指定对象在本轮用户消息中没有依据",
    "budget_fields_together": "相关字段必须同时提供",
    "frozen_task_type": "不能改变已确定的任务类型",
    "scope_objects": "指定对象的范围不合法",
    "scope_question": "澄清问题与请求状态不匹配",
    "plan_clarification": "澄清字段与计划状态不匹配",
}

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

# 带阈值的规则：把这些规则的阈值一并报出来，修复指令才能给出可执行的目标。
# 只说 ``maxLength`` 而不说上限，模型只能猜，第二次尝试往往同样超限。阈值
# 取自 schema 或校验器自己声明的元数据（属服务端合同），不是模型文本，
# 因此可以安全进入提示词与持久化诊断。
_BOUNDED_RULES = frozenset(
    {
        "maxLength",
        "minLength",
        "maxItems",
        "minItems",
        "maximum",
        "minimum",
        "exclusiveMaximum",
        "exclusiveMinimum",
    }
)

# pydantic 把同一个阈值放在 ``ctx`` 里，键名与 jsonschema 的规则名不同。
# 只在 pydantic 路径（bound 是 ctx 字典）下查这张表。
_BOUND_CTX_KEYS = {
    "maxLength": ("max_length",),
    "minLength": ("min_length",),
    "maxItems": ("max_length",),
    "minItems": ("min_length",),
    "maximum": ("le",),
    "minimum": ("ge",),
    "exclusiveMaximum": ("lt",),
    "exclusiveMinimum": ("gt",),
}


def _bounded_limit(rule: str, bound: Any) -> int | None:
    """Return the schema-declared threshold for a bounded rule, if any."""

    if rule not in _BOUNDED_RULES:
        return None
    if isinstance(bound, dict):
        for key in _BOUND_CTX_KEYS.get(rule, ()):
            value = bound.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None
    if isinstance(bound, int) and not isinstance(bound, bool):
        return bound
    return None


def _path_part(part: Any, names: set[str]) -> str:
    """Keep schema property names and array positions; mask everything else.

    Array indices are structure, not model text: they are positions inside the
    object the schema itself declares, so they carry no learner content and are
    safe to reach the prompt. Masking them reduced a repair instruction to
    "some item is wrong", which the model cannot act on. Anything that is not a
    schema-owned property name is still replaced by ``*``.
    """

    if isinstance(part, int) and not isinstance(part, bool):
        return str(part)
    text = str(part)
    return text if text in names else "*"


def _safe_property_names(node: Any) -> list[str]:
    """The schema-declared property names at one object level.

    ``additionalProperties`` 违规只说“这个对象里有多余字段”，而多余字段的名字
    是模型写的文本，不能进提示词（见本模块约定）。真正可执行的信息是服务端
    自己声明的允许字段清单：把它报给模型，模型才知道该删哪个字段，第二次
    尝试才不会原样重犯同一个违规。
    """

    if not isinstance(node, dict):
        return []
    properties = node.get("properties")
    if not isinstance(properties, dict):
        return []
    return [
        name
        for name in properties
        if isinstance(name, str)
        and 0 < len(name) <= 40
        and name.isascii()
        and name.replace("_", "").isalnum()
    ][:12]


def _resolve_ref(root: Any, node: Any) -> Any:
    """Follow local ``$ref`` pointers so a schema can be walked by path."""

    for _ in range(8):
        if not isinstance(node, dict) or "$ref" not in node:
            return node
        ref = node.get("$ref")
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return None
        target: Any = root
        for segment in ref[2:].split("/"):
            if not isinstance(target, dict):
                return None
            target = target.get(segment.replace("~1", "/").replace("~0", "~"))
        node = target
    return None


def _schema_node(root: Any, schema: Any, path: Any) -> Any:
    """Resolve the schema that governs ``path`` inside ``root``.

    pydantic 把多余字段的名字放在 ``loc`` 末尾，但真正需要报给模型的是父对象
    的允许字段清单；父对象 schema 只能沿路径走一遍才能拿到。
    """

    node = schema
    for part in list(path)[:12]:
        node = _resolve_ref(root, node)
        if not isinstance(node, dict):
            return None
        if isinstance(part, int) and not isinstance(part, bool):
            node = node.get("items")
            continue
        properties = node.get("properties")
        node = properties.get(str(part)) if isinstance(properties, dict) else None
    return _resolve_ref(root, node)


def validation_issues(error: BaseException, schema: Any) -> list[dict[str, Any]]:
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

    def issue(
        path: Any,
        rule: str,
        bound: Any = None,
        allowed: list[str] | None = None,
    ) -> dict[str, Any]:
        parts = [_path_part(part, names) for part in list(path)[:12]]
        item: dict[str, Any] = {
            "field_path": "/" + "/".join(parts),
            "rule": rule if rule in _RULES else "business_rule",
        }
        limit = _bounded_limit(item["rule"], bound)
        if limit is not None:
            item["limit"] = limit
        if item["rule"] == "additionalProperties" and allowed:
            item["allowed"] = list(allowed)
        return item

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
                if field in names and isinstance(current.instance, dict) and mapping:
                    if field not in current.instance:
                        return [issue([*current.absolute_path, field], "required")]
                    if not isinstance(selected, str):
                        return [issue([*current.absolute_path, field], "type")]
                    if target is None:
                        return [issue([*current.absolute_path, field], "enum")]
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
                if not target:
                    # 没有 discriminator 时，jsonschema 的 anyOf/oneOf 错误本身
                    # 不含任何可执行信息：只说“不符合契约允许的任一分支”，既
                    # 不说哪个字段，也不说违反哪条规则。线上因此出现过两轮
                    # 重试全部失败——第二次尝试拿到的反馈与第一次一样空洞，
                    # 模型只能靠猜。下钻到子分支取一条具体错误：报错条数最少
                    # 的分支最贴近实例的实际结构（`list[X] | None` 里报出
                    # `items` 级错误的那一支，永远比只报 `type: null` 的那一支
                    # 具体），同分时按 schema 声明顺序取靠前的分支，结果稳定。
                    best: list[dict[str, Any]] | None = None
                    for child in current.context:
                        found = schema_issues(child, depth + 1)
                        if not found:
                            continue
                        if best is None or len(found) < len(best):
                            best = found
                    if best:
                        return best[:8]
            path = list(current.absolute_path)
            if current.validator == "required" and isinstance(current.instance, dict):
                return [issue([*path, name], "required") for name in current.validator_value
                        if name not in current.instance][:8]
            if (
                current.validator == "additionalProperties"
                and current.validator_value is False
            ):
                # 只报服务端声明的允许字段，不回填模型写下的那个多余字段名。
                return [
                    issue(
                        path,
                        "additionalProperties",
                        None,
                        _safe_property_names(current.schema),
                    )
                ]
            return [issue(path, str(current.validator), current.validator_value)]

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
                location = list(detail.get("loc", ()))
                allowed = (
                    _safe_property_names(_schema_node(schema, schema, location[:-1]))
                    if rule == "additionalProperties"
                    else None
                )
                result.append(issue(location, rule, detail.get("ctx"), allowed))
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
        limit = entry.get("limit")
        if (
            isinstance(limit, int)
            and not isinstance(limit, bool)
            and -1_000_000 <= limit <= 1_000_000
        ):
            item["limit"] = limit
        allowed = entry.get("allowed")
        if isinstance(allowed, list):
            names = [
                name
                for name in allowed[:12]
                if isinstance(name, str)
                and 0 < len(name) <= 40
                and name.isascii()
                and name.replace("_", "").isalnum()
            ]
            if names:
                item["allowed"] = names
        result.append(item)
    return result


def _render_field_path(field_path: str) -> str:
    """Render a schema path for humans: ``/items/2/stem`` → ``items[2].stem``."""

    segments = [segment for segment in field_path.split("/") if segment]
    if not segments:
        return "输出对象"
    if segments[0] == "*":
        return "输出对象（未知字段）"
    rendered = segments[0]
    for segment in segments[1:]:
        if segment.isdigit() or segment == "*":
            rendered += f"[{segment}]"
        else:
            rendered += f".{segment}"
    return rendered


def describe_validation_issues(issues: Any) -> str:
    """Explain the contract violations in words, for the repair attempt.

    Rules come from the fixed vocabulary and thresholds from the schema, so the
    sentence names what failed and by how much without quoting the response.
    Only the first few violations are described: a repair prompt that lists
    everything dilutes the one field that actually needs changing.
    """

    entries = safe_validation_issues(issues)
    if not entries:
        return ""
    lines = []
    for entry in entries[:6]:
        description = _RULE_DESCRIPTIONS.get(entry["rule"])
        if description is None:
            continue
        limit = entry.get("limit")
        if limit is not None:
            description = f"{description}（{limit}）"
        elif entry["rule"] == "additionalProperties" and entry.get("allowed"):
            # 多余的字段名是模型文本，不能回填；改为给出服务端声明的允许字段，
            # 模型据此就能自己找出该删哪一个，而不是第二次原样重犯。
            description = f"{description}（只允许：{'、'.join(entry['allowed'])}）"
        lines.append(f"- {_render_field_path(entry['field_path'])}：{description}")
    if not lines:
        return ""
    return (
        f"上一次返回的 JSON 有 {len(lines)} 处不符合契约，"
        "请只修正下列字段，其余内容保持原样：\n" + "\n".join(lines)
    )