import pytest

from competition_app.agents.expert import ExpertAgent
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import (
    EvidenceItem,
    EvidencePack,
    QuestionBridge,
    QuestionDetail,
    QuestionRetrievalMetadata,
)


class CandidateModel:
    def __init__(self, use: bool, selected: list[str]) -> None:
        self.use = use
        self.selected = selected

    async def complete_json(self, role, payload, on_delta=None):
        return {
            "learning_tip": "完成主动回忆。",
            "use_question_candidates": self.use,
            "usage_reason": "当前资源需要练习题。" if self.use else "当前只需知识讲解。",
            "selected_question_ids": self.selected,
            "resource_type": "practice" if self.use else "none",
        }


class ReviewCardLabelModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "learning_tip": "完成主动回忆并对照教材自检。",
            "use_question_candidates": False,
            "usage_reason": "当前资源不使用题目候选。",
            "resource_type": "review_card",
        }


class LiveReviewCardModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "learning_tip": "完成首次主动回忆。",
            "use_question_candidates": True,
            "usage_reason": "题目作为可选延伸。",
            "selected_question_ids": ["generated_填空题__unknown"],
            "resource_type": "review_card",
        }


class AliasReviewCardModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "learning_tip": "完成首次主动回忆。",
            "use_question_candidates": True,
            "usage_reason": "题目作为可选延伸。",
            "selected_question_ids": ["generated_unknown"],
            "resource_type": "knowledge_card",
        }


class ExplanationWithQuestionModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "learning_tip": "【知识结论】四君子汤由人参、白术、茯苓、甘草组成，功用为益气健脾。\n【辨析要点】人参补气，白术健脾燥湿。\n【自测】说明四味药的配伍关系。",
            "use_question_candidates": True,
            "usage_reason": "用于知识解释后的练习。",
            "selected_question_ids": ["generated_invalid"],
            "resource_type": "knowledge_card",
        }


class ExplanationWithoutQuestionModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "learning_tip": "请完成主动回忆。",
            "use_question_candidates": False,
            "usage_reason": "本次以回忆为主。",
            "selected_question_ids": [],
            "resource_type": "none",
        }


class FixedKnowledgeCardModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "exp": "理中丸用于温中祛寒、补气健脾，核心是围绕脾胃虚寒理解其组成与功用。",
            "use_question_candidates": False,
            "usage_reason": "知识卡片由 exp 直接渲染。",
            "selected_question_ids": [],
            "resource_type": "none",
        }


class OverlongExplanationModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "exp": "理中丸的教学解释。" + ("补充内容。" * 400),
            "learning_tip": "请先主动回忆，再对照知识卡片自查。",
        }

class ContradictoryQuestionConsumptionModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "body": "四君子汤由人参、白术、茯苓和炙甘草组成。",
            "use_question_candidates": True,
            "selected_question_ids": ["Q_1"],
            "resource_type": "none",
        }


def context_with_candidates() -> dict:
    question = QuestionDetail(
        question_id="Q_1", question_type="单项选择题", stem="四君子汤功效是？",
        reference_answer="益气健脾", analysis="内部解析", tags=["方剂学"], source_metadata={},
        bridges=[QuestionBridge(kp_id="KP_1", bridge_layer="strict", relation="primary", confidence=0.9, rank=1, evidence_chunk_uid="C_1", match_method="strict")],
        retrieval=QuestionRetrievalMetadata(channels=["bridge"], channel_scores={"bridge": 1.0}, fusion_score=1.0),
    )
    knowledge = EvidencePack(
        evidence_pack_id="EP_1", query="四君子汤", resolved_kp_ids=["KP_1"],
        evidence_items=[EvidenceItem(evidence_id="E_1", source_id="C_1", content_summary="教材证据", authority_level="textbook", confidence=0.9)],
    )
    knowledge._question_details = [question]
    return {
        "case_id": "CASE_1", "trace_id": "TRACE_1", "request_id": "REQ_1", "execution_id": "EXE_1",
        "step_id": "expert", "learner_id": "L1", "topic": "四君子汤", "available_minutes": 10,
        "dependency_outputs": {
            "knowledge": AgentEnvelope(
                artifact_id="A_1", artifact_type="evidence_pack", case_id="CASE_1", trace_id="TRACE_1",
                request_id="REQ_1", execution_id="EXE_1", step_id="knowledge", producer="knowledge_base_agent",
                task_type="personalized_review_card", learner_id="L1", payload=knowledge,
            )
        },
    }


@pytest.mark.asyncio
async def test_expert_can_skip_available_question_candidates() -> None:
    draft = (await ExpertAgent(CandidateModel(False, [])).run(context_with_candidates())).payload

    assert draft.question_consumption.use_question_candidates is True
    assert draft.content["练习资源"]
    assert "核心证据" not in draft.content
    assert "知识卡片" in draft.content


@pytest.mark.asyncio
async def test_expert_normalizes_display_resource_type_when_candidates_are_unused() -> None:
    draft = (await ExpertAgent(ReviewCardLabelModel()).run(context_with_candidates())).payload

    assert draft.question_consumption.use_question_candidates is True
    assert draft.question_consumption.resource_type == "practice"


