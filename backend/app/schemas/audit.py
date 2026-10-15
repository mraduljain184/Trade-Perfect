"""Audit schemas - mirror PROJECT_CONTEXT section 8.2 (AuditEvent, AuditBox)."""
import re
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from app.schemas.base import CamelModel

_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class AuditEventOut(CamelModel):
    id: int
    event_time: datetime
    actor_username: str
    actor_role: str | None
    action: str
    entity_type: str
    trade_id: str | None
    queue_id: str | None
    risk_score: float | None
    status_after: str | None
    likely_reason: str | None
    recommended_fix: str | None
    applied_fix: str | None
    details: dict[str, Any] | None


class ReportRequest(CamelModel):
    email_to: list[str] | None = Field(None, max_length=10, description="Default: audit_report_recipients from the config")

    @field_validator("email_to")
    @classmethod
    def _valid_addresses(cls, value):
        if value is None:
            return None
        cleaned = [address.strip() for address in value if address and address.strip()]
        bad = [address for address in cleaned if not _EMAIL.match(address)]
        if bad:
            raise ValueError(f"not a valid e-mail address: {', '.join(bad)}")
        return cleaned or None


class ReportOut(CamelModel):
    report_url: str
    email_status: str   # PENDING (will be sent), LOGGED_ONLY (no SMTP configured) or NOT_SENT (no recipients)
    file_name: str
    email_to: list[str] = []


class VerifyOut(CamelModel):
    valid: bool
    broken_at_id: int | None
