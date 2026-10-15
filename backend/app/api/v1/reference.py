"""Reference endpoints 4-7 (PROJECT_CONTEXT section 8.1)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_permission
from app.models import User
from app.schemas.base import ERR_401, ERR_AUTH
from app.schemas.reference import AccountOut, CounterpartyOut, SymbolOut, UserBrief
from app.services import reference_service

router = APIRouter(prefix="/reference", tags=["Reference"])


@router.get("/symbols", response_model=list[SymbolOut], responses=ERR_401, summary="Active symbols for the booking form")
def symbols(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reference_service.list_symbols(db)


@router.get("/counterparties", response_model=list[CounterpartyOut], responses=ERR_401,
            summary="Counterparties with their risk tier")
def counterparties(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reference_service.list_counterparties(db)


@router.get("/accounts", response_model=list[AccountOut], responses=ERR_401,
            summary="Accounts; ?active=true returns only active ones")
def accounts(active: bool | None = Query(None), _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reference_service.list_accounts(db, active)


@router.get("/users", response_model=list[UserBrief], responses=ERR_AUTH,
            summary="Active users for assignee / audit dropdowns; ?role=OPS_USER filters by role")
def users(role: str | None = Query(None, examples=["OPS_USER"]),
          _: User = Depends(require_permission("QUEUE_ASSIGN", "AUDIT_VIEW")), db: Session = Depends(get_db)):
    return reference_service.list_users(db, role)
