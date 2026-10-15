"""Notifications for the bell icon (endpoint 51): a user's own ones plus the ones for their role."""
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models import Notification, User
from app.schemas.admin import NotificationOut

MAX_ITEMS = 100


def _mine(stmt: Select, user: User) -> Select:
    return stmt.where(or_(Notification.user_id == user.id, Notification.role_code == user.role.code))


def list_for(db: Session, user: User, unread_only: bool, limit: int) -> list[NotificationOut]:
    stmt = _mine(select(Notification), user)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    rows = db.scalars(stmt.order_by(Notification.id.desc()).limit(min(limit, MAX_ITEMS))).all()
    return [NotificationOut.model_validate(row) for row in rows]


def mark_read(db: Session, user: User, notification_id: int) -> NotificationOut:
    """A role-wide notification has ONE read flag: when one member reads it, it is read for the role."""
    row = db.scalar(_mine(select(Notification), user).where(Notification.id == notification_id))
    if row is None:
        raise NotFoundError(f"Notification {notification_id} not found")
    row.is_read = True
    db.commit()
    return NotificationOut.model_validate(row)
