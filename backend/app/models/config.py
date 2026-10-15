"""System configuration key/value store (schema.sql section 10)."""
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    # none_as_null=False: a Python None is stored as JSON null (the column is NOT NULL).
    value: Mapped[Any] = mapped_column(JSONB(none_as_null=False))
    description: Mapped[str | None] = mapped_column(String(255))
    editable_by: Mapped[str] = mapped_column(String(30), server_default="SYS_ADMIN")
    updated_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
