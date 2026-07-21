import pytest

from competition_app.agents.expert import ExpertAgent
from competition_app.agents.audit import AuditAgent
from competition_app.agents.knowledge_base import KnowledgeBaseAgent
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack


class FakeRetrievalTool:
    async def build_evidence_pack(self, query: str) -> EvidencePack:
        return EvidencePack(
            evidence_pack_id="EP_1",
            query=query,
            resolved_kp_ids=["KP_FJ_018"],
            evidence_items=[
                EvidenceItem(
                    evidence_id="E_1",
                    source_id="方剂学:2",
                    content_summary="理中丸由人参、干姜、白术、炙甘草组成。",
                    authority_level="textbook",
                    confidence=0.95,
                    bridge_layer="strict",
                )
            ],
        )


def context() -> dict[str, object]:
    return {
        "case_id": "CASE_1",
        "trace_id": "TRACE_1",
        "request_id": "REQ_1",
        "execution_id": "EXE_1",
        "step_id": "knowledge",
        "learner_id": "L1",
        "topic": "理中丸",
        "available_minutes": 10,
        "dependency_outputs": {},
    }


@pytest.mark.asyncio
async def test_knowledge_agent_uses_retrieval_tool() -> None:
    output = await KnowledgeBaseAgent(FakeRetrievalTool()).run(context())
    assert output.payload.resolved_kp_ids == ["KP_FJ_018"]


@pytest.mark.asyncio
async def test_expert_uses_evidence_instead_of_fixed_formula() -> None:
    ctx = context()
    ctx["step_id"] = "expert"
    ctx["dependency_outputs"] = {
        "knowledge": AgentEnvelope(
            artifact_id="A1", artifact_type="evidence_pack", case_id="CASE_1",
            trace_id="TRACE_1", request_id="REQ_1", execution_id="EXE_1",
            step_id="knowledge", producer="knowledge_base_agent",
            task_type="personalized_review_card", learner_id="L1",
            payload=await FakeRetrievalTool().build_evidence_pack("理中丸"),
        )
    }
    output = await ExpertAgent().run(ctx)

    assert "核心证据" not in output.payload.content
    assert "知识卡片" in output.payload.content
    assert output.payload.claims[0].evidence_ids == ["E_1"]
    assert "四君子汤" not in str(output.payload.content)


@pytest.mark.asyncio
async def test_audit_results_have_unique_ids_across_executions() -> None:
    ctx = context()
    ctx["step_id"] = "audit"
    knowledge = await KnowledgeBaseAgent(FakeRetrievalTool()).run(context())
    expert_context = dict(ctx)
    expert_context["step_id"] = "expert"
    expert_context["dependency_outputs"] = {"knowledge": knowledge}
    expert = await ExpertAgent().run(expert_context)
    ctx["dependency_outputs"] = {"knowledge": knowledge, "expert": expert}
    first = await AuditAgent().run(ctx)
    second = await AuditAgent().run(ctx)
    assert first.payload.audit_result_id != second.payload.audit_result_id


class InvalidAuditModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {"status": "approved"}


class AdvisoryRevisionAuditModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "decision": "revise",
            "findings": ["学习范围略宽，可在后续版本继续精简。"],
        }


@pytest.mark.asyncio
async def test_invalid_audit_protocol_requires_human_review() -> None:
    ctx = context()
    ctx["step_id"] = "audit"
    knowledge = await KnowledgeBaseAgent(FakeRetrievalTool()).run(context())
    expert_ctx = dict(ctx)
    expert_ctx["step_id"] = "expert"
    expert_ctx["dependency_outputs"] = {"knowledge": knowledge}
    expert = await ExpertAgent().run(expert_ctx)
    ctx["dependency_outputs"] = {"knowledge": knowledge, "expert": expert}

    result = await AuditAgent(InvalidAuditModel()).run(ctx)

    assert result.payload.decision == "needs_human_review"
    assert "协议" in result.payload.findings[0]


@pytest.mark.asyncio
async def test_second_review_turns_non_blocking_revision_into_pass() -> None:
    ctx = context()
    ctx["step_id"] = "audit"
    ctx["task_type"] = "personalized_review_card"
    knowledge = await KnowledgeBaseAgent(FakeRetrievalTool()).run(context())
    expert_ctx = dict(ctx)
    expert_ctx["step_id"] = "expert"
    expert_ctx["dependency_outputs"] = {"knowledge": knowledge}
    expert = await ExpertAgent().run(expert_ctx)
    ctx["dependency_outputs"] = {"knowledge": knowledge, "expert": expert}
    ctx["audit_feedback"] = {"findings": ["请精简教学扩展。"]}

    result = await AuditAgent(AdvisoryRevisionAuditModel()).run(ctx)

    assert result.payload.decision == "pass"
    assert any("非阻断建议" in finding for finding in result.payload.findings)


class CapturingModel:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.payload: dict[str, object] | None = None

    async def complete_json(self, role, payload, on_delta=None):
        self.payload = payload
        return self.result


class RetrievalSummaryModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete_json(self, role, payload, on_delta=None):
        self.calls.append(payload)
        if payload["payload"].get("phase") == "plan_retrieval":
            return {
                "kp_query": "理中丸",
                "question_query": "理中丸 相关题目",
                "retrieval_reason": "检索教材依据和候选练习。",
            }
        return {
            "retrieval_summary": "理中丸由人参、干姜、白术、炙甘草组成，核心在温中祛寒、补气健脾。",
            "quality_labels": ["教材依据相关"],
            "uncertainty": [],
        }


@pytest.mark.asyncio
async def test_knowledge_agent_summarizes_non_question_evidence_for_downstream() -> None:
    model = RetrievalSummaryModel()

    output = await KnowledgeBaseAgent(FakeRetrievalTool(), model).run(context())

    assert "温中祛寒" in output.payload.retrieval_summary
    processing_payload = model.calls[1]["payload"]
    assert "question_candidates" not in processing_payload
    assert processing_payload["evidence"][0]["text"].startswith("理中丸由人参")


@pytest.mark.asyncio
async def test_review_expert_uses_model_body_as_card正文() -> None:
    ctx = context()
    ctx["step_id"] = "expert"
    ctx["dependency_outputs"] = {
        "knowledge": AgentEnvelope(
            artifact_id="A1", artifact_type="evidence_pack", case_id="CASE_1",
            trace_id="TRACE_1", request_id="REQ_1", execution_id="EXE_1",
            step_id="knowledge", producer="knowledge_base_agent",
            task_type="personalized_review_card", learner_id="L1",
            payload=(await FakeRetrievalTool().build_evidence_pack("理中丸")).model_copy(
                update={"retrieval_summary": "教材总结：理中丸温中祛寒、补气健脾。"}
            ),
        )
    }
    model = CapturingModel({
        "body": "理中丸以干姜温中祛寒，人参补气，白术健脾燥湿，炙甘草调和诸药。",
        "learning_tip": "闭卷复述组成和配伍关系。",
    })

    output = await ExpertAgent(model).run(ctx)

    assert output.payload.content["知识卡片"]["exp"].startswith("理中丸以干姜")
    assert model.payload["payload"]["retrieval_summary"].startswith("教材总结")


@pytest.mark.asyncio
async def test_expert_receives_semantic_evidence_without_system_identifiers() -> None:
    ctx = context()
    ctx["step_id"] = "expert"
    ctx["dependency_outputs"] = {
        "knowledge": AgentEnvelope(
            artifact_id="A1", artifact_type="evidence_pack", case_id="CASE_1",
            trace_id="TRACE_1", request_id="REQ_1", execution_id="EXE_1",
            step_id="knowledge", producer="knowledge_base_agent",
            task_type="personalized_review_card", learner_id="L1",
            payload=await FakeRetrievalTool().build_evidence_pack("理中丸"),
        )
    }
    model = CapturingModel({"learning_tip": "进行主动回忆。"})

    await ExpertAgent(model).run(ctx)

    business_payload = model.payload["payload"]
    rendered = str(business_payload)
    assert "evidence_id" not in rendered
    assert "claim_id" not in rendered
    assert "KP_FJ_018" not in rendered
    assert "理中丸由人参" in rendered


@pytest.mark.asyncio
async def test_review_expert_context_is_compact_and_has_line_breaks() -> None:
    ctx = context()
    ctx["step_id"] = "expert"
    ctx["dependency_outputs"] = {
        "knowledge": AgentEnvelope(
            artifact_id="A1", artifact_type="evidence_pack", case_id="CASE_1",
            trace_id="TRACE_1", request_id="REQ_1", execution_id="EXE_1",
            step_id="knowledge", producer="knowledge_base_agent",
            task_type="personalized_review_card", learner_id="L1",
            payload=await FakeRetrievalTool().build_evidence_pack("理中丸"),
        )
    }
    model = CapturingModel({"learning_tip": "解释理中丸。"})

    await ExpertAgent(model).run(ctx)

    instructions = model.payload["task_instructions"]
    business = model.payload["payload"]
    assert "\n" in instructions
    assert set(business) <= {
        "topic", "retrieval_summary", "evidence", "candidate_questions", "task", "output_contract"
    }
    assert "semantic_evidence" not in business
    assert "learning_data" not in business
    assert "question_candidate_catalog" not in business


@pytest.mark.asyncio
async def test_audit_receives_semantic_review_context_without_resource_or_evidence_ids() -> None:
    ctx = context()
    ctx["step_id"] = "audit"
    knowledge = await KnowledgeBaseAgent(FakeRetrievalTool()).run(context())
    expert_ctx = dict(ctx)
    expert_ctx["step_id"] = "expert"
    expert_ctx["dependency_outputs"] = {"knowledge": knowledge}
    expert = await ExpertAgent().run(expert_ctx)
    ctx["dependency_outputs"] = {"knowledge": knowledge, "expert": expert}
    model = CapturingModel({"decision": "pass", "findings": []})

    await AuditAgent(model).run(ctx)

    rendered = str(model.payload["payload"])
    assert expert.payload.resource_draft_id not in rendered
    assert expert.payload.claims[0].claim_id not in rendered
    assert "evidence_id" not in rendered
    assert "理中丸由人参" in rendered