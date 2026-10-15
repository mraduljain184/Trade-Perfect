"""Notifications, email outbox, chat history (schema.sql section 10)."""
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EmailStatusEnum


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    role_code: Mapped[str | None] = mapped_column(String(30))
    type: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(10), server_default="INFO")
    title: Mapped[str] = mapped_column(String(150))
    message: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(255))
    is_read: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmailOutbox(Base):
    __tablename__ = "email_outbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    to_addresses: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    attachment_path: Mapped[str | None] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(EmailStatusEnum, server_default="PENDING")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    session_id: Mapped[str] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(10))  # 'user' / 'assistant'
    content: Mapped[str] = mapped_column(Text)
    tool_used: Mapped[str | None] = mapped_column(String(50))
    tool_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
