import pytest

from competition_app.contracts.base import AgentEnvelope, ArtifactReference
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


def test_diagnosis_does_not_treat_a_generic_request_as_a_learning_goal() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(step_id="diagnosis", agent="diagnosis_agent"),
        root_context=base_context(
            user_request="hello",
            available_minutes=25,
            multi_scale_learning_state={"macro": {}, "meso": {}, "micro": {}},
        ),
        dependency_outputs={},
    )

    assert "learning_goal" in result.gap.blocking_fields


def test_analyzer_preserves_zero_time_budget_without_defaulting_it() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(step_id="diagnosis", agent="diagnosis_agent"),
        root_context=base_context(
            learning_goal="完成诊断",
            available_minutes=0,
            multi_scale_learning_state={"macro": {}, "meso": {}, "micro": {}},
        ),
        dependency_outputs={},
    )

    assert "time_budget" not in result.gap.missing_fields
    assert next(fact.content for fact in result.bundle.confirmed_facts if fact.category == "time_budget") == "0"


def test_analyzer_keeps_unknown_numeric_time_budget_absent() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(step_id="diagnosis", agent="diagnosis_agent"),
        root_context=base_context(
            learning_goal="完成诊断",
            available_minutes=None,
            multi_scale_learning_state={"macro": {}, "meso": {}, "micro": {}},
        ),
        dependency_outputs={},
    )

    assert "time_budget" in result.gap.blocking_fields


def test_analyzer_excludes_cross_user_dependency_envelopes() -> None:
    other_learner_output = AgentEnvelope(
        artifact_id="ART_OTHER",
        artifact_type="learner_memory",
        case_id="CASE_OTHER",
        trace_id="TRACE_1",
        request_id="REQ_1",
        execution_id="EXE_1",
        step_id="memory",
        producer="memory_agent",
        task_type="learning_plan",
        learner_id="LEARNER_2",
        payload={"time_budget": 30},
    )
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(step_id="diagnosis", agent="diagnosis_agent", depends_on=["memory"]),
        root_context=base_context(
            learning_goal="完成诊断",
            multi_scale_learning_state={"macro": {}, "meso": {}, "micro": {}},
        ),
        dependency_outputs={"memory": other_learner_output},
    )

    assert "time_budget" in result.gap.blocking_fields
    assert all(fact.source_step_id != "memory" for fact in result.bundle.confirmed_facts)


def test_unknown_agent_uses_only_safe_direct_dependency_facts() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(step_id="internal", agent="internal_service", depends_on=["upstream"]),
        root_context=base_context(task_constraints={"max_items": 3}),
        dependency_outputs={
            "upstream": AgentEnvelope(
                artifact_id="ART_UPSTREAM",
                artifact_type="artifact",
                case_id="CASE_1",
                trace_id="TRACE_1",
                request_id="REQ_1",
                execution_id="EXE_1",
                step_id="upstream",
                producer="upstream_agent",
                task_type="learning_plan",
                learner_id="LEARNER_1",
                payload={"artifact": "safe-direct-output", "api_token": "do-not-forward"},
            )
        },
    )

    assert result.bundle.task_constraints == {}
    assert [fact.category for fact in result.bundle.confirmed_facts] == ["artifact"]
    assert result.gap.blocking_fields == []


def test_untrusted_evidence_does_not_satisfy_a_trusted_evidence_need() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(step_id="expert", agent="expert_agent", depends_on=["knowledge"]),
        root_context=base_context(
            formal_task="explain algebra",
            source_policy={"trusted_source_types": ["textbook"]},
        ),
        dependency_outputs={
            "knowledge": AgentEnvelope(
                artifact_id="ART_KNOWLEDGE",
                artifact_type="knowledge",
                case_id="CASE_1",
                trace_id="TRACE_1",
                request_id="REQ_1",
                execution_id="EXE_1",
                step_id="knowledge",
                producer="knowledge_base_agent",
                task_type="knowledge_explanation",
                learner_id="LEARNER_1",
                payload={},
                evidence_refs=[ArtifactReference(ref_type="web", ref_id="WEB_1")],
            )
        },
    )

    assert "evidence" in result.gap.blocking_fields


@pytest.mark.parametrize("source_policy", [None, {}])
def test_evidence_fails_closed_without_usable_trust_policy(source_policy: object) -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(step_id="expert", agent="expert_agent", depends_on=["knowledge"]),
        root_context=base_context(
            formal_task="explain algebra",
            source_policy=source_policy,
        ),
        dependency_outputs={
            "knowledge": AgentEnvelope(
                artifact_id="ART_KNOWLEDGE",
                artifact_type="knowledge",
                case_id="CASE_1",
                trace_id="TRACE_1",
                request_id="REQ_1",
                execution_id="EXE_1",
                step_id="knowledge",
                producer="knowledge_base_agent",
                task_type="knowledge_explanation",
                learner_id="LEARNER_1",
                payload={},
                evidence_refs=[ArtifactReference(ref_type="web", ref_id="WEB_1")],
            )
        },
    )

    assert "evidence" in result.gap.blocking_fields
