import pytest

from competition_app.agents.memory import MemoryAgent
from competition_app.llm.stub import StubChatModel


@pytest.mark.asyncio
async def test_memory_agent_compresses_context_with_sources_without_persisting_memory() -> None:
    agent = MemoryAgent(StubChatModel(), compression_threshold_chars=10)
    context = {
        "case_id": "CASE_1",
        "trace_id": "TRACE_1",
        "request_id": "REQ_1",
        "execution_id": "EXE_1",
        "step_id": "memory_context",
        "learner_id": "learner_001",
        "messages": [
            {"message_id": "MSG_1", "role": "user", "content": "我想复习四君子汤，并偏好对比表。"},
            {"message_id": "MSG_2", "role": "assistant", "content": "已记录本次临时学习请求。"},
        ],
        "profile": {"confirmed_preferences": {"resource_types": ["comparison_card"]}},
        "memory_required": True,
    }

    envelope = await agent.run(context)

    assert envelope.producer == "memory_agent"
    assert envelope.payload.context_summary is not None
    assert [ref.ref_id for ref in envelope.payload.context_summary.source_refs] == ["MSG_1", "MSG_2"]
    assert envelope.payload.learner_context.context_summary_ref.ref_id == envelope.artifact_id
    assert envelope.writeback_intents == []
    assert all(candidate.status == "pending_confirmation" for candidate in envelope.payload.memory_candidates)


def build_context():
    return {
        "case_id": "CASE_1", "trace_id": "TRACE_1", "request_id": "REQ_1",
        "execution_id": "EXE_1", "step_id": "memory_context",
        "learner_id": "learner_001", "profile": {},
    }


@pytest.mark.asyncio
async def test_memory_agent_rejects_message_without_source_id() -> None:
    context = build_context()
    context["messages"] = [{"role": "user", "content": "无来源消息"}]
    with pytest.raises(ValueError, match="message_id"):
        await MemoryAgent(StubChatModel()).run(context)


@pytest.mark.asyncio
async def test_memory_agent_rejects_invalid_role_or_cross_learner_message() -> None:
    context = build_context()
    context["messages"] = [{"message_id": "M2", "role": "admin", "content": "非法角色"}]
    with pytest.raises(ValueError, match="role"):
        await MemoryAgent(StubChatModel()).run(context)

    context["messages"] = [
        {"message_id": "M3", "role": "user", "content": "跨用户", "learner_id": "OTHER"}
    ]
    with pytest.raises(ValueError, match="learner"):
        await MemoryAgent(StubChatModel()).run(context)


class InvalidMemoryModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "summary": "把学习者身份错误地当作模型产物。",
            "preserved_facts": [],
            "unresolved_questions": [],
            "temporary_constraints": [],
            "memory_candidates": [],
            "learner_id": "other_learner",
        }


@pytest.mark.asyncio
async def test_memory_agent_rejects_model_owned_identity_fields() -> None:
    context = build_context()
    context["messages"] = [{"message_id": "M4", "role": "user", "content": "请总结本次会话"}]

    with pytest.raises(ValueError, match="training output contract"):
        await MemoryAgent(InvalidMemoryModel(), compression_threshold_chars=1).run(context)


class CountingMemoryModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete_json(self, role, payload, on_delta=None):
        self.calls += 1
        business_payload = payload.get("payload", payload)
        if "current_user_request" in business_payload:
            return {
                "governance_notes": "没有发现相关记忆冲突。",
                "memory_candidates": [],
                "conflicts": [],
                "requires_clarification": False,
                "clarification_questions": [],
                "resolution": "none",
            }
        return {
            "summary": "已压缩长对话。",
            "preserved_facts": [],
            "unresolved_questions": [],
            "temporary_constraints": [],
            "memory_candidates": [],
        }


@pytest.mark.asyncio
async def test_memory_agent_governs_short_conversation_without_compressing() -> None:
    model = CountingMemoryModel()
    context = build_context()
    context["messages"] = [{"message_id": "M5", "role": "user", "content": "短对话"}]

    envelope = await MemoryAgent(model, compression_threshold_chars=100).run(context)

    assert model.calls == 1
    assert envelope.payload.context_summary is None
    assert envelope.payload.governance is not None
    assert envelope.payload.governance.requires_clarification is False


