"""Stable HTTP contracts shared by routers and OpenAPI generation."""

from APP.backend.contracts.common import ApiErrorResponse, FieldError, PageMeta

__all__ = ["ApiErrorResponse", "FieldError", "PageMeta"]
