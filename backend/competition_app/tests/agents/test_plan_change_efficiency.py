import pytest

from competition_app.agents.diagnosis import DiagnosisAgent


class CapturingPlanChangeModel:
    def __init__(self) -> None:
        self.payload = None

    async def complete_json(self, role, payload, on_delta=None):
        self.payload = payload
        return {
            "long_term_action": "reuse",
            "short_term_action": "reuse",
            "daily_task_action": "reuse",
            "replan_requested": False,
            "changed_facts": [],
            "requires_clarification": False,
            "clarification_questions": [],
            "reason": "模型根据完整语义判断用户只是查看已有计划。",
            "decision_mode": "reuse_fast_path",
        }


@pytest.mark.asyncio
async def test_plan_change_semantic_gate_receives_compact_plan_briefs() -> None:
    model = CapturingPlanChangeModel()
    marker = "不应发送的完整计划明细"
    context = {
        "trace_id": "TRACE_CHANGE",
        "request_id": "REQ_CHANGE",
        "execution_id": "EXE_CHANGE",
        "step_id": "diagnosis",
        "learner_id": "LEARNER_CHANGE",
        "user_request": "我想看看当前计划是否还适合我。",
        "messages": [],
        "current_long_term_plan": {
            "status": "active",
            "version": 7,
            "content": "长期规划正文" + ("阶段安排" * 1000),
            "stages": [{"private_detail": marker}] * 100,
        },
        "current_short_term_plan": {"status": "active", "content": "短期计划正文"},
        "user_profile": {"learning_background": "零基础"},
        "multi_scale_learning_state": {"weaknesses": ["阴阳学说"]},
        "learning_monitoring": {"evidence_status": "sufficient"},
    }

    decision = await DiagnosisAgent(model)._assess_plan_change(
        context, "short_term"
    )

    business = model.payload["payload"]
    rendered = str(business)
    assert decision.decision_mode == "reuse_fast_path"
    assert business["existing_plans"]["long_term"]["version"] == 7
    assert len(business["existing_plans"]["long_term"]["content_summary"]) <= 1500
    assert marker not in rendered
    assert "stages" not in business["existing_plans"]["long_term"]

