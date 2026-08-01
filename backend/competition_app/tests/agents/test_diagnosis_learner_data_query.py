import pytest

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.runtime.tool_registry import ToolPermissionError, ToolRegistry


class NaturalAnswerModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "answer": (
                "近7天你完成了四君子汤相关题目练习。"
                "这里只统计服务端确认完成的记录。"
            )
        }


class NextLearningAnswerModel:
    async def complete_json(self, role, payload, on_delta=None):
        evidence = payload["payload"]["learner_evidence"]
        assert evidence["plan_progress"]["daily_task"]["learning_chapter"] == "补益剂"
        assert evidence["mastery_and_review"]["mastery"][0]["kp_name"] == "四君子汤"
        return {
            "answer": (
                "接下来优先完成补益剂章节中的四君子汤训练；"
                "这是当前任务与薄弱点的交集，不会新建短期计划。"
            )
        }


class FailingAnswerModel:
    async def complete_json(self, role, payload, on_delta=None):
        raise RuntimeError("offline answer unavailable")


@pytest.mark.asyncio
async def test_diagnosis_queries_current_learner_and_excludes_recommendation_noise() -> None:
    captured = {}
    registry = ToolRegistry()

    def recent_learning(external_user_id, *, days, recent_limit):
        captured.update(
            learner_id=external_user_id,
            days=days,
            recent_limit=recent_limit,
        )
        return {
            "evidence_status": "observed",
            "verified_event_count": 1,
            "verified_learning_events": [
                {
                    "activity_type": "question_attempt",
                    "completion_status": "completed",
                    "title": "",
                    "knowledge_points": ["四君子汤"],
                }
            ],
            "task_completion": {"completed": 1, "total": 1},
            "focus": {"active_seconds": 600},
            "evidence_rule": "推荐和点击不算学习完成。",
        }

    registry.register(
        "get_recent_learning_summary",
        recent_learning,
        allowed_agents={"diagnosis_agent"},
    )
    result = await DiagnosisAgent(NaturalAnswerModel()).run(
        {
            "case_id": "C_DATA",
            "trace_id": "T_DATA",
            "request_id": "R_DATA",
            "execution_id": "E_DATA",
            "step_id": "diagnosis",
            "learner_id": "CURRENT_USER",
            "user_request": "我最近学了些什么东西？",
            "task_type": "learner_data_query",
            "learner_data_query_kind": "recent_learning",
            "tool_registry": registry,
        }
    )

    assert captured == {
        "learner_id": "CURRENT_USER",
        "days": 7,
        "recent_limit": 20,
    }
    assert "四君子汤" in result.payload.summary
    assert result.payload.learning_plan_proposal is None
    assert result.payload.learner_data["source"] == "get_recent_learning_summary"

    with pytest.raises(ToolPermissionError):
        await registry.invoke(
            "get_recent_learning_summary",
            "expert_agent",
            external_user_id="CURRENT_USER",
            days=7,
            recent_limit=20,
        )


@pytest.mark.asyncio
async def test_empty_verified_history_is_reported_honestly() -> None:
    registry = ToolRegistry()
    registry.register(
        "get_recent_learning_summary",
        lambda external_user_id, *, days, recent_limit: {
            "evidence_status": "no_verified_records",
            "verified_event_count": 0,
            "verified_learning_events": [],
        },
        allowed_agents={"diagnosis_agent"},
    )

    result = await DiagnosisAgent().run(
        {
            "case_id": "C_EMPTY",
            "trace_id": "T_EMPTY",
            "request_id": "R_EMPTY",
            "execution_id": "E_EMPTY",
            "step_id": "diagnosis",
            "learner_id": "EMPTY_USER",
            "user_request": "这周学了什么？",
            "task_type": "learner_data_query",
            "learner_data_query_kind": "recent_learning",
            "tool_registry": registry,
        }
    )

    assert "没有查到可确认的学习完成记录" in result.payload.summary
    assert "推荐过、打开过" in result.payload.summary


