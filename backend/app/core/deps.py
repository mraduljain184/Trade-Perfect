"""FastAPI dependencies: DB session, current user, permission guard."""
from collections.abc import Callable

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.models import Role, RolePermission, User

__all__ = ["get_db", "get_current_user", "require_permission", "client_ip"]

# auto_error=False so a missing token goes through OUR 401 error shape.
bearer_scheme = HTTPBearer(auto_error=False, description="Paste the accessToken from POST /auth/login")


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Not authenticated")
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise UnauthorizedError("Invalid or expired token") from None

    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.role).selectinload(Role.role_permissions).selectinload(RolePermission.permission))
    )
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive")
    request.state.username = user.username  # picked up by the request log line
    return user


def require_permission(*codes: str) -> Callable[..., User]:
    """Route guard. The user needs AT LEAST ONE of the given permission codes, else 403.

        @router.get("/x")
        def x(user: User = Depends(require_permission("TRADE_VIEW"))): ...
    """

    def dependency(user: User = Depends(get_current_user)) -> User:
        if not any(code in user.permission_codes for code in codes):
            raise ForbiddenError(f"Permission required: {' or '.join(codes)}")
        return user

    return dependency
