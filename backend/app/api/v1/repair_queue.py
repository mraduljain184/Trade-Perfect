"""Repair queue endpoints 20-28 (PROJECT_CONTEXT section 8.1). HTTP only: the rules are in repair_queue_service."""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_db, require_permission
from app.models import User
from app.schemas.base import ERR_AUTH, ErrorResponse, Paged
from app.schemas.repair_queue import (
    ApproveRequest, AssignRequest, BulkResult, QueueIdsRequest, QueueItemOut, QueueSummary, RejectRequest,
)
from app.services import export_service, repair_queue_service, trade_service
from app.services.repair_queue_service import PRIORITIES, STATUSES, QueueFilters

router = APIRouter(prefix="/repair-queue", tags=["Repair queue"])

ERR_404 = {404: {"model": ErrorResponse, "description": "Queue item not found (or not visible to this user)"}}
ERR_409 = {409: {"model": ErrorResponse, "description": "This action is not allowed in the item's current status / priority"}}


def queue_filters(
    status: str | None = Query(None, description="OPEN, IN_PROGRESS, RESOLVED, REJECTED (comma-separated for several)"),
    priority: str | None = Query(None, description="HIGH, MEDIUM, LOW (comma-separated for several)"),
    assigned_to: str | None = Query(None, alias="assignedTo", max_length=20, description="A user id, `me` or `unassigned`"),
    search: str | None = Query(None, max_length=50, description="Part of a Queue ID, Trade ID or symbol"),
) -> QueueFilters:
    return QueueFilters(status=trade_service.parse_multi(status, STATUSES, "status"),
                        priority=trade_service.parse_multi(priority, PRIORITIES, "priority"),
                        assigned_to=assigned_to, search=search)


SortParam = Query(None, description="e.g. riskScore:desc,updatedAt:asc (default: priority HIGH to LOW, then risk score)")


@router.get("", response_model=Paged[QueueItemOut], responses=ERR_AUTH,
            summary="List repair queue items (Ops Users see their own and unassigned OPEN items)")
def list_items(filters: QueueFilters = Depends(queue_filters), page: int = Query(1, ge=1),
               page_size: int = Query(50, ge=1, le=500, alias="pageSize"), sort: str | None = SortParam,
               db: Session = Depends(get_db), user: User = Depends(require_permission("REPAIR_QUEUE_MANAGE"))):
    items, total = repair_queue_service.list_items(db, user, filters, page, page_size, sort)
    return Paged[QueueItemOut](items=items, total=total, page=page, page_size=page_size)


# NOTE: /summary and /export must stay ABOVE /{queue_id}, otherwise they would be read as a queue ID.
@router.get("/summary", response_model=QueueSummary, responses=ERR_AUTH,
            summary="Top bar: total items and counts by status and priority")
def summary(db: Session = Depends(get_db),
            user: User = Depends(require_permission("REPAIR_QUEUE_MANAGE"))) -> QueueSummary:
    return repair_queue_service.summary(db, user)


@router.get("/export", response_class=StreamingResponse, responses=ERR_AUTH,
            summary="Download the filtered queue as CSV")
def export_queue(request: Request, filters: QueueFilters = Depends(queue_filters), sort: str | None = SortParam,
                 db: Session = Depends(get_db), user: User = Depends(require_permission("REPAIR_QUEUE_MANAGE"))):
    chunks, file_name = export_service.export_queue(db, user, filters, sort, client_ip(request))
    return StreamingResponse(iter(chunks), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{file_name}"'})


@router.post("/assign", response_model=BulkResult, responses=ERR_AUTH,
             summary="Assign items to a user (assigneeId null = unassign). Ops Users can only pick up unassigned items")
def assign(body: AssignRequest, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_permission("QUEUE_ASSIGN"))) -> BulkResult:
    return repair_queue_service.assign(db, user, body.queue_ids, body.assignee_id, client_ip(request))


@router.post("/bulk-resubmit", response_model=BulkResult, responses=ERR_AUTH,
             summary="Re-submit many LOW items at once; items that are not eligible are listed with the reason")
def bulk_resubmit(body: QueueIdsRequest, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_permission("QUEUE_BULK_OPS"))) -> BulkResult:
    return repair_queue_service.bulk_resubmit(db, user, body.queue_ids, client_ip(request))


@router.get("/{queue_id}", response_model=QueueItemOut, responses={**ERR_AUTH, **ERR_404},
            summary="One queue item with its trade, triggered rules and suggested fixes")
def get_item(queue_id: str, db: Session = Depends(get_db),
             user: User = Depends(require_permission("REPAIR_QUEUE_MANAGE"))) -> QueueItemOut:
    return repair_queue_service.get_item(db, queue_id, user)


@router.post("/{queue_id}/resubmit", response_model=QueueItemOut, responses={**ERR_AUTH, **ERR_404, **ERR_409},
             summary="Re-score a MEDIUM/LOW item and resolve it (409 for HIGH items)")
def resubmit(queue_id: str, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_permission("FIX_APPLY"))) -> QueueItemOut:
    return repair_queue_service.resubmit(db, user, queue_id, client_ip(request))


@router.post("/{queue_id}/reject", response_model=QueueItemOut, responses={**ERR_AUTH, **ERR_404, **ERR_409},
             summary="Reject a HIGH item (reason required): the queue item and the trade become REJECTED")
def reject(queue_id: str, body: RejectRequest, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_permission("FIX_APPLY"))) -> QueueItemOut:
    return repair_queue_service.reject(db, user, queue_id, body.reason, client_ip(request))


@router.post("/{queue_id}/approve", response_model=QueueItemOut, responses={**ERR_AUTH, **ERR_404, **ERR_409},
             summary="Manager resolves a HIGH item with a note")
def approve(queue_id: str, body: ApproveRequest, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_permission("HIGH_RESOLUTION_APPROVE"))) -> QueueItemOut:
    return repair_queue_service.approve(db, user, queue_id, body.note, client_ip(request))
