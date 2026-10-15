"""Audit endpoints 30-36 (PROJECT_CONTEXT section 8.1). HTTP only: the logic is in the services."""
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_db, require_permission
from app.models import User
from app.schemas.audit import AuditEventOut, ReportOut, ReportRequest, VerifyOut
from app.schemas.base import ERR_AUTH, ErrorResponse, Paged
from app.schemas.trade import AuditBoxOut
from app.services import audit_query_service, audit_service, email_service, report_service, trade_service
from app.services.audit_query_service import AuditFilters

router = APIRouter(prefix="/audit", tags=["Audit"])

ERR_404 = {404: {"model": ErrorResponse, "description": "Nothing found (or not visible to this user)"}}
ACTIONS = tuple(sorted(audit_service.ACTIONS))


def audit_filters(
    trade_id: str | None = Query(None, alias="tradeId", max_length=30),
    date_from: date | None = Query(None, alias="dateFrom"),
    date_to: date | None = Query(None, alias="dateTo"),
    date_field: str = Query("eventTime", alias="dateField", description="eventTime, tradeDate or settlementDate"),
    user_id: int | None = Query(None, alias="userId"),
    action: str | None = Query(None, description="One or more audit actions, comma-separated (e.g. FIX_APPLIED,QUEUE_RESOLVED)"),
) -> AuditFilters:
    return AuditFilters(trade_id=trade_id, date_from=date_from, date_to=date_to, date_field=date_field, user_id=user_id,
                        action=trade_service.parse_multi(action, ACTIONS, "action"))


@router.get("", response_model=Paged[AuditEventOut], responses=ERR_AUTH,
            summary="Search the audit trail, newest first (Ops Users only see trades assigned to them)")
def list_events(filters: AuditFilters = Depends(audit_filters), page: int = Query(1, ge=1),
                page_size: int = Query(50, ge=1, le=500, alias="pageSize"), db: Session = Depends(get_db),
                user: User = Depends(require_permission("AUDIT_VIEW"))):
    items, total = audit_query_service.list_events(db, user, filters, page, page_size)
    return Paged[AuditEventOut](items=items, total=total, page=page, page_size=page_size)


@router.get("/export", response_class=StreamingResponse, responses=ERR_AUTH,
            summary="Download the filtered audit trail as CSV (FR-AT-006 columns)")
def export_events(request: Request, filters: AuditFilters = Depends(audit_filters),
                  detailed: bool = Query(False, description="Also add Action, User and Event Time columns"),
                  db: Session = Depends(get_db), user: User = Depends(require_permission("AUDIT_VIEW"))):
    chunks, file_name = audit_query_service.export_csv(db, user, filters, detailed, client_ip(request))
    return StreamingResponse(iter(chunks), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{file_name}"'})


@router.get("/verify", response_model=VerifyOut, responses=ERR_AUTH,
            summary="Re-compute the audit hash chain: proves no row was changed or removed (Sys Admin)")
def verify(db: Session = Depends(get_db), _: User = Depends(require_permission("SYSTEM_CONFIG"))) -> VerifyOut:
    valid, broken_at = audit_query_service.verify(db)
    return VerifyOut(valid=valid, broken_at_id=broken_at)


@router.get("/reports/{file_name}", response_class=FileResponse, responses={**ERR_AUTH, **ERR_404},
            summary="Download a generated PDF audit report")
def download_report(file_name: str, db: Session = Depends(get_db),
                    user: User = Depends(require_permission("AUDIT_VIEW"))):
    return FileResponse(report_service.find_report(db, file_name, user), media_type="application/pdf", filename=file_name)


@router.get("/trades/{trade_id}/timeline", response_model=list[AuditEventOut], responses={**ERR_AUTH, **ERR_404},
            summary="Every audit event of one trade, oldest first")
def timeline(trade_id: str, db: Session = Depends(get_db),
             user: User = Depends(require_permission("AUDIT_VIEW"))) -> list[AuditEventOut]:
    return audit_query_service.timeline(db, trade_id, user)


@router.get("/trades/{trade_id}/latest", response_model=AuditBoxOut, responses={**ERR_AUTH, **ERR_404},
            summary="Audit box for the trade page: latest score, reason, fix and who applied it")
def latest(trade_id: str, db: Session = Depends(get_db),
           user: User = Depends(require_permission("AUDIT_VIEW", "TRADE_VIEW"))) -> AuditBoxOut:
    return trade_service.get_trade(db, trade_id, user).audit_box


@router.post("/trades/{trade_id}/report", response_model=ReportOut, responses={**ERR_AUTH, **ERR_404},
             summary="Generate the PDF audit report for a trade and e-mail it")
def create_report(trade_id: str, request: Request, background_tasks: BackgroundTasks, body: ReportRequest | None = None,
                  db: Session = Depends(get_db), user: User = Depends(require_permission("AUDIT_VIEW"))) -> ReportOut:
    result = report_service.generate(db, trade_id, user, body.email_to if body else None, client_ip(request))
    background_tasks.add_task(email_service.send_pending)
    return result
