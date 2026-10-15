"""Upload batches, trades, versions, rule evaluations (schema.sql sections 5-6)."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BatchStatusEnum, PriorityEnum, TradeSourceEnum, TradeStatusEnum, TradeTypeEnum


class UploadBatch(Base):
    __tablename__ = "upload_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(30), unique=True)
    file_name: Mapped[str] = mapped_column(String(255))
    uploaded_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    total_rows: Mapped[int] = mapped_column(Integer, server_default="0")
    processed_rows: Mapped[int] = mapped_column(Integer, server_default="0")
    accepted_count: Mapped[int] = mapped_column(Integer, server_default="0")
    rejected_count: Mapped[int] = mapped_column(Integer, server_default="0")
    status: Mapped[str] = mapped_column(BatchStatusEnum, server_default="PENDING")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    trade_id: Mapped[str | None] = mapped_column(String(30), index=True)
    trade_type: Mapped[str | None] = mapped_column(TradeTypeEnum)
    symbol: Mapped[str | None] = mapped_column(String(20), index=True)
    quantity: Mapped[int | None] = mapped_column(BigInteger)
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    counterparty: Mapped[str | None] = mapped_column(String(100), index=True)
    trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    settlement_date: Mapped[date | None] = mapped_column(Date, index=True)
    account_id: Mapped[str | None] = mapped_column(String(20))
    trader_name: Mapped[str | None] = mapped_column(String(100))
    trader_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    ssi_matched: Mapped[bool | None] = mapped_column(Boolean)
    currency_matched: Mapped[bool | None] = mapped_column(Boolean)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    source: Mapped[str] = mapped_column(TradeSourceEnum, server_default="UI")
    batch_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("upload_batches.id"))
    batch_row_number: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(TradeStatusEnum, index=True)
    priority: Mapped[str | None] = mapped_column(PriorityEnum, index=True)
    rule_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    ml_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    final_score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    risk_factors: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    likely_reason: Mapped[str | None] = mapped_column(Text)
    suggested_action: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    rejection_reasons: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    model_version_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("model_versions.id"))

    version: Mapped[int] = mapped_column(Integer, server_default="1")
    is_resolved: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TradeVersion(Base):
    __tablename__ = "trade_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trade_pk: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trades.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    change_type: Mapped[str] = mapped_column(String(30))
    changed_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    change_reason: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RuleEvaluation(Base):
    __tablename__ = "rule_evaluations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trade_pk: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trades.id", ondelete="CASCADE"))
    trade_version: Mapped[int] = mapped_column(Integer)
    rule_id: Mapped[str] = mapped_column(String(10))
    rule_category: Mapped[str] = mapped_column(String(20))
    triggered: Mapped[bool] = mapped_column(Boolean)
    score_contribution: Mapped[Decimal] = mapped_column(Numeric(5, 4), server_default="0")
    message: Mapped[str | None] = mapped_column(Text)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
