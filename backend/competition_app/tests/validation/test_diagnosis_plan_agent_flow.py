from typing import Any

import pytest

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.default_route import ResolvedPlanningRoute
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack


class CapturingPlanModel:
    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None

    async def complete_json(self, role, payload, on_delta=None):
        self.payload = payload
        return {
            "summary": "近期回忆不稳。",
            "risk_flags": ["间隔偏长"],
            "recommendations": ["完成主动回忆"],
            "uncertainty": [],
            "long_term_plan_content": (
                "【最终目标】稳定掌握四君子汤。"
                "【能力路径与阶段】组成→功效→应用。"
                "【阶段里程碑】完成闭卷说明；截止两周后。"
                "【资源预算】每天10分钟，缓冲待确认。"
                "【重规划条件】连续两次不达标时调整。"
                "【保温底线】每周一次回忆。"
            ),
            "short_term_plan_content": (
                "【当前主目标】今天先回忆再纠错。"
                "【长期目标保温】本周保留一次回忆。"
                "【时间分配】10分钟用于当前任务。"
                "【具体任务块】默写并核对，产出默写结果，完成标准为四味药正确。"
                "【复习任务】完成后纠错复述。"
                "【反馈指标】记录正确率和错因。"
            ),
            "priority_mode": "recovery",
            "adjustment_reason": "当前掌握度较低。",
            "route_context": {
                "goal_type": "literacy",
                "goal_name": "模型尝试改写的目标",
                "planning_status": "approved_route",
                "match_reason": "model_claim",
                "route_id": "MODEL_ROUTE",
                "route_version": 99,
                "route_status": "approved",
                "phases": [],
                "sources": [],
                "assumptions": ["模型假设"],
                "unknowns_to_confirm": ["模型待确认"],
                "runtime_checks": [],
            },
            "goal_contract": {
                "goal_type": "course",
                "goal_name": "四君子汤学习",
                "observable_ability": "能够闭卷说明组成和配伍。",
                "acceptance_evidence": ["闭卷说明记录"],
            },
            "milestones": [{
                "milestone_id": "M1",
                "name": "完成组成回忆",
                "success_criteria": "闭卷写出全部组成。",
                "evidence_required": ["闭卷默写记录"],
            }],
            "short_term_learning_package": {
                "time_window_weeks": 1,
                "current_goal": "一周内稳定回忆组成。",
                "task_blocks": ["默写", "核对", "订正"],
                "expected_output": "默写与订正记录。",
                "completion_criteria": "四味药全部正确。",
            },
            "recovery_policy": {
                "trigger_conditions": ["连续两次未达标"],
                "recovery_actions": ["缩小任务并回到组成回忆"],
            },
            "recommendation_trace": {
                "default_route": "依据可信课程路线。",
                "user_state": "当前掌握度较低。",
                "time_constraint": "当前可用18分钟。",
                "current_task": "执行10分钟默写与核对。",
            },
            "assumptions": ["模型假设"],
            "unknowns_to_confirm": ["模型待确认"],
            "learning_task": {
                "task_type": "active_recall",
                "task_content": "默写组成并核对。",
                "estimated_minutes": 10,
                "expected_output": "一份默写结果。",
                "completion_criteria": "四味药全部正确。",
            },
        }


class RevisingPlanModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete_json(self, role, payload, on_delta=None):
        self.calls.append(payload)
        long_content = (
            "## 目标契约\n掌握方剂学。\n"
            "## 长期阶段路径\n基础阶段使用《方剂学》，通过闭卷测评后晋级。\n"
            "## 长期重规划触发器\n目标稳定变化时调整。"
        )
        daily = (
            "## 今日目标\n练习游泳。\n## 分步动作和时间分配\n下水。\n"
            "## 客观完成标准\n游完五百米。"
            if len(self.calls) == 1
            else "## 今日目标\n回忆四君子汤。\n## 分步动作和时间分配\n闭卷默写。\n## 客观完成标准\n组成正确。"
        )
        return {
            "long_term_plan_content": long_content,
            "short_term_plan_content": (
                "## 当前周期目标\n本周复习四君子汤。\n"
                "## 本周期任务\n周初闭卷回忆四君子汤，周中完成纠错复述，周末验收。\n"
                "## 短期重规划触发器\n连续未完成时调整。"
            ),
            "daily_task_content": daily,
            "estimated_minutes": 10,
            "expected_output": "一份默写。",
            "completion_criteria": "组成正确。",
            "long_term_plan_stages": [
                {
                    "stage": 1,
                    "book": ["《方剂学》"],
                    "goal": "掌握基础理论",
                }
            ],
        }


