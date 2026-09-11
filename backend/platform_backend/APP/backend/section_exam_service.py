"""Grade and persist electronic-textbook section practice answers.

The section worksheet is the only practice surface whose questions come from
``question_bank_items``; the training workshop serves generated papers and
private questions instead, so it needs its own write path.

Grading lives here rather than in the browser because the verdict the learner
sees, the verdict stored for the study report, and the mistake-book entry have
to come from one implementation.  When the browser owned this logic, two
independent format assumptions broke it:

* ``question_bank_items.answer`` held ``["√"]`` for 24 of 37 rows, so a learner
  who chose 正确 compared ``正确`` against ``["√"]`` and was always told the
  answer was wrong;
* the reference ``整体观念、辨证论治`` and the learner answer
  ``整体观念和辨证论治`` are the same answer written with a different
  separator, but only exact string equality was accepted.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from APP.backend.database import (
    LearningActivityRecord,
    LearningAttemptRecord,
    LearningQuestion,
    MistakeRecord,
    QuestionAttempt,
    QuestionBankItem,
    utc_now,
)
from APP.backend.system_data_service import rebuild_system_data

# Question types are a closed set owned by the atlas importer
# (``daily_task_progress_service._QUESTION_TYPES``) plus the English names the
# importer writes into the database.
_QUESTION_KINDS = {
    "single_choice": "single_choice",
    "单项选择题": "single_choice",
    "单选题": "single_choice",
    "单项选择": "single_choice",
    "multiple_choice": "multiple_choice",
    "多项选择题": "multiple_choice",
    "多选题": "multiple_choice",
    "多项选择": "multiple_choice",
    "true_false": "true_false",
    "判断题": "true_false",
    "fill_blank": "fill_blank",
    "填空题": "fill_blank",
}

# Subjective answers are prose.  The platform never grades those automatically
# (``/training/practice/grade`` routes them through the LLM grader and an audit
# step), so the worksheet only displays the reference answer for them and
# records the attempt without a score.
AUTO_GRADED_KINDS = frozenset(
    {"single_choice", "multiple_choice", "true_false", "fill_blank"}
)

_FULLWIDTH = str.maketrans(
    {
        **{chr(0xFF21 + offset): chr(0x41 + offset) for offset in range(26)},
        **{chr(0xFF41 + offset): chr(0x61 + offset) for offset in range(26)},
        **{chr(0xFF10 + offset): chr(0x30 + offset) for offset in range(10)},
        "（": "(",
        "）": ")",
        "：": ":",
        "；": ";",
        "，": ",",
    }
)

_SEPARATOR_CLASS = r"\s,;:/|、。，；："
# 「和」「与」「及」 join enumerated items, but they are also ordinary characters
# inside a term (「和法」 is one of the eight treatment methods).  Treat them as
# separators only when they actually sit between two items, so 「和法」 survives
# intact while 「整体观念和辨证论治」 matches 「整体观念、辨证论治」.
_JOINER_RE = re.compile(
    rf"(?<=[^{_SEPARATOR_CLASS}])[和与及跟同](?=[^{_SEPARATOR_CLASS}])"
)
_SEPARATOR_RE = re.compile(rf"[{_SEPARATOR_CLASS}]+")
_OPTION_LETTER_RE = re.compile(r"(?<![A-Za-z])([A-Za-z])(?![A-Za-z])")
# Learners commonly prefix a written answer with 「答：」.
_ANSWER_PREFIX_RE = re.compile(r"^\s*答\s*[:：]?\s*")

_TRUE_TOKENS = frozenset(
    {"√", "✓", "✔", "☑", "⊤", "⊨", "正确", "对", "是", "t", "true", "yes", "y", "right", "correct", "a"}
)
_FALSE_TOKENS = frozenset(
    {"×", "✗", "✘", "☒", "⊥", "⊭", "错误", "错", "否", "f", "false", "no", "n", "wrong", "incorrect", "b"}
)


class SectionExamError(Exception):
    """Raised when a section-exam submission cannot be processed."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def question_kind(question_type: Any) -> str:
    return _QUESTION_KINDS.get(str(question_type or "").strip(), "short_answer")


