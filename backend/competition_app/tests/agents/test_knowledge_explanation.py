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
    RetrievalSummaryItem,
)
from competition_app.contracts.resource import ResourceDraft


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


class CapturingRepairModel(CapturingExplanationModel):
    async def complete_json(self, role, payload, on_delta=None):
        self.payload = payload
        repair_request = payload["payload"]["repair_request"]
        assert repair_request["issue_ids"] == ["FACT-1"]
        assert repair_request["locations"][0]["location_key"] == "resource:claim:1"
        assert "只修正以下已定位问题" in repair_request["instruction"]
        assert repair_request["audit_findings"] == [
            "正文把选项 C 判定为正确，但证据要求选项 E。"
        ]
        assert repair_request["previous_resource"]["title"] == "上一版讲解"
        assert repair_request["previous_resource"]["content"]["题目讲解"].startswith(
            "上一版正文"
        )
        return {
            "title": "修订后的讲解",
            "explanation_content": (
                "正确答案是 E。依据教材证据，选项 C 与题目要求不一致。"
                "逐项辨析时应以题干和教材依据为准。"
            ),
            "thinking_questions": [],
            "uncertainty": [],
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
async def test_knowledge_explanation_injects_bounded_local_repair_request() -> None:
    model = CapturingRepairModel()
    context = _context()
    context["audit_feedback"] = {
        "findings": ["正文把选项 C 判定为正确，但证据要求选项 E。"]
    }
    context["repair_instruction"] = {
        "issue_ids": ["FACT-1"],
        "locations": [{"location_key": "resource:claim:1", "display_label": "答案"}],
        "repair_instruction": "只修正以下已定位问题，保留其他已经通过的内容。",
    }
    context["previous_step_output"] = ResourceDraft(
        resource_draft_id="DRAFT_PREVIOUS",
        title="上一版讲解",
        content={"题目讲解": "上一版正文：正确答案是 C。"},
        estimated_minutes=10,
    )

    await KnowledgeExplanationAgent(model).run(context)

    repair_request = model.payload["payload"]["repair_request"]
    assert repair_request["issue_ids"] == ["FACT-1"]
    assert repair_request["previous_resource"]["title"] == "上一版讲解"
    assert "audit_feedback" in model.payload["payload"]


@pytest.mark.asyncio
async def test_natural_language_repair_request_survives_text_payload_conversion() -> None:
    class NaturalLanguageRepairModel:
        def __init__(self) -> None:
            self.payload = None

        async def complete_text(self, role, payload, on_delta=None):
            self.payload = payload
            assert payload["payload"]["repair_request"]["issue_ids"] == ["FACT-2"]
            assert "上一版正文" in str(payload["payload"]["repair_request"])
            return (
                "# 修订后的讲解\n\n"
                "依据教材证据，正确答案应与题目证据保持一致。"
                "选项辨析必须同步说明错误选项的具体原因。"
            )

    model = NaturalLanguageRepairModel()
    context = _context()
    context["audit_feedback"] = {"findings": ["答案与教材证据冲突。"]}
    context["repair_instruction"] = {
        "issue_ids": ["FACT-2"],
        "repair_instruction": "只修正答案与证据冲突。",
    }
    context["previous_step_output"] = {
        "title": "旧讲解",
        "content": {"题目讲解": "上一版正文"},
        "estimated_minutes": 8,
    }

    result = await KnowledgeExplanationAgent(model).run(context)

    assert result.payload.title == "修订后的讲解"


@pytest.mark.asyncio
async def test_knowledge_explanation_keeps_heuristic_thinking_questions() -> None:
    result = await KnowledgeExplanationAgent(CapturingExplanationModel()).run(_context())

    assert result.payload.content["思考问题"] == [
        "如果患者发热重、恶寒轻，你会优先考虑哪个证型？",
        "风寒与风热感冒的鉴别关键点是什么？",
    ]
    assert result.payload.content["知识讲解"] == (
        "【结合学情定位】感冒证型是辨证论治的基础，常考且容易混淆。"
        "【讲解核心】教材优先，网络与模型知识作标注补充。"
        "【启发式思考问题】试着用教材分型对比风寒与风热感冒。"
        "【自然收尾】先说说你的判断，再继续引导。"
    )


@pytest.mark.asyncio
async def test_heading_only_detailed_explanation_falls_back_to_safe_evidence() -> None:
    class HeadingOnlyModel:
        async def complete_json(self, role, payload, on_delta=None):
            assert callable(payload["_result_validator"])
            return {
                "title": "阴阳学说",
                "explanation_content": "## 为什么先学阴阳学说？",
                "thinking_questions": ["阴阳之间有什么关系？"],
                "uncertainty": [],
            }

    context = _context()
    context["user_request"] = "请依据教材详细讲解感冒证型，不要臆造。"

    result = await KnowledgeExplanationAgent(HeadingOnlyModel()).run(context)

    assert result.payload.content["知识讲解"] == "感冒证候分型见表2-1-1。\n普通感冒中医诊疗指南摘要。"
    assert result.payload.content["待确认项"] == [
        "模型讲解未通过完整性校验，系统仅展示已检索到的可靠摘要。"
    ]
    assert "思考问题" not in result.payload.content


@pytest.mark.asyncio
async def test_detailed_explanation_validator_accepts_substantive_body() -> None:
    class ValidatorAwareModel:
        async def complete_json(self, role, payload, on_delta=None):
            result = {
                "title": "感冒证型详解",
                "explanation_content": (
                    "感冒辨证首先看恶寒与发热的轻重，再结合汗出、口渴、咽部表现和舌脉综合判断。"
                    "风寒证常见恶寒较重、发热较轻、无汗、头身疼痛，治法侧重辛温解表；"
                    "风热证常见发热较重、微恶风、口渴和咽痛，治法侧重辛凉解表。"
                    "这些表现需要放在同一证候组合中判断，不能凭单个症状直接下结论。"
                    "复习时可按证候、治法和代表方三个层次对照，避免只背方名而忽略辨证依据。"
                ),
                "thinking_questions": ["若同时出现恶寒和口渴，应继续核对哪些证据？"],
                "uncertainty": [],
            }
            return payload["_result_validator"](result)

    context = _context()
    context["user_request"] = "请依据教材详细讲解感冒证型，不要臆造。"

    result = await KnowledgeExplanationAgent(ValidatorAwareModel()).run(context)

    assert result.payload.content["知识讲解"].startswith("感冒辨证首先看")
    assert "待确认项" not in result.payload.content


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
async def test_question_explanation_does_not_append_a_domain_specific_sticking_point() -> None:
    context = _context()
    context["user_request"] = "这道题我不会，应该怎么答？"

    result = await KnowledgeExplanationAgent(CapturingExplanationModel()).run(context)

    body = result.payload.content["题目讲解"]
    assert "这道题你主要卡在哪一步" not in body
    assert "证候识别、治法选择、代表方对应" not in body


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
                    "你对本题的风寒与风热鉴别点还有哪里不清楚？"
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
    assert "小节标题自由拟定" in model.payload["payload"]["output_contract"]["content"]
    assert "直接给出答案/思路与依据" in model.payload["payload"]["output_contract"]["content"]
    assert result.payload.title == "感冒辨证题目讲解"
    assert result.payload.content["题目讲解"].startswith(
        "【考查要点】这道题考风寒与风热感冒的证候鉴别。"
    )
    assert "【易错提示】注意恶寒与发热的轻重对比，避免证候错辨。" in result.payload.content["题目讲解"]
    assert "你对本题的风寒与风热鉴别点还有哪里不清楚？" in result.payload.content["题目讲解"]
    assert "这道题你主要卡在哪一步" not in result.payload.content["题目讲解"]


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
    assert "小节标题自由拟定" in model.payload["payload"]["output_contract"]["content"]
    assert "末尾提出 2-3 个开放式思考问题" in model.payload["payload"]["output_contract"]["content"]
    assert result.payload.content["知识讲解"] == (
        "【结合学情定位】感冒证型是辨证论治的基础。"
        "【讲解核心】风寒与风热感冒的主要区别在于恶寒与发热的轻重。"
        "【启发式思考问题】试着用教材分型对比风寒与风热感冒。"
        "【自然收尾】先说说你的判断，再继续引导。"
    )


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
async def test_knowledge_explanation_compacts_large_evidence_context_for_expert() -> None:
    context = _context()
    pack = context["dependency_outputs"]["knowledge"].payload
    pack.evidence_items = []
    pack.summary_items = []
    for index in range(15):
        resource_type = "textbook" if index >= 5 else "reference"
        evidence_id = f"E{index}"
        pack.evidence_items.append(
            EvidenceItem(
                evidence_id=evidence_id,
                source_id=f"SOURCE_{index}",
                content_summary=f"第{index}条可靠内容。",
                authority_level=("textbook" if resource_type == "textbook" else "web_reference"),
                confidence=0.9,
                resource_type=resource_type,
            )
        )
        pack.summary_items.append(
            RetrievalSummaryItem(
                evidence_id=evidence_id,
                source_id=f"SOURCE_{index}",
                content=f"第{index}条可靠内容。" * 20,
                authority_level=("textbook" if resource_type == "textbook" else "web_reference"),
                resource_type=resource_type,
            )
        )
    model = CapturingExplanationModel()

    await KnowledgeExplanationAgent(model).run(context)

    evidence = model.payload["payload"]["semantic_evidence"]
    assert len(evidence) == 10
    assert all(item["resource_type"] == "textbook" for item in evidence)
    # The complete auditable evidence remains on the pack for downstream Audit.
    assert len(pack.evidence_items) == 15
    assert len(pack.summary_items) == 15


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


@pytest.mark.asyncio
async def test_production_capable_expert_prefers_natural_language_without_json_controls() -> None:
    class NaturalLanguageCapableModel:
        def __init__(self) -> None:
            self.text_payload = None
            self.json_calls = 0

        async def complete_text(self, role, payload, on_delta=None):
            self.text_payload = payload
            return (
                "# 风寒与风热感冒辨析\n\n"
                "感冒辨证不能只看单个症状，而要综合恶寒与发热轻重、汗出、口渴及舌脉。"
                "风寒证通常恶寒较重、无汗，风热证通常发热较重并可见口渴咽痛。"
            )

        async def complete_json(self, role, payload, on_delta=None):
            self.json_calls += 1
            raise AssertionError("ordinary Expert prose must not request strict JSON")

    model = NaturalLanguageCapableModel()
    result = await KnowledgeExplanationAgent(model).run(_context())

    assert model.json_calls == 0
    assert "_result_validator" not in model.text_payload
    assert "output_schema" not in model.text_payload["payload"]
    assert "evaluation_contract" not in model.text_payload["payload"]
    assert "只输出给学习者看的完整自然语言" in (
        model.text_payload["payload"]["output_contract"]["content"]
    )
    body = result.payload.content["知识讲解"]
    assert result.payload.title == "风寒与风热感冒辨析"
    assert body.startswith("# 风寒与风热感冒辨析")
    assert "<<REFS:" in body


@pytest.mark.asyncio
async def test_d1_evaluation_uses_production_prose_and_system_compiles_binding() -> None:
    class EvaluationModel:
        def __init__(self) -> None:
            self.text_calls = 0
            self.json_calls = 0
            self.text_payload = None

        async def complete_text(self, role, payload, on_delta=None):
            self.text_calls += 1
            self.text_payload = payload
            return "# 冲突证据讲解\n\n两条资料对同一证型的判断存在差异，应明确说明分歧而不能自动放行。"

        async def complete_json(self, role, payload, on_delta=None):
            self.json_calls += 1
            raise AssertionError("D1 Expert prose must not request strict JSON")

    model = EvaluationModel()
    context = _context()
    context["d1_evaluation_mode"] = True
    context["d1_conflict_binding"] = {
        "issue_id": "D1_TEST",
        "claim_location": "resource.claims[0]",
        "required_evidence_ids": ["E1", "E2"],
    }

    result = await KnowledgeExplanationAgent(model).run(context)

    assert model.text_calls == 1
    assert model.json_calls == 0
    assert "output_schema" not in model.text_payload["payload"]
    assert "evaluation_contract" not in model.text_payload["payload"]
    materials = model.text_payload["payload"]["conflict_materials_to_reconcile"]
    assert [item["text"] for item in materials] == [
        "感冒证候分型见表2-1-1。",
        "普通感冒中医诊疗指南摘要。",
    ]
    assert all("evidence_id" not in item for item in materials)
    assert result.payload.claims[0].evidence_ids == ["E1", "E2"]
