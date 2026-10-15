"""Login / logout business logic."""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import UnauthorizedError
from app.core.security import DUMMY_HASH, create_access_token, verify_password
from app.models import Role, RolePermission, User
from app.services import audit_service

logger = logging.getLogger("app.auth")


def _find_user(db: Session, username: str) -> User | None:
    return db.scalar(
        select(User)
        .where(User.username == username)
        .options(selectinload(User.role).selectinload(Role.role_permissions).selectinload(RolePermission.permission))
    )


def login(db: Session, username: str, password: str, ip_address: str | None) -> tuple[User, str, int]:
    """Check the password. Returns (user, token, expires_in_seconds) or raises 401.

    Both outcomes are written to the audit trail. The error message is the same for
    "no such user", "wrong password" and "inactive user" so attackers learn nothing.
    """
    username = username.strip().lower()
    user = _find_user(db, username)
    password_ok = verify_password(password, user.password_hash if user else DUMMY_HASH)

    if user is None or not password_ok or not user.is_active:
        reason = "unknown_user" if user is None else ("inactive_user" if password_ok else "wrong_password")
        audit_service.log(
            db, "LOGIN_FAILED", "AUTH",
            actor=user, actor_username=None if user else username,
            entity_id=user.id if user else None, ip_address=ip_address, details={"reason": reason},
        )
        db.commit()
        logger.warning("Login failed for username=%s (%s)", username, reason)
        raise UnauthorizedError("Invalid username or password")

    user.last_login_at = datetime.now(timezone.utc)
    audit_service.log(db, "LOGIN", "AUTH", actor=user, entity_id=user.id, ip_address=ip_address)
    db.commit()
    token, expires_in = create_access_token(user.id, user.username, user.role.code)
    logger.info("Login ok for username=%s role=%s", user.username, user.role.code)
    return user, token, expires_in


def logout(db: Session, user: User, ip_address: str | None) -> None:
    """JWTs are stateless, so logout only records the event; the frontend discards the token."""
    audit_service.log(db, "LOGOUT", "AUTH", actor=user, entity_id=user.id, ip_address=ip_address)
    db.commit()
