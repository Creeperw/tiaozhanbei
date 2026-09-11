import pytest

from competition_app.agents.expert import ExpertAgent
from competition_app.agents.audit import AuditAgent
from competition_app.agents.knowledge_base import KnowledgeBaseAgent
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack
from competition_app.llm.openai_compatible import ModelResponseError


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


class GenericQueryRetrievalTool(FakeRetrievalTool):
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def build_evidence_pack(self, query: str) -> EvidencePack:
        self.queries.append(query)
        if query == "中医药基础知识点":
            raise LookupError("generic query is not a formal knowledge point")
        return (await super().build_evidence_pack(query)).model_copy(
            update={"query": query}
        )


class GenericQueryModel:
    async def complete_json(self, role, payload, on_delta=None):
        if payload["payload"].get("phase") == "plan_retrieval":
            return {
                "kp_query": "中医药基础知识点",
                "question_query": "感冒 练习题",
                "retrieval_reason": "检索中医药基础内容。",
            }
        return {
            "retrieval_summary": "教材中的感冒知识摘要。",
            "quality_labels": ["教材依据相关"],
            "uncertainty": [],
        }


class SummaryItemsModel:
    """Invalid source IDs must be corrected by the same model, not filtered."""

    def __init__(self):
        self.summary_calls = 0

    async def complete_json(self, role, payload, on_delta=None):
        if payload["payload"].get("phase") == "plan_retrieval":
            return {
                "kp_query": "理中丸",
                "question_query": "理中丸 相关题目",
                "retrieval_reason": "检索教材依据和候选练习。",
            }
        self.summary_calls += 1
        repaired = payload["payload"]["protocol_repair"]
        result = {
            "retrieval_summary": "",
            "summary_items": [
                {"evidence_id": "E_1", "content": "理中丸由人参、干姜、白术、炙甘草组成。"},
                {"evidence_id": "E_FAKE_NOT_EXISTS", "content": "伪造来源内容。"},
                {"evidence_id": "E_2", "content": "（E_2 不在本次证据集中，应被过滤）"},
            ],
            "learning_focus_status": "supported",
            "learning_focus_items": [
                {"name": "理中丸", "evidence_id": "E_1"},
                {"name": "四君子汤", "evidence_id": "E_1"},
                {"name": "伪造方剂", "evidence_id": "E_FAKE_NOT_EXISTS"},
            ],
            "quality_labels": ["教材依据相关"],
            "uncertainty": [],
        }
        if repaired:
            result["summary_items"] = result["summary_items"][:1]
            result["learning_focus_items"] = result["learning_focus_items"][:2]
        return result


class RetrievalPlanTransportFailureModel:
    async def complete_json(self, role, payload, on_delta=None):
        if payload["payload"].get("phase") == "plan_retrieval":
            raise ModelResponseError("transport failed", reason="transport_error")
        return {
            "retrieval_summary": "理中丸教材摘要。",
            "summary_items": [
                {
                    "evidence_id": "E_1",
                    "content": "理中丸由人参、干姜、白术、炙甘草组成。",
                }
            ],
            "quality_labels": ["教材依据相关"],
            "uncertainty": [],
            "need_more_retrieval": False,
            "supplemental_queries": [],
        }


class SummaryTransportFailureModel:
    async def complete_json(self, role, payload, on_delta=None):
        if payload["payload"].get("phase") == "plan_retrieval":
            return {
                "kp_query": "理中丸",
                "question_query": "理中丸 相关题目",
                "retrieval_reason": "检索教材依据和候选练习。",
            }
        raise ModelResponseError("transport failed", reason="transport_error")


class QuestionTimeoutRetrievalTool(FakeRetrievalTool):
    async def get_question_with_content(self, *args, **kwargs):
        raise TimeoutError("question embedding timed out")


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
async def test_knowledge_agent_does_not_rewrite_unmatched_query() -> None:
    retrieval = GenericQueryRetrievalTool()
    ctx = context()
    ctx["user_request"] = "给我讲讲感冒的知识点"

    output = await KnowledgeBaseAgent(retrieval, GenericQueryModel()).run(ctx)

    assert retrieval.queries == ["中医药基础知识点"]
    assert output.payload.query == "中医药基础知识点"


@pytest.mark.asyncio
async def test_knowledge_agent_keeps_evidence_when_optional_question_search_times_out() -> None:
    output = await KnowledgeBaseAgent(QuestionTimeoutRetrievalTool()).run(context())

    assert output.payload.resolved_kp_ids == ["KP_FJ_018"]
    assert output.payload.question_candidates == []
    assert any("题目候选检索暂不可用" in note for note in output.payload.risk_notes)