@pytest.mark.asyncio
async def test_memory_agent_does_not_infer_compression_from_local_message_length() -> None:
    model = CountingMemoryModel()
    context = build_context()
    context["messages"] = [{
        "message_id": "M5_LONG",
        "role": "user",
        "content": "很长的历史内容" * 1000,
    }]
    # The application did not set the system-owned flag, so the Memory Agent
    # must still govern memory but must not invoke compression itself.
    envelope = await MemoryAgent(model, compression_threshold_chars=1).run(context)
    assert model.calls == 1
    assert envelope.payload.context_summary is None
    assert envelope.payload.governance is not None


@pytest.mark.asyncio
async def test_memory_agent_compresses_only_when_system_flag_is_true() -> None:
    model = CountingMemoryModel()
    context = build_context()
    context["messages"] = [{
        "message_id": "M5_FLAGGED",
        "role": "user",
        "content": "短消息也可以由系统明确要求压缩",
    }]
    context["memory_required"] = True

    envelope = await MemoryAgent(model, compression_threshold_chars=100000).run(context)
    assert model.calls == 2
    assert envelope.payload.context_summary is not None


class OmittedOptionalMemoryFieldsModel:
    async def complete_json(self, role, payload, on_delta=None):
        business_payload = payload.get("payload", payload)
        if "current_user_request" in business_payload:
            return {
                "governance_notes": "本轮没有可持久化信息或记忆冲突。",
                "resolution": "none",
            }
        return {"summary": "本轮只有需要压缩的对话摘要。"}


@pytest.mark.asyncio
async def test_memory_agent_accepts_omitted_empty_list_fields() -> None:
    context = build_context()
    context["messages"] = [
        {"message_id": "M6", "role": "user", "content": "请总结本轮对话内容。"}
    ]
    context["memory_required"] = True

    envelope = await MemoryAgent(
        OmittedOptionalMemoryFieldsModel(), compression_threshold_chars=1
    ).run(context)

    assert envelope.payload.context_summary is not None
    assert envelope.payload.context_summary.preserved_facts == []
    assert envelope.payload.context_summary.unresolved_questions == []
    assert envelope.payload.context_summary.temporary_constraints == []
    assert envelope.payload.memory_candidates == []


class ConflictingMemoryModel:
    async def complete_json(self, role, payload, on_delta=None):
        business_payload = payload.get("payload", payload)
        answer = str(business_payload.get("memory_conflict_answer") or "")
        return {
            "governance_notes": "新的稳定每日时长与已有学习时长上限不能同时成立。",
            "memory_candidates": [],
            "conflicts": [
                {
                    "memory_id": 7,
                    "proposed_memory": "以后每天学习一小时。",
                    "reason": "与每天最多二十分钟冲突。",
                }
            ],
            "requires_clarification": not bool(answer),
            "clarification_questions": (
                ["请确认保留原记忆、仅本次采用，还是替换原记忆？"]
                if not answer
                else []
            ),
            "resolution": "replace_existing" if answer else "needs_clarification",
        }


@pytest.mark.asyncio
async def test_memory_agent_interrupts_on_conflict_and_accepts_confirmed_resolution() -> None:
    context = build_context()
    context.update(
        {
            "user_request": "以后每天学习一小时。",
            "messages": [
                {
                    "message_id": "M7",
                    "role": "user",
                    "content": "以后每天学习一小时。",
                }
            ],
            "relevant_personalization_memories": [
                {
                    "id": 7,
                    "category": "preference",
                    "title": "每日时长",
                    "content": "每天最多二十分钟。",
                    "similarity": 0.93,
                }
            ],
        }
    )
    agent = MemoryAgent(ConflictingMemoryModel(), compression_threshold_chars=100)

    interrupted = await agent.run(context)

    assert interrupted.payload.requires_clarification is True
    assert interrupted.payload.interrupt_type == "memory_conflict"

    context["memory_conflict_answer"] = "请用一小时替换原来的记忆"
    resumed = await agent.run(context)

    assert resumed.payload.requires_clarification is False
    assert resumed.payload.governance.resolution == "replace_existing"
