"""Chat schemas (endpoint 52)."""
from typing import Any

from pydantic import Field, field_validator

from app.schemas.base import CamelModel


class ChatRequest(CamelModel):
    session_id: str = Field(min_length=1, max_length=50, description="Any ID the frontend generates per login")
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("session_id", "message", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value


class ChatResponse(CamelModel):
    answer: str
    tool_used: str | None
    data: Any | None = None
