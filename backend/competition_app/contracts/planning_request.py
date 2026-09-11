from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class PlanningFocusProtocolError(ValueError):
    """Diagnosis exhausted its local protocol repair; do not rerun the Agent."""


class NumberedFocusEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_no: StrictInt
    evidence_id: str = Field(min_length=1, max_length=200)


class NumberedFocusAssessment(BaseModel):
    """Wire-only choices; all display names are restored by the backend."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["sufficient", "needs_retrieval", "unresolved"]
    focus_object_nos: list[StrictInt] = Field(max_length=12)
    focus_stage_no: StrictInt | None
    focus_book_nos: list[StrictInt] = Field(max_length=2)
    evidence_links: list[NumberedFocusEvidence] = Field(max_length=24)
    cross_stage_mode: Literal["none", "introductory_preview"]
    source_quote: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=2000)


class PlanningFocusEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    evidence_id: str = Field(min_length=1, max_length=200)


class PlanningFocusAssessment(BaseModel):
    """Diagnosis semantic judgment; catalog identities remain system-owned."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["sufficient", "needs_retrieval", "unresolved"]
    focus_names: list[str] = Field(max_length=12)
    focus_stage_id: str | None
    focus_books: list[str] = Field(max_length=2)
    evidence_links: list[PlanningFocusEvidence] = Field(max_length=24)
    cross_stage_mode: Literal["none", "introductory_preview"]
    source_quote: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=2000)


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