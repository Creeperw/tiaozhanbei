"""End-to-end tests for the inline review-task adjustment path.

The Planner classifies a request such as “复习任务太多了，少安排一点” as
``review_task_adjustment`` with an execution intent; the use case then executes
the write (capacity preference / task status) deterministically and composes
the reply from the real, post-change state.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from competition_app.agents.common import envelope
from competition_app.agents.memory import MemoryAgentResult
from competition_app.agents.planner import PlannerDecision
from competition_app.application.personalized_review_card import (
    PersonalizedReviewCardUseCase,
    ReviewCardRequest,
)
from competition_app.contracts.memory import (
    LearnerContextBrief,
    MemoryGovernanceDecision,
)
from competition_app.contracts.review import ReviewMemoryUnit, ReviewTask
from competition_app.repositories.review import InMemoryReviewRepository
from competition_app.services.review import ReviewService

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


class _Planner:
    def __init__(self, decision: PlannerDecision) -> None:
        self.decision = decision

    async def run(self, context):
        return envelope(context, "planner_agent", "planner_decision", self.decision)


class _MemoryAgent:
    def __init__(self, result: MemoryAgentResult) -> None:
        self.result = result

    async def run(self, context):
        return envelope(
            context,
            "memory_agent",
            "memory_agent_result",
            self.result,
        )


class _AgentRegistry:
    def __init__(
        self,
        planner: _Planner,
        memory_agent: _MemoryAgent | None = None,
    ) -> None:
        self.planner = planner
        self.memory_agent = memory_agent

    def get(self, name):
        if name == "planner_agent":
            return self.planner
        if name == "memory_agent":
            assert self.memory_agent is not None
            return self.memory_agent
        raise AssertionError(f"unexpected agent: {name}")


class _Orchestrator:
    tool_registry = None

    def __init__(
        self,
        planner: _Planner,
        memory_agent: _MemoryAgent | None = None,
    ) -> None:
        self.agent_registry = _AgentRegistry(planner, memory_agent)


class _SnapshotExporter:
    def export(self, case_id, execution_id, payload):
        return Path("snapshot.json")


def _use_case(
    decision: PlannerDecision,
    review_service: ReviewService | None,
    *,
    memory_agent: _MemoryAgent | None = None,
    memory_governance_writer=None,
):
    return PersonalizedReviewCardUseCase(
        orchestrator=_Orchestrator(_Planner(decision), memory_agent),
        snapshot_exporter=_SnapshotExporter(),
        review_service=review_service,
        memory_governance_writer=memory_governance_writer,
    )


def _decision(
    adjustment: str,
    *,
    selected_agents: list[str] | None = None,
) -> PlannerDecision:
    return PlannerDecision(
        task_type="review_task_adjustment",
        review_adjustment=adjustment,
        selected_agents=list(selected_agents or []),
        routing_reason="用户要求调整复习任务安排。",
    )


def _memory_result() -> MemoryAgentResult:
    return MemoryAgentResult(
        learner_context=LearnerContextBrief(learner_id="L1"),
        governance=MemoryGovernanceDecision(
            analysis="用户希望控制每日复习任务量。",
            resolution="none",
        ),
    )


def _request(learner_id: str = "L1", text: str = "复习任务太多了，少安排一点"):
    return ReviewCardRequest(
        thread_id="THREAD_REVIEW_ADJUST",
        conversation_id="THREAD_REVIEW_ADJUST",
        learner_id=learner_id,
        user_request=text,
        available_minutes=15,
        messages=[
            {
                "message_id": "M1",
                "role": "user",
                "content": text,
            }
        ],
    )


def _unit(learner_id: str, kp_id: str, abstract: str) -> ReviewMemoryUnit:
    return ReviewMemoryUnit(
        memory_unit_id=f"RMU_{kp_id}",
        learner_id=learner_id,
        kp_id=kp_id,
        prompt_abstract=abstract,
        mastery_score=60.0,
        lambda_per_day=0.08,
        next_review_at=NOW - timedelta(days=1),
        source_calculated_at=NOW - timedelta(days=7),
        source_attempt_id="ATT_1",
        created_at=NOW - timedelta(days=7),
        updated_at=NOW - timedelta(days=1),
    )


def _active_task(learner_id: str, kp_id: str) -> ReviewTask:
    return ReviewTask(
        review_task_id=f"RT_{kp_id}",
        learner_id=learner_id,
        primary_kp_id=kp_id,
        source_type="system_recommended",
        priority_score=0.8,
        status="pending",
    )


@pytest.mark.asyncio
async def test_reduce_capacity_without_preference_sets_capacity_one() -> None:
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    result = await _use_case(_decision("reduce_capacity"), service).execute(
        _request()
    )

    assert result.status == "success"
    assert result.task_type == "review_task_adjustment"
    assert service.get_daily_capacity("L1") == 1
    assert "1 条/天" in result.direct_response


@pytest.mark.asyncio
async def test_reduce_capacity_lowers_existing_preference_by_one() -> None:
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    service.set_daily_capacity("L1", 3)
    result = await _use_case(_decision("reduce_capacity"), service).execute(
        _request()
    )

    assert service.get_daily_capacity("L1") == 2
    assert "2 条/天" in result.direct_response


@pytest.mark.asyncio
async def test_reduce_capacity_never_goes_below_one() -> None:
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    service.set_daily_capacity("L1", 1)
    result = await _use_case(_decision("reduce_capacity"), service).execute(
        _request()
    )

    assert service.get_daily_capacity("L1") == 1
    assert "1 条/天" in result.direct_response


@pytest.mark.asyncio
async def test_adjustment_with_memory_agent_persists_governance() -> None:
    """复习调整附带长期偏好时必须执行并持久化 Memory Agent 分支。"""
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    written: list[dict] = []
    use_case = _use_case(
        _decision("reduce_capacity", selected_agents=["memory_agent"]),
        service,
        memory_agent=_MemoryAgent(_memory_result()),
        memory_governance_writer=lambda *args, **kwargs: written.append(kwargs),
    )

    result = await use_case.execute(_request())

    assert result.status == "success"
    assert service.get_daily_capacity("L1") == 1
    assert [item.producer for item in result.agent_outputs] == [
        "planner_agent",
        "memory_agent",
    ]
    assert len(written) == 1
    assert written[0]["resolution"] == "none"


@pytest.mark.asyncio
async def test_increase_capacity_without_preference_defaults_to_three() -> None:
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    result = await _use_case(_decision("increase_capacity"), service).execute(
        _request("L1", "复习任务太少了，每天多安排一点")
    )

    assert service.get_daily_capacity("L1") == 3
    assert "3 条/天" in result.direct_response


@pytest.mark.asyncio
async def test_cancel_tasks_cancels_active_deliveries_and_postpones_unit() -> None:
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    repository.save_memory_unit(_unit("L1", "KP_A", "中药学 第一章"))
    repository.update_task_status(_active_task("L1", "KP_A").review_task_id, "pending")
    repository._tasks["RT_KP_A"] = _active_task("L1", "KP_A")

    result = await _use_case(_decision("cancel_tasks"), service).execute(
        _request("L1", "取消今天的中药复习任务")
    )

    assert result.status == "success"
    cancelled = repository.get_task("RT_KP_A")
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    unit = repository.get_memory_unit("L1", "KP_A")
    assert unit is not None
    assert unit.next_review_at > datetime.now(timezone.utc) + timedelta(hours=23)
    assert "中药学 第一章" in result.direct_response


@pytest.mark.asyncio
async def test_snooze_tasks_returns_task_to_pending_and_postpones_unit() -> None:
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    repository.save_memory_unit(_unit("L1", "KP_B", "方剂学 麻黄汤"))
    repository._tasks["RT_KP_B"] = _active_task("L1", "KP_B")

    result = await _use_case(_decision("snooze_tasks"), service).execute(
        _request("L1", "把明天的复习任务推迟")
    )

    assert result.status == "success"
    task = repository.get_task("RT_KP_B")
    assert task is not None
    assert task.status == "pending"
    unit = repository.get_memory_unit("L1", "KP_B")
    assert unit is not None
    assert unit.next_review_at > datetime.now(timezone.utc) + timedelta(hours=23)
    assert "方剂学 麻黄汤" in result.direct_response


@pytest.mark.asyncio
async def test_cancel_without_active_tasks_reports_nothing_to_adjust() -> None:
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    result = await _use_case(_decision("cancel_tasks"), service).execute(
        _request("L1", "取消复习任务")
    )

    assert result.status == "success"
    assert "当前没有待处理的复习任务" in result.direct_response


@pytest.mark.asyncio
async def test_cancel_postpones_due_memory_units_without_tasks() -> None:
    """到期但尚未物化为 review_tasks 的记忆单元也必须被“取消”（推迟）。"""
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    repository.save_memory_unit(_unit("L1", "KP_DUE_1", "湿性黏滞"))
    repository.save_memory_unit(_unit("L1", "KP_DUE_2", "饮食宜忌"))

    result = await _use_case(_decision("cancel_tasks"), service).execute(
        _request("L1", "取消我今天的所有复习任务")
    )

    assert result.status == "success"
    assert "已取消以下复习任务" in result.direct_response
    assert "湿性黏滞" in result.direct_response
    assert "饮食宜忌" in result.direct_response
    for kp_id in ("KP_DUE_1", "KP_DUE_2"):
        unit = repository.get_memory_unit("L1", kp_id)
        assert unit is not None
        assert unit.next_review_at > datetime.now(timezone.utc) + timedelta(hours=23)


@pytest.mark.asyncio
async def test_snooze_postpones_due_memory_units_without_tasks() -> None:
    """snooze 同样覆盖到期但无任务的记忆单元。"""
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    repository.save_memory_unit(_unit("L1", "KP_DUE_3", "结节性红斑"))

    result = await _use_case(_decision("snooze_tasks"), service).execute(
        _request("L1", "把今天的复习任务推迟")
    )

    assert result.status == "success"
    assert "已把以下复习任务推迟到 24 小时后再提醒" in result.direct_response
    assert "结节性红斑" in result.direct_response
    unit = repository.get_memory_unit("L1", "KP_DUE_3")
    assert unit is not None
    assert unit.next_review_at > datetime.now(timezone.utc) + timedelta(hours=23)


@pytest.mark.asyncio
async def test_cancel_handles_mixed_due_units_and_active_tasks() -> None:
    """到期记忆单元与活跃任务混合时两者都被处理，回复合并。"""
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    repository.save_memory_unit(_unit("L1", "KP_DUE_4", "腹中痛"))
    repository.save_memory_unit(_unit("L1", "KP_TASK_1", "中医学的学科属性"))
    repository._tasks["RT_KP_TASK_1"] = _active_task("L1", "KP_TASK_1")

    result = await _use_case(_decision("cancel_tasks"), service).execute(
        _request("L1", "取消今天的复习任务")
    )

    assert result.status == "success"
    assert "已取消以下复习任务" in result.direct_response
    assert "腹中痛" in result.direct_response
    assert "中医学的学科属性" in result.direct_response
    # 活跃任务被取消
    assert repository.get_task("RT_KP_TASK_1").status == "cancelled"
    # 到期记忆单元被推迟
    unit = repository.get_memory_unit("L1", "KP_DUE_4")
    assert unit.next_review_at > datetime.now(timezone.utc) + timedelta(hours=23)


@pytest.mark.asyncio
async def test_adjustment_without_review_service_returns_friendly_error() -> None:
    result = await _use_case(_decision("reduce_capacity"), None).execute(_request())

    assert result.status == "success"
    assert "没能完成复习任务调整" in result.direct_response


@pytest.mark.asyncio
async def test_adjustment_reply_is_persisted_to_conversation() -> None:
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    use_case = _use_case(_decision("reduce_capacity"), service)
    await use_case.execute(_request("L1", "少安排一点复习"))

    messages = use_case.conversation_repository.get_messages(
        "THREAD_REVIEW_ADJUST", "L1"
    )
    assert messages
    assert any(
        item.get("role") == "assistant" and "条/天" in str(item.get("content", ""))
        for item in messages
    )


@pytest.mark.asyncio
async def test_system_operation_creates_lightweight_hidden_session() -> None:
    """系统自动任务（system_operation）保留轻量会话：仅请求消息落库、不出现在
    侧边栏列表、不保存 assistant 回复，供排查使用。"""
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    use_case = _use_case(_decision("reduce_capacity"), service)
    request = _request("L1", "复习任务太多了，少安排一点")
    request.system_operation = "due_review_dispatch"
    result = await use_case.execute(request)

    assert result.status == "success"
    conversation = use_case.conversation_repository
    # 会话被标记为 system：默认列表不可见，显式查询可见（轻量保留）
    assert conversation.sessions["THREAD_REVIEW_ADJUST"]["source"] == "system"
    assert conversation.list_sessions("L1") == []
    assert conversation.list_sessions("L1", include_system=True)
    # 只保留请求消息，不保存 assistant 回复
    messages = conversation.get_messages("THREAD_REVIEW_ADJUST", "L1")
    assert [item.get("role") for item in messages] == ["user"]
    assert "复习任务太多了" in messages[0]["content"]


@pytest.mark.asyncio
async def test_built_in_wizard_surface_hides_the_session_but_keeps_the_reply() -> None:
    """产品内置向导（conversation_surface=system_task）不出现在 AI 助手历史里。

    与 system_operation 的区别：system_operation 表示「这一轮没有会话内的消费
    方」，所以不落库 assistant 回复；而学习路径规划是同一会话的多阶段向导，后
    续阶段要靠已落库的上下文摘要接续，所以回复必须完整保留。
    """
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    use_case = _use_case(_decision("reduce_capacity"), service)
    request = _request("L1", "少安排一点复习")
    request.conversation_surface = "system_task"
    result = await use_case.execute(request)

    assert result.status == "success"
    conversation = use_case.conversation_repository
    assert conversation.sessions["THREAD_REVIEW_ADJUST"]["source"] == "system"
    assert conversation.list_sessions("L1") == []
    messages = conversation.get_messages("THREAD_REVIEW_ADJUST", "L1")
    assert [item.get("role") for item in messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_system_sessions_do_not_take_the_internal_prompt_as_their_title() -> None:
    """系统会话的标题不得来自内部指令原文。

    规划向导的首条消息是服务端拼的内部指令（【当前考试】…【执行要求】…）。
    把它截断成 40 字当标题，会让会话在排障日志和任何按来源展示的地方留下一条
    机器指令；调用方已经给了可读标题，服务端不该覆盖它。
    """
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    use_case = _use_case(_decision("reduce_capacity"), service)
    internal_prompt = (
        "【当前考试】中医执业医师资格考试（考试标识：EXAM_2025_TCM_PHYS）\n"
        "【当前任务】生成长期学习计划\n"
        "【执行要求】只输出计划本身。"
    )
    request = _request("L1", internal_prompt)
    request.conversation_surface = "system_task"
    await use_case.execute(request)

    conversation = use_case.conversation_repository
    title = conversation.sessions["THREAD_REVIEW_ADJUST"]["title"]
    assert title == "新对话"
    assert "【当前考试】" not in title


@pytest.mark.asyncio
async def test_user_sessions_still_take_their_first_message_as_the_title() -> None:
    """用户会话仍以首条消息命名——收紧系统会话不能顺带弄丢这个行为。"""
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    use_case = _use_case(_decision("reduce_capacity"), service)
    await use_case.execute(_request("L1", "复习任务太多了，少安排一点"))

    conversation = use_case.conversation_repository
    assert conversation.sessions["THREAD_REVIEW_ADJUST"]["title"] == "复习任务太多了，少安排一点"


@pytest.mark.asyncio
async def test_user_operation_keeps_full_conversation_visible() -> None:
    """用户发起的任务（无 system_operation）保持完整会话：可见、含 assistant 回复。"""
    repository = InMemoryReviewRepository()
    service = ReviewService(repository)
    use_case = _use_case(_decision("reduce_capacity"), service)
    await use_case.execute(_request("L1", "少安排一点复习"))

    conversation = use_case.conversation_repository
    assert conversation.sessions["THREAD_REVIEW_ADJUST"]["source"] == "user"
    assert [item["id"] for item in conversation.list_sessions("L1")] == [
        "THREAD_REVIEW_ADJUST"
    ]
    messages = conversation.get_messages("THREAD_REVIEW_ADJUST", "L1")
    roles = [item.get("role") for item in messages]
    assert roles == ["user", "assistant"]
