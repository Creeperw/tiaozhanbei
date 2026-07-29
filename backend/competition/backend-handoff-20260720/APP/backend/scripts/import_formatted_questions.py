"""Import formatted_questions.json into QuestionBankItem and LearningQuestion tables."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure the handoff package is importable
_HANDOFF_ROOT = Path(__file__).resolve().parents[2]
if str(_HANDOFF_ROOT) not in sys.path:
    sys.path.insert(0, str(_HANDOFF_ROOT))

from APP.backend.database import KnowledgePoint, LearningQuestion, QuestionBankItem, SessionLocal

_TYPE_MAP = {
    "单项选择题": "single_choice",
    "多项选择题": "multiple_choice",
    "多选题": "multiple_choice",
    "单选题": "single_choice",
    "判断题": "true_false",
    "填空题": "fill_blank",
    "简答题": "short_answer",
    "案例题": "case_quiz",
    "案例分析题": "case_quiz",
}

BATCH_SIZE = 500


def normalize_type(raw: str) -> str:
    return _TYPE_MAP.get(raw.strip(), "single_choice")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import formatted questions into the database")
    parser.add_argument("--questions", type=Path, required=True, help="Path to formatted_questions.json")
    parser.add_argument("--knowledge-points", type=Path, help="Path to final_knowledge_points.json (optional)")
    parser.add_argument("--limit", type=int, default=0, help="Max questions to import (0 = all)")
    args = parser.parse_args()

    with open(args.questions, "r", encoding="utf-8") as f:
        raw_questions = json.load(f)

    questions = raw_questions if args.limit <= 0 else raw_questions[: args.limit]
    total = len(questions)
    print(f"Loaded {total} questions from {args.questions}")

    db = SessionLocal()
    bank_count = 0
    learn_count = 0
    kp_count = 0
    errors = 0

    try:
        # Import knowledge points first if provided
        if args.knowledge_points and args.knowledge_points.is_file():
            with open(args.knowledge_points, "r", encoding="utf-8") as f:
                kp_data = json.load(f)
            kp_items = kp_data if isinstance(kp_data, list) else kp_data.get("items", kp_data.get("knowledge_points", []))
            existing_kps = {r.kp_id for r in db.query(KnowledgePoint.kp_id).all()}
            for item in kp_items:
                kp_id = str(item.get("kp_id") or item.get("id") or "").strip()
                if not kp_id or kp_id in existing_kps:
                    continue
                db.add(KnowledgePoint(
                    kp_id=kp_id,
                    name=str(item.get("name") or item.get("title") or kp_id)[:200],
                    status="active",
                ))
                existing_kps.add(kp_id)
                kp_count += 1
            db.flush()
            print(f"Imported {kp_count} knowledge points")

        # Batch import questions
        existing_bank = {r.question_id for r in db.query(QuestionBankItem.question_id).all()}
        existing_learn = {r.question_id for r in db.query(LearningQuestion.question_id).all()}

        for i, q in enumerate(questions):
            if i % 1000 == 0:
                print(f"Processing {i}/{total}...")

            qid = str(q.get("question_id", "")).strip()
            if not qid:
                errors += 1
                continue
            if len(qid) > 120:
                qid = qid[:120]

            qtype = normalize_type(str(q.get("question_type", "")))
            stem = str(q.get("question_content") or q.get("stem") or "")
            answer = q.get("answer", [])
            if isinstance(answer, list):
                answer_str = ", ".join(str(a) for a in answer)
            else:
                answer_str = str(answer)
            explanation = str(q.get("explanation") or "")
            options = q.get("options", [])
            if not isinstance(options, list):
                options = []
            options_json = json.dumps(options, ensure_ascii=False)
            kp_ids = q.get("kp_ids", [])
            if not isinstance(kp_ids, list):
                kp_ids = []
            kp_ids_json = json.dumps([str(k) for k in kp_ids], ensure_ascii=False)

            difficulty_raw = q.get("difficulty", "")
            try:
                difficulty = float(difficulty_raw) if difficulty_raw else None
            except (ValueError, TypeError):
                difficulty = None

            now = datetime.now(timezone.utc)

            # Insert into QuestionBankItem
            if qid not in existing_bank:
                db.add(QuestionBankItem(
                    question_id=qid,
                    stem=stem,
                    answer=answer_str,
                    analysis=explanation,
                    kp_ids_json=kp_ids_json,
                    question_type=qtype,
                    difficulty=difficulty,
                    quality_score=0.7,
                    source="formatted_import",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ))
                existing_bank.add(qid)
                bank_count += 1

            # Insert into LearningQuestion
            if qid not in existing_learn:
                db.add(LearningQuestion(
                    question_id=qid,
                    question_type=qtype,
                    question_content=stem,
                    options_json=options_json,
                    answer_json=json.dumps(answer if isinstance(answer, list) else [answer], ensure_ascii=False),
                    explanation=explanation,
                    difficulty=difficulty,
                    kp_ids_json=kp_ids_json,
                    tokenized_content_json=json.dumps(q.get("tokenized_content", []), ensure_ascii=False) if q.get("tokenized_content") else "[]",
                    scoring_rubric=str(q.get("scoring_rubric", "") or ""),
                    key_points=str(q.get("key_points", "") or ""),
                ))
                existing_learn.add(qid)
                learn_count += 1

            if (bank_count + learn_count) % BATCH_SIZE == 0:
                db.flush()

        db.commit()
        print(f"\nDone! Bank: {bank_count}, Learning: {learn_count}, KP: {kp_count}, Errors: {errors}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
