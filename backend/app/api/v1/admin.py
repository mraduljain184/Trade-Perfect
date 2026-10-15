"""Admin endpoints 48-50 (PROJECT_CONTEXT section 8.1). HTTP only: the logic is in admin_service."""
from datetime import date

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_db, require_permission
from app.models import User
from app.schemas.admin import (
    AdminUserOut, ConfigItemOut, ConfigUpdate, HolidayCreate, HolidayOut, UserCreate, UserUpdate,
)
from app.schemas.base import ERR_AUTH, ErrorResponse
from app.services import admin_service

router = APIRouter(prefix="/admin", tags=["Admin"])

ERR_400 = {400: {"model": ErrorResponse, "description": "The value is not valid for this setting"}}
ERR_404 = {404: {"model": ErrorResponse, "description": "Not found"}}
ERR_409 = {409: {"model": ErrorResponse, "description": "Already exists, or not allowed"}}
ConfigAdmin = Depends(require_permission("SYSTEM_CONFIG", "BUSINESS_RULES_CONFIG"))


# ---------------------------------------------------------------- users
@router.get("/users", response_model=list[AdminUserOut], responses=ERR_AUTH, summary="All users with role and status")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_permission("USER_MANAGE"))):
    return admin_service.list_users(db)


@router.post("/users", response_model=AdminUserOut, status_code=201, responses={**ERR_AUTH, **ERR_400, **ERR_409},
             summary="Create a user (the password is stored as a bcrypt hash)")
def create_user(body: UserCreate, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_permission("USER_MANAGE"))) -> AdminUserOut:
    return admin_service.create_user(db, body, user, client_ip(request))


@router.put("/users/{user_id}", response_model=AdminUserOut, responses={**ERR_AUTH, **ERR_400, **ERR_404, **ERR_409},
            summary="Change name, e-mail, password, active flag or role (a role change also needs ROLE_ASSIGN)")
def update_user(user_id: int, body: UserUpdate, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_permission("USER_MANAGE"))) -> AdminUserOut:
    return admin_service.update_user(db, user_id, body, user, client_ip(request))


# ---------------------------------------------------------------- system configuration
@router.get("/config", response_model=list[ConfigItemOut], responses=ERR_AUTH, summary="Every system_config key")
def list_config(db: Session = Depends(get_db), _: User = ConfigAdmin):
    return admin_service.list_config(db)


@router.get("/config/{key}", response_model=ConfigItemOut, responses={**ERR_AUTH, **ERR_404}, summary="One setting")
def get_config(key: str, db: Session = Depends(get_db), _: User = ConfigAdmin) -> ConfigItemOut:
    return admin_service.get_config(db, key)


@router.put("/config/{key}", response_model=ConfigItemOut, responses={**ERR_AUTH, **ERR_400, **ERR_404},
            summary="Change one setting: rule weights, thresholds, business date, e-mail lists, batch size ...")
def put_config(key: str, body: ConfigUpdate, request: Request, db: Session = Depends(get_db),
               user: User = ConfigAdmin) -> ConfigItemOut:
    return admin_service.set_config(db, key, body.value, user, client_ip(request))


# ---------------------------------------------------------------- holidays
@router.get("/holidays", response_model=list[HolidayOut], responses=ERR_AUTH, summary="Holiday calendar")
def list_holidays(db: Session = Depends(get_db), _: User = Depends(require_permission("SYSTEM_CONFIG"))):
    return admin_service.list_holidays(db)


@router.post("/holidays", response_model=HolidayOut, status_code=201, responses={**ERR_AUTH, **ERR_409},
             summary="Add a holiday (settlement on that day is then flagged like a weekend)")
def add_holiday(body: HolidayCreate, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_permission("SYSTEM_CONFIG"))) -> HolidayOut:
    return admin_service.add_holiday(db, body, user, client_ip(request))


@router.delete("/holidays/{day}", status_code=204, responses={**ERR_AUTH, **ERR_404}, summary="Remove a holiday")
def delete_holiday(day: date, request: Request, market: str = Query("GLOBAL", max_length=10),
                   db: Session = Depends(get_db), user: User = Depends(require_permission("SYSTEM_CONFIG"))) -> Response:
    admin_service.delete_holiday(db, day, market, user, client_ip(request))
    return Response(status_code=204)
