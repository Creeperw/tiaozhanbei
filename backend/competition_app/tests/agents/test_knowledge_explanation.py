import json

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

    assert "这道题你主要卡在哪一步" in result.payload.content["题目讲解"]


@pytest.mark.asyncio
async def test_question_explanation_uses_direct_explanation_skill() -> None:
    class QuestionExplanationModel:
        def __init__(self) -> None:
            self.payload = None

        async def complete_json(self, role, payload, on_delta=None):
            self.payload = payload
            return {
                "title": "感冒辨证题目讲解",
                "explanation_content": (
                    "【考查要点】这道题考风寒与风热感冒的证候鉴别。"
                    "【直接作答】正确答案是 A：辛温解表。依据教材：风寒感冒恶寒重、发热轻。"
                    "【选项辨析】B 选项益气健脾适用于气虚，与本题证候不符。"
                    "【易错提示】注意恶寒与发热的轻重对比，避免证候错辨。"
                ),
                "thinking_questions": [],
                "uncertainty": [],
            }

    context = _context()
    context["user_request"] = "这道题我不会，应该怎么答？"

    model = QuestionExplanationModel()
    result = await KnowledgeExplanationAgent(model).run(context)

    assert model.payload["prompt_skill_id"] == "expert.explain_question"
    assert model.payload["payload"]["phase"] == "question_explanation"
    assert model.payload["payload"]["question_explanation_request"] is True
    assert "直接讲题结构" in model.payload["payload"]["output_contract"]["content"]
    assert result.payload.title == "感冒辨证题目讲解"
    assert result.payload.content["题目讲解"].startswith("【考查要点】")


@pytest.mark.asyncio
async def test_knowledge_explanation_keeps_heuristic_skill_for_plain_requests() -> None:
    class CapturingModel:
        def __init__(self) -> None:
            self.payload = None

        async def complete_json(self, role, payload, on_delta=None):
            self.payload = payload
            return {
                "title": "感冒证型讲解",
                "explanation_content": (
                    "【结合学情定位】感冒证型是辨证论治的基础。"
                    "【讲解核心】风寒与风热感冒的主要区别在于恶寒与发热的轻重。"
                    "【启发式思考问题】试着用教材分型对比风寒与风热感冒。"
                    "【自然收尾】先说说你的判断，再继续引导。"
                ),
                "thinking_questions": ["风寒与风热感冒的鉴别关键点是什么？"],
                "uncertainty": [],
            }

    model = CapturingModel()
    result = await KnowledgeExplanationAgent(model).run(_context())

    assert model.payload["prompt_skill_id"] == "expert.explain_domain_knowledge"
    assert model.payload["payload"]["phase"] == "knowledge_explanation"
    assert "启发式引导式结构" in model.payload["payload"]["output_contract"]["content"]
    assert result.payload.content["知识讲解"].startswith("【结合学情定位】")


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


class RefsModel:
    """Emits evidence_refs for the two evidence items in _context()."""

    def __init__(self) -> None:
        self.payload = None

    async def complete_json(self, role, payload, on_delta=None):
        self.payload = payload
        return {
            "title": "感冒证型讲解",
            "explanation_content": "风寒与风热感冒的鉴别要点。",
            "thinking_questions": [],
            "uncertainty": [],
            "evidence_refs": ["E1", "E2"],
        }


@pytest.mark.asyncio
async def test_knowledge_explanation_sends_evidence_ids_to_the_model() -> None:
    model = RefsModel()

    await KnowledgeExplanationAgent(model).run(_context())

    evidence = model.payload["payload"]["semantic_evidence"]
    assert evidence[0]["evidence_id"] == "E1"
    assert evidence[1]["evidence_id"] == "E2"


@pytest.mark.asyncio
async def test_reference_card_marks_textbook_and_web_sources() -> None:
    result = await KnowledgeExplanationAgent(RefsModel()).run(_context())

    markup = result.payload.content["知识讲解"]
    assert markup.startswith("风寒与风热感冒的鉴别要点。")
    assert "<<REFS:" in markup
    refs = json.loads(markup.split("<<REFS:", 1)[1].rsplit(">>", 1)[0])
    assert refs[0]["type"] == "rag"
    assert refs[0]["title"] == "教材来源"
    assert refs[0]["evidence_id"] == "E1"
    assert refs[1]["type"] == "web"
    assert refs[1]["title"] == "reference来源"
    assert refs[1]["url"] == "https://example.test/guide"
    assert refs[1]["evidence_id"] == "E2"


@pytest.mark.asyncio
async def test_reference_card_uses_source_label_when_present() -> None:
    class LabeledModel:
        async def complete_json(self, role, payload, on_delta=None):
            return {
                "title": "护理讲解",
                "explanation_content": "护理辨证要点。",
                "evidence_refs": ["E_BOOK"],
            }

    evidence = EvidencePack(
        evidence_pack_id="EP2",
        query="护理",
        evidence_items=[
            EvidenceItem(
                evidence_id="E_BOOK",
                source_id="中医临床护理学_clean:00042",
                content_summary="辨证护理与辨病护理的要点。",
                authority_level="textbook",
                confidence=0.9,
                source_label="《中医临床护理学》· 第一节 中医临床护理的病证特点",
            )
        ],
    )
    context = _context()
    context["dependency_outputs"]["knowledge"].payload = evidence

    result = await KnowledgeExplanationAgent(LabeledModel()).run(context)

    markup = result.payload.content["知识讲解"]
    refs = json.loads(markup.split("<<REFS:", 1)[1].rsplit(">>", 1)[0])
    assert refs[0]["type"] == "rag"
    assert refs[0]["title"] == "《中医临床护理学》· 第一节 中医临床护理的病证特点"


@pytest.mark.asyncio
async def test_reference_card_drops_fabricated_evidence_ids() -> None:
    class FabricatingModel:
        async def complete_json(self, role, payload, on_delta=None):
            return {
                "title": "感冒证型讲解",
                "explanation_content": "风寒与风热感冒的鉴别要点。",
                "evidence_refs": ["E1", "MADE_UP_BOOK", "E2", "MADE_UP_URL"],
            }

    result = await KnowledgeExplanationAgent(FabricatingModel()).run(_context())

    markup = result.payload.content["知识讲解"]
    refs = json.loads(markup.split("<<REFS:", 1)[1].rsplit(">>", 1)[0])
    assert [ref["evidence_id"] for ref in refs] == ["E1", "E2"]


@pytest.mark.asyncio
async def test_no_reference_card_when_evidence_refs_empty() -> None:
    class EmptyRefsModel:
        async def complete_json(self, role, payload, on_delta=None):
            return {
                "title": "感冒证型讲解",
                "explanation_content": "风寒与风热感冒的鉴别要点。",
                "evidence_refs": [],
            }

    result = await KnowledgeExplanationAgent(EmptyRefsModel()).run(_context())

    assert result.payload.content["知识讲解"] == "风寒与风热感冒的鉴别要点。"
    assert "<<REFS:" not in result.payload.content["知识讲解"]


@pytest.mark.asyncio
async def test_legacy_model_without_evidence_refs_stays_compatible() -> None:
    result = await KnowledgeExplanationAgent(CapturingExplanationModel()).run(_context())

    assert "<<REFS:" not in result.payload.content["知识讲解"]
