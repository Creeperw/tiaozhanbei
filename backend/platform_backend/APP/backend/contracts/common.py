from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FieldError(ContractModel):
    field: str
    message: str
    error_type: str


class ApiErrorResponse(ContractModel):
    code: str
    detail: str
    request_id: str
    field_errors: list[FieldError] = Field(default_factory=list)


class PageMeta(ContractModel):
    limit: int = Field(ge=0)
    next_cursor: str | None = None
    total: int | None = Field(default=None, ge=0)


def page_meta(*, item_count: int, total: int | None = None, limit: int | None = None) -> dict[str, Any]:
    resolved_limit = item_count if limit is None else limit
    return PageMeta(
        limit=resolved_limit,
        next_cursor=None,
        total=item_count if total is None else total,
    ).model_dump()
