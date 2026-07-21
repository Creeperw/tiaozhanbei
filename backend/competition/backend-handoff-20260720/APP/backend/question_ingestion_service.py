from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from APP.backend.database import QuestionBankItem


class QuestionPipeline(Protocol):
    def ingest(self, raw: dict[str, Any]) -> dict[str, Any]: ...


def question_pipeline_settings() -> dict[str, Any]:
    backend_root = Path(__file__).resolve().parent
    return {
        "embedding_provider": "openai_compatible",
        "runtime_dir": str(backend_root / "knowledge_runtime" / "question_pipeline"),
        "kp_file": "",
        "base_questions": "",
        "dedup_threshold": 0.92,
        "bridge_threshold": 0.35,
        "expert_min_confidence": 0.75,
        "quality_pass_threshold": 0.80,
        "question_vdb_dir": str(backend_root / "vdb_store" / "indexes" / "题库"),
        "audit_provider": "remote",
        "expert_provider": "remote",
        "judge_provider": "remote",
        "revision_provider": "remote",
        "llm_base_url": "https://api.deepseek.com",
        "llm_model": "deepseek-chat",
        "llm_key_env": "DEEPSEEK_API_KEY",
        "judge_base_url": "https://api.deepseek.com",
        "judge_model": "deepseek-chat",
        "judge_key_env": "DEEPSEEK_API_KEY",
        "embedding_base_url": "https://api.siliconflow.cn/v1",
        "embedding_model": "Qwen/Qwen3-Embedding-4B",
        "embedding_key_env": "SILICONFLOW_API_KEY",
        "embedding_model_path": "",
        "embedding_device": "cpu",
        "disable_diagnostic_jsonl": True,
    }


class BuiltinQuestionPipeline:
    def ingest(self, raw: dict[str, Any]) -> dict[str, Any]:
        stem = str(raw.get("stem") or "").strip()
        answer = str(raw.get("answer") or "").strip()
        kp_ids = [
            str(value).strip()
            for value in raw.get("requested_kp_ids", [])
            if str(value).strip()
        ]
        result = {
            "question_id": None,
            "audit": {"quality_score": 0.0},
            "question": {
                "stem": stem,
                "answer": answer,
                "analysis": str(raw.get("analysis") or "").strip(),
            },
            "kp_matches": [{"kp_id": value} for value in kp_ids],
        }
        if not stem or not answer:
            return {**result, "status": "needs_human_review"}
        return {**result, "status": "needs_human_review"}


def build_question_pipeline() -> QuestionPipeline:
    return BuiltinQuestionPipeline()


class QuestionIngestionService:
    def __init__(self, pipeline_factory: Callable[[], QuestionPipeline] = build_question_pipeline):
        self._pipeline_factory = pipeline_factory

    def ingest(self, db: Session, payload: dict[str, Any]) -> dict[str, Any]:
        stem = str(payload["stem"]).strip()
        existing = db.query(QuestionBankItem).filter(QuestionBankItem.stem == stem).first()
        if existing is not None:
            return {"status": "duplicate", "question_id": existing.question_id, "stored": False}
        result = self._pipeline_factory().ingest(payload)
        if result.get("status") != "active":
            return {**result, "stored": False}

        question_id = str(result.get("question_id") or "").strip()
        if not question_id:
            raise ValueError("Active ingestion result requires question_id")
        if db.query(QuestionBankItem).filter(QuestionBankItem.question_id == question_id).first():
            return {**result, "status": "duplicate", "stored": False}

        kp_ids = [
            str(match.get("kp_id")).strip()
            for match in result.get("kp_matches", [])
            if str(match.get("kp_id") or "").strip()
        ]
        audit = result.get("audit") or {}
        reviewed_question = result.get("question") or {}
        item = QuestionBankItem(
            question_id=question_id,
            stem=str(reviewed_question.get("stem") or payload["stem"]).strip(),
            answer=str(reviewed_question.get("answer") or payload.get("answer") or "").strip(),
            analysis=str(reviewed_question.get("analysis") or payload.get("analysis") or "").strip(),
            kp_ids_json=json.dumps(kp_ids, ensure_ascii=False),
            question_type=str(payload.get("question_type") or "short_answer"),
            difficulty=float(payload.get("difficulty") or 2.0),
            quality_score=float(audit.get("quality_score") or 0.7),
            source=str(payload.get("source_type") or "user_upload"),
            status="active",
        )
        try:
            with db.begin_nested():
                db.add(item)
                db.flush()
        except IntegrityError:
            return {**result, "status": "duplicate", "stored": False}
        return {**result, "stored": True}
