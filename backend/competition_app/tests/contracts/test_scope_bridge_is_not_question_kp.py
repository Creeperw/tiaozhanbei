"""蓝图单元范围桥接不是题目知识点。

组卷在填空缺口上补题时，会把整个蓝图单元的宽召回范围挂成
``match_method="resolved_blueprint_unit"`` 的桥接。那是检索范围，一旦被当成
题目知识点写进卷面快照，学习者的掌握度与复习排期里就会出现几百个没学过的
知识点。
"""

from __future__ import annotations

from competition_app.contracts.knowledge import (
    QuestionBridge,
    QuestionDetail,
    QuestionRetrievalMetadata,
    SCOPE_BRIDGE_MATCH_METHOD,
    question_kp_ids,
    to_learner_view,
)
from competition_app.contracts.paper import ExamPaperDraft, ExamPaperItem


def _generated_question(bridges: list[QuestionBridge]) -> QuestionDetail:
    return QuestionDetail(
        question_id="GENERATED_1",
        question_type="单项选择题",
        stem="合成题题干",
        reference_answer="A",
        analysis="",
        tags=[],
        source_metadata={},
        bridges=bridges,
        retrieval=QuestionRetrievalMetadata(
            channels=[], channel_scores={}, fusion_score=0.0
        ),
    )


def _bridge(kp_id: str, *, rank: int, match_method: str) -> QuestionBridge:
    return QuestionBridge(
        kp_id=kp_id,
        bridge_layer="llm",
        relation=(
            "blueprint_unit_scope" if match_method == SCOPE_BRIDGE_MATCH_METHOD else "primary"
        ),
        confidence=0.8,
        rank=rank,
        evidence_chunk_uid=f"chunk:{kp_id}",
        match_method=match_method,
    )


def test_scope_bridge_is_not_a_question_knowledge_point() -> None:
    question = _generated_question([
        _bridge("004341", rank=1, match_method=SCOPE_BRIDGE_MATCH_METHOD),
        _bridge("005692", rank=2, match_method=SCOPE_BRIDGE_MATCH_METHOD),
        _bridge("062769", rank=3, match_method=SCOPE_BRIDGE_MATCH_METHOD),
    ])

    assert question_kp_ids(question) == []
    assert to_learner_view(question).kp_ids == []


def test_real_bridges_survive_while_scope_bridges_are_dropped() -> None:
    question = _generated_question([
        _bridge("004307", rank=1, match_method="strict"),
        _bridge("004308", rank=2, match_method="llm"),
        _bridge("005692", rank=3, match_method=SCOPE_BRIDGE_MATCH_METHOD),
    ])

    assert question_kp_ids(question) == ["004307", "004308"]
    assert to_learner_view(question).kp_ids == ["004307", "004308"]


def test_paper_learner_questions_use_the_same_rule() -> None:
    paper = ExamPaperDraft(
        paper_draft_id="DRAFT_1",
        blueprint_id="BLUEPRINT_1",
        candidate_pool_id="POOL_1",
        title="测试卷",
        instructions="按题目要求作答。",
        items=[
            ExamPaperItem(
                sequence=1,
                unit_id="UNIT_01",
                score=50.0,
                selection_rationale="覆盖阴阳学说",
                question=_generated_question([
                    _bridge("004307", rank=1, match_method="strict"),
                    _bridge("005692", rank=2, match_method=SCOPE_BRIDGE_MATCH_METHOD),
                ]),
            ),
            ExamPaperItem(
                sequence=2,
                unit_id="UNIT_01",
                score=50.0,
                selection_rationale="补足题量",
                question=_generated_question([
                    _bridge("004341", rank=1, match_method=SCOPE_BRIDGE_MATCH_METHOD),
                ]),
            ),
        ],
        answer_key={"GENERATED_1": "A"},
        explanations={"GENERATED_1": None},
    )

    views = paper.learner_questions()

    assert views[0].kp_ids == ["004307"]
    assert views[1].kp_ids == []
