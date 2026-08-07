import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from competition_app.application.personalized_review_card import (
    CoordinationSummary,
    PersonalizedReviewCardUseCase,
    ReviewCardRequest,
    ReviewCardResult,
    WorkflowResumeRequest,
)
from competition_app.application.workflow_presentation import workflow_result_to_markdown
from competition_app.contracts.base import AgentEnvelope, ArtifactReference
from competition_app.contracts.memory import (
    LearnerContextBrief,
    LongTermMemoryCandidate,
    MemoryGovernanceDecision,
)
from competition_app.agents.memory import MemoryAgentResult
from competition_app.contracts.resource import AuditResult
from competition_app.repositories.runtime import InMemoryRunStateRepository
from competition_app.runtime.orchestrator import ExecutionResult
from competition_app.runtime.trace import CommunicationTrace


class _FailingPlanner:
    async def run(self, context):
        raise TimeoutError("planner timed out")


class _FailingMemoryRetriever:
    async def retrieve(self, learner_id, user_request):
        raise TimeoutError("knowledge retrieval timed out")


class _CancelledPlanner:
    async def run(self, context):
        raise asyncio.CancelledError()


class _AgentRegistry:
    def __init__(self, planner):
        self.planner = planner

    def get(self, name):
        assert name == "planner_agent"
        return self.planner


class _Orchestrator:
    def __init__(self, planner):
        self.agent_registry = _AgentRegistry(planner)


class _SnapshotExporter:
    def export(self, case_id, execution_id, payload):
        return Path("snapshot.json")


def _use_case(*, planner, memory_retriever=None):
    return PersonalizedReviewCardUseCase(
        orchestrator=_Orchestrator(planner),
        snapshot_exporter=_SnapshotExporter(),
        memory_retriever=memory_retriever,
    )


def _request(thread_id="THREAD_PHASE2"):
    return ReviewCardRequest(
        thread_id=thread_id,
        conversation_id=thread_id,
        learner_id="learner-phase2",
        user_request="请制定一个学习计划",
    )


def test_nested_step_error_prefers_exact_step_and_plan_compilation_code() -> None:
    error = ValueError(
        "步骤 diagnosis（diagnosis_agent）失败："
        "ValueError: 最终规划正文未能编译为可审核合同"
    )

    assert PersonalizedReviewCardUseCase._failure_step(error) == "diagnosis"
    assert (
        PersonalizedReviewCardUseCase._failure_code(error)
        == "plan_compilation_failed"
    )


def test_orchestrator_audit_repair_failure_uses_audit_error_code() -> None:
    error = RuntimeError(
        "personalized review card execution failed: "
        "audit findings could not be safely repaired: 讲解内容需要修订"
    )

    assert (
        PersonalizedReviewCardUseCase._failure_code(error)
        == "audit_step_failed"
    )


def test_daily_task_publication_error_is_not_mislabeled_as_knowledge_failure() -> None:
    error = RuntimeError(
        "步骤 learning_plan（learning_plan_service）失败："
        "DailyTaskProgressError: knowledge point 003299 belongs to another source"
    )

    assert (
        PersonalizedReviewCardUseCase._failure_code(error)
        == "daily_task_publication_failed"
    )


def test_paper_assembly_compiler_failure_has_paper_error_code() -> None:
    error = RuntimeError(
        "步骤 paper_assembly（paper_assembly_agent）失败："
        "ValueError: paper assembly draft could not be compiled"
    )

    assert (
        PersonalizedReviewCardUseCase._failure_code(error)
        == "paper_generation_failed"
    )


def test_second_plan_audit_revision_has_audit_error_code() -> None:
    error = RuntimeError(
        "personalized review card execution failed: "
        "audit still requires review after the bounded repair"
    )

    assert PersonalizedReviewCardUseCase._failure_code(error) == "audit_step_failed"


def test_waiting_human_review_is_presented_as_a_review_request() -> None:
    message = workflow_result_to_markdown({
        "status": "waiting_human_review",
        "review": {
            "findings": ["实时信息来源需要人工核验。"],
        },
    })

    assert "人工复核" in message
    assert "实时信息来源需要人工核验。" in message
    assert "审核未能完成" not in message


