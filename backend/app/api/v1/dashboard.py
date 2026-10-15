"""Dashboard endpoints 18-19 (PROJECT_CONTEXT section 8.1). HTTP only: the SQL is in dashboard_service."""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.models import User
from app.schemas.base import ERR_AUTH
from app.schemas.dashboard import DashboardCharts, DashboardSummary
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

DashboardUser = Depends(require_permission("EXCEPTION_DASHBOARD_VIEW", "TRADE_VIEW"))


@router.get("/summary", response_model=DashboardSummary, responses=ERR_AUTH,
            summary="KPI cards: trades booked on one day (default today) and all trades, by status")
def summary(day: date | None = Query(None, alias="date", description="YYYY-MM-DD, default = today (IST)"),
            db: Session = Depends(get_db), user: User = DashboardUser) -> DashboardSummary:
    return dashboard_service.summary(db, user, day)


@router.get("/charts", response_model=DashboardCharts, responses=ERR_AUTH,
            summary="Chart data: status per trade date, top 10 counterparties and symbols, score histogram")
def charts(db: Session = Depends(get_db), user: User = DashboardUser) -> DashboardCharts:
    return dashboard_service.charts(db, user)
