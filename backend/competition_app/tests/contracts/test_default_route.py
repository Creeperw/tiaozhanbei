import pytest
from pydantic import ValidationError

from competition_app.contracts.default_route import (
    DefaultLearningRoute,
    ResolvedPlanningRoute,
)


def approved_route_data() -> dict[str, object]:
    return {
        "route_id": "ROUTE_TCM_LITERACY_V1",
        "route_version": 1,
        "route_status": "approved",
        "status": "approved",
        "goal_type": "literacy",
        "goal_name": "提升中医经典阅读能力",
        "phases": [
            {
                "phase_id": "FOUNDATION",
                "name": "基础阅读",
                "objective": "建立术语和句读基础。",
                "exit_evidence": ["完成一篇原文的断句与释义。"],
                "source_refs": ["TEXTBOOK_001"],
            }
        ],
        "sources": [
            {"source_id": "TEXTBOOK_001", "source_type": "textbook", "title": "中医经典选读"}
        ],
        "review_metadata": {
            "reviewed_by": "CURRICULUM_REVIEWER",
            "reviewed_at": "2026-07-17T00:00:00Z",
        },
        "aliases": ["中医经典导读"],
        "planning_label": "literacy_default_route",
        "personalization_rules": ["根据已有文言基础调整节奏。"],
        "refresh_rule": "教材版本更新后复核。",
        "runtime_checks": ["核验教材版本。"],
        "project_import_metadata": {
            "imported_from": "competition/reference.json",
            "imported_at": "2026-07-17T00:00:00Z",
            "import_policy": "应用自有副本。",
        },
    }


def test_approved_route_requires_sources_and_review_metadata() -> None:
    route = DefaultLearningRoute.model_validate(approved_route_data())

    assert route.status == "approved"
    assert route.phases[0].exit_evidence
    assert route.aliases == ["中医经典导读"]
    assert route.phases[0].source_refs == ["TEXTBOOK_001"]


@pytest.mark.parametrize(
    "field",
    [
        "sources",
        "review_metadata",
        "planning_label",
        "personalization_rules",
        "refresh_rule",
        "runtime_checks",
        "project_import_metadata",
    ],
)
def test_approved_route_rejects_missing_governance_metadata(field: str) -> None:
    data = approved_route_data()
    data.pop(field)

    with pytest.raises(ValidationError):
        DefaultLearningRoute.model_validate(data)


def test_provisional_resolution_records_assumptions() -> None:
    value = ResolvedPlanningRoute(
        goal_type="literacy",
        goal_name="提升中医经典阅读能力",
        planning_status="provisional",
        match_reason="没有匹配的已批准路线。",
        assumptions=["先按基础阅读能力设计临时阶段。"],
        unknowns_to_confirm=["目标典籍范围待确认。"],
    )

    assert value.route_id is None


def test_approved_resolution_requires_approved_route_identity() -> None:
    with pytest.raises(ValidationError):
        ResolvedPlanningRoute(
            goal_type="literacy",
            goal_name="提升中医经典阅读能力",
            planning_status="approved_route",
            match_reason="匹配已批准路线。",
        )