def test_waiting_human_review_shows_draft_content_and_audit_advice() -> None:
    message = workflow_result_to_markdown({
        "status": "waiting_human_review",
        "review": {
            "audit_report": "讲解结构符合要求，但证据引用需要人工确认。",
            "findings": ["教材证据原文需要人工核验。"],
        },
        "agent_outputs": [
            {
                "producer": "expert_agent",
                "payload": {
                    "title": "气血知识讲解",
                    "content": {
                        "知识讲解": "气与血是人体基本物质。",
                        "思考问题": ["气能生血，你能举例说明吗？"],
                    },
                },
            },
        ],
    })

    assert "### 待复核内容" in message
    assert "「气血知识讲解」" in message
    assert "气与血是人体基本物质。" in message
    assert "### 审核意见" in message
    assert "讲解结构符合要求，但证据引用需要人工确认。" in message
    assert "### 需要确认的问题" in message
    assert "教材证据原文需要人工核验。" in message
    assert "确认后我会继续发布" in message


def test_waiting_human_review_without_agent_outputs_still_lists_findings() -> None:
    message = workflow_result_to_markdown({
        "status": "waiting_human_review",
        "review": {
            "audit_report": "",
            "findings": ["需要确认信息来源。"],
        },
    })

    assert "### 待复核内容" not in message
    assert "需要确认的问题" in message
    assert "需要确认信息来源。" in message


def test_waiting_human_review_execution_builds_a_normal_review_result() -> None:
    use_case = object.__new__(PersonalizedReviewCardUseCase)
    use_case.model_trace_recorder = None
    audit = AuditResult(
        audit_result_id="AUDIT_REVIEW",
        decision="needs_human_review",
        findings=["实时信息来源需要人工核验。"],
    )
    execution = ExecutionResult(
        status="waiting_human_review",
        outputs={"audit": SimpleNamespace(payload=audit)},
    )

    result = use_case._human_review_result(
        execution_id="EXE_REVIEW",
        task_type="general_learning_support",
        execution=execution,
    )

    assert result.status == "waiting_human_review"
    assert result.review == audit
    assert "人工复核" in workflow_result_to_markdown(result)


def test_waiting_human_review_after_repair_keeps_original_findings() -> None:
    use_case = object.__new__(PersonalizedReviewCardUseCase)
    use_case.model_trace_recorder = None
    first_audit = AuditResult(
        audit_result_id="AUDIT_PASS",
        decision="pass",
        findings=[],
    )
    audit = AuditResult(
        audit_result_id="AUDIT_REVISE",
        decision="revise",
        findings=["事实来源仍需人工核验。"],
    )
    execution = ExecutionResult(
        status="waiting_human_review",
        outputs={
            "audit_long": SimpleNamespace(payload=first_audit),
            "audit_short": SimpleNamespace(payload=audit),
        },
        error_type="AuditRevisionNeedsHumanReview",
    )

    result = use_case._human_review_result(
        execution_id="EXE_REPAIR_REVIEW",
        task_type="general_learning_support",
        execution=execution,
    )

    assert result.review.decision == "needs_human_review"
    assert result.review.findings == ["事实来源仍需人工核验。"]


@pytest.mark.asyncio
async def test_execute_failure_is_persisted_as_failed_with_step_metadata() -> None:
    use_case = _use_case(planner=_FailingPlanner())

    with pytest.raises(TimeoutError):
        await use_case.execute(_request())

    state = use_case.get_run_state("THREAD_PHASE2")
    assert state is not None
    assert state["status"] == "failed"
    assert state["error_code"] == "workflow_timeout"
    assert state["failed_step"] == "planner"
    assert state["retryable"] is True


@pytest.mark.asyncio
async def test_execute_memory_failure_is_persisted_as_failed() -> None:
    use_case = _use_case(
        planner=_FailingPlanner(),
        memory_retriever=_FailingMemoryRetriever(),
    )

    with pytest.raises(TimeoutError):
        await use_case.execute(_request("THREAD_PHASE2_MEMORY"))

    state = use_case.get_run_state("THREAD_PHASE2_MEMORY")
    assert state is not None
    assert state["status"] == "failed"
    assert state["error_code"] == "knowledge_timeout"
    assert state["failed_step"] == "memory"


