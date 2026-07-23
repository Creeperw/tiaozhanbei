from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.execution import ExecutionStep
from competition_app.runtime.agent_communication import CognitiveGapAnalyzer


def diagnosis_dependencies() -> dict[str, AgentEnvelope[dict[str, object]]]:
    return {
        "memory": AgentEnvelope(
            artifact_id="ART_MEMORY",
            artifact_type="learner_memory",
            case_id="CASE_1",
            trace_id="TRACE_1",
            request_id="REQ_1",
            execution_id="EXE_1",
            step_id="memory",
            producer="memory_agent",
            task_type="learning_plan",
            learner_id="LEARNER_1",
            payload={"learning_goal": "通过本周测验", "time_budget": 25},
        ),
        "route_resolution": AgentEnvelope(
            artifact_id="ART_ROUTE",
            artifact_type="planning_route",
            case_id="CASE_1",
            trace_id="TRACE_1",
            request_id="REQ_1",
            execution_id="EXE_1",
            step_id="route_resolution",
            producer="route_agent",
            task_type="learning_plan",
            learner_id="LEARNER_1",
            payload={"route": "short_term"},
        ),
    }


def base_context(**overrides: object) -> dict[str, object]:
    context: dict[str, object] = {
        "trace_id": "TRACE_1",
        "execution_id": "EXE_1",
        "learner_id": "LEARNER_1",
        "user_request": "制定短期计划",
        "task_type": "learning_plan",
    }
    context.update(overrides)
    return context


def test_analyzer_sends_diagnosis_only_learning_relevant_information() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(
            step_id="diagnosis",
            agent="diagnosis_agent",
            depends_on=["memory", "route_resolution"],
        ),
        root_context={
            "trace_id": "TRACE_1",
            "execution_id": "EXE_1",
            "learner_id": "LEARNER_1",
            "user_request": "制定短期计划",
            "available_minutes": 25,
            "multi_scale_learning_state": {"macro": {}, "meso": {}, "micro": {}},
            "dashscope_api_key": "must-not-leak",
            "unrelated_blob": "x" * 100_000,
        },
        dependency_outputs=diagnosis_dependencies(),
    )

    assert result.bundle.target_agent == "diagnosis_agent"
    assert "dashscope_api_key" not in result.bundle.model_dump_json()
    assert "unrelated_blob" in result.gap.omitted_categories
    assert result.gap.blocking_fields == []


def test_analyzer_blocks_agent_when_required_evidence_is_missing() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(step_id="expert", agent="expert_agent"),
        root_context=base_context(task_type="knowledge_explanation"),
        dependency_outputs={},
    )

    assert "evidence" in result.gap.blocking_fields
