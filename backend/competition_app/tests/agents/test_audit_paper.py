import pytest

from competition_app.agents.audit import AuditAgent
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import QuestionDetail, QuestionRetrievalMetadata
from competition_app.contracts.paper import (
    BlueprintUnit,
    ExamPaperDraft,
    ExamPaperItem,
    PaperBlueprint,
    QuestionCandidatePool,
    UnitQuestionCandidates,
)


class PassingAuditModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {"decision": "pass", "findings": []}


class RevisingAuditModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "decision": "revise",
            "findings": ["存在可进一步优化的知识覆盖表达。"],
        }


class InvalidAuditModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {"result": "试卷整体可用"}


def test_paper_audit_uses_the_same_subjective_question_aliases_as_assembly() -> None:
    assert AuditAgent._matches_question_type("临床案例问答", ["简答题"])
    assert AuditAgent._matches_question_type("病例分析_实践技能", ["病例分析题"])


def _question(question_id: str) -> QuestionDetail:
    return QuestionDetail(
        question_id=question_id,
        question_type="单项选择题",
        stem=f"题干{question_id}",
        reference_answer="A",
        analysis="解析",
        options=["A. 正确", "B. 错误"],
        tags=[],
        source_metadata={},
        bridges=[],
        retrieval=QuestionRetrievalMetadata(
            channels=["vector"], channel_scores={"vector": 1.0}, fusion_score=1.0
        ),
    )


def _envelope(step_id: str, artifact_type: str, payload):
    return AgentEnvelope(
        artifact_id=f"A_{step_id}", artifact_type=artifact_type, case_id="C1",
        trace_id="T1", request_id="R1", execution_id="E1", step_id=step_id,
        producer="test", task_type="paper_generation", learner_id="L1", payload=payload,
    )


def _audit_context(actual_count: int, required_count: int = 20) -> dict:
    questions = [_question(f"Q{index}") for index in range(1, actual_count + 1)]
    blueprint = PaperBlueprint(
        blueprint_id="BP1", title="测试卷", source_status="practice_sample",
        scope_summary="测试", required_total_question_count=required_count,
        question_count_is_hard_constraint=True,
        units=[
            BlueprintUnit(
                unit_id="U1", sequence=1, knowledge_module="测试",
                learning_objective="测试", retrieval_query="测试",
                question_type_preferences=["单项选择题"],
                required_question_count=required_count,
            )
        ],
    )
    pool = QuestionCandidatePool(
        pool_id="P1", blueprint_id="BP1",
        units=[
            UnitQuestionCandidates(
                unit_id="U1", retrieval_query="测试", resolved_kp_ids=[],
                requested_limit=max(1, actual_count), required_question_count=required_count,
                items=questions,
            )
        ],
    )
    items = [
        ExamPaperItem(
            sequence=index, unit_id="U1", question=question,
            selection_rationale="测试",
        )
        for index, question in enumerate(questions, start=1)
    ]
    paper = ExamPaperDraft(
        paper_draft_id="D1", blueprint_id="BP1", candidate_pool_id="P1",
        title="测试卷", instructions="请作答。", items=items,
        answer_key={question.question_id: "A" for question in questions},
        explanations={question.question_id: "解析" for question in questions},
    )
    return {
        "case_id": "C1", "trace_id": "T1", "request_id": "R1",
        "execution_id": "E1", "step_id": "audit", "learner_id": "L1",
        "task_type": "paper_generation",
        "dependency_outputs": {
            "paper_blueprint": _envelope("paper_blueprint", "paper_blueprint", blueprint),
            "question_pool": _envelope("question_pool", "question_candidate_pool", pool),
            "paper_assembly": _envelope("paper_assembly", "exam_paper_draft", paper),
        },
    }


@pytest.mark.asyncio
async def test_paper_audit_revises_when_hard_question_count_is_short() -> None:
    result = await AuditAgent(PassingAuditModel()).run(_audit_context(19))

    assert result.payload.decision == "revise"
    assert any("20题" in finding and "19题" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_paper_audit_passes_when_hard_question_count_is_met() -> None:
    result = await AuditAgent(PassingAuditModel()).run(_audit_context(20))

    assert result.payload.decision == "pass"


@pytest.mark.asyncio
async def test_paper_audit_accepts_generated_short_answer_without_options() -> None:
    context = _audit_context(1, required_count=2)
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.units.append(
        BlueprintUnit(
            unit_id="U2",
            sequence=2,
            knowledge_module="配伍意义",
            learning_objective="说明配伍逻辑",
            retrieval_query="四君子汤 配伍意义",
            question_type_preferences=["简答题"],
            required_question_count=1,
        )
    )
    generated = QuestionDetail(
        question_id="GENERATED_SHORT_ANSWER",
        question_type="简答题",
        stem="请简述四君子汤的配伍意义。",
        reference_answer="人参为君，白术为臣，茯苓为佐，甘草为使。",
        analysis="考查君臣佐使的配伍逻辑。",
        options=[],
        origin="generated",
        source_tier="model_knowledge",
        tags=[],
        source_metadata={},
        bridges=[],
        retrieval=QuestionRetrievalMetadata(
            channels=[], channel_scores={}, fusion_score=0.0
        ),
    )
    paper = context["dependency_outputs"]["paper_assembly"].payload
    paper.items.append(
        ExamPaperItem(
            sequence=2,
            unit_id="U2",
            question=generated,
            selection_rationale="补足简答题单元。",
        )
    )
    paper.answer_key[generated.question_id] = generated.reference_answer
    paper.explanations[generated.question_id] = generated.analysis

    result = await AuditAgent(PassingAuditModel()).run(context)

    assert result.payload.decision == "pass"
    assert not any("缺少选项" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_paper_audit_does_not_loop_on_soft_revision_after_one_repair() -> None:
    context = _audit_context(2, required_count=2)
    context["audit_feedback"] = _envelope(
        "audit_previous",
        "audit_result",
        {"decision": "revise", "findings": ["请优化知识覆盖表达。"]},
    )

    result = await AuditAgent(RevisingAuditModel()).run(context)

    assert result.payload.decision == "pass"
    assert any("非阻断建议" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_paper_audit_does_not_rebuild_valid_paper_for_model_only_advice() -> None:
    result = await AuditAgent(RevisingAuditModel()).run(
        _audit_context(2, required_count=2)
    )

    assert result.payload.decision == "pass"
    assert any("优化" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_valid_paper_uses_deterministic_gate_when_audit_format_drifts() -> None:
    result = await AuditAgent(InvalidAuditModel()).run(
        _audit_context(2, required_count=2)
    )

    assert result.payload.decision == "pass"
    assert any("格式" in finding for finding in result.payload.findings)