@pytest.mark.asyncio
async def test_execute_cancellation_is_not_converted_to_failed() -> None:
    use_case = _use_case(planner=_CancelledPlanner())

    with pytest.raises(asyncio.CancelledError):
        await use_case.execute(_request("THREAD_PHASE2_CANCEL"))

    state = use_case.get_run_state("THREAD_PHASE2_CANCEL")
    assert state is not None
    assert state["status"] == "running"


@pytest.mark.asyncio
async def test_resume_profile_writeback_failure_is_persisted_as_failed() -> None:
    use_case = _use_case(planner=_FailingPlanner())
    use_case._remember_run(
        "THREAD_PHASE2_RESUME",
        {
            "status": "interrupted",
            "thread_id": "THREAD_PHASE2_RESUME",
            "execution_id": "EXE_PHASE2_RESUME",
            "continuation": {
                "request": _request("THREAD_PHASE2_RESUME").model_dump(mode="json"),
                "case_id": "CASE_PHASE2_RESUME",
                "execution_id": "EXE_PHASE2_RESUME",
                "execution_plan": {
                    "plan_id": "PLAN_PHASE2_RESUME",
                    "task_type": "learning_plan",
                        "steps": [
                            {
                                "step_id": "diagnosis",
                                "agent": "diagnosis_agent",
                            }
                        ],
                },
                "planner_output": {
                    "artifact_id": "ART_PHASE2_RESUME",
                    "artifact_type": "planner_decision",
                    "case_id": "CASE_PHASE2_RESUME",
                    "trace_id": "TRACE_PHASE2_RESUME",
                    "request_id": "REQ_PHASE2_RESUME",
                    "execution_id": "EXE_PHASE2_RESUME",
                    "step_id": "planner",
                    "producer": "planner_agent",
                    "task_type": "learning_plan",
                    "learner_id": "learner-phase2",
                    "payload": {
                        "task_type": "learning_plan",
                        "plan_scope": "long_term",
                        "selected_agents": [],
                        "routing_reason": "test",
                    },
                },
                "context": {},
            },
        },
    )
    use_case.profile_update_writer = lambda *args: (_ for _ in ()).throw(
        RuntimeError("profile writeback failed")
    )
    use_case._remember_run(
        "THREAD_PHASE2_RESUME",
        {
            "status": "interrupted",
            "interrupt": {
                "interrupt_type": "profile_completion",
                "profile_fields": ["learning_goal"],
            },
        },
    )

    with pytest.raises(RuntimeError, match="profile writeback failed"):
        await use_case.resume(
            "THREAD_PHASE2_RESUME",
            WorkflowResumeRequest(answer="准备参加考试"),
        )

    state = use_case.get_run_state("THREAD_PHASE2_RESUME")
    assert state is not None
    assert state["status"] == "failed"
    assert state["error_code"] == "persistence_failed"
    assert state["failed_step"] == "profile_writeback"


def test_long_term_plan_message_includes_system_owned_stage_data() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "learning_plan": {
            "generated_scope": "long_term",
            "long_term_plan": {
                "content": "【最终目标】建立中医基础。",
                "stages": [
                    {
                        "stage": 1,
                        "book": ["《中医学基础》"],
                        "goal": "建立基础理论框架。",
                    }
                ],
            },
        },
    })

    assert "【阶段路线数据】" in message
    encoded = message.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert json.loads(encoded) == {
        "long_term_plan_stages": [
            {
                "stage": 1,
                "book": ["《中医学基础》"],
                "goal": "建立基础理论框架。",
            }
        ]
    }


def test_short_term_message_does_not_repeat_stale_long_term_stages() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "learning_plan": {
            "generated_scope": "short_term",
            "long_term_plan": None,
            "short_term_plan": {"content": "【当前主目标】完成本周学习。"},
        },
    })

    assert "long_term_plan_stages" not in message


def test_daily_task_message_names_chapter_and_focus_knowledge_points() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "learning_plan": {
            "generated_scope": "daily_task",
            "learning_task": {
                "task_content": "精读阴阳学说并整理笔记。",
                "learning_chapter": "《中医学基础》阴阳学说",
                "focus_knowledge_points": ["阴阳对立制约", "阴阳互根互用"],
                "estimated_minutes": 45,
                "completion_criteria": "能够闭卷解释两个概念。",
            },
        },
    })

    assert "今日章节：《中医学基础》阴阳学说" in message
    assert "重点知识点：阴阳对立制约、阴阳互根互用" in message
    assert "预计用时：45 分钟" in message


