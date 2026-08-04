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
                "【结合学情定位】感冒证型是辨证论治的基础，常考且容易混淆。"
                "【讲解核心】教材优先，网络与模型知识作标注补充。"
                "【启发式思考问题】试着用教材分型对比风寒与风热感冒。"
                "【自然收尾】先说说你的判断，再继续引导。"
            ),
            "thinking_questions": [
                "如果患者发热重、恶寒轻，你会优先考虑哪个证型？",
                "风寒与风热感冒的鉴别关键点是什么？",
            ],
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
            stem="风寒感冒的常用治法是？",
            options=[
                "{'option_id': 'A', 'content': '辛温解表'}",
                "{'option_id': 'B', 'content': '益气健脾'}",
                "{'option_id': 'C', 'content': ''}",
            ],
            reference_answer="A",
            analysis="风寒感冒以辛温解表为常用治法。",
            tags=["感冒"],
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
async def test_knowledge_explanation_keeps_heuristic_thinking_questions() -> None:
    result = await KnowledgeExplanationAgent(CapturingExplanationModel()).run(_context())

    assert result.payload.content["思考问题"] == [
        "如果患者发热重、恶寒轻，你会优先考虑哪个证型？",
        "风寒与风热感冒的鉴别关键点是什么？",
    ]
    assert result.payload.content["知识讲解"].startswith("【结合学情定位】")


@pytest.mark.asyncio
async def test_knowledge_explanation_normalizes_string_thinking_questions() -> None:
    class StringQuestionsModel:
        async def complete_json(self, role, payload, on_delta=None):
            return {
                "title": "气血讲解",
                "explanation_content": "气与血的关系是中医基础的重要内容。",
                "thinking_questions": "气能生血，血能载气，你能举一个生活中的例子吗？",
            }

    result = await KnowledgeExplanationAgent(StringQuestionsModel()).run(_context())

    assert result.payload.content["思考问题"] == ["气能生血，血能载气，你能举一个生活中的例子吗？"]


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
async def test_general_learning_support_keeps_free_form_natural_language() -> None:
    class FlexibleSupportModel:
        async def complete_json(self, role, payload, on_delta=None):
            assert payload["prompt_skill_id"] == "expert.general_learning_support"
            assert "不要求固定标题" in payload["payload"]["output_contract"]["content"]
            return {
                "title": "阴阳学说章节学习要点",
                "content": (
                    "先抓住阴阳的基本属性，再理解对立制约、互根互用、消长平衡和"
                    "相互转化之间的关系。学习时用同一实例贯穿这些关系即可。"
                ),
                "uncertainty": [],
            }

    context = _context()
    context["task_type"] = "general_learning_support"
    context["user_request"] = "梳理《中医学基础》阴阳学说章节的学习要点"
    result = await KnowledgeExplanationAgent(FlexibleSupportModel()).run(context)

    assert "知识讲解" not in result.payload.content
    assert result.payload.content["学习支持"].startswith("先抓住阴阳")
    assert "配套练习" not in result.payload.content


@pytest.mark.asyncio
async def test_current_fact_answer_does_not_add_a_practice_question() -> None:
    model = CapturingExplanationModel()
    context = _context()
    context.update(
        {
            "task_type": "general_learning_support",
            "user_request": "距离下次执业医师资格考试还有多久？",
            "external_information_request": True,
        }
    )

    result = await KnowledgeExplanationAgent(model).run(context)

    assert model.payload["payload"]["external_information_request"] is True
    assert model.payload["payload"]["current_date"]
    assert "配套练习" not in result.payload.content


@pytest.mark.asyncio
async def test_question_explanation_invites_the_learner_to_identify_the_sticking_point() -> None:
    context = _context()
    context["user_request"] = "这道题我不会，应该怎么答？"

    result = await KnowledgeExplanationAgent(CapturingExplanationModel()).run(context)

    assert "这道题你主要卡在哪一步" in result.payload.content["知识讲解"]


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
    assert "配套练习" not in result.payload.content


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
async def test_knowledge_explanation_does_not_force_retrieved_questions() -> None:
    result = await KnowledgeExplanationAgent(CapturingExplanationModel()).run(
        _add_question_candidates(_context())
    )

    assert "配套练习" not in result.payload.content
    assert result.payload.question_consumption is not None
    assert result.payload.question_consumption.use_question_candidates is False
    assert result.payload.question_consumption.selected_question_ids == []


@pytest.mark.asyncio
async def test_knowledge_explanation_keeps_open_questions_in_prose_contract() -> None:
    result = await KnowledgeExplanationAgent(CapturingExplanationModel()).run(_context())

    assert "配套练习" not in result.payload.content
    assert result.payload.content["思考问题"]
    assert result.payload.question_consumption is not None
    assert result.payload.question_consumption.use_question_candidates is False
    assert result.payload.question_consumption.resource_type == "none"


@pytest.mark.asyncio
async def test_knowledge_explanation_rejects_off_topic_question_candidate() -> None:
    context = _add_question_candidates(_context())
    question = context["dependency_outputs"]["knowledge"].payload._question_details[0]
    question.stem = "中医理论体系形成于哪个时期？"
    question.options = ["{'option_id': 'A', 'content': '先秦至汉代'}"]
    question.tags = ["阴阳学说", "中医学史"]

    result = await KnowledgeExplanationAgent(CapturingExplanationModel()).run(context)

    assert result.payload.question_consumption.use_question_candidates is False
    assert result.payload.question_consumption.selected_question_ids == []
    assert "配套练习" not in result.payload.content
