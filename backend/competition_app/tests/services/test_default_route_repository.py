from __future__ import annotations

import json
from pathlib import Path

import pytest

from competition_app.contracts.default_route import DefaultLearningRoute
from competition_app.services.default_route import DefaultRouteRepository


DATA_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "default_routes"
SOURCE_CATALOG = (
    Path(__file__).resolve().parents[3]
    / "competition"
    / "zhongyi-learning-planning"
    / "references"
    / "tcm-credential-default-routes.json"
)


@pytest.fixture
def repository() -> DefaultRouteRepository:
    return DefaultRouteRepository.from_directory(DATA_DIRECTORY)


def test_repository_resolves_explicit_id_to_approved_route(
    repository: DefaultRouteRepository,
) -> None:
    result = repository.resolve(
        goal_type="credential",
        goal_name="任意目标",
        explicit_route_id="tcm_physician_standard_degree",
    )

    assert result.planning_status == "approved_route"
    assert result.route_id == "tcm_physician_standard_degree"
    assert result.match_reason == "explicit_route_id"


def test_repository_normalizes_explicit_route_id_whitespace_and_case(
    repository: DefaultRouteRepository,
) -> None:
    route = repository.get("  TCM_PHYSICIAN_STANDARD_DEGREE  ")

    assert route is not None
    assert route.route_id == "tcm_physician_standard_degree"


def test_repository_resolves_canonical_name_to_approved_route(
    repository: DefaultRouteRepository,
) -> None:
    result = repository.resolve(
        goal_type="credential",
        goal_name="中医类别执业医师 / 执业助理医师（规定学历路径）",
    )

    assert result.planning_status == "approved_route"
    assert result.route_id == "tcm_physician_standard_degree"
    assert result.match_reason == "canonical_name"


def test_repository_resolves_physician_alias_to_approved_route(
    repository: DefaultRouteRepository,
) -> None:
    result = repository.resolve(goal_type="credential", goal_name="中医执业医师")

    assert result.planning_status == "approved_route"
    assert result.route_id == "tcm_physician_standard_degree"
    assert result.route_status == "approved"
    assert result.match_reason == "alias"


def test_repository_treats_multiple_named_credential_routes_as_ambiguous(
    repository: DefaultRouteRepository,
) -> None:
    result = repository.resolve(
        goal_type="credential",
        goal_name="规定学历、中医（专长）医师考核",
    )

    assert result.planning_status == "provisional"
    assert result.route_id is None
    assert result.match_reason == "unsupported_target"


def test_approved_resolution_carries_trusted_route_context(
    repository: DefaultRouteRepository,
) -> None:
    result = repository.resolve(goal_type="credential", goal_name="中医执业医师")

    assert result.planning_label == "synthesized_default_route"
    assert result.phases
    assert result.phases[0].exit_evidence
    assert result.sources
    assert result.sources[0].source_id
    assert result.runtime_checks


def test_repository_does_not_fall_back_to_an_unsupported_goal_type(
    repository: DefaultRouteRepository,
) -> None:
    result = repository.resolve(goal_type="vocational_skill", goal_name="不同的表达")

    assert result.planning_status == "provisional"
    assert result.route_id is None
    assert result.match_reason == "no_safe_match"


def test_repository_returns_provisional_for_ambiguous_goal_type(
    repository: DefaultRouteRepository,
) -> None:
    result = repository.resolve(goal_type="credential", goal_name="不同的表达")

    assert result.planning_status == "provisional"
    assert result.route_id is None
    assert result.match_reason == "ambiguous_goal_type"


def test_repository_falls_back_for_unmatched_literacy_goal(
    repository: DefaultRouteRepository,
) -> None:
    result = repository.resolve(goal_type="literacy", goal_name="中医经典阅读与表达")

    assert result.planning_status == "provisional"
    assert result.route_id is None
    assert result.match_reason == "no_safe_match"


@pytest.mark.parametrize("route_status", ["draft", "retired"])
def test_repository_never_resolves_non_approved_routes(
    tmp_path: Path, route_status: str
) -> None:
    route = {
        "route_id": f"{route_status}_route",
        "route_version": 1,
        "route_status": route_status,
        "status": route_status,
        "goal_type": route_status,
        "goal_name": f"{route_status} 路线",
        "phases": [
            {
                "phase_id": "phase-1",
                "name": "阶段一",
                "objective": "测试非正式路线不得匹配",
                "exit_evidence": ["测试证据"],
            }
        ],
    }
    (tmp_path / "routes.json").write_text(
        json.dumps({"routes": [route]}, ensure_ascii=False), encoding="utf-8"
    )
    repository = DefaultRouteRepository.from_directory(tmp_path)

    result = repository.resolve(
        goal_type=route_status,
        goal_name=f"{route_status} 路线",
        explicit_route_id=f"{route_status}_route",
    )

    assert result.planning_status == "provisional"
    assert result.route_id is None


def test_all_seed_routes_validate_and_include_governance_metadata() -> None:
    repository = DefaultRouteRepository.from_directory(DATA_DIRECTORY)

    for route in repository._routes:
        assert isinstance(route, DefaultLearningRoute)
        assert route.sources
        assert route.review_metadata is not None
        assert route.planning_label
        assert route.personalization_rules
        assert route.refresh_rule
        assert route.runtime_checks
        assert route.project_import_metadata is not None
        assert all(phase.source_refs for phase in route.phases)


def test_repository_retains_all_route_governance_fields(
    repository: DefaultRouteRepository,
) -> None:
    route = repository.get("tcm_physician_standard_degree")

    assert route is not None
    assert "中医执业医师" in route.aliases
    assert route.planning_label == "synthesized_default_route"
    assert route.personalization_rules
    assert route.refresh_rule
    assert route.runtime_checks
    assert route.project_import_metadata is not None
    assert route.project_import_metadata.imported_from
    assert all(phase.source_refs for phase in route.phases)


def test_route_selection_catalog_only_exposes_supported_exam_routes(
    repository: DefaultRouteRepository,
) -> None:
    assert {
        item["route_id"] for item in repository.route_selection_catalog()
    } == {
        "tcm_physician_standard_degree",
        "licensed_pharmacist_tcm",
    }


def test_retired_selection_targets_are_not_matched_for_new_requests(
    repository: DefaultRouteRepository,
) -> None:
    result = repository.resolve(
        goal_type="vocational_skill",
        goal_name="保健艾灸师",
    )

    assert result.planning_status == "provisional"
    assert result.route_id is None
    assert result.match_reason == "unsupported_target"


def test_application_seed_preserves_every_source_catalog_route_id() -> None:
    source_payload = json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))
    application_route_ids = {
        route["route_id"]
        for seed_file in DATA_DIRECTORY.glob("*.json")
        for route in DefaultRouteRepository._load_payload(seed_file)["routes"]
    }

    assert {route["route_id"] for route in source_payload["routes"]} == application_route_ids
