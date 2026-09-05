from pathlib import Path

from competition_app.services.textbook_route import TextbookRouteRepository


DATA_FILE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "textbook_routes"
    / "tcm_textbook_routes.v1.json"
)


def repository() -> TextbookRouteRepository:
    return TextbookRouteRepository.from_file(DATA_FILE)


def test_catalog_loads_seven_complete_textbook_routes() -> None:
    routes = repository().routes

    assert {route.goal_name for route in routes} == {
        "中医执业医师",
        "针灸推拿",
        "中医骨伤",
        "中药学",
        "中西医结合临床",
        "护理",
        "中医养生与治未病",
    }
    assert len({route.route_id for route in routes}) == 7
    assert all(route.status == "approved" for route in routes)
    assert all(stage.books for route in routes for stage in route.stages)
    assert all(stage.objective for route in routes for stage in route.stages)
    assert all(stage.exit_evidence for route in routes for stage in route.stages)


def test_physician_exam_uses_physician_textbook_route_by_default() -> None:
    result = repository().resolve(
        exam_route_id="tcm_physician_standard_degree",
        goal_text="我准备参加中医执业医师考试",
    )

    assert result.planning_status == "resolved"
    assert result.route is not None
    assert result.route.route_id == "textbook_tcm_physician"


def test_integrated_physician_exam_uses_integrated_textbook_route() -> None:
    result = repository().resolve(
        exam_route_id="tcm_physician_standard_degree",
        goal_text="我准备参加中西医结合执业医师考试",
    )

    assert result.planning_status == "resolved"
    assert result.route is not None
    assert result.route.route_id == "textbook_integrated_clinical"


def test_clinical_title_route_selects_specialty_textbook_route() -> None:
    acupuncture = repository().resolve(
        exam_route_id="health_technical_title_tcm_clinical",
        goal_text="针灸推拿职称考试",
    )
    orthopedics = repository().resolve(
        exam_route_id="health_technical_title_tcm_clinical",
        goal_text="中医骨伤职称考试",
    )

    assert acupuncture.route is not None
    assert acupuncture.route.route_id == "textbook_acupuncture_tuina"
    assert orthopedics.route is not None
    assert orthopedics.route.route_id == "textbook_tcm_orthopedics"


def test_pharmacy_backbone_is_shared_without_merging_exam_identity() -> None:
    licensed = repository().resolve(
        exam_route_id="licensed_pharmacist_tcm",
        goal_text="中药执业药师",
    )
    title = repository().resolve(
        exam_route_id="health_technical_title_tcm_pharmacy",
        goal_text="主管中药师职称考试",
    )

    assert licensed.route is not None
    assert title.route is not None
    assert licensed.route.route_id == title.route.route_id == "textbook_tcm_pharmacy"


def test_direction_without_an_exam_identity_requests_clarification() -> None:
    for goal in ("我想学护理准备考试", "我要考中医养生证"):
        result = repository().resolve(exam_route_id=None, goal_text=goal)

        assert result.planning_status == "needs_clarification"
        assert result.route is not None
        assert result.clarification_questions


def test_catalog_preserves_prerequisite_and_equivalence_rules() -> None:
    routes = {route.route_id: route for route in repository().routes}

    pharmacy = routes["textbook_tcm_pharmacy"]
    assert {rule.course for rule in pharmacy.prerequisites} >= {
        "无机化学",
        "有机化学",
        "细胞生物学",
        "分析化学",
    }
    physician = routes["textbook_tcm_physician"]
    alternatives = {
        alternative
        for group in physician.equivalence_groups
        for alternative in group.alternatives
    }
    assert "系统解剖学" in alternatives
    assert "生理学基础" in alternatives
    assert "病理学基础" in alternatives
