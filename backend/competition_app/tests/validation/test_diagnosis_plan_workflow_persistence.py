import json

import pytest

from competition_app.agents.planner import PlannerAgent
from competition_app.application.container import ApplicationContainer
from competition_app.application.personalized_review_card import ReviewCardRequest
from competition_app.config import Settings


@pytest.mark.asyncio
async def test_stub_workflow_persists_diagnosis_proposal_without_system_plan_ids(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="LEARNER_PLAN_REGRESSION",
            user_request="生成四君子汤复习卡",
            available_minutes=15,
        )
    )

    diagnosis = next(
        item for item in result.agent_outputs if item.producer == "diagnosis_agent"
    )
    assert diagnosis.payload.learning_plan_proposal is None
    assert result.learning_plan is None

    snapshot = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
    persisted_diagnosis = next(
        item for item in snapshot["agent_outputs"] if item["producer"] == "diagnosis_agent"
    )
    assert persisted_diagnosis["payload"]["learning_plan_proposal"] is None


def test_planner_materializes_mastery_review_template() -> None:
    from competition_app.agents.planner import PlannerDecision

    plan = PlannerAgent.build_plan(
        PlannerDecision(
            task_type="personalized_review_card",
            selected_agents=[
                "knowledge_base_agent",
                "default_route_resolver",
                "diagnosis_agent",
                "learning_plan_service",
                "review_scheduler",
                "expert_agent",
                "audit_agent",
            ],
            routing_reason="生成资源需要完整链路",
        )
    )

    assert [step.step_id for step in plan.steps] == [
        "knowledge",
        "route_resolution",
        "diagnosis",
        "schedule",
        "expert",
        "audit",
    ]
    diagnosis_step = next(step for step in plan.steps if step.step_id == "diagnosis")
    assert diagnosis_step.agent == "diagnosis_agent"
    assert set(diagnosis_step.depends_on) == {"knowledge", "route_resolution"}
    assert "learning_plan_service" not in [step.agent for step in plan.steps]