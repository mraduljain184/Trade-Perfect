"""Notification endpoint 51 (PROJECT_CONTEXT section 8.1): the bell icon."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.models import User
from app.schemas.admin import NotificationOut
from app.schemas.base import ERR_401, ErrorResponse
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=list[NotificationOut], responses=ERR_401,
            summary="My notifications and those for my role, newest first")
def list_notifications(unread_only: bool = Query(False, alias="unreadOnly"), limit: int = Query(50, ge=1, le=100),
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return notification_service.list_for(db, user, unread_only, limit)


@router.post("/{notification_id}/read", response_model=NotificationOut,
             responses={**ERR_401, 404: {"model": ErrorResponse, "description": "Not one of my notifications"}},
             summary="Mark one notification as read")
def mark_read(notification_id: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)) -> NotificationOut:
    return notification_service.mark_read(db, user, notification_id)