def reference_answer_values(raw: Any) -> list[str]:
    """Return the reference answer as plain text fragments.

    ``question_bank_items.answer`` is a plain-text column, but rows imported
    before the atlas bundle fix stored the JSON array that
    ``learning_questions.answer_json`` needs, so ``["√"]`` reached the UI.
    Reading both shapes keeps already-stored rows working.
    """
    if isinstance(raw, (list, tuple)):
        values = [str(value).strip() for value in raw]
    else:
        text = str(raw or "").strip()
        values = []
        if text.startswith("[") and text.endswith("]"):
            try:
                decoded = json.loads(text)
            except (TypeError, ValueError):
                decoded = None
            if isinstance(decoded, list):
                values = [str(value).strip() for value in decoded]
        if not values:
            values = [text]
    return [value for value in values if value]


def format_reference_answer(raw: Any) -> str:
    """Render a reference answer for display, never as a raw JSON array."""
    values = reference_answer_values(raw)
    return "；".join(values) if values else ""


def _normalize_text(value: Any) -> str:
    text = str(value or "").translate(_FULLWIDTH).lower()
    text = _JOINER_RE.sub("、", text)
    return _SEPARATOR_RE.sub("", text)


def _reference_has_items(value: Any) -> bool:
    text = str(value or "").translate(_FULLWIDTH)
    return bool(_SEPARATOR_RE.search(_JOINER_RE.sub("、", text)))


def _option_letters(value: Any) -> set[str]:
    return {
        letter.upper()
        for letter in _OPTION_LETTER_RE.findall(str(value or "").translate(_FULLWIDTH))
    }


def _boolean_value(value: Any) -> bool | None:
    token = str(value or "").strip().translate(_FULLWIDTH).lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return None


def _text_match(submitted: str, reference: str) -> bool:
    actual = _normalize_text(_ANSWER_PREFIX_RE.sub("", submitted))
    expected = _normalize_text(reference)
    if not actual or not expected:
        return False
    if actual == expected:
        return True
    # A multi-part reference (「整体观念、辨证论治」) states several required
    # items, so a fragment is not an answer.  A single-item reference may still
    # be restated with extra wording.
    if _reference_has_items(reference):
        return False
    shorter, longer = sorted((actual, expected), key=len)
    # One-character answers are too weak to accept as substrings: 「√」 and 「A」
    # are complete answers of their own kind, not fragments of prose.
    return len(shorter) >= 2 and shorter in longer


def reference_option_keys(question_type: Any, reference: Any) -> list[str]:
    """Return the option keys the worksheet should highlight as correct.

    The browser needs to mark the right options once an answer is revealed, and
    deriving that from the raw reference answer is the same format guessing that
    broke grading.  The server resolves it once instead.
    """
    kind = question_kind(question_type)
    values = reference_answer_values(reference)
    if not values:
        return []
    if kind == "true_false":
        expected = _boolean_value(values[0])
        if expected is None:
            return []
        return ["正确"] if expected else ["错误"]
    if kind in {"single_choice", "multiple_choice"}:
        return sorted(_option_letters(" ".join(values)))
    return []


def judge_answer(question_type: Any, submitted: Any, reference: Any) -> bool | None:
    """Return the verdict, or ``None`` when the question is not auto-graded."""
    kind = question_kind(question_type)
    if kind not in AUTO_GRADED_KINDS:
        return None
    values = reference_answer_values(reference)
    answer = str(submitted or "").strip()
    if not answer or not values:
        return False

    if kind == "true_false":
        expected = _boolean_value(values[0])
        actual = _boolean_value(answer)
        if expected is not None and actual is not None:
            return expected == actual

    if kind in {"single_choice", "multiple_choice"}:
        expected_letters = _option_letters(" ".join(values))
        actual_letters = _option_letters(answer)
        if expected_letters and actual_letters:
            return expected_letters == actual_letters

    return any(_text_match(answer, value) for value in values)


def _load_question(db: Session, question_id: str) -> tuple[QuestionBankItem, LearningQuestion | None]:
    item = (
        db.query(QuestionBankItem)
        .filter_by(question_id=question_id, status="active")
        .one_or_none()
    )
    if item is None:
        raise SectionExamError("题目不存在或已下架", 404)
    detail = db.query(LearningQuestion).filter_by(question_id=question_id).one_or_none()
    return item, detail


