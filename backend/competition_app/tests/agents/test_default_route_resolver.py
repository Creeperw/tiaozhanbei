from __future__ import annotations

from pathlib import Path

import pytest

from competition_app.agents.default_route_resolver import DefaultRouteResolverAgent
from competition_app.services.default_route import DefaultRouteRepository
from competition_app.services.textbook_route import TextbookRouteRepository


DATA_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "default_routes"
TEXTBOOK_DATA_FILE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "textbook_routes"
    / "tcm_textbook_routes.v1.json"
)


@pytest.fixture
def repository() -> DefaultRouteRepository:
    return DefaultRouteRepository.from_directory(DATA_DIRECTORY)


@pytest.fixture
def textbook_repository() -> TextbookRouteRepository:
    return TextbookRouteRepository.from_file(TEXTBOOK_DATA_FILE)


def agent_context() -> dict[str, str]:
    return {
        "case_id": "CASE_1",
        "trace_id": "TRACE_1",
        "request_id": "REQUEST_1",
        "execution_id": "EXECUTION_1",
        "step_id": "default_route_resolver",
        "task_type": "learning_plan",
        "learner_id": "LEARNER_1",
    }


class RouteDecisionModel:
    def __init__(self, decision: dict) -> None:
        self.decision = decision
        self.payload = None

    async def complete_json(self, role, payload, on_delta=None):
        assert role == "default_route_resolver"
        self.payload = payload
        return self.decision


@pytest.mark.asyncio
async def test_resolver_prefers_structured_profile_goal(
    repository: DefaultRouteRepository,
) -> None:
    result = await DefaultRouteResolverAgent(repository).run(
        {
            **agent_context(),
            "user_request": "帮我制定计划",
            "user_profile": {"goals": {"type": "credential", "name": "中医执业医师"}},
        }
    )

    assert result.producer == "default_route_resolver"
    assert result.artifact_type == "resolved_planning_route"
    assert result.payload.planning_status == "approved_route"
    assert result.payload.goal_type == "credential"
    assert result.payload.match_reason == "alias"
    assert result.payload.planning_label == "synthesized_default_route"
    assert result.payload.phases
    assert result.payload.sources
    assert result.payload.runtime_checks


@pytest.mark.asyncio
async def test_resolver_uses_first_structured_goal_from_list_before_request(
    repository: DefaultRouteRepository,
) -> None:
    result = await DefaultRouteResolverAgent(repository).run(
        {
            **agent_context(),
            "user_request": "我想提升阅读能力",
            "user_profile": {
                "goals": [
                    {"goal_type": "credential", "goal_name": "中医执业医师"},
                    {"type": "literacy", "name": "中医经典阅读"},
                ]
            },
        }
    )

    assert result.payload.planning_status == "approved_route"
    assert result.payload.goal_type == "credential"
    assert result.payload.match_reason == "alias"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_request",
    [
        "我正在进行中医药课题研究，请制定研究计划",
        "我想阅读中医药文献并开展研究",
    ],
)
async def test_resolver_prioritizes_research_over_course_like_terms(
    repository: DefaultRouteRepository, user_request: str
) -> None:
    result = await DefaultRouteResolverAgent(repository).run(
        {**agent_context(), "user_request": user_request, "user_profile": {}}
    )

    assert result.payload.goal_type == "research"
    assert result.payload.goal_name == user_request


@pytest.mark.asyncio
async def test_resolver_uses_structured_type_with_request_when_name_is_missing(
    repository: DefaultRouteRepository,
) -> None:
    result = await DefaultRouteResolverAgent(repository).run(
        {
            **agent_context(),
            "user_request": "帮我制定中医执业医师复习计划",
            "user_profile": {"goals": {"goal_type": "research"}},
        }
    )

    assert result.payload.goal_type == "research"
    assert result.payload.goal_name == "帮我制定中医执业医师复习计划"


@pytest.mark.asyncio
async def test_resolver_preserves_structured_name_and_classifies_it_conservatively(
    repository: DefaultRouteRepository,
) -> None:
    result = await DefaultRouteResolverAgent(repository).run(
        {
            **agent_context(),
            "user_request": "帮我制定计划",
            "user_profile": {"goals": {"goal_name": "中医药课题研究"}},
        }
    )

    assert result.payload.goal_type == "research"
    assert result.payload.goal_name == "中医药课题研究"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_request", "goal_type"),
    [
        ("我想备考中医执业医师资格证", "credential"),
        ("帮我准备研究生入学考试", "admission"),
        ("制定针灸学课程学习计划", "course"),
        ("提升中医辨证论治能力", "competency"),
        ("培养中医经典阅读素养", "literacy"),
        ("开展中医药文献研究", "research"),
        ("养成每日背诵方剂的习惯", "habit"),
    ],
)
async def test_resolver_conservatively_classifies_supported_natural_language_goals(
    repository: DefaultRouteRepository, user_request: str, goal_type: str
) -> None:
    result = await DefaultRouteResolverAgent(repository).run(
        {**agent_context(), "user_request": user_request, "user_profile": {}}
    )

    assert result.payload.goal_type == goal_type
    assert result.payload.goal_name == user_request


