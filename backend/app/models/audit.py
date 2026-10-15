"""Audit log (schema.sql section 9). Append-only: write ONLY through services/audit_service.py."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, CHAR, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    actor_username: Mapped[str] = mapped_column(String(50), server_default="SYSTEM")
    actor_role: Mapped[str | None] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(40), index=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    trade_pk: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    trade_id: Mapped[str | None] = mapped_column(String(30), index=True)
    queue_id: Mapped[str | None] = mapped_column(String(20))
    trade_date: Mapped[date | None] = mapped_column(Date)
    settlement_date: Mapped[date | None] = mapped_column(Date)
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    status_after: Mapped[str | None] = mapped_column(String(20))
    likely_reason: Mapped[str | None] = mapped_column(Text)
    recommended_fix: Mapped[str | None] = mapped_column(Text)
    applied_fix: Mapped[str | None] = mapped_column(Text)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    prev_hash: Mapped[str | None] = mapped_column(CHAR(64))
    hash: Mapped[str] = mapped_column(CHAR(64))
