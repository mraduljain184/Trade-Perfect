"""Auth endpoints 1-3 (PROJECT_CONTEXT section 8.1)."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_current_user, get_db
from app.models import User
from app.schemas.auth import LoginRequest, LoginResponse, UserOut
from app.schemas.base import ERR_401, MessageResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=LoginResponse, summary="Log in and get a JWT access token", responses=ERR_401)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    user, token, expires_in = auth_service.login(db, body.username, body.password, client_ip(request))
    return LoginResponse(access_token=token, expires_in=expires_in, user=UserOut.from_user(user))


@router.get("/me", response_model=UserOut, summary="Current user with role and permissions", responses=ERR_401)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.from_user(user)


@router.post("/logout", response_model=MessageResponse, summary="Log out (records an audit event)", responses=ERR_401)
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MessageResponse:
    auth_service.logout(db, user, client_ip(request))
    return MessageResponse(message="Logged out")