@pytest.mark.asyncio
async def test_resolver_matches_unique_alias_embedded_in_goal_request(
    repository: DefaultRouteRepository,
) -> None:
    result = await DefaultRouteResolverAgent(repository).run(
        {
            **agent_context(),
            "user_request": (
                "我准备参加中医执业医师考试，每周可以学习8小时，"
                "请根据默认路线制定长期阶段规划。"
            ),
            "user_profile": {
                "goals": {
                    "long_term_goal": "参加中医执业医师考试",
                    "short_term_goal": "建立中医基础理论与诊断框架",
                }
            },
        }
    )

    assert result.payload.planning_status == "approved_route"
    assert result.payload.route_id == "tcm_physician_standard_degree"
    assert result.payload.match_reason == "embedded_alias"


@pytest.mark.asyncio
async def test_resolver_honors_explicit_route_id(
    repository: DefaultRouteRepository,
) -> None:
    result = await DefaultRouteResolverAgent(repository).run(
        {
            **agent_context(),
            "user_request": "帮我制定计划",
            "user_profile": {},
            "route_id": "tcm_physician_standard_degree",
        }
    )

    assert result.payload.planning_status == "approved_route"
    assert result.payload.match_reason == "explicit_route_id"


@pytest.mark.asyncio
async def test_resolver_marks_unknown_target_provisional_with_uncertainty(
    repository: DefaultRouteRepository,
) -> None:
    result = await DefaultRouteResolverAgent(repository).run(
        {
            **agent_context(),
            "user_request": "帮我规划一个未来方向",
            "user_profile": {},
        }
    )

    assert result.payload.planning_status == "provisional"
    assert result.payload.goal_type == "literacy"
    assert result.payload.goal_name == "帮我规划一个未来方向"
    assert result.payload.unknowns_to_confirm
    assert any("无法确定" in item for item in result.payload.unknowns_to_confirm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_request", "exam_route_id", "textbook_route_id"),
    [
        ("制定中医执业医师考试学习计划", "tcm_physician_standard_degree", "textbook_tcm_physician"),
        ("制定中西医结合执业医师学习计划", "tcm_physician_standard_degree", "textbook_integrated_clinical"),
        ("制定针灸推拿职称考试计划", "health_technical_title_tcm_clinical", "textbook_acupuncture_tuina"),
        ("制定中医骨伤职称考试计划", "health_technical_title_tcm_clinical", "textbook_tcm_orthopedics"),
        ("制定中药学专业职称考试计划", "health_technical_title_tcm_pharmacy", "textbook_tcm_pharmacy"),
        ("制定中药执业药师学习计划", "licensed_pharmacist_tcm", "textbook_tcm_pharmacy"),
    ],
)
async def test_resolver_carries_bound_textbook_route(
    repository: DefaultRouteRepository,
    textbook_repository: TextbookRouteRepository,
    user_request: str,
    exam_route_id: str,
    textbook_route_id: str,
) -> None:
    result = await DefaultRouteResolverAgent(repository, textbook_repository).run(
        {**agent_context(), "user_request": user_request, "user_profile": {}}
    )

    assert result.payload.route_id == exam_route_id
    assert result.payload.textbook_route is not None
    assert result.payload.textbook_route.planning_status == "resolved"
    assert result.payload.textbook_route.route is not None
    assert result.payload.textbook_route.route.route_id == textbook_route_id


@pytest.mark.asyncio
async def test_resolver_preserves_textbook_clarification_for_vague_nursing_exam(
    repository: DefaultRouteRepository,
    textbook_repository: TextbookRouteRepository,
) -> None:
    result = await DefaultRouteResolverAgent(repository, textbook_repository).run(
        {
            **agent_context(),
            "user_request": "我想学护理准备考试",
            "user_profile": {},
        }
    )

    assert result.payload.textbook_route is not None
    assert result.payload.textbook_route.planning_status == "needs_clarification"
    assert result.payload.textbook_route.clarification_questions