@pytest.mark.asyncio
async def test_next_learning_combines_plan_mastery_review_and_recent_history() -> None:
    calls = []
    registry = ToolRegistry()

    def plan_progress(external_user_id):
        calls.append(("plan", external_user_id))
        return {
            "daily_task": {
                "status": "active",
                "learning_chapter": "补益剂",
                "focus_knowledge_points": ["四君子汤"],
                "acceptance_gate": "完成训练",
            }
        }

    def mastery(external_user_id, *, history_limit):
        calls.append(("mastery", external_user_id, history_limit))
        return {
            "mastery": [{"kp_name": "四君子汤", "mastery_score": 0.35}],
            "review_states": [],
            "review_tasks": [],
        }

    def recent(external_user_id, *, days, recent_limit):
        calls.append(("recent", external_user_id, days, recent_limit))
        return {
            "evidence_status": "observed",
            "verified_event_count": 1,
            "verified_learning_events": [
                {
                    "activity_type": "question_attempt",
                    "completion_status": "completed",
                    "knowledge_points": ["理中丸"],
                }
            ],
        }

    registry.register(
        "get_current_plan_progress",
        plan_progress,
        allowed_agents={"diagnosis_agent"},
    )
    registry.register(
        "get_mastery_snapshot",
        mastery,
        allowed_agents={"diagnosis_agent"},
    )
    registry.register(
        "get_recent_learning_summary",
        recent,
        allowed_agents={"diagnosis_agent"},
    )

    result = await DiagnosisAgent(NextLearningAnswerModel()).run(
        {
            "case_id": "C_NEXT",
            "trace_id": "T_NEXT",
            "request_id": "R_NEXT",
            "execution_id": "E_NEXT",
            "step_id": "diagnosis",
            "learner_id": "CURRENT_USER",
            "user_request": "我最近需要学习些什么？",
            "task_type": "learner_data_query",
            "learner_data_query_kind": "next_learning",
            "tool_registry": registry,
        }
    )

    assert [item[0] for item in calls] == ["plan", "mastery", "recent"]
    assert "四君子汤" in result.payload.summary
    assert result.payload.learning_plan_proposal is None
    assert result.payload.learner_data["source"] == "combined_learner_evidence"
    assert result.payload.learner_data["sources"] == [
        "get_current_plan_progress",
        "get_mastery_snapshot",
        "get_recent_learning_summary",
    ]


@pytest.mark.asyncio
async def test_plan_lookup_returns_requested_long_term_content_not_short_term() -> None:
    registry = ToolRegistry()
    registry.register(
        "get_current_plan_progress",
        lambda external_user_id: {
            "long_term": {
                "status": "active",
                "content": "这是正式长期规划正文。",
                "structured": {"stages": [{"stage": 1, "goal": "长期目标"}]},
                "stage_progress": [{"stage": 1, "name": "基础阶段", "status": "in_progress"}],
            },
            "short_term": {
                "status": "active",
                "content": "这是短期计划正文。",
                "structured": {},
                "acceptance_gate": {"status": "in_progress"},
            },
            "daily_task": None,
        },
        allowed_agents={"diagnosis_agent"},
    )

    result = await DiagnosisAgent(FailingAnswerModel()).run(
        {
            "case_id": "C_PLAN_LOOKUP",
            "trace_id": "T_PLAN_LOOKUP",
            "request_id": "R_PLAN_LOOKUP",
            "execution_id": "E_PLAN_LOOKUP",
            "step_id": "diagnosis",
            "learner_id": "CURRENT_USER",
            "user_request": "给我看看我的长期学习计划",
            "task_type": "learner_data_query",
            "learner_data_query_kind": "plan_progress",
            "tool_registry": registry,
        }
    )

    assert result.payload.summary == "这是正式长期规划正文。"
    snapshot = result.payload.learner_data["snapshot"]
    assert snapshot["requested_scope"] == "long_term"
    assert snapshot["long_term"]["structured"]["stages"][0]["goal"] == "长期目标"