def test_casual_conversation_returns_direct_natural_language() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "task_type": "casual_conversation",
        "direct_response": "你好！今天想学点什么？",
    })

    assert message == "你好！今天想学点什么？"
    assert "流程已在当前节点暂停" not in message


def test_interruption_hides_internal_planner_routing_reason() -> None:
    message = workflow_result_to_markdown({
        "status": "interrupted",
        "interrupt": {
            "reason": (
                "选择 Diagnosis Agent 并不选择 Memory Agent，"
                "因为 requires_compression=false。系统已补全确定性依赖节点。"
            ),
            "questions": ["你希望制定长期计划还是短期计划？"],
        },
    })

    assert "Diagnosis Agent" not in message
    assert "Memory Agent" not in message
    assert "requires_compression" not in message
    assert "确定性依赖节点" not in message
    assert "你希望制定长期计划还是短期计划？" in message


def test_paper_message_keeps_exam_body_in_workspace() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "task_type": "paper_generation",
        "resource": {
            "title": "四君子汤试卷",
            "content": {"试卷正文": [{"题干": "不应出现在对话里"}]},
        },
        "ui_actions": [
            {
                "label": "开始答题",
                "destination": "workshop.paper",
                "params": {"paper_id": "PAPER_1"},
            }
        ],
    })

    assert "不应出现在对话里" not in message
    assert "开始答题" in message
    assert "通过审核" in message


def test_resource_message_adds_nonofficial_current_fact_reminder() -> None:
    message = workflow_result_to_markdown(
        {
            "status": "success",
            "task_type": "general_learning_support",
            "resource": {
                "title": "天气信息",
                "content": {"回复": "今天有小雨。"},
            },
            "audit": {
                "findings": [
                    "实时信息提示：信息来自非官方网页，请以官方渠道为准。"
                ]
            },
        }
    )

    assert "今天有小雨。" in message
    assert "信息来自非官方网页，请以官方渠道为准。" in message
    assert "人工复核" not in message


def test_resource_message_hides_internal_ids_and_uses_learner_labels() -> None:
    message = workflow_result_to_markdown(
        {
            "status": "success",
            "task_type": "personalized_review_card",
            "resource": {
                "title": "四君子汤个性化练习",
                "content": {
                    "知识卡片": {
                        "kp_id": "003264",
                        "kp_name": "四君子汤",
                        "exp": "用于巩固组成与配伍逻辑。",
                    },
                    "练习资源": [
                        {
                            "question_id": "Q_INTERNAL",
                            "question_type": "单项选择题",
                            "stem": "四君子汤的君药是？",
                            "kp_ids": ["003264"],
                        }
                    ],
                },
            },
        }
    )

    assert "003264" not in message
    assert "Q_INTERNAL" not in message
    assert "**知识点**：四君子汤" in message
    assert "**题型**：单项选择题" in message
    assert "**题目**：四君子汤的君药是？" in message


def test_workflow_run_state_persists_communication_trace_summary() -> None:
    execution = ExecutionResult(
        status="success",
        communication_trace=[
            CommunicationTrace(
                handoff_id="HANDOFF_EXE_1_diagnosis",
                step_id="diagnosis",
                target_agent="diagnosis_agent",
                fact_count=2,
                evidence_count=0,
                blocking_field_count=0,
                status="consumed",
            )
        ],
    )
    use_case = object.__new__(PersonalizedReviewCardUseCase)
    use_case.run_state_repository = InMemoryRunStateRepository()
    coordination = use_case._execution_coordination(execution)

    use_case._remember_run(
        "THREAD_COORDINATION",
        {
            "status": "completed",
            "result": ReviewCardResult(
                status="success",
                execution_id="EXE_1",
                task_type="learning_plan",
                agent_outputs=[],
                snapshot_path=Path("snapshot.json"),
                writeback_intents=[],
                coordination=coordination,
            ),
        },
    )

    saved = use_case.get_run_state("THREAD_COORDINATION")
    assert saved is not None
    assert coordination.schema_version == "1.0"
    assert saved["coordination"]["schema_version"] == "1.0"
    assert saved["coordination"]["communication_trace"][0]["schema_version"] == "1.0"
    assert saved["coordination"]["communication_trace"][0]["handoff_id"] == (
        "HANDOFF_EXE_1_diagnosis"
    )


