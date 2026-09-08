from copy import deepcopy

import pytest

from competition_app.services.planning_prerequisites import (
    interpret_judgments, judgment_sources, missing_execution_prerequisites,
    refresh_candidate_prerequisites,
)
from competition_app.services.plan_audit import plan_audit_subject_digest
from competition_app.services.parent_stage import resolve_parent_stage


@pytest.fixture
def route():
    return {
        "route_id": "tcm", "stages": [
            {"stage_id": "stage-1", "order": 1, "books": ["基础"]},
            {"stage_id": "stage-2", "order": 2, "books": ["方剂学", "无关教材"]},
        ],
        "prerequisites": [{"course": "中医诊断学", "before_stage_id": "stage-2", "applies_to_books": ["方剂学"]}],
    }


def judgment(status="unknown", **kwargs):
    return {"course": "中医诊断学", "status": status, "source_ref": "user_request",
            "source_quote": "不是已经掌握", "rationale": "尚待能力诊断", **kwargs}


def test_missing_judgment_is_unknown_without_parsing_positive_words(route):
    evidence = interpret_judgments([], route, {"user_request": "已经学完，但是尚未通过测评"})
    assert evidence["unknown_courses"] == ["中医诊断学"]
    assert evidence["satisfied_courses"] == []


@pytest.mark.parametrize("raw", [
    [judgment(course="其他课程")], [judgment(source_ref="invented")],
    [judgment(source_quote="已掌握")], [judgment(), judgment()],
    [judgment(status="passed")], [judgment(extra="forbidden")],
    {"course": "中医诊断学"},
])
def test_invalid_judgments_fail_closed(route, raw):
    with pytest.raises(ValueError):
        interpret_judgments(raw, route, {"user_request": "不是已经掌握"})


@pytest.mark.parametrize("books,missing", [
    (["中医诊断学"], []), (["方剂学"], ["中医诊断学"]),
    (["中医诊断学", "方剂学"], ["中医诊断学"]),
    (["无关教材"], []),
])
def test_prerequisite_exemption_is_per_book(route, books, missing):
    assert missing_execution_prerequisites(route, "stage-2", books, set()) == missing


def test_judgment_change_rebinds_audit_digest(route):
    sources = {"user_request": "不是已经掌握"}
    unknown = interpret_judgments([judgment()], route, sources)
    altered = deepcopy(unknown)
    altered["judgments"][0]["status"] = "satisfied"
    kwargs = dict(plan_scope="long_term", proposal={"content": "same"}, compiled_plan_contract={})
    assert plan_audit_subject_digest(**kwargs, prerequisite_assessment=unknown) != plan_audit_subject_digest(**kwargs, prerequisite_assessment=altered)
    assert plan_audit_subject_digest(**kwargs) == plan_audit_subject_digest(**kwargs, prerequisite_assessment=None)


def test_rebinding_empty_route_offers_conditions_not_fake_parent(route):
    original = {"eligible": [{"candidate_id": "old"}], "prerequisite_evidence": {"route_id": ""}}
    result = refresh_candidate_prerequisites(original, interpret_judgments([], route, {}), route)
    assert result["eligible"] == result["blocked"] == []
    assert result["route_conditions"][1]["required_courses"] == ["中医诊断学"]
    assert "plan_id" not in result
    assert original["eligible"] == [{"candidate_id": "old"}]


def test_candidate_rechecks_prerequisites_without_waiving_other_gates(route):
    candidate = {"candidate_id": "c", "stage": {"stage_id": "stage-2"}, "books": [{"name": "方剂学"}],
                 "hard_constraint_results": [{"key": "prerequisite_satisfied", "passed": True},
                                             {"key": "parent_plan_exists", "passed": False}]}
    original = {"eligible": [candidate], "prerequisite_evidence": {"route_id": "tcm"}}
    unknown = interpret_judgments([], route, {})
    result = refresh_candidate_prerequisites(original, unknown, route)
    assert result["blocked"][0]["hard_constraint_results"][0]["passed"] is False
    satisfied = interpret_judgments([judgment("satisfied", source_quote="已通过")], route, {"user_request": "已通过"})
    result = refresh_candidate_prerequisites(result, satisfied, route)
    assert result["eligible"] == []
    assert result["blocked"][0]["hard_constraint_results"][0]["passed"] is True
    assert result["blocked"][0]["hard_constraint_results"][1]["passed"] is False


def test_parent_stage_does_not_infer_completion_from_two_days():
    parent = {"textbook_selection": {"stage_id": "stage-1"}, "stages": [
        {"stage": 1, "duration_days": 2}, {"stage": 2, "duration_days": 30},
    ]}
    assert resolve_parent_stage(parent)[1]["duration_days"] == 2
    parent.pop("textbook_selection")
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_parent_stage(parent)


def test_sources_bounded_and_assistant_messages_excluded():
    sources = judgment_sources({"messages": [{"role": "user", "content": "x" * 3000}] * 20 + [{"role": "assistant", "content": "claim"}], "user_request": "x" * 9000})
    assert len(sources["user_request"]) == 8000
    assert len(sources) == 12
    assert all(len(v) <= 2000 for k, v in sources.items() if k.startswith("message:"))