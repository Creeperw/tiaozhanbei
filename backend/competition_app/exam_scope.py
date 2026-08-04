from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from competition_app.contracts.exam_scope import (
    LEGACY_EXAM_SCOPE,
    ExamWorkspaceContext,
)


_CURRENT_EXAM_WORKSPACE: ContextVar[ExamWorkspaceContext | None] = ContextVar(
    "competition_current_exam_workspace", default=None
)


def bind_exam_workspace(
    learner_id: str, learning_target: dict[str, Any] | None
) -> Token[ExamWorkspaceContext | None]:
    """Pin the active exam to the current async workflow/request."""

    return _CURRENT_EXAM_WORKSPACE.set(
        ExamWorkspaceContext.from_learning_target(learner_id, learning_target)
    )


def bind_exam_workspace_context(
    workspace: ExamWorkspaceContext,
) -> Token[ExamWorkspaceContext | None]:
    return _CURRENT_EXAM_WORKSPACE.set(workspace)


def reset_exam_workspace(token: Token[ExamWorkspaceContext | None]) -> None:
    _CURRENT_EXAM_WORKSPACE.reset(token)


def current_exam_workspace(learner_id: str | None = None) -> ExamWorkspaceContext | None:
    workspace = _CURRENT_EXAM_WORKSPACE.get()
    if workspace is None:
        return None
    if learner_id is not None and workspace.learner_id != learner_id:
        return None
    return workspace


def current_exam_scope(learner_id: str) -> str:
    workspace = current_exam_workspace(learner_id)
    return workspace.storage_scope if workspace is not None else LEGACY_EXAM_SCOPE
