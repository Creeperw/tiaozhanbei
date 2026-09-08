"""One parent-stage lookup for generation and publication; never infer completion."""

from typing import Any


def field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def resolve_parent_stage(parent: Any, requested_stage_id: str | None = None) -> tuple[str, Any]:
    stages = list(field(parent, "stages", []) or [])
    if not stages:
        raise ValueError("short-term plan requires a long-term parent stage")
    selection = field(parent, "textbook_selection") or {}
    stage_id = requested_stage_id or field(selection, "stage_id")
    if not stage_id:
        if len(stages) != 1:
            raise ValueError("long-term parent stage is ambiguous; explicit selection required")
        stage = stages[0]
        return str(field(stage, "stage_id") or f"stage-{field(stage, 'stage')}"), stage
    matches = [stage for stage in stages if str(stage_id) in {
        str(field(stage, "stage_id") or ""), f"stage-{field(stage, 'stage')}",
    }]
    if len(matches) != 1:
        raise ValueError("selected stage does not uniquely identify a long-term parent stage")
    return str(stage_id), matches[0]