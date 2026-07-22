import pytest

from competition_app.application.container import ApplicationContainer
from competition_app.application.personalized_review_card import ReviewCardRequest
from competition_app.config import Settings


def request(*, message: str, workflow: str = "auto") -> ReviewCardRequest:
    return ReviewCardRequest(
        learner_id="ROUTING_USER_1",
        user_request=message,
        available_minutes=15,
        messages=[{"message_id": "ROUTING_MSG_1", "role": "user", "content": message}],
    )


@pytest.mark.asyncio
async def test_planner_routes_plan_request_without_expert_or_audit(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        request(message="请帮我制定本周四君子汤复习计划")
    )

    producers = {item.producer for item in result.agent_outputs}
    assert result.task_type == "learning_plan"
    assert producers == {
        "planner_agent",
        "default_route_resolver",
        "knowledge_base_agent",
        "diagnosis_agent",
        "learning_plan_service",
    }
    assert result.learning_plan is not None
    assert result.resource is None
    assert result.audit is None
    assert result.review_task is None


@pytest.mark.asyncio
async def test_server_behavior_context_is_loaded_before_diagnosis(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    captured = {}
    original = container.review_card_use_case.orchestrator.agent_registry.get(
        "diagnosis_agent"
    )

    class CapturingDiagnosisAgent:
        async def run(self, context):
            captured.update(context)
            return await original.run(context)

    container.review_card_use_case.orchestrator.agent_registry._agents[
        "diagnosis_agent"
    ] = CapturingDiagnosisAgent()
    container.review_card_use_case.behavior_context_loader = lambda learner_id: {
        "source": "frontend_backend",
        "calculated_at": "2026-07-21T08:00:00+08:00",
        "learning_profile": {
            "current_status": {
                "status_code": "T2",
                "status_name": "节奏恢复",
                "confidence": 0.91,
                "evidence": ["服务端近七日行为"],
            }
        },
        "system_data": {"task_completion_rate": {"value": 0.43}},
        "question_attempt": [
            {
                "attempt_id": "SERVER_ATTEMPT_1",
                "user_id": learner_id,
                "question_id": "Q_1",
                "is_correct": False,
            }
        ],
    }

    await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="BEHAVIOR_USER_1",
            user_request="请结合我的学习状态制定本周学习计划",
            learning_profile={"current_status": {"status_code": "FORGED"}},
            system_data={"task_completion_rate": {"value": 1.0}},
        )
    )

    assert captured["learning_profile"]["current_status"]["status_code"] == "T2"
    assert captured["system_data"]["task_completion_rate"]["value"] == 0.43
    assert captured["question_attempts"][0]["attempt_id"] == "SERVER_ATTEMPT_1"
    assert captured["behavior_context_source"] == "frontend_backend"


@pytest.mark.asyncio
async def test_empty_server_attempts_reject_client_forged_queue_admission(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    container.review_card_use_case.behavior_context_loader = lambda learner_id: {
        "source": "frontend_backend",
        "question_attempt": [],
    }

    await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="BEHAVIOR_USER_2",
            user_request="请生成四君子汤复习卡",
            question_attempt=[{
                "attempt_id": "FORGED_ATTEMPT_1",
                "kp_ids": ["KP_FJ_001"],
                "is_correct": True,
                "score": 100,
                "answered_at": "2026-07-21T08:00:00Z",
            }],
        )
    )

    assert container.review_service.get_queue("BEHAVIOR_USER_2").entries == []


@pytest.mark.asyncio
async def test_planner_routes_resource_request_through_expert_and_audit(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        request(message="请生成一张可以直接学习的四君子汤复习卡")
    )

    producers = {item.producer for item in result.agent_outputs}
    assert result.task_type == "personalized_review_card"
    assert "default_route_resolver" in producers
    assert "expert_agent" in producers
    assert "audit_agent" in producers
    assert result.resource is not None
    assert result.audit is not None


