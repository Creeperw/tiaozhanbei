from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, ConfigDict, Field

from APP.backend.contracts.common import ContractModel, PageMeta


QuestionStatus = Literal[
    "processing",
    "preview_ready",
    "needs_human_review",
    "active",
    "inactive",
    "rejected",
    "failed",
]


class QuestionWorkspaceItem(ContractModel):
    question_id: str
    question_type: str
    stem: str
    answer: str
    explanation: str = ""
    options: list[str] = Field(default_factory=list)
    kp_ids: list[str] = Field(default_factory=list)
    status: QuestionStatus
    review_reason: str = ""


class QuestionRevisionRequest(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    question_type: str | None = Field(default=None, max_length=80)
    stem: str | None = Field(default=None, max_length=10000)
    answer: str | None = Field(default=None, max_length=10000)
    explanation: str | None = Field(
        default=None,
        max_length=20000,
        validation_alias=AliasChoices("explanation", "analysis"),
    )
    options: list[str] | None = None
    kp_ids: list[str] | None = None


class QuestionImportJob(ContractModel):
    job_id: str
    status: QuestionStatus
    item_count: int = Field(ge=0)
    original_filename: str
    error_message: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class QuestionImportCreated(ContractModel):
    job_id: str
    status: QuestionStatus
    item_count: int = Field(ge=0)
    items: list[QuestionWorkspaceItem] = Field(default_factory=list)


class QuestionImportDetail(ContractModel):
    job_id: str
    status: QuestionStatus
    item_count: int = Field(ge=0)
    original_filename: str
    created_at: datetime | None = None


class QuestionImportCollection(ContractModel):
    items: list[QuestionImportJob] = Field(default_factory=list)
    page: PageMeta


class QuestionWorkspaceCollection(ContractModel):
    items: list[QuestionWorkspaceItem] = Field(default_factory=list)
    page: PageMeta


class QuestionStateResponse(ContractModel):
    question_id: str
    status: QuestionStatus
    vector_index: dict[str, Any] | None = None


class QuestionIndexResponse(ContractModel):
    vector_index: dict[str, Any]
