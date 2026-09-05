"""Read-only qualification-paper templates and per-user attempt state."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


class QualificationPaperRepository:
    _FORBIDDEN_TITLE = re.compile(r"答案|解析|详解|讲解")
    _FORBIDDEN_QUESTION_CONTENT = re.compile(r"(?:^|[\s【\[])答案[】\]]?\s*[:：]?|(?:^|[\s【\[])解析[】\]]?\s*[:：]?")

    def __init__(self, data_root: Path, *, runtime_root: Path) -> None:
        self.data_root = Path(data_root)
        self.runtime_root = Path(runtime_root)

    def list_catalog(
        self, *, exam_id: str = "", year: str = "", paper_type: str = ""
    ) -> dict:
        catalog = self._read_json(self.data_root / "catalog.json", {"exams": [], "papers": []})
        papers = []
        for paper in catalog.get("papers", []):
            if not isinstance(paper, dict) or not paper.get("published"):
                continue
            if self._FORBIDDEN_TITLE.search(str(paper.get("title") or "")):
                continue
            if int(paper.get("question_count") or 0) < 1:
                continue
            if exam_id and paper.get("exam_id") != exam_id:
                continue
            if year and paper.get("year") != year:
                continue
            if paper_type and paper.get("paper_type") != paper_type:
                continue
            summary = {
                key: paper.get(key, "")
                for key in ("template_id", "exam_id", "year", "paper_type", "title", "question_count")
            }
            if str(paper.get("availability") or "").strip():
                summary["availability"] = paper["availability"]
            papers.append(summary)
        visible_exam_ids = {str(paper.get("exam_id") or "") for paper in papers}
        exams = []
        for exam in catalog.get("exams", []):
            if not isinstance(exam, dict):
                continue
            exam_id_value = str(exam.get("exam_id") or "")
            exams.append({
                **exam,
                "available_paper_count": sum(1 for paper in papers if str(paper.get("exam_id") or "") == exam_id_value),
                "availability": "available" if exam_id_value in visible_exam_ids else "pending_structure_cleanup",
            })
        return {"schema_version": "1.0", "exams": exams, "papers": papers}

    def create_attempt(
        self,
        user_id: str,
        template_id: str,
        *,
        answer_mode: str,
        duration_minutes: int | None = None,
    ) -> dict:
        if answer_mode not in {"practice", "test"}:
            raise ValueError("answer_mode must be practice or test")
        if answer_mode == "test" and (not isinstance(duration_minutes, int) or not 10 <= duration_minutes <= 300):
            raise ValueError("测试模式时长必须在 10 至 300 分钟之间")
        template = self._template(template_id)
        attempt_id = f"qualification-{uuid4().hex}"
        state = {
            "attempt_id": attempt_id,
            "template_id": template_id,
            "source": "qualification_paper",
            "title": template["title"],
            "answer_mode": answer_mode,
            "duration_minutes": duration_minutes if answer_mode == "test" else None,
            "status": "not_started",
            "created_at": self._now(),
            "started_at": None,
            "submitted_at": None,
            "current_position": 1,
            "marked_positions": [],
            "answers": {},
            "submission_requests": {},
            "questions": template["questions"],
        }
        self._save_attempt(user_id, state)
        return self._serialize_attempt(state)

    def list_attempts(self, user_id: str, *, offset: int = 0, limit: int = 50) -> dict:
        user_dir = self.runtime_root / user_id
        if not user_dir.is_dir():
            return {"items": [], "total": 0, "offset": offset, "limit": limit}
        attempts = []
        for path in sorted(user_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.suffix == ".json":
                continue
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            attempts.append(self._serialize_attempt(state))
        total = len(attempts)
        return {
            "items": attempts[offset:offset + limit],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def get_attempt(self, user_id: str, attempt_id: str) -> dict:
        state = self._load_attempt(user_id, attempt_id)
        if state["status"] == "not_started":
            state["status"] = "in_progress"
            state["started_at"] = self._now()
            self._save_attempt(user_id, state)
        self._ensure_test_not_expired(state)
        return self._serialize_attempt(state)

    def _ensure_test_not_expired(self, state: dict) -> None:
        if state.get("answer_mode") != "test" or not state.get("started_at"):
            return
        started_at = datetime.fromisoformat(str(state["started_at"]).replace("Z", "+00:00"))
        deadline = started_at + timedelta(minutes=int(state.get("duration_minutes") or 0))
        now = datetime.fromisoformat(self._now().replace("Z", "+00:00"))
        if now > deadline:
            raise ValueError("测试时间已结束，请重新开始一份试卷")

    def save_progress(
        self,
        user_id: str,
        attempt_id: str,
        *,
        answers: dict[str, str],
        current_position: int,
        marked_positions: list[int],
        paused: bool = False,
    ) -> dict:
        state = self._load_attempt(user_id, attempt_id)
        if state["status"] == "submitted":
            raise ValueError("试卷已提交")
        self._ensure_test_not_expired(state)
        valid_ids = {str(item["question_id"]) for item in state["questions"]}
        state["answers"] = {
            str(key): str(value).strip() for key, value in answers.items() if str(key) in valid_ids
        }
        state["current_position"] = max(1, min(int(current_position), len(state["questions"])))
        state["marked_positions"] = sorted({position for position in marked_positions if 1 <= position <= len(state["questions"])})
        state["status"] = "paused" if paused else "in_progress"
        self._save_attempt(user_id, state)
        return self._serialize_attempt(state)

    def submit_attempt(self, user_id: str, attempt_id: str, request_id: str) -> dict:
        state = self._load_attempt(user_id, attempt_id)
        cached = state["submission_requests"].get(request_id)
        if cached:
            return cached
        self._ensure_test_not_expired(state)
        result_items = []
        score = 0
        for position, question in enumerate(state["questions"], start=1):
            answer = state["answers"].get(question["question_id"], "")
            expected = [str(value) for value in question.get("answer", [])]
            actual = sorted(value.strip() for value in answer.split(",") if value.strip())
            has_answer_key = bool(expected)
            is_correct = actual == sorted(expected) if has_answer_key else None
            score += int(bool(is_correct))
            result_items.append({
                "position": position,
                "question_id": question["question_id"],
                "submitted_answer": answer,
                "standard_answer": expected,
                "explanation": question.get("explanation", ""),
                "is_correct": is_correct,
                "answer_status": "graded" if has_answer_key else "pending",
            })
        result = {
            "attempt_id": attempt_id,
            "status": "submitted",
            "score": score,
            "max_score": sum(bool(question.get("answer")) for question in state["questions"]),
            "items": result_items,
        }
        state["status"] = "submitted"
        state["submitted_at"] = self._now()
        state["score"] = score
        state["max_score"] = sum(bool(question.get("answer")) for question in state["questions"])
        state["submission_requests"][request_id] = result
        self._save_attempt(user_id, state)
        return result

    def get_explanation(self, user_id: str, attempt_id: str, question_id: str) -> dict:
        state = self._load_attempt(user_id, attempt_id)
        if state["answer_mode"] != "practice" and state["status"] != "submitted":
            raise PermissionError("测试模式交卷后才可查看解析")
        question = next((item for item in state["questions"] if item["question_id"] == question_id), None)
        if question is None:
            raise PermissionError("题目不存在")
        return {
            "question_id": question_id,
            "answer": question.get("answer", []),
            "explanation": question.get("explanation", ""),
        }

    def submission_outcomes(self, user_id: str, attempt_id: str) -> list[dict]:
        """Return server-owned attempt details for learning-state writeback."""
        state = self._load_attempt(user_id, attempt_id)
        if state.get("status") != "submitted":
            raise ValueError("试卷尚未提交")
        outcomes = []
        for question in state["questions"]:
            submitted_answer = state["answers"].get(question["question_id"], "")
            expected = sorted(str(value) for value in question.get("answer", []))
            actual = sorted(
                value.strip() for value in submitted_answer.split(",") if value.strip()
            )
            outcomes.append({
                "question_id": question["question_id"],
                "question_type": question.get("question_type", "short_answer"),
                "question_content": question.get("question_content", ""),
                "options": question.get("options", []),
                "standard_answer": question.get("answer", []),
                "explanation": question.get("explanation", ""),
                "kp_ids": question.get("kp_ids", []),
                "submitted_answer": submitted_answer,
                "is_correct": actual == expected if expected else None,
            })
        return outcomes

    def _template(self, template_id: str) -> dict:
        catalog = self._read_json(self.data_root / "catalog.json", {"papers": []})
        entry = next((item for item in catalog.get("papers", []) if item.get("template_id") == template_id and item.get("published")), None)
        if entry is None:
            raise KeyError("套题不存在或尚未发布")
        payload = self._read_json(self.data_root / str(entry["data_file"]), {})
        questions = payload.get("questions", [])
        if not isinstance(questions, list) or not questions:
            raise ValueError("套题没有可用题目")
        for question in questions:
            if not isinstance(question, dict):
                raise ValueError("题目内容不符合发布规范")
            visible_values = [question.get("question_content", "")]
            visible_values.extend(
                option.get("content", "")
                for option in question.get("options", [])
                if isinstance(option, dict)
            )
            if any(self._FORBIDDEN_QUESTION_CONTENT.search(str(value)) for value in visible_values):
                raise ValueError("题目内容不符合发布规范")
        return {"title": str(entry["title"]), "questions": deepcopy(questions)}

    def _attempt_path(self, user_id: str, attempt_id: str) -> Path:
        return self.runtime_root / user_id / f"{attempt_id}.json"

    def _load_attempt(self, user_id: str, attempt_id: str) -> dict:
        path = self._attempt_path(user_id, attempt_id)
        if not path.is_file():
            raise KeyError("作答记录不存在")
        return self._read_json(path, {})

    def _save_attempt(self, user_id: str, state: dict) -> None:
        path = self._attempt_path(user_id, state["attempt_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _read_json(path: Path, fallback: dict) -> dict:
        if not path.is_file():
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _serialize_attempt(state: dict) -> dict:
        items = []
        show_answers = state["status"] == "submitted"
        for position, question in enumerate(state["questions"], start=1):
            item = {
                "position": position,
                "question_id": question["question_id"],
                "question_type": question["question_type"],
                "question_content": question["question_content"],
                "options": question.get("options", []),
                "media": question.get("media", []),
                "answer": state["answers"].get(question["question_id"], ""),
            }
            if show_answers:
                item["standard_answer"] = question.get("answer", [])
                item["explanation"] = question.get("explanation", "")
            items.append(item)
        return {
            key: state.get(key)
            for key in ("attempt_id", "template_id", "source", "title", "answer_mode", "duration_minutes", "status", "current_position", "marked_positions", "created_at", "started_at", "submitted_at")
        } | ({"remaining_seconds": QualificationPaperRepository._remaining_seconds(state)} if state.get("answer_mode") == "test" else {}) | {"items": items}

    @staticmethod
    def _remaining_seconds(state: dict) -> int | None:
        if not state.get("started_at") or state.get("status") == "submitted":
            return None
        started_at = datetime.fromisoformat(str(state["started_at"]).replace("Z", "+00:00"))
        deadline = started_at + timedelta(minutes=int(state.get("duration_minutes") or 0))
        return max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))
