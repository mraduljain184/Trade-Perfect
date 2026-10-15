"""Declarative base + the PostgreSQL enum types (created by schema.sql, so create_type=False)."""
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _enum(name: str, *values: str) -> ENUM:
    return ENUM(*values, name=name, create_type=False)


TradeTypeEnum = _enum("trade_type_enum", "BUY", "SELL")
TradeStatusEnum = _enum("trade_status_enum", "PASS", "MONITOR", "EXCEPTION", "REJECTED")
PriorityEnum = _enum("priority_enum", "HIGH", "MEDIUM", "LOW")
QueueStatusEnum = _enum("queue_status_enum", "OPEN", "IN_PROGRESS", "RESOLVED", "REJECTED")
TradeSourceEnum = _enum("trade_source_enum", "UI", "FILE", "STREAM", "API")
BatchStatusEnum = _enum("batch_status_enum", "PENDING", "PROCESSING", "COMPLETED", "FAILED")
ModelStatusEnum = _enum("model_status_enum", "CANDIDATE", "PRODUCTION", "ARCHIVED", "FAILED")
RetrainStatusEnum = _enum("retrain_status_enum", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED")
EmailStatusEnum = _enum("email_status_enum", "PENDING", "SENT", "FAILED", "LOGGED_ONLY")