def knowledge_output() -> AgentEnvelope[EvidencePack]:
    return AgentEnvelope(
        artifact_id="ART_KNOWLEDGE",
        artifact_type="evidence_pack",
        case_id="CASE_1",
        trace_id="TRACE_1",
        request_id="REQ_1",
        execution_id="EXE_1",
        step_id="knowledge",
        producer="knowledge_base_agent",
        task_type="knowledge_result",
        learner_id="LEARNER_1",
        payload=EvidencePack(
            evidence_pack_id="EP_1",
            query="四君子汤",
            resolved_kp_ids=["KP_TRUSTED_1"],
            evidence_items=[
                EvidenceItem(
                    evidence_id="E_1",
                    source_id="教材:1",
                    content_summary="四君子汤由四味药组成。",
                    authority_level="textbook",
                    confidence=0.95,
                    bridge_layer="strict",
                )
            ],
        ),
    )


def route_output() -> AgentEnvelope[ResolvedPlanningRoute]:
    return AgentEnvelope(
        artifact_id="ART_ROUTE",
        artifact_type="resolved_planning_route",
        case_id="CASE_1",
        trace_id="TRACE_1",
        request_id="REQ_1",
        execution_id="EXE_1",
        step_id="route_resolution",
        producer="default_route_resolver",
        task_type="resolved_planning_route",
        learner_id="LEARNER_1",
        payload=ResolvedPlanningRoute(
            goal_type="course",
            goal_name="四君子汤学习",
            planning_status="approved_route",
            match_reason="canonical_name",
            route_id="ROUTE_TRUSTED",
            route_version=3,
            route_status="approved",
        ),
    )


def test_diagnosis_exposes_only_relevant_existing_plan_semantics() -> None:
    context = {
        "current_long_term_plan": {
            "plan_id": "LP_LONG_INTERNAL",
            "version": 7,
            "status": "active",
            "content": "长期规划正文",
        },
        "current_short_term_plan": {
            "plan_id": "LP_SHORT_INTERNAL",
            "content": "短期计划正文",
        },
        "current_learning_task": {
            "task_id": "TASK_INTERNAL",
            "task_content": "闭卷默写",
            "estimated_minutes": 15,
            "expected_output": "一份默写",
            "completion_criteria": "全部正确",
        },
    }

    short_context = DiagnosisAgent._model_existing_plans(context, "short_term")
    assert short_context == {
        "long_term": {"content": "长期规划正文"},
        "short_term": {"content": "短期计划正文"},
    }
    assert "LP_LONG_INTERNAL" not in str(short_context)
    assert "TASK_INTERNAL" not in str(short_context)

    daily_context = DiagnosisAgent._model_existing_plans(context, "daily_task")
    assert daily_context["daily_task"] == {
        "task_content": "闭卷默写",
        "estimated_minutes": 15,
        "expected_output": "一份默写",
        "completion_criteria": "全部正确",
    }


@pytest.mark.asyncio
async def test_diagnosis_consumes_semantics_and_injects_trusted_system_facts() -> None:
    model = CapturingPlanModel()
    context = {
        "case_id": "CASE_1",
        "trace_id": "TRACE_1",
        "request_id": "REQ_1",
        "execution_id": "EXE_1",
        "step_id": "diagnosis",
        "learner_id": "LEARNER_1",
        "topic": "四君子汤",
        "available_minutes": 18,
        "user_profile": {"learning_goals": ["掌握组成"]},
        "learning_profile": {"behavior_metrics": {"recent_accuracy": 0.55}},
        "system_data": {"current_stage_id": "STAGE_TRUSTED", "target_difficulty": 4},
        "user_knowledge_states": [{"kp_id": "KP_TRUSTED_1", "review_status": "due"}],
        "dependency_outputs": {
            "knowledge": knowledge_output(),
            "route_resolution": route_output(),
        },
    }

    result = (await DiagnosisAgent(model).run(context)).payload

    assert model.payload is not None
    assert model.payload["target_agent"] == "diagnosis_agent"
    facts = model.payload["payload"]
    assert facts["time_constraints"]["available_minutes_today"] == 18
    assert facts["learning_evidence"]["behavior_summary"] == {
        "current_stage_id": "STAGE_TRUSTED",
        "target_difficulty": 4,
    }
    assert facts["goals"] == ["掌握组成"]
    assert facts["learning_evidence"]["evidence_summaries"] == ["四君子汤由四味药组成。"]
    assert "user_knowledge_states" not in facts
    assert "route_id" not in facts["default_route"]
    assert result.stage_id == "STAGE_TRUSTED"
    assert result.weak_kp_ids == ["KP_TRUSTED_1"]
    assert result.target_difficulty == 4
    assert result.daily_review_policy.target_difficulty == 4
    assert result.learning_plan_proposal.priority_mode == "recovery"
    assert result.learning_plan_proposal.task_proposal.estimated_minutes == 10
    assert result.learning_plan_proposal.planning_route.route_id == "ROUTE_TRUSTED"
    assert result.learning_plan_proposal.planning_route.route_version == 3
    assert result.learning_plan_proposal.planning_route.planning_status == "approved_route"
    assert result.learning_plan_proposal.milestones[0].evidence_required == ["一份默写结果。"]
    assert result.learning_plan_proposal.short_term_learning_package.time_window_weeks == 1
    assert set(result.learning_plan_proposal.recommendation_trace.model_dump()) == {
        "default_route",
        "user_state",
        "time_constraint",
        "current_task",
    }