def _analysis_text(item: QuestionBankItem, detail: LearningQuestion | None) -> str:
    for candidate in (
        getattr(detail, "explanation", None),
        item.analysis,
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _already_recorded(db: Session, learner_id: int, request_id: str) -> bool:
    if not request_id:
        return False
    return (
        db.query(LearningAttemptRecord.id)
        .filter_by(learner_id=learner_id, request_id=request_id)
        .first()
        is not None
    )


def submit_section_exam_answer(
    db: Session,
    learner_id: int,
    *,
    question_id: str,
    submitted_answer: str,
    request_id: str = "",
    section_id: str = "",
    section_name: str = "",
    book: str = "",
    chapter_id: str = "",
    chapter_name: str = "",
) -> dict[str, Any]:
    """Grade one worksheet answer and write it into the learner's history."""

    item, detail = _load_question(db, question_id)
    reference_text = format_reference_answer(item.answer)
    reference_options = reference_option_keys(item.question_type, item.answer)
    analysis = _analysis_text(item, detail)
    verdict = judge_answer(item.question_type, submitted_answer, item.answer)
    score = None if verdict is None else (100.0 if verdict else 0.0)

    try:
        kp_ids = [str(value) for value in json.loads(item.kp_ids_json or "[]")]
    except (TypeError, ValueError):
        kp_ids = []

    replay = _already_recorded(db, learner_id, request_id)
    if replay:
        # Re-revealing an unchanged answer must not inflate the attempt count or
        # the mistake book.  Grading is deterministic, so replaying it returns
        # exactly the verdict that was stored the first time.
        return {
            "question_id": question_id,
            "question_type": item.question_type,
            "is_correct": verdict,
            "score": score,
            "reference_answer": reference_text,
            "reference_options": reference_options,
            "analysis": analysis,
            "kp_ids": kp_ids,
            "attempt_id": "",
            "mistake_id": None,
            "recorded": False,
        }

    now = utc_now()
    attempt_id = str(uuid.uuid4())
    db.add(
        LearningAttemptRecord(
            attempt_id=attempt_id,
            learner_id=learner_id,
            attempt_type="textbook_section_exam",
            request_id=request_id,
            status="completed",
            submitted_at=now,
            source_kind="textbook_section_exam",
        )
    )
    db.add(
        QuestionAttempt(
            user_id=learner_id,
            question_id=question_id,
            answer=str(submitted_answer or ""),
            is_correct=bool(verdict),
            score=score,
            kp_ids_json=json.dumps(kp_ids, ensure_ascii=False),
            feedback=analysis,
            created_at=now,
        )
    )

    mistake_id = None
    if verdict is False:
        mistake = (
            db.query(MistakeRecord)
            .filter_by(user_id=learner_id, question_id=question_id, status="active")
            .one_or_none()
        )
        if mistake is None:
            mistake = MistakeRecord(
                user_id=learner_id, question_id=question_id, status="active"
            )
            db.add(mistake)
        mistake.kp_ids_json = json.dumps(kp_ids, ensure_ascii=False)
        mistake.error_type = "电子教材小节练习错因"
        mistake.summary = analysis or "该题在电子教材小节练习中作答错误。"
        db.flush()
        mistake_id = int(mistake.id)

    # ``learning_activity_records`` is what the study report reads for the
    # 「近30天作答」 count, so the row has to carry the same timestamp as the
    # attempt it describes.
    db.add(
        LearningActivityRecord(
            user_id=learner_id,
            activity_type="question_attempt",
            resource_id=question_id,
            resource_type="textbook_section_question",
            completion_status="completed",
            score=score,
            payload_json=json.dumps(
                {
                    "request_id": request_id,
                    "is_correct": verdict,
                    "question_type": item.question_type,
                    "practice_origin": "textbook_section_exam",
                    "section_id": section_id,
                    "section_name": section_name,
                    "book": book,
                    "chapter_id": chapter_id,
                    "chapter_name": chapter_name,
                },
                ensure_ascii=False,
            ),
            created_at=now,
        )
    )

    rebuild_system_data(db, user_id=learner_id)
    db.commit()

    return {
        "question_id": question_id,
        "question_type": item.question_type,
        "is_correct": verdict,
        "score": score,
        "reference_answer": reference_text,
        "reference_options": reference_options,
        "analysis": analysis,
        "kp_ids": kp_ids,
        "attempt_id": attempt_id,
        "mistake_id": mistake_id,
        "recorded": True,
    }