@pytest.mark.asyncio
async def test_knowledge_agent_extracts_each_evidence_with_system_filled_source() -> None:
    model = SummaryItemsModel()
    output = await KnowledgeBaseAgent(FakeRetrievalTool(), model).run(context())

    assert model.summary_calls == 2
    # 由模型修正非法引用，系统不会静默删掉其选用材料。
    assert [item.evidence_id for item in output.payload.summary_items] == ["E_1"]
    item = output.payload.summary_items[0]
    assert item.content == "理中丸由人参、干姜、白术、炙甘草组成。"
    # 来源字段由系统从 EvidenceItem 确定性补全
    assert item.source_id == "方剂学:2"
    assert item.authority_level == "textbook"
    assert item.resource_type == "textbook"
    # 引用 id 列表与逐条提取一致
    assert output.payload.summary_evidence_ids == ["E_1"]
    # 兼容文本字段仍可拼接展示
    assert "理中丸由人参" in output.payload.retrieval_summary
    # 系统复核教材 ID，语义支持由模型判断，不按正文关键词删除对象。
    assert output.payload.learning_focus_status == "supported"
    assert [item.name for item in output.payload.learning_focus_items] == ["理中丸", "四君子汤"]
    assert output.payload.learning_focus_items[0].evidence_id == "E_1"


@pytest.mark.asyncio
async def test_knowledge_agent_does_not_search_when_retrieval_planner_transport_fails() -> None:
    retrieval = GenericQueryRetrievalTool()
    ctx = context()
    ctx["user_request"] = "请讲解理中丸"

    with pytest.raises(ModelResponseError):
        await KnowledgeBaseAgent(retrieval, RetrievalPlanTransportFailureModel()).run(ctx)
    assert retrieval.queries == []


@pytest.mark.asyncio
async def test_knowledge_agent_does_not_fabricate_selection_when_summary_transport_fails() -> None:
    with pytest.raises(ModelResponseError):
        await KnowledgeBaseAgent(
            FakeRetrievalTool(), SummaryTransportFailureModel()
        ).run(context())


@pytest.mark.asyncio
async def test_knowledge_agent_stops_after_one_protocol_repair_without_raw_fallback() -> None:
    class InvalidModel:
        summary_calls = 0

        async def complete_json(self, role, payload, on_delta=None):
            if payload["payload"].get("phase") == "plan_retrieval":
                return {
                    "kp_query": "理中丸",
                    "question_query": "理中丸 相关题目",
                    "retrieval_reason": "检索教材依据和候选练习。",
                }
            self.summary_calls += 1
            assert payload["payload"]["protocol_repair"] is (self.summary_calls == 2)
            return {
                "retrieval_summary": "",
                "summary_items": "not-a-list",
                "quality_labels": {"not": "a-list"},
                "uncertainty": [],
            }

    model = InvalidModel()
    with pytest.raises(ValueError):
        await KnowledgeBaseAgent(FakeRetrievalTool(), model).run(context())
    assert model.summary_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    {"summary_items": [{"evidence_id": "UNKNOWN", "content": "内容"}]},
    {"summary_items": "not-a-list"},
    {"summary_items": [{"id": "E_1", "text": "内容"}]},
    {"summary_items": [{"evidence_id": "E_1", "content": " "}]},
    {"learning_focus_status": "supported", "learning_focus_items": [
        {"name": "理中丸", "evidence_id": "UNKNOWN"}]},
    {"learning_focus_status": "supported", "learning_focus_items": [
        {"name": "理中丸", "evidence_id": "E_1"},
        {"name": "理中丸", "evidence_id": "E_1"}]},
    {"learning_focus_items": "not-a-list"},
])
async def test_invalid_selection_is_repaired_once_then_stops(invalid):
    class InvalidSelectionModel:
        def __init__(self):
            self.calls = 0

        async def complete_json(self, role, payload, on_delta=None):
            if payload["payload"].get("phase") == "plan_retrieval":
                return {"kp_query": "理中丸", "question_query": None,
                        "retrieval_reason": "核查教材依据。"}
            self.calls += 1
            assert payload["payload"]["protocol_repair"] is (self.calls == 2)
            return {"summary_items": [], "learning_focus_items": [],
                    "need_more_retrieval": False, **invalid}

    model = InvalidSelectionModel()
    with pytest.raises(ValueError):
        await KnowledgeBaseAgent(FakeRetrievalTool(), model).run(context())
    assert model.calls == 2


