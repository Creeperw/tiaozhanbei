"""Pure, immutable state transitions for a case-training session."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

DEFAULT_EXPIRATION = timedelta(hours=24)
HELP_AVAILABLE_AFTER_MESSAGES = 10
MAX_ROUNDS = 30
_TERMINAL_STATUSES = frozenset({"completed", "needs_revision", "rejected", "failed", "abandoned", "expired"})


@dataclass(frozen=True)
class CaseTrainingState:
    status: str = "created"
    learner_messages: int = 0
    scoring_enabled: bool = True
    help_used: bool = False
    created_at: datetime | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            raise ValueError("created_at is required")
        if not 0 <= self.learner_messages <= MAX_ROUNDS:
            raise ValueError("learner_messages must be between zero and the maximum rounds")
        if self.status == "help_available":
            if self.learner_messages < HELP_AVAILABLE_AFTER_MESSAGES:
                raise ValueError("help requires at least ten learner messages")
            if self.help_used:
                raise ValueError("help cannot be available after it has been used")
        if self.expires_at is None:
            object.__setattr__(self, "expires_at", self.created_at + DEFAULT_EXPIRATION)


def transition(
    state: CaseTrainingState,
    event: str,
    *,
    now: datetime | None = None,
) -> CaseTrainingState:
    """Return the state produced by one permitted case-training event."""
    if event == "expire":
        if state.status in _TERMINAL_STATUSES:
            return state
        return replace(state, status="expired")

    if state.status not in _TERMINAL_STATUSES:
        if now is None:
            raise ValueError("now is required for nonterminal transitions")
        if now >= state.expires_at:
            return replace(state, status="expired")

    if event == "activate" and state.status == "created":
        return replace(state, status="active")
    if event == "abandon" and state.status in {"active", "help_available"}:
        return replace(state, status="abandoned")
    if event == "submit" and state.status in {"active", "help_available"}:
        return replace(state, status="submitted")
    if event == "start_grading" and state.status == "submitted":
        return replace(state, status="grading")
    if event == "request_human_review" and state.status == "grading":
        return replace(state, status="needs_human_review")
    if event == "complete" and state.status in {"grading", "needs_human_review"}:
        return replace(state, status="completed")
    if event == "request_revision" and state.status == "grading":
        return replace(state, status="needs_revision")
    if event == "reject" and state.status == "grading":
        return replace(state, status="rejected")
    if event == "fail" and state.status == "grading":
        return replace(state, status="failed")
    if event == "learner_message" and state.status in {"active", "help_available"}:
        if state.learner_messages >= MAX_ROUNDS:
            raise ValueError("case training session has reached the maximum rounds")
        messages = state.learner_messages + 1
        status = (
            "help_available"
            if messages >= HELP_AVAILABLE_AFTER_MESSAGES and not state.help_used
            else "active"
        )
        return replace(state, status=status, learner_messages=messages)
    if (
        event == "make_help_available"
        and state.status == "active"
        and state.learner_messages >= HELP_AVAILABLE_AFTER_MESSAGES
        and not state.help_used
    ):
        return replace(state, status="help_available")
    if event == "answer_help" and state.status == "help_available" and not state.help_used:
        return replace(state, status="active", scoring_enabled=False, help_used=True)

    raise ValueError(f"event {event!r} is not allowed while status is {state.status!r}")
