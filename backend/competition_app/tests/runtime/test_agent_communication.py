import pytest
from pydantic import BaseModel

from competition_app.contracts.base import AgentEnvelope, ArtifactReference
from competition_app.contracts.execution import ExecutionStep
from competition_app.runtime.agent_communication import CognitiveGapAnalyzer


class TypedDiagnosisPayload(BaseModel):
    learning_plan_proposal: dict[str, object] | None = None
    requires_clarification: bool = False
    clarification_questions: list[str] = []


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


def test_typed_diagnosis_result_satisfies_learning_plan_handoff() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(
            step_id="learning_plan",
            agent="learning_plan_service",
            depends_on=["diagnosis"],
        ),
        root_context=base_context(),
        dependency_outputs={
            "diagnosis": AgentEnvelope(
                artifact_id="ART_DIAGNOSIS",
                artifact_type="diagnosis_result",
                case_id="CASE_1",
                trace_id="TRACE_1",
                request_id="REQ_1",
                execution_id="EXE_1",
                step_id="diagnosis",
                producer="diagnosis_agent",
                task_type="learning_plan",
                learner_id="LEARNER_1",
                payload=TypedDiagnosisPayload(
                    learning_plan_proposal={
                        "plan_scope": "long_term",
                        "content": "通过资格考试",
                    }
                ),
            )
        },
    )

    assert result.gap.blocking_fields == []
    assert any(
        fact.category == "diagnosis_proposal"
        for fact in result.bundle.confirmed_facts
    )


def test_typed_diagnosis_clarification_satisfies_learning_plan_handoff() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(
            step_id="learning_plan",
            agent="learning_plan_service",
            depends_on=["diagnosis"],
        ),
        root_context=base_context(),
        dependency_outputs={
            "diagnosis": AgentEnvelope(
                artifact_id="ART_DIAGNOSIS_CLARIFY",
                artifact_type="diagnosis_result",
                case_id="CASE_1",
                trace_id="TRACE_1",
                request_id="REQ_1",
                execution_id="EXE_1",
                step_id="diagnosis",
                producer="diagnosis_agent",
                task_type="learning_plan",
                learner_id="LEARNER_1",
                payload=TypedDiagnosisPayload(
                    requires_clarification=True,
                    clarification_questions=["请补充考试名称"],
                ),
            )
        },
    )

    assert result.gap.blocking_fields == []


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

    assert "learning_goal" in result.gap.missing_fields
    assert "learning_goal" not in result.gap.blocking_fields


def test_review_scheduler_accepts_explicit_empty_authoritative_state() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(step_id="schedule", agent="review_scheduler"),
        root_context=base_context(user_knowledge_states=[]),
        dependency_outputs={},
    )

    assert result.gap.blocking_fields == []
    assert "graded_knowledge_state" in result.gap.satisfied_fields


def test_expert_accepts_typed_evidence_pack_and_review_schedule() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(
            step_id="expert",
            agent="expert_agent",
            depends_on=["knowledge", "schedule"],
        ),
        root_context=base_context(
            source_policy={
                "trusted_source_types": [
                    "textbook",
                    "knowledge_base",
                    "official_question_bank",
                ]
            }
        ),
        dependency_outputs={
            "knowledge": AgentEnvelope(
                artifact_id="ART_KNOWLEDGE",
                artifact_type="evidence_pack",
                case_id="CASE_1",
                trace_id="TRACE_1",
                request_id="REQ_1",
                execution_id="EXE_1",
                step_id="knowledge",
                producer="knowledge_base_agent",
                task_type="personalized_review_card",
                learner_id="LEARNER_1",
                payload={
                    "evidence_items": [
                        {
                            "evidence_id": "E1",
                            "source_id": "BOOK_1",
                            "content_summary": "教材证据",
                            "resource_type": "textbook",
                        }
                    ]
                },
            ),
            "schedule": AgentEnvelope(
                artifact_id="ART_SCHEDULE",
                artifact_type="review_schedule",
                case_id="CASE_1",
                trace_id="TRACE_1",
                request_id="REQ_1",
                execution_id="EXE_1",
                step_id="schedule",
                producer="review_scheduler",
                task_type="personalized_review_card",
                learner_id="LEARNER_1",
                payload={"selected_task": {"primary_kp_id": "KP_1"}},
            ),
        },
    )

    assert result.gap.blocking_fields == []
    assert result.gap.satisfied_fields == ["evidence", "formal_task"]


