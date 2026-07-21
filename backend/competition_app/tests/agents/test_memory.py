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
        return {
            "summary": "已压缩长对话。",
            "preserved_facts": [],
            "unresolved_questions": [],
            "temporary_constraints": [],
            "memory_candidates": [],
        }


@pytest.mark.asyncio
async def test_memory_agent_does_not_call_model_below_compression_threshold() -> None:
    model = CountingMemoryModel()
    context = build_context()
    context["messages"] = [{"message_id": "M5", "role": "user", "content": "短对话"}]

    with pytest.raises(ValueError, match="compression threshold"):
        await MemoryAgent(model, compression_threshold_chars=100).run(context)

    assert model.calls == 0