@pytest.mark.asyncio
async def test_route_agent_asks_before_treating_formula_subject_as_course(
    repository: DefaultRouteRepository,
    textbook_repository: TextbookRouteRepository,
) -> None:
    model = RouteDecisionModel(
        {
            "decision": "clarify",
            "selected_route_id": None,
            "confidence": 0.93,
            "reason": "方剂学既可能是独立课程，也可能服务于考试目标。",
            "clarification_question": "学习方剂学是单独课程学习，还是为了具体考试？",
        }
    )

    result = await DefaultRouteResolverAgent(
        repository, textbook_repository, model
    ).run(
        {
            **agent_context(),
            "plan_scope": "long_term",
            "user_request": "请结合我的真实掌握状态制定长期规划",
            "user_profile": {
                "goals": {"long_term_goal": "建立方剂学知识体系"}
            },
        }
    )

    assert result.payload.planning_status == "provisional"
    assert result.payload.match_reason == "agent_requires_clarification"
    assert result.payload.route_id is None
    assert result.payload.unknowns_to_confirm == [
        "学习方剂学是单独课程学习，还是为了具体考试？"
    ]
    catalog = model.payload["payload"]["route_catalog"]
    assert {item["route_id"] for item in catalog} >= {
        "tcm_formula_course",
        "tcm_physician_standard_degree",
        "licensed_pharmacist_tcm",
    }


@pytest.mark.asyncio
async def test_route_agent_selects_only_an_approved_catalog_route(
    repository: DefaultRouteRepository,
    textbook_repository: TextbookRouteRepository,
) -> None:
    model = RouteDecisionModel(
        {
            "decision": "select",
            "selected_route_id": "tcm_physician_standard_degree",
            "confidence": 0.96,
            "reason": "用户明确准备中医执业医师考试。",
            "clarification_question": None,
        }
    )

    result = await DefaultRouteResolverAgent(
        repository, textbook_repository, model
    ).run(
        {
            **agent_context(),
            "plan_scope": "long_term",
            "user_request": "制定中医执业医师考试长期规划",
            "user_profile": {},
        }
    )

    assert result.payload.route_id == "tcm_physician_standard_degree"
    assert result.payload.match_reason == "agent_selected"
    assert result.payload.textbook_route is not None
    assert result.payload.textbook_route.planning_status == "resolved"
    assert result.payload.textbook_route.route.route_id == "textbook_tcm_physician"


@pytest.mark.asyncio
async def test_route_agent_uses_catalog_when_model_over_clarifies_exact_exam_alias(
    repository: DefaultRouteRepository,
    textbook_repository: TextbookRouteRepository,
) -> None:
    model = RouteDecisionModel(
        {
            "decision": "clarify",
            "selected_route_id": None,
            "confidence": 0.9,
            "reason": "需要继续确认考试路径。",
            "clarification_question": "是规定学历还是师承路径？",
        }
    )

    result = await DefaultRouteResolverAgent(
        repository,
        textbook_repository,
        model,
    ).run(
        {
            **agent_context(),
            "plan_scope": "long_term",
            "user_request": (
                "请结合我的学习状态，给我制定一份长期学习计划。\n"
                "用户补充的具体变化：我想考中医执业医师资格考试"
            ),
            "user_profile": {},
        }
    )

    assert result.payload.planning_status == "approved_route"
    assert result.payload.route_id == "tcm_physician_standard_degree"
    assert result.payload.match_reason == "agent_catalog_fallback"
    assert result.payload.textbook_route is not None
    assert result.payload.textbook_route.planning_status == "resolved"


@pytest.mark.asyncio
async def test_route_agent_keeps_course_only_formula_goal_ambiguous(
    repository: DefaultRouteRepository,
    textbook_repository: TextbookRouteRepository,
) -> None:
    model = RouteDecisionModel(
        {
            "decision": "clarify",
            "selected_route_id": None,
            "confidence": 0.95,
            "reason": "方剂学可能是课程、考试或升学目标。",
            "clarification_question": "学习方剂学是课程学习还是为了考试？",
        }
    )

    result = await DefaultRouteResolverAgent(
        repository,
        textbook_repository,
        model,
    ).run(
        {
            **agent_context(),
            "plan_scope": "long_term",
            "user_request": "请制定方剂学长期学习计划",
            "user_profile": {},
        }
    )

    assert result.payload.planning_status == "provisional"
    assert result.payload.match_reason == "agent_requires_clarification"


@pytest.mark.asyncio
async def test_route_agent_cannot_invent_a_route_id(
    repository: DefaultRouteRepository,
    textbook_repository: TextbookRouteRepository,
) -> None:
    model = RouteDecisionModel(
        {
            "decision": "select",
            "selected_route_id": "invented_postgraduate_route",
            "confidence": 0.99,
            "reason": "模型虚构路线。",
            "clarification_question": None,
        }
    )

    result = await DefaultRouteResolverAgent(
        repository, textbook_repository, model
    ).run(
        {
            **agent_context(),
            "plan_scope": "long_term",
            "user_request": "我要准备考研",
            "user_profile": {},
        }
    )

    assert result.payload.planning_status == "provisional"
    assert result.payload.match_reason == "agent_requires_clarification"
    assert result.payload.route_id is None
    assert any("路线" in item for item in result.payload.unknowns_to_confirm)


