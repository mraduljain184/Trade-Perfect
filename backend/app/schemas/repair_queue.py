"""Repair-queue schemas - mirror PROJECT_CONTEXT section 8.2 (QueueItem, BulkResult)."""
from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.base import CamelModel
from app.schemas.trade import TradeResult

MAX_BULK_IDS = 500


class AssigneeOut(CamelModel):
    id: int
    full_name: str


class SuggestedFixOut(CamelModel):
    factor: str
    fix: str


class QueueItemOut(CamelModel):
    queue_id: str
    trade_id: str | None
    trade: TradeResult
    priority: str
    status: str
    risk_score: float
    suggested_fixes: list[SuggestedFixOut] = []
    assigned_to: AssigneeOut | None
    assigned_at: datetime | None
    applied_fix: str | None
    applied_by: str | None
    applied_at: datetime | None
    can_resubmit: bool
    can_reject: bool
    created_at: datetime
    updated_at: datetime


class QueueSummary(CamelModel):
    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]


class BulkFailure(CamelModel):
    id: str
    reason: str


class BulkResult(CamelModel):
    succeeded: list[str] = []
    failed: list[BulkFailure] = []


class QueueIdsRequest(CamelModel):
    queue_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_IDS)


class AssignRequest(QueueIdsRequest):
    assignee_id: int | None = Field(None, description="User to assign to; null = unassign (back to OPEN)")


class _TextRequest(CamelModel):
    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value


class RejectRequest(_TextRequest):
    reason: str = Field(min_length=3, max_length=2000)


class ApproveRequest(_TextRequest):
    note: str = Field(min_length=3, max_length=2000)


class TeamMemberPerformance(CamelModel):
    user_id: int
    username: str
    full_name: str
    assigned: int
    in_progress: int
    resolved: int
    avg_resolution_hours: float | None
