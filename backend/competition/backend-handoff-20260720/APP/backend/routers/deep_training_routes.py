from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from APP.backend.auth import get_current_user
from APP.backend.database import UserModel, get_db
from APP.backend.deep_training_service import (
    align_knowledge_points,
    compute_evaluation_metrics,
    create_intervention,
    cross_validate_output,
    diagnose_learning_state,
    generate_mistake_variant,
    select_practice_questions,
)

router = APIRouter(prefix="/deep-training", tags=["Deep Training"])
SAMPLE_FILE = Path(__file__).resolve().parents[1] / "sample_data" / "phase5_deep_training_seed.json"


class KnowledgeAlignRequest(BaseModel):
    text: str
    knowledge_points: list[dict[str, Any]] = Field(default_factory=list)


class QuestionSelectRequest(BaseModel):
    target_kp_ids: list[str] = Field(default_factory=list)
    mistakes: list[dict[str, Any]] = Field(default_factory=list)
    question_bank: list[dict[str, Any]] = Field(default_factory=list)
    limit: int = 5


class DiagnosisRequest(BaseModel):
    l0_baseline: dict[str, Any] = Field(default_factory=dict)
    l3_behavior: dict[str, Any] = Field(default_factory=dict)
    mistakes: list[dict[str, Any]] = Field(default_factory=list)


class CrossValidateRequest(BaseModel):
    generated: dict[str, Any]
    evidence: dict[str, Any]


@router.post("/knowledge/align")
def align_knowledge(req: KnowledgeAlignRequest, current_user: UserModel = Depends(get_current_user)):
    return align_knowledge_points(text=req.text, knowledge_points=req.knowledge_points)


@router.post("/questions/select")
def select_questions(req: QuestionSelectRequest, current_user: UserModel = Depends(get_current_user), db=Depends(get_db)):
    result = select_practice_questions(
        target_kp_ids=req.target_kp_ids,
        mistakes=req.mistakes,
        question_bank=req.question_bank,
        limit=req.limit,
        db=db,
        user_id=current_user.id,
        session_id=None,
    )
    if req.mistakes and result["questions"]:
        result["mistake_variant_preview"] = generate_mistake_variant(req.mistakes[0], result["questions"][0])
    return result


@router.post("/diagnosis")
def diagnose(req: DiagnosisRequest, current_user: UserModel = Depends(get_current_user)):
    diagnosis = diagnose_learning_state(
        l0_baseline=req.l0_baseline,
        l3_behavior=req.l3_behavior,
        mistakes=req.mistakes,
    )
    return {**diagnosis, "intervention": create_intervention(diagnosis)}


@router.post("/cross-validate")
def cross_validate(req: CrossValidateRequest, current_user: UserModel = Depends(get_current_user)):
    review = cross_validate_output(generated=req.generated, evidence=req.evidence)
    return {"review": review, "metrics": compute_evaluation_metrics([review])}


@router.get("/demo")
def get_deep_training_demo(current_user: UserModel = Depends(get_current_user)):
    seed = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    first_task = seed["tasks"][0]
    alignment = align_knowledge_points(text=first_task["text"], knowledge_points=seed["knowledge_points"])
    paper = select_practice_questions(
        target_kp_ids=alignment["resolved_kp_ids"],
        mistakes=seed["mistakes"],
        question_bank=seed["question_bank"],
        limit=2,
    )
    diagnosis = diagnose_learning_state(
        l0_baseline=seed["analytics"]["l0_baseline"],
        l3_behavior=seed["analytics"]["l3_behavior"],
        mistakes=seed["mistakes"],
    )
    return {
        "knowledge_alignment": alignment,
        "practice_selection": paper,
        "diagnosis": diagnosis,
        "intervention": create_intervention(diagnosis),
    }