@pytest.mark.asyncio
async def test_diagnosis_result_keeps_system_fields_outside_model_proposal() -> None:
    model = CapturingPlanModel()
    context = {
        "case_id": "CASE_1",
        "trace_id": "TRACE_1",
        "request_id": "REQ_1",
        "execution_id": "EXE_1",
        "step_id": "diagnosis",
        "learner_id": "LEARNER_1",
        "topic": "四君子汤",
        "system_data": {"current_stage_id": "STAGE_TRUSTED", "target_difficulty": 4},
        "dependency_outputs": {
            "knowledge": knowledge_output(),
            "route_resolution": route_output(),
        },
    }

    result = (await DiagnosisAgent(model).run(context)).payload
    serialized = result.learning_plan_proposal.model_dump()
    forbidden = {
        "plan_id",
        "task_id",
        "short_term_plan_id",
        "user_id",
        "learner_id",
        "created_at",
        "updated_at",
        "due_at",
        "stage_id",
        "kp_id",
        "kp_ids",
        "target_difficulty",
    }

    assert forbidden.isdisjoint(serialized)
    assert forbidden.isdisjoint(serialized["task_proposal"])
    assert serialized["planning_route"]["route_status"] == "approved"
    assert serialized["planning_route"]["route_version"] == 3


@pytest.mark.asyncio
async def test_diagnosis_revises_invalid_three_layer_output_only_once() -> None:
    model = RevisingPlanModel()
    route = route_output()
    route.payload.phases = [
        {
            "phase_id": "P1",
            "name": "基础阶段",
            "objective": "掌握基础理论",
            "books": ["《方剂学》"],
            "exit_evidence": ["闭卷测评"],
        }
    ]
    context = {
        "case_id": "CASE_1",
        "trace_id": "TRACE_1",
        "request_id": "REQ_1",
        "execution_id": "EXE_1",
        "step_id": "diagnosis",
        "learner_id": "LEARNER_1",
        "topic": "四君子汤",
        "available_minutes": 15,
        "user_request": "请制定学习计划",
        "dependency_outputs": {
            "knowledge": knowledge_output(),
            "route_resolution": route,
        },
    }

    result = (await DiagnosisAgent(model).run(context)).payload

    assert len(model.calls) == 2
    assert model.calls[1]["payload"]["revision_issues"] == ["当日任务与短期计划完全失配。"]
    assert "四君子汤" in result.learning_plan_proposal.daily_task_content


@pytest.mark.asyncio
async def test_diagnosis_stops_after_second_invalid_output() -> None:
    model = RevisingPlanModel()

    async def always_invalid(role, payload, on_delta=None):
        result = await RevisingPlanModel.complete_json(model, role, payload, on_delta)
        result["daily_task_content"] = (
            "## 今日目标\n练习游泳。\n## 分步动作和时间分配\n下水。\n"
            "## 客观完成标准\n游完五百米。"
        )
        return result

    model.complete_json = always_invalid
    context = {
        "case_id": "CASE_1", "trace_id": "TRACE_1", "request_id": "REQ_1",
        "execution_id": "EXE_1", "step_id": "diagnosis", "learner_id": "LEARNER_1",
        "topic": "四君子汤", "available_minutes": 15, "user_request": "请制定学习计划",
        "dependency_outputs": {"knowledge": knowledge_output(), "route_resolution": route_output()},
    }

    with pytest.raises(ValueError, match="三层规划修订后仍未通过校验"):
        await DiagnosisAgent(model).run(context)

    assert len(model.calls) == 2
