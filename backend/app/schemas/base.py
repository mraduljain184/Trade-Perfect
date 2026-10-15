"""Base for every API schema: camelCase JSON <-> snake_case Python (PROJECT_CONTEXT section 8)."""
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class Paged(CamelModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    page_size: int = 50


class FieldErrorOut(CamelModel):
    field: str
    rule_id: str | None = None
    message: str


class ErrorDetail(CamelModel):
    code: str
    message: str
    fields: list[FieldErrorOut] = []


class ErrorResponse(CamelModel):
    """The one error shape used by every endpoint."""
    error: ErrorDetail


class MessageResponse(CamelModel):
    message: str


# Reusable "responses=" blocks so Swagger documents the error cases.
ERR_401 = {401: {"model": ErrorResponse, "description": "Missing, invalid or expired token"}}
ERR_403 = {403: {"model": ErrorResponse, "description": "Logged in, but the role lacks the permission"}}
ERR_AUTH = {**ERR_401, **ERR_403}
