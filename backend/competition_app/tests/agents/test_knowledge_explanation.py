import pytest

from competition_app.agents.knowledge_explanation import KnowledgeExplanationAgent
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import (
    EvidenceItem,
    EvidencePack,
    QuestionBridge,
    QuestionDetail,
    QuestionRetrievalMetadata,
)


class CapturingExplanationModel:
    def __init__(self) -> None:
        self.payload = None

    async def complete_json(self, role, payload, on_delta=None):
        self.payload = payload
        return {
            "title": "感冒的常见证型",
            "explanation_content": (
                "【先给结论】直接回答常见证型。"
                "【核心概念】教材优先，网络与模型知识作标注补充。"
                "【关键机制或辨析】不同教材口径可能不同。"
                "【学习者易错点】不要把教学分类当成现实诊断。"
                "【小结】结合来源层级理解。"
            ),
            "uncertainty": ["具体分类以所用教材为准。"],
        }


def _context() -> dict:
    evidence = EvidencePack(
        evidence_pack_id="EP1",
        query="感冒证型",
        resolved_kp_ids=[],
        evidence_items=[
            EvidenceItem(
                evidence_id="E1", source_id="教材:1",
                content_summary="感冒证候分型见表2-1-1。",
                authority_level="textbook", confidence=0.9,
            ),
            EvidenceItem(
                evidence_id="E2", source_id="WEB1",
                content_summary="普通感冒中医诊疗指南摘要。",
                authority_level="web_reference", confidence=0.7,
                resource_type="reference", source_url="https://example.test/guide",
            ),
        ],
    )
    return {
        "case_id": "C1", "trace_id": "T1", "request_id": "R1",
        "execution_id": "E1", "step_id": "expert", "learner_id": "L1",
        "task_type": "knowledge_explanation", "user_request": "感冒有哪几种症型？",
        "dependency_outputs": {
            "knowledge": AgentEnvelope(
                artifact_id="A1", artifact_type="evidence_pack", case_id="C1",
                trace_id="T1", request_id="R1", execution_id="E1", step_id="knowledge",
                producer="knowledge_base_agent", task_type="knowledge_explanation",
                learner_id="L1", payload=evidence,
            )
        },
    }


def _add_question_candidates(context: dict) -> dict:
    evidence = context["dependency_outputs"]["knowledge"].payload
    evidence._question_details = [
        QuestionDetail(
            question_id="Q_1",
            question_type="单项选择题",
            stem="四君子汤的主要功用是？",
            options=[
                "{'option_id': 'A', 'content': '益气健脾'}",
                "{'option_id': 'B', 'content': '温中祛寒'}",
                "{'option_id': 'C', 'content': ''}",
            ],
            reference_answer="A",
            analysis="四君子汤为益气健脾代表方。",
            tags=["方剂学"],
            source_metadata={},
            bridges=[
                QuestionBridge(
                    kp_id="KP_1",
                    bridge_layer="strict",
                    relation="primary",
                    confidence=0.9,
                    rank=1,
                    evidence_chunk_uid="C_1",
                    match_method="strict",
                )
            ],
            retrieval=QuestionRetrievalMetadata(
                channels=["bridge"],
                channel_scores={"bridge": 1.0},
                fusion_score=1.0,
            ),
        )
    ]
    return context


@pytest.mark.asyncio
async def test_knowledge_explanation_receives_source_metadata_and_fallback_policy() -> None:
    model = CapturingExplanationModel()

    await KnowledgeExplanationAgent(model).run(_context())

    evidence = model.payload["payload"]["semantic_evidence"]
    assert evidence[1]["resource_type"] == "reference"
    assert evidence[1]["source_url"] == "https://example.test/guide"
    instructions = model.payload["task_instructions"]
    assert "模型自身知识" in instructions
    assert "不得伪造" in instructions


@pytest.mark.asyncio
async def test_knowledge_explanation_accepts_plain_natural_language_body() -> None:
    class PlainTextModel:
        async def complete_json(self, role, payload, on_delta=None):
            return {
                "content": "四君子汤以人参补气为核心，配白术、茯苓和炙甘草共同益气健脾。",
                "notes": "教材版本可能存在用药名称差异。",
            }

    result = await KnowledgeExplanationAgent(PlainTextModel()).run(_context())

    assert result.payload.title
    assert result.payload.content["知识讲解"].startswith("四君子汤以人参")
    assert result.payload.content["待确认项"] == ["教材版本可能存在用药名称差异。"]


@pytest.mark.asyncio
async def test_knowledge_explanation_drops_generic_uncertainty_placeholders() -> None:
    class PlaceholderModel:
        async def complete_json(self, role, payload, on_delta=None):
            return {
                "title": "四君子汤讲解",
                "explanation_content": "四君子汤以益气健脾为主要功用。",
                "uncertainty": ["待确认", "待确认。", "暂无", "无待确认项", "N/A"],
            }

    result = await KnowledgeExplanationAgent(PlaceholderModel()).run(_context())

    assert result.payload.content["知识讲解"] == "四君子汤以益气健脾为主要功用。"
    assert "待确认项" not in result.payload.content
    assert result.payload.content["配套练习"]


@pytest.mark.asyncio
async def test_knowledge_explanation_keeps_only_specific_uncertainty() -> None:
    class MixedUncertaintyModel:
        async def complete_json(self, role, payload, on_delta=None):
            return {
                "title": "四君子汤讲解",
                "explanation_content": "四君子汤以益气健脾为主要功用。",
                "uncertainty": [
                    "待确认",
                    "不同教材对人参名称的标注版本需要确认。",
                    "不同教材对人参名称的标注版本需要确认。",
                ],
            }

    result = await KnowledgeExplanationAgent(MixedUncertaintyModel()).run(_context())

    assert result.payload.content["待确认项"] == [
        "不同教材对人参名称的标注版本需要确认。"
    ]


@pytest.mark.asyncio
async def test_knowledge_explanation_uses_retrieved_questions_without_answers() -> None:
    result = await KnowledgeExplanationAgent(CapturingExplanationModel()).run(
        _add_question_candidates(_context())
    )

    assert result.payload.content["配套练习"] == [
        {
            "题型": "单项选择题",
            "题目": "四君子汤的主要功用是？",
            "选项": ["A. 益气健脾", "B. 温中祛寒"],
        }
    ]
    assert result.payload.question_consumption is not None
    assert result.payload.question_consumption.selected_question_ids == ["Q_1"]
    assert "reference_answer" not in str(result.payload.content["配套练习"])
    assert "analysis" not in str(result.payload.content["配套练习"])


@pytest.mark.asyncio
async def test_knowledge_explanation_falls_back_to_open_self_check_questions() -> None:
    result = await KnowledgeExplanationAgent(CapturingExplanationModel()).run(_context())

    questions = result.payload.content["配套练习"]
    assert len(questions) == 2
    assert all(item["选项"] == [] for item in questions)
    assert result.payload.question_consumption is not None
    assert result.payload.question_consumption.use_question_candidates is False
    assert result.payload.question_consumption.resource_type == "practice"
