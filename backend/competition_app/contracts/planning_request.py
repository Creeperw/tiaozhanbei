from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanningRequestScope(BaseModel):
    """Semantic request intent, independent of retrieval success/failure."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["route", "explicit_focus", "clarify"]
    objects: list[str] = Field(max_length=12)
    source_quote: str = Field(min_length=1, max_length=300)
    clarification_question: str | None = Field(max_length=220)

    @model_validator(mode="after")
    def coherent_scope(self) -> "PlanningRequestScope":
        if self.mode == "explicit_focus":
            if not self.objects or any(
                not name.strip() or len(name) > 120 for name in self.objects
            ):
                raise ValueError("explicit focus requires bounded nonempty objects")
            if len(set(self.objects)) != len(self.objects):
                raise ValueError("duplicate requested focus objects")
        elif self.objects:
            raise ValueError("only explicit focus may contain requested objects")
        if self.mode == "clarify":
            if not self.clarification_question or not self.clarification_question.strip():
                raise ValueError("ambiguous request requires a specific question")
        elif self.clarification_question is not None:
            raise ValueError("resolved request must not contain a question")
        return self

    def validate_request(self, request: str, messages: list[dict]) -> None:
        if not self.source_quote.strip() or self.source_quote not in request:
            raise ValueError("planning scope requires an exact current-message quote")
        # History resolves references; profile, assistant recommendations and
        # retrieved text alone cannot introduce mandatory user objects.
        user_texts = [request, *[
            str(item.get("content") or "") for item in messages
            if isinstance(item, dict) and item.get("role") == "user"
        ]]
        if any(not any(name in text for text in user_texts) for name in self.objects):
            raise ValueError("requested focus object has no user-message anchor")