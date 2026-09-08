"""Bind immutable route facts separately from authored learning decisions."""

from copy import deepcopy
import json
import re
from typing import Any

from pydantic import Field

from competition_app.contracts.base import ContractModel


class RouteBoundStage(ContractModel):
    stage_id: str = Field(min_length=1)
    duration_days: int = Field(gt=0, le=3650)
    schedule_summary: str = Field(min_length=1)
    acceptance: list[str] = Field(default_factory=list)


def quote_within_stage(document: str, quote: str, stage_id: str, stages: list[dict[str, Any]]) -> bool:
    """Use explicit heading boundaries, never incidental stage mentions.

    Legacy unheaded prose retains the conservative no-other-ID rule. Repeated
    or ambiguous headings fail closed rather than selecting a convenient one.
    """
    ids = [str(stage["stage_id"]) for stage in stages]
    starts: dict[str, list[tuple[int, int]]] = {value: [] for value in ids}
    headings = list(re.finditer(r"(?m)^#{1,6}[ \t]+[^\n]+|^(?:stage-[\w-]+)(?:[ \t]+[^\n]*)?$", document))
    for heading in headings:
        found = [value for value in ids if re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", heading.group())]
        for value in found:
            starts[value].append((heading.start(), heading.end()))
    if any(starts.values()):
        if any(len(starts[value]) != 1 for value in ids):
            return False
        ordered = [starts[value][0][0] for value in ids]
        if ordered != sorted(set(ordered)) or stage_id not in starts:
            return False
        start, title_end = starts[stage_id][0]
        title = document[start:title_end]
        level = len(title) - len(title.lstrip("#")) if title.startswith("#") else 6
        end = len(document)
        next_column = re.search(r"(?m)^[ \t#]*【[^】]+】", document[title_end:])
        if next_column:
            end = title_end + next_column.start()
        for heading in headings:
            if heading.start() <= start:
                continue
            text = heading.group()
            other_stage = any(heading.start() == pos[0][0] for key, pos in starts.items() if key != stage_id)
            heading_level = len(text) - len(text.lstrip("#")) if text.startswith("#") else 6
            if other_stage or heading_level <= level:
                end = min(end, heading.start())
                break
        position = document.find(quote)
        return bool(quote) and start <= position and position + len(quote) <= end
    return not any(
        re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", quote)
        for value in ids if value != stage_id
    )


def document_issues(document: str) -> list[dict[str, str]]:
    """Check fixed document headings, not open-text intent or business meaning."""
    headings = {
        "最终目标": "final_goal", "能力路径与阶段": "stages",
        "阶段里程碑": "milestones", "资源预算": "resource_budget",
        "重规划条件": "replanning", "保温底线": "maintenance",
    }
    matches = list(re.finditer(r"【([^】]+)】", document))
    populated = set()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(document)
        if document[match.end():end].strip("\n\r\t #*-:"):
            populated.add(match.group(1))
    return [
        {"code": "missing_required_field", "category": "missing", "field_path": f"/plan_document/{field}"}
        for heading, field in headings.items() if heading not in populated
    ]


def binding_schema(schema: dict[str, Any], stages: list[dict[str, Any]]) -> dict[str, Any]:
    result = deepcopy(schema)
    definition = RouteBoundStage.model_json_schema()
    definition["additionalProperties"] = False
    definition["properties"]["stage_id"]["enum"] = [stage["stage_id"] for stage in stages]
    result["$defs"]["CompiledLongTermStage"] = definition
    return result


def bind_stages(raw: Any, stages: list[dict[str, Any]]) -> dict[str, Any]:
    """Never infer omitted stages or use authored text as route authority."""
    def rejected(code: str, path: str) -> dict[str, Any]:
        return {"status": "needs_revision", "issues": [{
            "code": code, "category": "invalid", "field_path": path,
        }]}

    if not isinstance(raw, dict):
        return rejected("schema_invalid", "/")
    if raw.get("status") != "compiled":
        return raw
    contract = raw.get("contract") or {}
    if contract.get("scope") != "long_term":
        return rejected("scope_violation", "/scope")
    extracted = contract.get("stages")
    if not isinstance(extracted, list) or len(extracted) != len(stages):
        return rejected("route_stage_missing", "/stages")
    if [stage.get("stage_id") if isinstance(stage, dict) else None for stage in extracted] != [
        stage["stage_id"] for stage in stages
    ]:
        return rejected("immutable_route_conflict", "/stages")
    result = deepcopy(raw)
    anchors = result["contract"].setdefault("field_anchors", {})
    # The collection anchor cannot claim route-owned values came from prose.
    anchors.pop("/stages", None)
    bound = []
    for index, (authored, trusted) in enumerate(zip(extracted, stages)):
        decision = RouteBoundStage.model_validate(authored)
        for field in ("stage", "stage_name", "books", "goal", "stage_id"):
            anchors.pop(f"/stages/{index}/{field}", None)
        fixed = {"stage": index + 1, "stage_name": trusted["name"],
                 "books": list(trusted["books"]), "goal": trusted["goal"]}
        bound.append({**fixed, "duration_days": decision.duration_days,
                      "schedule_summary": decision.schedule_summary,
                      "acceptance": decision.acceptance})
        # Source fields are installed by the compiler, not by a model response.
        for field, value in fixed.items():
            anchors[f"/stages/{index}/{field}"] = [{
                "source_field": "trusted_route_binding",
                "source_quote": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True),
            }]
    result["contract"]["stages"] = bound
    return result