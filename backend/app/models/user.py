"""Users, roles, permissions (schema.sql section 2)."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    primary_function: Mapped[str | None] = mapped_column(String(200))
    key_access: Mapped[str | None] = mapped_column(String(200))

    role_permissions: Mapped[list["RolePermission"]] = relationship(back_populates="role", cascade="all, delete-orphan")


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    description: Mapped[str] = mapped_column(String(200))


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(Integer, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(10), server_default="ALL")  # ALL / ASSIGNED / OWN

    role: Mapped[Role] = relationship(back_populates="role_permissions")
    permission: Mapped[Permission] = relationship()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    full_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(150), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("roles.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role: Mapped[Role] = relationship()

    @property
    def permission_codes(self) -> list[str]:
        return sorted(rp.permission.code for rp in self.role.role_permissions)

    def permission_scope(self, code: str) -> str | None:
        """'ALL' / 'ASSIGNED' / 'OWN', or None if the user does not have the permission."""
        for rp in self.role.role_permissions:
            if rp.permission.code == code:
                return rp.scope
        return None