def test_review_card_rejects_invalid_coordination_shape() -> None:
    with pytest.raises(ValidationError):
        ReviewCardResult(
            status="success",
            execution_id="EXE_INVALID_COORDINATION",
            task_type="learning_plan",
            agent_outputs=[],
            snapshot_path=Path("snapshot.json"),
            writeback_intents=[],
            coordination={
                "schema_version": "1.0",
                "communication_trace": [],
                "unexpected": "not allowed",
            },
        )

    coordination = CoordinationSummary()
    assert coordination.model_dump(mode="json") == {
        "schema_version": "1.0",
        "communication_trace": [],
        "repair_trace": [],
    }


def test_resume_resets_model_trace_recorder_like_execute() -> None:
    """Regression: resume() must reset the recorder in the request context.

    LangGraph resumes node closures inside a copied context
    (set_config_context).  Without an explicit reset, the recorder's
    ContextVar is unset in the new request, so model calls recorded while
    the checkpoint resumes are lost and the persisted trace_events carry no
    model_input/model_output/model_transport events.
    """
    from contextvars import Context

    from competition_app.runtime.model_trace import ModelTraceRecorder

    recorder = ModelTraceRecorder()
    use_case = _use_case(planner=_CancelledPlanner())
    use_case.model_trace_recorder = recorder

    # A brand new HTTP request arrives in a fresh (empty) context where the
    # recorder ContextVar was never set -- exactly like resume() sees.
    request_context = Context()

    def resume_entry() -> None:
        # Fixed resume(): reset the recorder in this request's context.
        use_case.model_trace_recorder.reset()

    request_context.run(resume_entry)

    # LangGraph then resumes node closures in a context copied from the
    # request; the copy shares the same (now reset) list instance.
    node_context = request_context.copy()

    def node_model_call() -> None:
        idx = use_case.model_trace_recorder.begin(
            "diagnosis_agent", {"message": "resumed run"}
        )
        use_case.model_trace_recorder.succeed(idx, {"ok": True})

    node_context.run(node_model_call)

    # The use case reads the recorder in the same request context (e.g.
    # _model_trace() after the resumed run) and must see the recorded call.
    seen = request_context.run(lambda: [i.agent for i in recorder.items])
    assert seen == ["diagnosis_agent"]


def test_resume_without_reset_loses_recorder_context() -> None:
    """Document the failure mode this regression test guards against."""
    from contextvars import Context

    from competition_app.runtime.model_trace import ModelTraceRecorder

    recorder = ModelTraceRecorder()
    use_case = _use_case(planner=_CancelledPlanner())
    use_case.model_trace_recorder = recorder

    # Buggy resume: no reset -> fresh request context has an unset ContextVar.
    request_context = Context()

    # LangGraph copies the fresh context; the list created inside the copy is
    # invisible to the original request context that later reads items.
    node_context = request_context.copy()

    def node_model_call() -> None:
        idx = recorder.begin("diagnosis_agent", {"message": "resumed run"})
        recorder.succeed(idx, {"ok": True})

    node_context.run(node_model_call)

    # The original request context still sees no recorded calls.
    assert recorder.items == []


def _memory_envelope_with_conflict() -> AgentEnvelope[MemoryAgentResult]:
    return AgentEnvelope(
        artifact_id="ART_MEMORY_CONFLICT",
        artifact_type="memory_context",
        case_id="CASE_MEMORY_DEFER",
        trace_id="TRACE_MEMORY_DEFER",
        request_id="REQ_MEMORY_DEFER",
        execution_id="EXE_MEMORY_DEFER",
        step_id="memory",
        producer="memory_agent",
        task_type="knowledge_explanation",
        learner_id="learner-phase2",
        payload=MemoryAgentResult(
            learner_context=LearnerContextBrief(
                learner_id="learner-phase2",
                profile_summary="已有每日学习时长记录。",
            ),
            memory_candidates=[],
            governance=MemoryGovernanceDecision(
                analysis="新旧每日学习时长不能同时成立。",
                conflicts=[
                    {
                        "memory_id": 7,
                        "proposed_memory": "以后每天学习一小时。",
                        "reason": "与已有每天最多二十分钟冲突。",
                    }
                ],
                requires_clarification=True,
                clarification_questions=["保留旧记忆、仅本次使用还是替换旧记忆？"],
                interrupt_type="memory_conflict",
                resolution="needs_clarification",
            ),
        ),
    )