@pytest.mark.asyncio
async def test_short_term_route_inherits_the_formal_long_term_parent(
    repository: DefaultRouteRepository,
    textbook_repository: TextbookRouteRepository,
) -> None:
    long_resolution = await DefaultRouteResolverAgent(
        repository, textbook_repository
    ).run(
        {
            **agent_context(),
            "user_request": "制定中医执业医师考试长期规划",
            "user_profile": {
                "goals": {"type": "credential", "name": "中医执业医师"}
            },
        }
    )

    result = await DefaultRouteResolverAgent(
        repository, textbook_repository, RouteDecisionModel({})
    ).run(
        {
            **agent_context(),
            "plan_scope": "short_term",
            "user_request": "结合长期规划制定短期计划",
            "user_profile": {
                "goals": {"long_term_goal": "建立方剂学知识体系"}
            },
            "current_long_term_plan": {
                "content": "已有中医执业医师长期规划",
                "planning_route": long_resolution.payload.model_dump(mode="json"),
            },
        }
    )

    assert result.payload.route_id == "tcm_physician_standard_degree"
    assert result.payload.textbook_route is not None
    assert result.payload.textbook_route.route.route_id == "textbook_tcm_physician"
    assert result.payload.match_reason == "inherited_long_term_plan"


@pytest.mark.asyncio
async def test_generic_long_term_request_uses_active_learning_target_without_reasking(
    repository: DefaultRouteRepository,
    textbook_repository: TextbookRouteRepository,
) -> None:
    model = RouteDecisionModel(
        {
            "decision": "clarify",
            "selected_route_id": None,
            "confidence": 0.99,
            "reason": "不应调用模型重复追问已锁定目标。",
            "clarification_question": "请再次说明考试目标。",
        }
    )

    result = await DefaultRouteResolverAgent(
        repository, textbook_repository, model
    ).run(
        {
            **agent_context(),
            "plan_scope": "long_term",
            "user_request": "请结合我的学习状态，给我制定一份长期规划。",
            "user_profile": {},
            "learning_target": {
                "target_type": "certification",
                "exam_name": "中医执业医师",
                "is_active": True,
                "is_locked": True,
            },
        }
    )

    assert result.payload.route_id == "tcm_physician_standard_degree"
    assert result.payload.match_reason == "active_learning_target"
    assert result.payload.textbook_route.route.route_id == "textbook_tcm_physician"
    assert model.payload is None


@pytest.mark.asyncio
async def test_generic_long_term_request_can_recover_route_from_existing_short_plan(
    repository: DefaultRouteRepository,
    textbook_repository: TextbookRouteRepository,
) -> None:
    approved = repository.resolve(
        goal_type="credential", goal_name="中医执业医师"
    )

    result = await DefaultRouteResolverAgent(
        repository, textbook_repository, RouteDecisionModel({})
    ).run(
        {
            **agent_context(),
            "plan_scope": "long_term",
            "user_request": "请结合我的学习状态，给我制定一份长期规划。",
            "user_profile": {},
            "current_short_term_plan": {
                "content": "已有短期计划",
                "planning_route": approved.model_dump(mode="json"),
            },
        }
    )

    assert result.payload.route_id == "tcm_physician_standard_degree"
    assert result.payload.match_reason == "inherited_current_plan"


@pytest.mark.asyncio
async def test_inherited_textbook_route_is_not_changed_by_keywords_in_plan_prose(
    repository: DefaultRouteRepository,
    textbook_repository: TextbookRouteRepository,
) -> None:
    parent = await DefaultRouteResolverAgent(
        repository, textbook_repository
    ).run(
        {
            **agent_context(),
            "user_request": "制定中医执业医师考试长期规划",
            "user_profile": {
                "goals": {"type": "credential", "name": "中医执业医师"}
            },
        }
    )

    result = await DefaultRouteResolverAgent(
        repository, textbook_repository, RouteDecisionModel({})
    ).run(
        {
            **agent_context(),
            "plan_scope": "long_term",
            "user_request": "请结合我的学习状态，给我制定一份长期规划。",
            "current_long_term_plan": {
                "content": "训练中西医结合分析能力，但考试目标不变。",
                "planning_route": parent.payload.model_dump(mode="json"),
            },
        }
    )

    assert result.payload.route_id == "tcm_physician_standard_degree"
    assert result.payload.textbook_route.route.route_id == "textbook_tcm_physician"
