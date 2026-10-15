"""Bulk-upload schemas - mirror PROJECT_CONTEXT section 8.2 (UploadBatch)."""
from datetime import datetime

from pydantic import Field

from app.schemas.base import CamelModel


class UploadAccepted(CamelModel):
    batch_id: str


class UploadSummary(CamelModel):
    accepted: int = 0
    pass_: int = Field(0, alias="pass")  # "pass" is a Python keyword, so the attribute has a trailing underscore
    monitor: int = 0
    exception: int = 0
    rejected: int = 0


class UploadBatchOut(CamelModel):
    batch_id: str
    file_name: str
    status: str
    total_rows: int
    processed_rows: int
    summary: UploadSummary
    started_at: datetime
    completed_at: datetime | None
    error_message: str | None