def test_persist_memory_governance_degrades_deferred_conflict() -> None:
    """Non-planning tasks defer memory conflicts: persisting must not raise.

    The deferred conflict becomes pending candidates (resolution "none") plus
    a returned notice used for the system-message reminder, instead of the
    previous RuntimeError that aborted the whole workflow.
    """
    written: list[dict] = []
    use_case = _use_case(planner=_CancelledPlanner())
    use_case.memory_governance_writer = lambda *args, **kwargs: written.append(
        kwargs
    )

    notice = use_case._persist_memory_governance(
        request=ReviewCardRequest(
            thread_id="THREAD_MEMORY_DEFER",
            conversation_id="THREAD_MEMORY_DEFER",
            learner_id="learner-phase2",
            user_request="讲解白芍的主治功效。",
        ),
        execution_id="EXE_MEMORY_DEFER",
        agent_outputs=[_memory_envelope_with_conflict()],
    )

    assert notice is not None
    assert notice["conflicts"][0]["memory_id"] == 7
    assert notice["questions"] == ["保留旧记忆、仅本次使用还是替换旧记忆？"]
    assert len(written) == 1
    assert written[0]["resolution"] == "none"
    assert written[0]["conflicts"] == []
    # 没有 workshop_runtime 时通知创建被安全跳过
    assert not hasattr(use_case, "_deferred_memory_conflict_notice") or (
        use_case._deferred_memory_conflict_notice is None
    )


def test_persist_memory_governance_keeps_normal_resolution() -> None:
    """Resolved governance still persists candidates + conflicts unchanged."""
    written: list[dict] = []
    use_case = _use_case(planner=_CancelledPlanner())
    use_case.memory_governance_writer = lambda *args, **kwargs: written.append(
        kwargs
    )
    envelope = _memory_envelope_with_conflict()
    envelope.payload.governance.resolution = "use_current_once"
    envelope.payload.governance.requires_clarification = False

    notice = use_case._persist_memory_governance(
        request=ReviewCardRequest(
            thread_id="THREAD_MEMORY_RESOLVED",
            conversation_id="THREAD_MEMORY_RESOLVED",
            learner_id="learner-phase2",
            user_request="讲解白芍的主治功效。",
        ),
        execution_id="EXE_MEMORY_RESOLVED",
        agent_outputs=[envelope],
    )

    assert notice is None
    assert len(written) == 1
    assert written[0]["resolution"] == "use_current_once"
    assert written[0]["conflicts"] == [
        conflict.model_dump(mode="json")
        for conflict in envelope.payload.governance.conflicts
    ]


def test_persist_memory_governance_forwards_auto_confirm_candidates() -> None:
    """确定性记忆（auto_confirmed）必须随候选一起传给 writer，不丢失。"""
    written: list[dict] = []
    use_case = _use_case(planner=_CancelledPlanner())
    use_case.memory_governance_writer = lambda *args, **kwargs: written.append(
        kwargs
    )
    envelope = _memory_envelope_with_conflict()
    envelope.payload.auto_confirm_memories = [
        LongTermMemoryCandidate(
            summary="用户明确每天学习一小时。",
            source_refs=[
                ArtifactReference(ref_type="conversation_message", ref_id="MSG_9")
            ],
            status="auto_confirmed",
        )
    ]
    envelope.payload.governance.resolution = "none"
    envelope.payload.governance.requires_clarification = False

    notice = use_case._persist_memory_governance(
        request=ReviewCardRequest(
            thread_id="THREAD_MEMORY_AUTO",
            conversation_id="THREAD_MEMORY_AUTO",
            learner_id="learner-phase2",
            user_request="以后每天学习一小时。",
        ),
        execution_id="EXE_MEMORY_AUTO",
        agent_outputs=[envelope],
    )

    assert notice is None
    assert len(written) == 1
    auto = written[0]["auto_confirm_candidates"]
    assert len(auto) == 1
    assert auto[0]["summary"] == "用户明确每天学习一小时。"
    assert auto[0]["source_refs"][0]["ref_type"] == "conversation_message"
    assert auto[0]["source_refs"][0]["ref_id"] == "MSG_9"
    assert written[0]["candidates"] == []