class WebEvidenceRetrievalTool:
    """同时包含教材切片与网络搜索（视频/参考）条目的证据集。"""

    async def build_evidence_pack(self, query: str) -> EvidencePack:
        return EvidencePack(
            evidence_pack_id="EP_WEB",
            query=query,
            resolved_kp_ids=["KP_FJ_018"],
            evidence_items=[
                EvidenceItem(
                    evidence_id="E_TEXTBOOK",
                    source_id="方剂学:2",
                    content_summary="理中丸由人参、干姜、白术、炙甘草组成。",
                    authority_level="textbook",
                    confidence=0.95,
                    bridge_layer="strict",
                ),
                EvidenceItem(
                    evidence_id="E_VIDEO",
                    source_id="video:1",
                    content_summary="【视频】理中丸方义讲解：温中祛寒、补气健脾。",
                    authority_level="web_video",
                    confidence=0.8,
                    bridge_layer="external",
                    source_url="https://example.com/video/1",
                    source_label="理中丸方义讲解视频",
                    resource_type="video",
                ),
                EvidenceItem(
                    evidence_id="E_REFERENCE",
                    source_id="ref:2",
                    content_summary="【论文】理中丸临床研究综述：辨证要点与加减应用。",
                    authority_level="web_reference",
                    confidence=0.7,
                    bridge_layer="external",
                    source_url="https://example.com/ref/2",
                    source_label="理中丸研究综述",
                    resource_type="reference",
                ),
            ],
        )


class TextbookOnlySummaryModel:
    """模型只提取教材条目，漏掉网络搜索条目。"""

    async def complete_json(self, role, payload, on_delta=None):
        if payload["payload"].get("phase") == "plan_retrieval":
            return {
                "kp_query": "理中丸",
                "question_query": "理中丸 相关题目",
                "retrieval_reason": "检索教材依据和候选练习。",
            }
        return {
            "retrieval_summary": "",
            "summary_items": [
                {"evidence_id": "E_TEXTBOOK", "content": "理中丸由人参、干姜、白术、炙甘草组成。"},
            ],
            "quality_labels": ["教材依据相关"],
            "uncertainty": [],
        }


@pytest.mark.asyncio
async def test_knowledge_agent_does_not_adopt_web_evidence_without_model_selection() -> None:
    """保留原始网络材料，不把未采用材料冒充为模型选择结果。"""
    output = await KnowledgeBaseAgent(
        WebEvidenceRetrievalTool(), TextbookOnlySummaryModel()
    ).run(context())

    by_id = {item.evidence_id: item for item in output.payload.summary_items}
    # 教材条目保留模型提取内容
    assert by_id["E_TEXTBOOK"].content == "理中丸由人参、干姜、白术、炙甘草组成。"
    assert set(by_id) == {"E_TEXTBOOK"}
    raw = {item.evidence_id: item for item in output.payload.evidence_items}
    assert raw["E_VIDEO"].source_url == "https://example.com/video/1"
    assert raw["E_REFERENCE"].source_url == "https://example.com/ref/2"
    assert set(raw) == {
        "E_TEXTBOOK", "E_VIDEO", "E_REFERENCE"
    }
    assert output.payload.summary_evidence_ids == ["E_TEXTBOOK"]


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
        if role == "audit_findings_compiler":
            message = payload["payload"]["findings"][0]
            return {"status": "compiled", "issues": [{
                "issue_type": "content_quality", "message": message,
                "blocking": False, "location_keys": ["resource:whole"],
                "source_anchors": [{"source_field": "findings", "source_quote": message}],
            }]}
        return {
            "decision": "revise",
            "findings": ["学习范围略宽，可在后续版本继续精简。"],
        }


@pytest.mark.asyncio
async def test_invalid_audit_protocol_is_safely_sent_to_human_review() -> None:
    """审核器没有形成有效结论时，待审核内容不得自动发布。"""
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
    assert any(
        issue.issue_type == "unresolved" and issue.blocking
        for issue in result.payload.structured_findings
    )


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
    # Shared conversation/request context is injected for every agent; the
    # business slice itself must stay compact and free of internal identifiers.
    assert set(business) <= {
        "topic", "retrieval_summary", "evidence", "candidate_questions", "task",
        "output_contract",
        "personalization", "acceptance_policy", "candidate_resources",
        "shared_context", "user_request", "original_user_request", "request_context",
    }
    assert "semantic_evidence" not in business
    assert "learning_data" not in business
    assert "question_candidate_catalog" not in business
    assert "evidence_id" not in str(business)
    assert "claim_id" not in str(business)


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
