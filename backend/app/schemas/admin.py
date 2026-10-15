"""Admin + notification schemas (endpoints 48-51)."""
import re
from datetime import date, datetime
from typing import Any

from pydantic import Field, field_validator

from app.schemas.base import CamelModel

_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,49}$")
_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class AdminUserOut(CamelModel):
    id: int
    username: str
    full_name: str
    email: str
    role: str
    role_name: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class _UserFields(CamelModel):
    @field_validator("username", "email", "full_name", "role", mode="before", check_fields=False)
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("username", check_fields=False)
    @classmethod
    def _username(cls, value):
        value = value.lower()
        if not _USERNAME.match(value):
            raise ValueError("3-50 characters: lowercase letters, digits, dot, dash or underscore")
        return value

    @field_validator("email", check_fields=False)
    @classmethod
    def _email(cls, value):
        if value is not None and not _EMAIL.match(value):
            raise ValueError("not a valid e-mail address")
        return value.lower() if value else value

    @field_validator("role", check_fields=False)
    @classmethod
    def _role(cls, value):
        return value.upper() if value else value


class UserCreate(_UserFields):
    username: str
    full_name: str = Field(min_length=2, max_length=100)
    email: str = Field(max_length=150)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(description="TRADER, OPS_USER, OPS_MANAGER, ML_ADMIN or SYS_ADMIN")
    is_active: bool = True


class UserUpdate(_UserFields):
    """Send only what you want to change."""
    full_name: str | None = Field(None, min_length=2, max_length=100)
    email: str | None = Field(None, max_length=150)
    password: str | None = Field(None, min_length=8, max_length=128)
    role: str | None = None
    is_active: bool | None = None


class ConfigItemOut(CamelModel):
    key: str
    value: Any
    description: str | None
    editable_by: str
    updated_at: datetime


class ConfigUpdate(CamelModel):
    value: Any = Field(description="The new value (number, text, list or object - depends on the key; null clears it)")


class HolidayOut(CamelModel):
    date: date
    market: str
    description: str | None


class HolidayCreate(CamelModel):
    date: date
    market: str = Field("GLOBAL", min_length=1, max_length=10)
    description: str | None = Field(None, max_length=100)

    @field_validator("market")
    @classmethod
    def _market(cls, value):
        return value.strip().upper()


class NotificationOut(CamelModel):
    id: int
    type: str
    severity: str
    title: str
    message: str
    link: str | None
    is_read: bool
    created_at: datetime
