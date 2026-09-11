from copy import deepcopy

import pytest
from jsonschema import ValidationError, validate

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.services.planning_prerequisites import (
    bind_numbered_judgments,
    interpret_judgments,
    numbered_judgment_sources,
)


def draft(number=1, quote="前置尚未确认。"):
    return {"plan_document": "七天复习计划。", "prerequisite_judgments": [{
        "course": "中医诊断学", "status": "unknown", "source_no": number,
        "source_quote": quote, "rationale": "没有通过证据。",
    }]}


def test_numbered_catalog_maps_without_exposing_ids_or_mutating_input():
    sources = {"original_user_request": "前置尚未确认。", "message:12": "只安排基础复习。"}
    baseline = deepcopy(sources)
    catalog, bindings = numbered_judgment_sources(sources)
    assert catalog == [{"source_no": 1, "content": "前置尚未确认。"}, {"source_no": 2, "content": "只安排基础复习。"}]
    raw = draft()
    original = deepcopy(raw)
    bound = bind_numbered_judgments(raw, bindings)
    judgment = bound["prerequisite_judgments"][0]
    assert judgment["source_ref"] == "original_user_request"
    assert "source_no" not in judgment
    result = interpret_judgments(bound["prerequisite_judgments"], {"prerequisites": [{"course": "中医诊断学"}]}, sources)
    assert result["unknown_courses"] == ["中医诊断学"]
    assert sources == baseline and raw == original


@pytest.mark.parametrize("number", [0, -1, 3, "1", True, 1.0, None])
def test_only_exact_authorized_integer_is_accepted(number):
    with pytest.raises(ValueError, match="authorized integer"):
        bind_numbered_judgments(draft(number), {1: "user_request", 2: "message:0"})


def test_caller_cannot_supply_source_ref_even_with_valid_number():
    raw = draft()
    raw["prerequisite_judgments"][0]["source_ref"] = "user_request"
    with pytest.raises(ValueError, match="server-owned"):
        bind_numbered_judgments(raw, {1: "user_request"})


def test_other_source_quote_is_not_accepted():
    sources = {"user_request": "前置尚未确认。", "message:0": "另一条内容"}
    _, bindings = numbered_judgment_sources(sources)
    raw = bind_numbered_judgments(draft(1, "另一条内容"), bindings)
    with pytest.raises(ValueError, match="exact authorized source quote"):
        interpret_judgments(raw["prerequisite_judgments"], {"prerequisites": [{"course": "中医诊断学"}]}, sources)


def test_schema_enum_matches_current_catalog_and_cannot_leak_between_calls():
    first = DiagnosisAgent._planning_draft_schema("short_term", [1, 2])
    second = DiagnosisAgent._planning_draft_schema("long_term", [1])
    props = first["properties"]["prerequisite_judgments"]["items"]["properties"]
    assert props["source_no"]["enum"] == [1, 2]
    assert "source_ref" not in props
    validate(draft(2), first)
    for raw in (draft(3), draft("1"), draft(True)):
        with pytest.raises(ValidationError):
            validate(raw, first)
    with pytest.raises(ValidationError):
        validate(draft(2), second)
    assert props["source_no"]["enum"] == [1, 2]


def test_empty_catalog_forbids_judgments_and_daily_schema_is_unchanged():
    schema = DiagnosisAgent._planning_draft_schema("short_term", [])
    validate({"plan_document": "计划。", "prerequisite_judgments": []}, schema)
    with pytest.raises(ValidationError):
        validate(draft(), schema)
    assert "prerequisite_judgments" not in DiagnosisAgent._planning_draft_schema("daily_task", [1])["properties"]
    assert bind_numbered_judgments({"plan_document": "计划。"}, {}) == {"plan_document": "计划。"}
    assert numbered_judgment_sources({}) == ([], {})
