"""Trade endpoints 8-14 (PROJECT_CONTEXT section 8.1). HTTP only: all logic is in the services."""
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_db, require_permission
from app.models import User
from app.schemas.base import ERR_AUTH, ErrorResponse, Paged
from app.schemas.trade import TradeDetail, TradeInput, TradeResult, TradeVersionOut, ValidationOut
from app.services import export_service, trade_service
from app.services.trade_service import PRIORITIES, STATUSES, TradeFilters

router = APIRouter(prefix="/trades", tags=["Trades"])

ERR_422 = {422: {"model": ErrorResponse, "description": "Hard-gate validation failed; `fields` lists every problem"}}
ERR_404 = {404: {"model": ErrorResponse, "description": "Trade not found (or not visible to this user)"}}
ERR_409 = {409: {"model": ErrorResponse, "description": "Trade is REJECTED, or the queue item is already closed"}}


def trade_filters(
    status: str | None = Query(None, description="PASS, MONITOR, EXCEPTION, REJECTED (comma-separated for several)"),
    priority: str | None = Query(None, description="HIGH, MEDIUM, LOW (comma-separated for several)"),
    trade_date_from: date | None = Query(None, alias="tradeDateFrom"),
    trade_date_to: date | None = Query(None, alias="tradeDateTo"),
    settlement_date_from: date | None = Query(None, alias="settlementDateFrom"),
    settlement_date_to: date | None = Query(None, alias="settlementDateTo"),
    search: str | None = Query(None, max_length=50, description="Part of a Trade ID or symbol"),
    trader_name: str | None = Query(None, alias="traderName", max_length=100),
    counterparty: str | None = Query(None, max_length=100),
    batch_id: str | None = Query(None, alias="batchId", max_length=30),
    mine: bool = Query(False, description="Only trades I booked"),
) -> TradeFilters:
    return TradeFilters(
        status=trade_service.parse_multi(status, STATUSES, "status"),
        priority=trade_service.parse_multi(priority, PRIORITIES, "priority"),
        trade_date_from=trade_date_from, trade_date_to=trade_date_to,
        settlement_date_from=settlement_date_from, settlement_date_to=settlement_date_to,
        search=search, trader_name=trader_name, counterparty=counterparty, batch_id=batch_id, mine=mine)


SortParam = Query(None, description="e.g. finalScore:desc,tradeDate:asc (default createdAt:desc)")


@router.post("/validate", response_model=ValidationOut, responses=ERR_AUTH,
             summary="Dry-run validation for the booking form (nothing is saved)")
def validate_trade(body: TradeInput, db: Session = Depends(get_db),
                   user: User = Depends(require_permission("TRADE_SUBMIT_SINGLE", "TRADE_AMEND"))) -> ValidationOut:
    result = trade_service.validate(db, body, user)
    return ValidationOut(valid=result.valid, warnings=result.warnings,
                         fields=[{"field": e.field, "rule_id": e.rule_id, "message": e.message} for e in result.errors])


@router.post("", response_model=TradeResult, status_code=201, responses={**ERR_AUTH, **ERR_422},
             summary="Book one trade: validate, score, classify and route to the repair queue")
def create_trade(body: TradeInput, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_permission("TRADE_SUBMIT_SINGLE"))) -> TradeResult:
    return trade_service.create_trade(db, body, user, client_ip(request))


@router.get("", response_model=Paged[TradeResult], responses=ERR_AUTH,
            summary="List trades with filters, paging and sorting (Traders see only their own)")
def list_trades(filters: TradeFilters = Depends(trade_filters), page: int = Query(1, ge=1),
                page_size: int = Query(50, ge=1, le=500, alias="pageSize"), sort: str | None = SortParam,
                db: Session = Depends(get_db), user: User = Depends(require_permission("TRADE_VIEW"))):
    items, total = trade_service.list_trades(db, user, filters, page, page_size, sort)
    return Paged[TradeResult](items=items, total=total, page=page, page_size=page_size)


# NOTE: /export must stay ABOVE /{trade_id}, otherwise "export" would be read as a trade ID.
@router.get("/export", response_class=StreamingResponse, responses=ERR_AUTH,
            summary="Download the filtered trade list as CSV")
def export_trades(request: Request, filters: TradeFilters = Depends(trade_filters), sort: str | None = SortParam,
                  db: Session = Depends(get_db), user: User = Depends(require_permission("TRADE_VIEW"))):
    chunks, file_name = export_service.export_trades(db, user, filters, sort, client_ip(request))
    return StreamingResponse(iter(chunks), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{file_name}"'})


@router.get("/{trade_id}", response_model=TradeDetail, responses={**ERR_AUTH, **ERR_404},
            summary="One trade with rule hits, version count and audit box")
def get_trade(trade_id: str, db: Session = Depends(get_db),
              user: User = Depends(require_permission("TRADE_VIEW"))) -> TradeDetail:
    return trade_service.get_trade(db, trade_id, user)


@router.get("/{trade_id}/history", response_model=list[TradeVersionOut], responses={**ERR_AUTH, **ERR_404},
            summary="All versions of a trade, newest first, with the changed fields")
def get_history(trade_id: str, db: Session = Depends(get_db),
                user: User = Depends(require_permission("TRADE_VIEW"))) -> list[TradeVersionOut]:
    return trade_service.get_history(db, trade_id, user)


@router.put("/{trade_id}", response_model=TradeResult, responses={**ERR_AUTH, **ERR_404, **ERR_409, **ERR_422},
            summary="Amend a trade (or Apply Fix when queueId is sent): new version, re-scored")
def amend_trade(trade_id: str, body: TradeInput, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_permission("TRADE_AMEND"))) -> TradeResult:
    return trade_service.amend_trade(db, trade_id, body, user, client_ip(request))
