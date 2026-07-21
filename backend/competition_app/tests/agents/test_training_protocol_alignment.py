import pytest

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.agents.knowledge_base import KnowledgeBaseAgent
from competition_app.agents.planner import PlannerAgent
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack


class FakeRetrievalTool:
    async def build_evidence_pack(self, query: str) -> EvidencePack:
        return EvidencePack(
            evidence_pack_id="EP_1",
            query=query,
            resolved_kp_ids=["KP_FJ_001"],
            evidence_items=[
                EvidenceItem(
                    evidence_id="E_1",
                    source_id="教材:1",
                    content_summary="四君子汤用于教学辨析。",
                    authority_level="textbook",
                    confidence=0.95,
                    bridge_layer="strict",
                )
            ],
        )


class InvalidLearningAnalysisModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "summary": "错误地生成客观字段",
            "risk_flags": [],
            "recommendations": [],
            "uncertainty": [],
            "kp_id": "KP_FJ_001",
        }


class LearningAnalysisModelWithSystemPlanField:
    def __init__(self, field: str) -> None:
        self.field = field

    async def complete_json(self, role, payload, on_delta=None):
        return {
            "summary": "错误地生成计划系统字段",
            "risk_flags": [],
            "recommendations": [],
            "uncertainty": [],
            "long_term_plan_content": (
                "【最终目标】长期计划。"
                "【能力路径与阶段】基础→应用。"
                "【阶段里程碑】完成验收；截止待确认。"
                "【资源预算】投入待确认。"
                "【重规划条件】连续不达标时调整。"
                "【保温底线】每周一次回忆。"
            ),
            "short_term_plan_content": (
                "【当前主目标】短期计划。"
                "【长期目标保温】保留一次回忆。"
                "【时间分配】10分钟用于任务。"
                "【具体任务块】完成主动回忆，产出口述结果，完成标准为完整复述。"
                "【复习任务】完成后复盘。"
                "【反馈指标】记录完成率。"
            ),
            "priority_mode": "normal",
            "adjustment_reason": "按当前学习状态安排。",
            "learning_task": {
                "task_type": "active_recall",
                "task_content": "完成一次主动回忆。",
                "estimated_minutes": 10,
                "expected_output": "口述结果",
                "completion_criteria": "完整复述",
            },
            self.field: "MODEL_GENERATED_ID",
        }


class InvalidKnowledgeModel:
    async def complete_json(self, role, payload, on_delta=None):
        if payload["payload"].get("phase") == "plan_retrieval":
            return {
                "kp_query": "四君子汤",
                "question_query": "四君子汤练习题",
                "retrieval_reason": "检索教材和题目内容。",
            }
        return {"quality_labels": [], "uncertainty": [], "items": [{"evidence_id": "E_1"}]}


class PlannerThatOverridesSystemFacts:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "task_type": "personalized_review_card",
            "selected_agents": ["expert_agent"],
            "routing_reason": "越权选择",
            "risk_level": "low",
            "requires_audit": True,
            "fallback_policy": "fail_closed",
            "tools": ["unapproved_tool"],
        }


def context(step_id: str) -> dict:
    return {
        "case_id": "CASE_1",
        "trace_id": "TRACE_1",
        "request_id": "REQ_1",
        "execution_id": "EXE_1",
        "step_id": step_id,
        "learner_id": "L1",
        "user_request": "生成四君子汤复习卡",
        "topic": "四君子汤",
        "available_minutes": 15,
        "dependency_outputs": {},
    }


@pytest.mark.asyncio
async def test_knowledge_model_cannot_generate_evidence_items_or_ids() -> None:
    with pytest.raises(ValueError, match="training output contract"):
        await KnowledgeBaseAgent(FakeRetrievalTool(), InvalidKnowledgeModel()).run(context("knowledge"))


@pytest.mark.asyncio
async def test_learning_analysis_model_cannot_generate_kp_ids() -> None:
    knowledge = await KnowledgeBaseAgent(FakeRetrievalTool()).run(context("knowledge"))
    diagnosis_context = context("diagnosis")
    diagnosis_context["dependency_outputs"] = {"knowledge": knowledge}

    with pytest.raises(ValueError, match="training output contract"):
        await DiagnosisAgent(InvalidLearningAnalysisModel()).run(diagnosis_context)


@pytest.mark.parametrize("field", ["plan_id", "task_id"])
@pytest.mark.asyncio
async def test_learning_analysis_model_cannot_generate_plan_or_task_ids(field: str) -> None:
    knowledge = await KnowledgeBaseAgent(FakeRetrievalTool()).run(context("knowledge"))
    diagnosis_context = context("diagnosis")
    diagnosis_context["dependency_outputs"] = {"knowledge": knowledge}

    with pytest.raises(ValueError, match=field):
        await DiagnosisAgent(LearningAnalysisModelWithSystemPlanField(field)).run(diagnosis_context)


@pytest.mark.asyncio
async def test_planner_rejects_model_fields_that_override_system_workflow_facts() -> None:
    with pytest.raises(ValueError, match="validation failed"):
        await PlannerAgent(PlannerThatOverridesSystemFacts()).run(context("planner"))