@pytest.mark.asyncio
async def test_expert_normalizes_live_review_card_label_to_safe_resource_contract() -> None:
    draft = (await ExpertAgent(LiveReviewCardModel()).run(context_with_candidates())).payload

    assert draft.question_consumption.use_question_candidates is True
    assert draft.question_consumption.selected_question_ids == ["Q_1"]
    assert draft.question_consumption.resource_type == "practice"


@pytest.mark.asyncio
async def test_expert_normalizes_resource_aliases_before_schema_validation() -> None:
    draft = (await ExpertAgent(AliasReviewCardModel()).run(context_with_candidates())).payload

    assert draft.question_consumption.use_question_candidates is True
    assert draft.question_consumption.selected_question_ids == ["Q_1"]
    assert draft.question_consumption.resource_type == "practice"


@pytest.mark.asyncio
async def test_expert_maps_invalid_live_question_ids_to_retrieved_candidates() -> None:
    draft = (await ExpertAgent(ExplanationWithQuestionModel()).run(context_with_candidates())).payload

    assert draft.question_consumption.use_question_candidates is True
    assert draft.question_consumption.resource_type == "practice"
    assert draft.question_consumption.selected_question_ids == ["Q_1"]
    assert draft.content["练习资源"][0]["question_id"] == "Q_1"
    assert "知识结论" in draft.content["知识卡片"]["exp"]


@pytest.mark.asyncio
async def test_expert_publishes_retrieved_practice_when_model_omits_questions() -> None:
    draft = (await ExpertAgent(ExplanationWithoutQuestionModel()).run(context_with_candidates())).payload

    assert draft.question_consumption.use_question_candidates is True
    assert draft.question_consumption.resource_type == "practice"
    assert draft.question_consumption.selected_question_ids == ["Q_1"]
    assert draft.content["练习资源"][0]["question_id"] == "Q_1"


@pytest.mark.asyncio
async def test_knowledge_card_uses_model_explanation_without_raw_evidence_copy() -> None:
    draft = (await ExpertAgent(ExplanationWithQuestionModel()).run(context_with_candidates())).payload

    card = draft.content["知识卡片"]
    assert "知识结论" in card["exp"]
    assert "教材证据" not in card["exp"]
    assert "核心证据" not in card["exp"]


def test_expert_filters_non_safe_review_question_types() -> None:
    assert ExpertAgent._is_review_question_type("单项选择题") is True
    assert ExpertAgent._is_review_question_type("多项选择题") is True
    assert ExpertAgent._is_review_question_type("判断题") is True
    assert ExpertAgent._is_review_question_type("临床案例问答") is False
    assert ExpertAgent._is_review_question_type("简答题") is False


@pytest.mark.asyncio
async def test_expert_builds_fixed_knowledge_card_envelope() -> None:
    context = context_with_candidates()
    context["dependency_outputs"]["schedule"] = None
    draft = (await ExpertAgent(FixedKnowledgeCardModel()).run(context)).payload

    card = draft.content["知识卡片"]
    assert card["kp_id"] == "KP_1"
    assert card["kp_name"] == "四君子汤"
    assert card["exp"].startswith("理中丸用于")


@pytest.mark.asyncio
async def test_expert_normalizes_overlong_natural_language_output() -> None:
    draft = (await ExpertAgent(OverlongExplanationModel()).run(context_with_candidates())).payload

    assert draft.content["知识卡片"]["exp"]
    assert len(draft.content["知识卡片"]["exp"]) <= 8_000


@pytest.mark.asyncio
async def test_knowledge_card_exp_is_separate_from_learning_tip() -> None:
    draft = (await ExpertAgent(FixedKnowledgeCardModel()).run(context_with_candidates())).payload

    assert draft.content["知识卡片"]["exp"] != draft.content["学习提示"]
    assert "主动回忆" in draft.content["学习提示"]
    assert "练习资源" in draft.content["学习提示"]


@pytest.mark.asyncio
async def test_expert_selected_candidate_becomes_learner_safe_view() -> None:
    draft = (await ExpertAgent(CandidateModel(True, ["Q_1"])).run(context_with_candidates())).payload

    question = draft.content["练习资源"][0]
    assert question["question_id"] == "Q_1"
    assert "reference_answer" not in question
    assert "analysis" not in question
    assert "核心证据" not in draft.content

@pytest.mark.asyncio
async def test_expert_system_normalizes_contradictory_question_consumption() -> None:
    draft = (
        await ExpertAgent(ContradictoryQuestionConsumptionModel()).run(
            context_with_candidates()
        )
    ).payload

    assert draft.question_consumption.use_question_candidates is True
    assert draft.question_consumption.resource_type == "practice"
    assert draft.question_consumption.selected_question_ids == ["Q_1"]


@pytest.mark.asyncio
async def test_expert_rejects_selected_question_outside_candidate_catalog() -> None:
    with pytest.raises(ValueError, match="selected question"):
        await ExpertAgent(CandidateModel(True, ["Q_NOT_FOUND"])).run(context_with_candidates())