def test_audit_accepts_expert_artifact_and_typed_evidence_pack() -> None:
    context = base_context(
        source_policy={"trusted_source_types": ["textbook", "knowledge_base"]}
    )
    expert = AgentEnvelope(
        artifact_id="ART_EXPERT",
        artifact_type="resource_draft",
        case_id="CASE_1",
        trace_id="TRACE_1",
        request_id="REQ_1",
        execution_id="EXE_1",
        step_id="expert",
        producer="expert_agent",
        task_type="personalized_review_card",
        learner_id="LEARNER_1",
        payload={"body": "理中丸复习内容"},
    )
    knowledge = AgentEnvelope(
        artifact_id="ART_KNOWLEDGE",
        artifact_type="evidence_pack",
        case_id="CASE_1",
        trace_id="TRACE_1",
        request_id="REQ_1",
        execution_id="EXE_1",
        step_id="knowledge",
        producer="knowledge_base_agent",
        task_type="personalized_review_card",
        learner_id="LEARNER_1",
        payload={
            "evidence_items": [
                {
                    "evidence_id": "E1",
                    "source_id": "BOOK_1",
                    "content_summary": "教材证据",
                    "resource_type": "textbook",
                }
            ]
        },
    )

    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(
            step_id="audit",
            agent="audit_agent",
            depends_on=["knowledge", "expert"],
        ),
        root_context=context,
        dependency_outputs={"knowledge": knowledge, "expert": expert},
    )

    assert result.gap.blocking_fields == []
    assert result.gap.satisfied_fields == ["artifact", "evidence"]


def test_audit_accepts_retrieved_textbook_question_pool_as_evidence() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(
            step_id="audit",
            agent="audit_agent",
            depends_on=["knowledge", "expert"],
        ),
        root_context=base_context(
            source_policy={"trusted_source_types": ["official_question_bank"]}
        ),
        dependency_outputs={
            "knowledge": AgentEnvelope(
                artifact_id="ART_POOL",
                artifact_type="question_candidate_pool",
                case_id="CASE_1",
                trace_id="TRACE_1",
                request_id="REQ_1",
                execution_id="EXE_1",
                step_id="knowledge",
                producer="knowledge_base_agent",
                task_type="paper_generation",
                learner_id="LEARNER_1",
                payload={
                    "units": [
                        {
                            "items": [
                                {
                                    "question_id": "Q1",
                                    "stem": "理中丸主治何证？",
                                    "origin": "retrieved",
                                    "source_tier": "textbook",
                                }
                            ]
                        }
                    ]
                },
            ),
            "expert": AgentEnvelope(
                artifact_id="ART_PAPER",
                artifact_type="exam_paper_draft",
                case_id="CASE_1",
                trace_id="TRACE_1",
                request_id="REQ_1",
                execution_id="EXE_1",
                step_id="expert",
                producer="expert_agent",
                task_type="paper_generation",
                learner_id="LEARNER_1",
                payload={"title": "理中丸测试卷"},
            ),
        },
    )

    assert result.gap.blocking_fields == []


def test_paper_audit_records_missing_evidence_without_blocking_audit() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(
            step_id="audit",
            agent="audit_agent",
            depends_on=["expert"],
        ),
        root_context=base_context(task_type="paper_generation"),
        dependency_outputs={
            "expert": AgentEnvelope(
                artifact_id="ART_PAPER",
                artifact_type="exam_paper_draft",
                case_id="CASE_1",
                trace_id="TRACE_1",
                request_id="REQ_1",
                execution_id="EXE_1",
                step_id="expert",
                producer="expert_agent",
                task_type="paper_generation",
                learner_id="LEARNER_1",
                payload={"title": "专家补题试卷"},
            )
        },
    )

    assert result.gap.missing_fields == ["evidence"]
    assert result.gap.blocking_fields == []
    assert result.bundle.uncertainties[0].blocking is False


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