@pytest.mark.asyncio
async def test_followup_knowledge_request_reuses_server_conversation_history(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    captured = {}
    original = container.review_card_use_case.orchestrator.agent_registry.get("knowledge_base_agent")

    class CapturingKnowledgeAgent:
        async def run(self, context):
            captured["messages"] = list(context.get("messages", []))
            return await original.run({**context, "user_request": "四君子汤"})

    container.review_card_use_case.orchestrator.agent_registry._agents["knowledge_base_agent"] = CapturingKnowledgeAgent()
    await container.review_card_use_case.execute(ReviewCardRequest(
        learner_id="CONTEXT_USER_1",
        conversation_id="CONTEXT_CONVERSATION_1",
        user_request="给我讲解一下感冒的证型有哪几种",
    ))
    await container.review_card_use_case.execute(ReviewCardRequest(
        learner_id="CONTEXT_USER_1",
        conversation_id="CONTEXT_CONVERSATION_1",
        user_request="这些证型分别怎么治疗？",
    ))

    contents = [item["content"] for item in captured["messages"]]
    assert "给我讲解一下感冒的证型有哪几种" in contents
    assert "这些证型分别怎么治疗？" in contents
    assert all(item.get("message_id") for item in captured["messages"])


@pytest.mark.asyncio
async def test_long_conversation_forces_memory_compression_before_knowledge(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    result = await container.review_card_use_case.execute(ReviewCardRequest(
        learner_id="CONTEXT_USER_2",
        conversation_id="CONTEXT_CONVERSATION_2",
        user_request="请解释四君子汤是什么？",
        messages=[{
            "role": "user",
            "content": "感冒证型学习背景：" + "风寒、风热、暑湿。" * 500,
        }],
    ))
    producers = [item.producer for item in result.agent_outputs]
    assert "memory_agent" in producers
    assert producers.index("memory_agent") < producers.index("knowledge_base_agent")


@pytest.mark.asyncio
async def test_planner_routes_plan_plus_learning_card_through_resource_chain(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        request(message="请结合我的学习状态，为四君子汤制定一份本周学习计划，需要生成学习卡片。")
    )

    producers = {item.producer for item in result.agent_outputs}
    assert result.task_type == "personalized_review_card"
    assert {
        "default_route_resolver", "learning_plan_service", "expert_agent", "audit_agent"
    }.issubset(producers)
    assert result.learning_plan is not None
    assert result.resource is not None
    assert "【本次目标】" in result.resource.content["学习提示"]


@pytest.mark.asyncio
async def test_learning_status_request_reuses_existing_plans(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    existing_long = {
        "plan_id": "LONG_OLD",
        "content": (
            "【最终目标】已有长期规划。"
            "【能力路径与阶段】基础→应用。"
            "【阶段里程碑】完成阶段验收；截止待确认。"
            "【资源预算】投入待确认。"
            "【重规划条件】目标或时间变化时调整。"
            "【保温底线】每周一次回忆。"
        ),
        "version": 3,
        "status": "active",
    }
    existing_short = {
        "plan_id": "SHORT_OLD",
        "content": (
            "【当前主目标】已有短期规划。"
            "【长期目标保温】每周一次回忆。"
            "【时间分配】时间待确认。"
            "【具体任务块】完成回忆，产出口述结果，完成标准为完整复述。"
            "【复习任务】完成后复盘。"
            "【反馈指标】记录完成率。"
        ),
        "version": 5,
        "status": "active",
    }

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="STATUS_REUSE_1",
            user_request="我最近的学习状态如何？",
            available_minutes=15,
            user_profile={"goals": {"short_term_goal": "本周掌握当前主题"}},
            long_term_plan=existing_long,
            short_term_plan=existing_short,
        )
    )

    assert result.task_type == "learning_plan"
    assert result.learning_plan.long_term_plan.content == existing_long["content"]
    assert result.learning_plan.short_term_plan.content == existing_short["content"]
    diagnosis = next(item for item in result.agent_outputs if item.producer == "diagnosis_agent")
    assert {item.producer for item in result.agent_outputs} == {
        "planner_agent", "default_route_resolver", "diagnosis_agent", "learning_plan_service"
    }
    assert diagnosis.payload.learning_plan_proposal.long_term_plan_action == "reuse"
    assert diagnosis.payload.learning_plan_proposal.short_term_plan_action == "reuse"


@pytest.mark.asyncio
async def test_planner_routes_exam_paper_request_to_blueprint_chain(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="PAPER_USER_1",
            user_request="请围绕四君子汤生成一份60分钟练习试卷蓝图",
            available_minutes=60,
            exam_constraints={
                "exam_type": "章节练习",
                "duration_minutes": 60,
                "total_score": 100,
            },
        )
    )

    assert result.task_type == "paper_generation"
    assert {item.producer for item in result.agent_outputs} == {
        "planner_agent", "knowledge_base_agent", "expert_agent", "audit_agent"
    }
    assert result.learning_plan is None
    assert result.review_task is None
    assert result.resource is not None
    assert result.resource.title == "四君子汤章节练习试卷"
    assert result.resource.content["试卷正文"]
    assert "answer_key" not in result.resource.content
    assert "explanations" not in result.resource.content
    assert result.resource_version is not None
    assert result.audit.decision == "pass"


@pytest.mark.asyncio
async def test_paper_publishes_separate_answers_when_user_requests_them(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="PAPER_USER_WITH_ANSWERS",
            user_request=(
                "请围绕四君子汤生成一份2道填空题的试卷，覆盖组成和功效主治，"
                "必须提供答案和解析。"
            ),
            available_minutes=30,
        )
    )

    assert result.task_type == "paper_generation"
    assert result.audit.decision == "pass"
    assert len(result.resource.content["试卷正文"]) == 2
    assert len(result.resource.content["参考答案"]) == 2
    assert len(result.resource.content["答案解析"]) == 2
    assert all(item["答案"] for item in result.resource.content["参考答案"])
    assert all(item["解析"] for item in result.resource.content["答案解析"])
    assert "内部检索信息仍不公开" in result.resource.safety_notes[0]


@pytest.mark.asyncio
async def test_planner_routes_plain_explanation_without_learning_plan(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="EXPLAIN_USER_1",
            user_request="给我讲一讲四君子汤",
            available_minutes=15,
        )
    )

    assert result.task_type == "knowledge_explanation"
    assert {item.producer for item in result.agent_outputs} == {
        "planner_agent", "knowledge_base_agent", "expert_agent", "audit_agent"
    }
    assert result.learning_plan is None
    assert result.review_task is None
    assert result.resource_binding is None
    assert result.resource is not None
    assert "知识讲解" in result.resource.content
    assert result.resource.content["配套练习"]
    assert {intent.effect_type for intent in result.writeback_intents} == {
        "record_audit", "publish_resource"
    }
