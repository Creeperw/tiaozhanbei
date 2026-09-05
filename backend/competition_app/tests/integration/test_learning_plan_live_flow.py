from __future__ import annotations

from dataclasses import replace
import os

import pytest

from competition_app.application.container import ApplicationContainer
from competition_app.application.personalized_review_card import ReviewCardRequest
from competition_app.config import Settings


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to use configured live model and embedding services",
)


def live_container(tmp_path) -> ApplicationContainer:
    settings = Settings.from_env()
    if settings.mode != "live":
        pytest.skip("COMPETITION_APP_MODE=live is required")
    return ApplicationContainer.build(
        replace(settings, mysql_password=None),
        snapshot_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_live_learning_plan_uses_approved_route(tmp_path) -> None:
    container = live_container(tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="LIVE_APPROVED_ROUTE",
            user_request="我准备参加中医执业医师考试，请制定长期学习路线。",
            available_minutes=30,
            plan_scope="long_term",
            user_profile={
                "goals": {"type": "credential", "name": "中医执业医师"}
            },
        )
    )

    route = result.learning_plan.long_term_plan.planning_route
    assert route.planning_status == "approved_route"
    assert route.route_id == "tcm_physician_standard_degree"
    assert result.learning_plan.long_term_plan.milestones
    assert result.learning_plan.short_term_plan is None
    assert result.learning_plan.learning_task is None
    assert result.snapshot_path.exists()


@pytest.mark.asyncio
async def test_live_learning_plan_uses_provisional_route_for_literacy_goal(tmp_path) -> None:
    container = live_container(tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="LIVE_PROVISIONAL_ROUTE",
            user_request="我想提升中医经典阅读与学术表达能力，每周学习五小时，请制定长期规划。",
            available_minutes=25,
            plan_scope="long_term",
            user_profile={
                "goals": {
                    "type": "literacy",
                    "name": "中医经典阅读与学术表达",
                }
            },
        )
    )

    plan = result.learning_plan
    route = plan.long_term_plan.planning_route
    assert route.planning_status == "provisional"
    assert route.route_id is None
    assert plan.long_term_plan.assumptions or plan.long_term_plan.unknowns_to_confirm
    assert result.snapshot_path.exists()


@pytest.mark.asyncio
async def test_live_specific_topic_supplies_route_and_knowledge_to_diagnosis(tmp_path) -> None:
    container = live_container(tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="LIVE_ROUTE_WITH_KNOWLEDGE",
            user_request="请结合教材为我制定四君子汤长期学习规划。",
            available_minutes=20,
            plan_scope="long_term",
            user_profile={
                "goals": {"type": "course", "name": "掌握四君子汤"}
            },
        )
    )

    producers = {output.producer for output in result.agent_outputs}
    assert {"default_route_resolver", "knowledge_base_agent", "diagnosis_agent"}.issubset(
        producers
    )
    diagnosis_call = next(
        item
        for item in result.model_trace
        if item.agent == "diagnosis_agent" and item.status == "success"
    )
    payload = diagnosis_call.input_payload["payload"]
    assert payload["route_context"]["planning_status"] == "provisional"
    assert payload["learning_data"]["evidence_summaries"]
    assert result.snapshot_path.exists()
