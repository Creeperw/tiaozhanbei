import json
import asyncio
from pathlib import Path

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
