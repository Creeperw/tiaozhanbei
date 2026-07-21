from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from competition_app.agents.common import envelope
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.review import (
    DailyReviewPolicy,
    ReviewFormulaPolicy,
    ReviewSchedule,
    UserKnowledgeState,
)
from competition_app.review.scheduler import ReviewScheduler


class ReviewSchedulerAdapter:
    def __init__(self) -> None:
        self.scheduler = ReviewScheduler()

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[ReviewSchedule]:
        diagnosis = context["dependency_outputs"]["diagnosis"].payload
        knowledge = context["dependency_outputs"]["knowledge"].payload
        policy = DailyReviewPolicy.model_validate(diagnosis.daily_review_policy)
        try:
            states = [
                UserKnowledgeState.model_validate(item)
                for item in context.get("user_knowledge_states", [])
            ]
        except ValidationError as exc:
            raise ValueError("user knowledge state violates review protocol") from exc
        mismatched_users = {
            state.user_id for state in states if state.user_id != str(context["learner_id"])
        }
        if mismatched_users:
            raise ValueError("user knowledge state identity does not match requested learner")
        resolved_ids = set(knowledge.resolved_kp_ids)
        relevant_states = [state for state in states if state.kp_id in resolved_ids]
        schedule = self.scheduler.rank_and_select(
            learner_id=str(context["learner_id"]),
            kp_ids=list(knowledge.resolved_kp_ids),
            states=relevant_states,
            daily_policy=policy,
            formula_policy=ReviewFormulaPolicy(),
            now=context.get("now", datetime.now(timezone.utc)),
            diagnosed_weak_kp_ids=list(diagnosis.weak_kp_ids),
            user_requested=True,
        )
        return envelope(context, "review_scheduler", "review_schedule", schedule)
