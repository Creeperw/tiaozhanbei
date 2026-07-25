from pathlib import Path

import pytest

from competition_app.services.qualification_papers import QualificationPaperRepository


def _write_catalog(root: Path) -> None:
    (root / "papers").mkdir(parents=True)
    (root / "catalog.json").write_text(
        """{
  "schema_version": "1.0",
  "exams": [{"exam_id": "tcm-practitioner", "name": "中医执业医师资格考试"}],
  "papers": [{
    "template_id": "tcm-2024-a",
    "exam_id": "tcm-practitioner",
    "year": "2024",
    "paper_type": "真题",
    "title": "2024 年中医执业医师资格考试",
    "question_count": 2,
    "published": true,
    "data_file": "papers/tcm-2024-a.json"
  }]
}""",
        encoding="utf-8",
    )
    (root / "papers" / "tcm-2024-a.json").write_text(
        """{
  "template_id": "tcm-2024-a",
  "questions": [{
    "question_id": "q1", "question_type": "single_choice", "question_content": "题目一",
    "options": [{"option_id": "A", "content": "甲"}, {"option_id": "B", "content": "乙"}],
    "answer": ["A"], "explanation": "解析一", "kp_ids": [], "media": [], "source_ref": {}
  }, {
    "question_id": "q2", "question_type": "multiple_choice", "question_content": "题目二",
    "options": [{"option_id": "A", "content": "甲"}, {"option_id": "B", "content": "乙"}],
    "answer": ["A", "B"], "explanation": "解析二", "kp_ids": [], "media": [], "source_ref": {}
  }]
}""",
        encoding="utf-8",
    )


def test_catalog_only_exposes_published_templates(tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    repository = QualificationPaperRepository(tmp_path, runtime_root=tmp_path / "runtime")

    result = repository.list_catalog(exam_id="tcm-practitioner")

    assert result["exams"][0]["name"] == "中医执业医师资格考试"
    assert result["papers"] == [
        {
            "template_id": "tcm-2024-a",
            "exam_id": "tcm-practitioner",
            "year": "2024",
            "paper_type": "真题",
            "title": "2024 年中医执业医师资格考试",
            "question_count": 2,
        }
    ]


def test_test_attempt_hides_answers_until_submitted_and_scores_idempotently(tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    repository = QualificationPaperRepository(tmp_path, runtime_root=tmp_path / "runtime")
    attempt = repository.create_attempt(
        "learner-1", "tcm-2024-a", answer_mode="test", duration_minutes=60
    )

    active = repository.get_attempt("learner-1", attempt["attempt_id"])
    assert active["answer_mode"] == "test"
    assert "standard_answer" not in active["items"][0]
    assert "explanation" not in active["items"][0]

    repository.save_progress(
        "learner-1", attempt["attempt_id"], answers={"q1": "A", "q2": "A,B"},
        current_position=2, marked_positions=[1], paused=True,
    )
    submitted = repository.submit_attempt("learner-1", attempt["attempt_id"], "submit-1")
    repeated = repository.submit_attempt("learner-1", attempt["attempt_id"], "submit-1")

    assert submitted["status"] == "submitted"
    assert submitted["score"] == 2
    assert repeated == submitted
    assert submitted["items"][0]["standard_answer"] == ["A"]
    assert submitted["items"][0]["is_correct"] is True


def test_practice_attempt_exposes_requested_explanation_only(tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    repository = QualificationPaperRepository(tmp_path, runtime_root=tmp_path / "runtime")
    attempt = repository.create_attempt("learner-1", "tcm-2024-a", answer_mode="practice")

    explanation = repository.get_explanation("learner-1", attempt["attempt_id"], "q1")

    assert explanation == {"question_id": "q1", "answer": ["A"], "explanation": "解析一"}
    with pytest.raises(PermissionError):
        repository.get_explanation("learner-1", attempt["attempt_id"], "missing")


def test_unanswered_key_is_reported_as_pending_without_being_scored(tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    paper_path = tmp_path / "papers" / "tcm-2024-a.json"
    payload = __import__("json").loads(paper_path.read_text(encoding="utf-8"))
    payload["questions"][1]["answer"] = []
    paper_path.write_text(__import__("json").dumps(payload, ensure_ascii=False), encoding="utf-8")
    repository = QualificationPaperRepository(tmp_path, runtime_root=tmp_path / "runtime")
    attempt = repository.create_attempt("learner-1", "tcm-2024-a", answer_mode="test", duration_minutes=60)

    submitted = repository.submit_attempt("learner-1", attempt["attempt_id"], "submit-pending")

    assert submitted["score"] == 0
    assert submitted["max_score"] == 1
    assert submitted["items"][1]["answer_status"] == "pending"
    assert submitted["items"][1]["is_correct"] is None
