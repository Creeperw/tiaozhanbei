import json

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


class ExplodingSmartPaperMemoryModel:
    async def complete_json(self, role, payload, on_delta=None):
        raise AssertionError("smart-paper Memory must use its bound read-only context")


@pytest.mark.asyncio
async def test_smart_paper_memory_runs_read_only_without_model_or_writes() -> None:
    context = build_context()
    context.update(
        {
            "smart_paper_v2": True,
            "messages": [
                {
                    "message_id": "M_SMART_PAPER",
                    "role": "user",
                    "content": "请围绕四君子汤生成试卷。",
                }
            ],
            "profile": {
                "confirmed_preferences": {"resource_types": ["practice"]}
            },
            "confirmed_memories": ["当前考试为中医执业医师资格考试。"],
        }
    )

    result = await MemoryAgent(ExplodingSmartPaperMemoryModel()).run(context)

    assert result.payload.learner_context.confirmed_preferences == {
        "resource_types": ["practice"]
    }
    assert result.payload.learner_context.relevant_memories == [
        "当前考试为中医执业医师资格考试。"
    ]
    assert result.payload.memory_candidates == []
    assert result.payload.auto_confirm_memories == []
    assert result.payload.governance.requires_clarification is False
    assert "不新增或改写长期记忆" in result.payload.governance.analysis


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
            "summary": "user：短消息也可以由系统明确要求压缩。\nassistant：已压缩长对话。",
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
        return {
            "summary": "user：请总结本轮对话内容。\nassistant：本轮只有需要压缩的对话摘要。"
        }


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


class CrossExamTargetConflictModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "governance_notes": "模型把另一考试工作区的目标误认为当前考试冲突。",
            "memory_candidates": [],
            "conflicts": [
                {
                    "memory_id": 51,
                    "proposed_memory": "当前考试为中西医结合执业医师资格考试。",
                    "reason": "旧记忆记录了另一考试目标。",
                }
            ],
            "requires_clarification": True,
            "clarification_questions": ["请确认替换哪一个考试目标？"],
            "resolution": "needs_clarification",
        }


class UnsupportedClarificationModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "governance_notes": "没有定位到任何与当前请求冲突的既有记忆。",
            "memory_candidates": [],
            "auto_confirm_candidates": [],
            "conflicts": [],
            # Live providers can occasionally set this flag despite providing
            # no conflict and no question.  Such an unsupported interruption
            # must not fail or block the learner workflow.
            "requires_clarification": True,
            "clarification_questions": [],
            "resolution": "needs_clarification",
        }


@pytest.mark.asyncio
async def test_memory_agent_drops_unsupported_clarification_without_inventing_conflict() -> None:
    context = build_context()
    context.update(
        {
            "user_request": "请推荐两项阴阳互根互用的公开学习资料。",
            "messages": [
                {
                    "message_id": "M_NO_CONFLICT",
                    "role": "user",
                    "content": "请推荐两项阴阳互根互用的公开学习资料。",
                }
            ],
            "relevant_personalization_memories": [],
        }
    )

    result = await MemoryAgent(UnsupportedClarificationModel()).run(context)

    assert result.payload.requires_clarification is False
    assert result.payload.governance.conflicts == []
    assert result.payload.governance.clarification_questions == []
    assert result.payload.governance.resolution == "none"
    assert "没有可定位冲突" in result.payload.governance.analysis


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


@pytest.mark.asyncio
async def test_other_exam_target_memory_cannot_interrupt_current_exam_workspace() -> None:
    context = build_context()
    context.update(
        {
            "user_request": "为当前考试制定长期规划。",
            "messages": [
                {
                    "message_id": "M_EXAM_SCOPE",
                    "role": "user",
                    "content": "为当前考试制定长期规划。",
                }
            ],
            "exam_scope_id": "EXAM_2025_INTEGRATED_PHYSICIAN",
            "learning_target": {
                "exam_track_id": "EXAM_2025_INTEGRATED_PHYSICIAN",
                "exam_name": "中西医结合执业医师资格考试",
            },
            "relevant_personalization_memories": [
                {
                    "id": 51,
                    "category": "learning_goal",
                    "title": "另一考试工作区目标",
                    "content": "中医执业医师资格考试（EXAM_2025_TCM_PHYSICIAN）",
                }
            ],
        }
    )

    result = await MemoryAgent(CrossExamTargetConflictModel()).run(context)

    assert result.payload.requires_clarification is False
    assert result.payload.governance.conflicts == []
    assert result.payload.governance.resolution == "none"
    assert "当前考试工作区隔离" in result.payload.governance.analysis


