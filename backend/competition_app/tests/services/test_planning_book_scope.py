import json
from pathlib import Path

import pytest

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.contracts.textbook_route import ResolvedTextbookRoute, TextbookLearningRoute
from competition_app.services.prerequisite_policy import required_courses_for_stage
from competition_app.services.planning_validator import PlanningValidator
from competition_app.tests.services.test_planning_validator import output, route


def pharmacy():
    data = json.loads((Path(__file__).parents[2] / "data/textbook_routes/tcm_textbook_routes.v1.json").read_text())
    rows = data["routes"]
    textbook = TextbookLearningRoute.model_validate(next(row for row in rows if row["route_id"] == "textbook_tcm_pharmacy"))
    return route().model_copy(update={"textbook_route": ResolvedTextbookRoute(planning_status="resolved", match_reason="test", route=textbook)})


@pytest.mark.parametrize("stage,books,expected", [
    ("stage-1", ["《中医学基础》"], []),
    ("stage-1", ["《无机化学实验》"], ["无机化学"]),
    ("stage-1", ["《有机化学实验》"], ["有机化学"]),
    ("stage-3", ["《方剂学》"], []),
    ("stage-5", ["《中药分析》"], ["分析化学"]),
])
def test_book_specific_prerequisites(stage, books, expected):
    assert required_courses_for_stage(pharmacy().textbook_route, stage, selected_books=books) == expected


@pytest.mark.parametrize("book,mode,expected", [
    ("《中医学基础》", "review", True),
    ("《中医学基础》", "diagnostic", True),
    ("《中医学基础》", "new_learning", False),
    ("《无机化学实验》", "new_learning", False),
    ("《有机化学实验》", "new_learning", False),
])
def test_completed_book_and_unknown_chemistry(book, mode, expected):
    value = output(selected_textbook_route_id="textbook_tcm_pharmacy", selected_stage_id="stage-1",
                   selected_books=[book], selection_reason="依据完成记录安排本周期", selection_mode=mode)
    result = PlanningValidator().validate(value, pharmacy(), active_scope="short_term",
                                         long_term_action="reuse", completed_textbooks={"《中医学基础》"})
    assert result.valid is expected, result.issues
    if "实验" in book:
        required = "无机化学" if book == "《无机化学实验》" else "有机化学"
        assert any(required in issue for issue in result.issues)


def test_completed_count_is_not_mastery():
    context = {"learning_path_progress": {"tool": "get_current_learning_state", "books": [
        {"book": "《中医学基础》", "availability": "available", "total_sections": 36, "completed_count": 36},
        {"book": "《方剂学》", "availability": "available", "total_sections": 40, "completed_count": 3},
    ]}}
    assert DiagnosisAgent._completed_textbooks(context) == {"《中医学基础》"}
    assert DiagnosisAgent._prerequisite_course_evidence(context, {"textbook_route": pharmacy().textbook_route.model_dump()}) == (set(), set())


def test_explicit_long_selection_survives_full_route_overview():
    textbook = pharmacy().textbook_route
    context = {"textbook_route": textbook.model_dump()}
    stages = [{"stage": s.order, "stage_name": s.name, "book": s.books, "goal": s.objective,
               "duration_days": 10, "schedule_summary": "按学习事实安排", "acceptance": ["真实测验"]}
              for s in textbook.route.stages]
    raw = {"plan_document": "当前stage-1复习《中医学基础》", "long_term_plan_content": "完整路线概览",
           "long_term_plan_stages": stages, "total_duration_days": 50,
           "selected_stage_id": "stage-1", "selected_books": ["《中医学基础》"],
           "selection_reason": "已完成36节，诊断薄弱处", "selection_mode": "diagnostic"}
    value = DiagnosisAgent._expand_scoped_planning_output("long_term", raw, {}, context)
    assert value.selected_books == ["《中医学基础》"]
    assert value.selection_mode == "diagnostic"
    assert value.long_term_plan_stages[0].book == textbook.route.stages[0].books
    assert value.long_term_plan_stages[0].acceptance == ["真实测验"]
    for key in ("selected_stage_id", "selected_books", "selection_reason", "selection_mode"):
        raw.pop(key)
    missing = DiagnosisAgent._expand_scoped_planning_output("long_term", raw, {}, context)
    assert missing.selected_books == []
    assert missing.selected_stage_id is None


def test_parent_stage_with_one_day_is_not_automatically_advanced():
    constraints = DiagnosisAgent._parent_plan_constraints({"current_long_term_plan": {
        "stages": [{"stage": 1, "duration_days": 1}, {"stage": 2, "duration_days": 14}],
        "textbook_selection": {"stage_id": "stage-1"},
    }}, "short_term")
    assert constraints["current_stage_id"] == "stage-1"
    assert constraints["current_stage_duration_days"] == 1


@pytest.mark.parametrize("snapshot_route", [None, "", "other-route"])
def test_unbound_snapshot_cannot_satisfy_chemistry(snapshot_route):
    context = {"path_candidates": {"prerequisite_evidence": {
        "route_id": snapshot_route, "required_courses": ["无机化学"], "satisfied_courses": ["无机化学"]}}}
    assert DiagnosisAgent._prerequisite_course_evidence(context, {"textbook_route": pharmacy().textbook_route.model_dump()}) == (set(), set())