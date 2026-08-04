from __future__ import annotations

from dataclasses import dataclass
from typing import Any


LEGACY_EXAM_SCOPE = "__legacy__"


@dataclass(frozen=True, slots=True)
class ExamWorkspaceContext:
    """Server-owned identity of one learner's independent exam workspace."""

    learner_id: str
    exam_track_id: str
    exam_name: str = ""
    scope_id: str = ""
    syllabus_version: str = ""

    @property
    def storage_scope(self) -> str:
        return self.exam_track_id or LEGACY_EXAM_SCOPE

    @classmethod
    def from_learning_target(
        cls, learner_id: str, target: dict[str, Any] | None
    ) -> "ExamWorkspaceContext":
        target = target if isinstance(target, dict) else {}
        return cls(
            learner_id=learner_id,
            exam_track_id=str(target.get("exam_track_id") or "").strip(),
            exam_name=str(target.get("exam_name") or "").strip(),
            scope_id=str(target.get("scope_id") or "").strip(),
            syllabus_version=str(target.get("syllabus_version") or "").strip(),
        )