class IncrementalCompressionModel:
    """Records the messages handed to the compression sub-step."""

    def __init__(self) -> None:
        self.compression_input_messages: list[dict[str, str]] = []

    async def complete_json(self, role, payload, on_delta=None):
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
        self.compression_input_messages = list(business_payload.get("messages") or [])
        return {
            "summary": (
                "user：用户今天提出增加方剂背诵侧重。\n"
                "assistant：已纳入后续学习安排。"
            ),
            "preserved_facts": [],
            "unresolved_questions": [],
            "temporary_constraints": [],
            "memory_candidates": [],
        }


@pytest.mark.asyncio
async def test_memory_agent_incremental_compression_uses_existing_summary_and_only_new_messages() -> None:
    model = IncrementalCompressionModel()
    context = build_context()
    context.update(
        {
            "memory_required": True,
            # The durable summary from a previous run, plus the message ids it
            # already covered.  Only the newest message must be compressed.
            "compressed_conversation_summary": (
                "user：用户偏好晚间学习。\nassistant：已确认每天最多60分钟。"
            ),
            "compressed_conversation_covered_message_ids": ["MSG_OLD_1", "MSG_OLD_2"],
            "messages": [
                {"message_id": "MSG_OLD_1", "role": "user", "content": "旧消息一"},
                {"message_id": "MSG_OLD_2", "role": "assistant", "content": "旧回复一"},
                {"message_id": "MSG_NEW_1", "role": "user", "content": "今天增加方剂背诵侧重。"},
            ],
        }
    )

    envelope = await MemoryAgent(model, compression_threshold_chars=1).run(context)

    assert envelope.payload.context_summary is not None
    # The compression sub-step received the previous digest parsed back into
    # pure user/assistant dialogue, plus only the uncovered message.  No
    # system prefixes, no synthetic roles, no covered history.
    assert model.compression_input_messages == [
        {"role": "user", "content": "用户偏好晚间学习。"},
        {"role": "assistant", "content": "已确认每天最多60分钟。"},
        {"role": "user", "content": "今天增加方剂背诵侧重。"},
    ]
    assert "此前" not in json.dumps(model.compression_input_messages, ensure_ascii=False)
    assert "MSG_OLD_1" not in json.dumps(model.compression_input_messages, ensure_ascii=False)
    assert "旧消息一" not in json.dumps(model.compression_input_messages, ensure_ascii=False)
    # The regenerated summary still carries source refs for the full history
    # so the next run knows exactly which messages are already covered.
    assert [ref.ref_id for ref in envelope.payload.context_summary.source_refs] == [
        "MSG_OLD_1",
        "MSG_OLD_2",
        "MSG_NEW_1",
    ]


@pytest.mark.asyncio
async def test_memory_agent_compression_drops_non_dialogue_summary_text() -> None:
    """A legacy/prose summary without user：/assistant： lines is never
    disguised as a formal answer; compression then re-reads full history."""
    model = IncrementalCompressionModel()
    context = build_context()
    context.update(
        {
            "memory_required": True,
            "compressed_conversation_summary": (
                "此前已压缩：用户偏好晚间学习，每天最多60分钟。"
            ),
            "compressed_conversation_covered_message_ids": ["MSG_OLD_1", "MSG_OLD_2"],
            "messages": [
                {"message_id": "MSG_OLD_1", "role": "user", "content": "旧消息一"},
                {"message_id": "MSG_OLD_2", "role": "assistant", "content": "旧回复一"},
                {"message_id": "MSG_NEW_1", "role": "user", "content": "今天增加方剂背诵侧重。"},
            ],
        }
    )

    await MemoryAgent(model, compression_threshold_chars=1).run(context)

    # The prose-only digest is not dialogue history, so the compression input
    # falls back to the full formal conversation only.
    assert model.compression_input_messages == [
        {"role": "user", "content": "旧消息一"},
        {"role": "assistant", "content": "旧回复一"},
        {"role": "user", "content": "今天增加方剂背诵侧重。"},
    ]
