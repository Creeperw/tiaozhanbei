from datetime import datetime, timezone

from competition_app.contracts.learning_plan import LongTermPlan, LongTermPlanStage
from competition_app.contracts.default_route import (
    DefaultRoutePhase,
    ResolvedPlanningRoute,
)
from competition_app.services.learning_path_projection import LearningPathProjectionService


def _plan() -> LongTermPlan:
    now = datetime.now(timezone.utc)
    return LongTermPlan(
        plan_id="LP_TEST",
        learner_id="LEARNER_TEST",
        content="长期规划",
        version=2,
        status="active",
        created_at=now,
        updated_at=now,
        stages=[
            LongTermPlanStage(stage=1, book=["《中医学基础》", "《医古文》"], goal="建立基础。"),
            LongTermPlanStage(stage=2, book=["《方剂学》"], goal="学习方剂。"),
        ],
    )


def _loader(book: str, offset: int, limit: int) -> dict:
    rows = {
        "中医学基础": [
            {"kp_id": "KP_1", "name": "阴阳学说", "chapter": "基础理论"},
            {"kp_id": "KP_2", "name": "五行学说", "chapter": "基础理论"},
        ]
    }.get(book, [])
    return {"items": rows[offset : offset + limit], "total": len(rows)}


def test_projects_stage_book_and_knowledge_point_pages() -> None:
    service = LearningPathProjectionService(_loader)
    root = service.page(learner_id="LEARNER_TEST", plan=_plan())

    assert root.schema_version == "1.0"
    assert [node.node_type for node in root.nodes] == ["stage", "stage"]
    assert root.nodes[0].status == "in_progress"
    assert root.nodes[0].child_count == 2

    books = service.page(
        learner_id="LEARNER_TEST", plan=_plan(), parent_id=root.nodes[0].node_id
    )
    assert [node.title for node in books.nodes] == ["《中医学基础》", "《医古文》"]
    assert books.nodes[0].navigation.book == "中医学基础"
    assert books.nodes[0].child_count == 2

    points = service.page(
        learner_id="LEARNER_TEST",
        plan=_plan(),
        parent_id=books.nodes[0].node_id,
        mastery_rows=[{"kp_id": "KP_1", "mastery": 0.85}],
    )
    assert points.parent_type == "book"
    assert points.nodes[0].node_type == "knowledge_point"
    assert points.nodes[0].status == "completed"
    assert points.nodes[0].navigation.kp_id == "KP_1"


def test_unknown_parent_is_rejected() -> None:
    service = LearningPathProjectionService(_loader)
    try:
        service.page(learner_id="LEARNER_TEST", plan=_plan(), parent_id="missing")
    except KeyError as exc:
        assert "parent node" in str(exc)
    else:
        raise AssertionError("unknown parent should fail")


def test_book_navigation_uses_a_route_that_contains_the_book() -> None:
    assert LearningPathProjectionService._atlas_route_id(
        "textbook_tcm_physician", ["textbook_14_5", "postgraduate"]
    ) == "textbook_14_5"
    assert LearningPathProjectionService._atlas_route_id(
        "textbook_tcm_physician", ["textbook_14_5", "tcm_assistant"]
    ) == "tcm_assistant"


def test_phase_names_drive_stage_titles_without_a_textbook_route() -> None:
    plan = _plan().model_copy(update={
        "planning_route": ResolvedPlanningRoute(
            goal_type="credential",
            goal_name="传统医学师承和确有专长人员医师资格考核",
            planning_status="approved_route",
            match_reason="用户已确认师承路径。",
            route_id="traditional_medicine_apprenticeship",
            route_version=1,
            route_status="approved",
            phases=[
                DefaultRoutePhase(
                    phase_id="phase-0",
                    name="师承关系与考核目标核验",
                    objective="核验材料。",
                    books=[],
                    exit_evidence=["材料已核验"],
                    source_refs=["official-rule"],
                ),
                DefaultRoutePhase(
                    phase_id="phase-1",
                    name="指导老师经验与中医基础",
                    objective="建立理论基础。",
                    books=[],
                    exit_evidence=["完成基础测评"],
                    source_refs=["official-rule"],
                ),
            ],
        )
    })

    root = LearningPathProjectionService(_loader).page(
        learner_id="LEARNER_TEST", plan=plan
    )

    assert [node.title for node in root.nodes] == [
        "师承关系与考核目标核验",
        "指导老师经验与中医基础",
    ]
    assert root.plan_ref.route_id == "traditional_medicine_apprenticeship"
    assert root.nodes[0].source_refs == ["official-rule"]
