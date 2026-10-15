"""Reading the audit trail (endpoints 30-33, 36). Writing stays in audit_service.

Row-level scoping: an Ops User (AUDIT_VIEW scope ASSIGNED) only sees events of trades whose
repair queue item is / was theirs. Ops Manager and Sys Admin see everything.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import BadRequestError, NotFoundError
from app.models import AuditLog, RepairQueueItem, User
from app.schemas.audit import AuditEventOut
from app.services import audit_service

DATE_FIELDS = ("eventTime", "tradeDate", "settlementDate")
# FR-AT-006: exactly these columns, in this order.
EXPORT_COLUMNS = ["Trade Id", "Queue ID", "Risk Score", "Likely Reason", "Recommended Fix", "Applied Fix",
                  "Applied By", "Applied Timestamp", "Status"]
DETAIL_COLUMNS = ["Action", "User", "Event Time"]


@dataclass
class AuditFilters:
    trade_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    date_field: str = "eventTime"
    user_id: int | None = None
    action: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [])}


def _scope(stmt: Select, user: User) -> Select:
    if user.permission_scope("AUDIT_VIEW") == "ALL":
        return stmt
    mine = select(RepairQueueItem.trade_pk).where(or_(RepairQueueItem.assigned_to == user.id,
                                                      RepairQueueItem.resolved_by == user.id))
    return stmt.where(AuditLog.trade_pk.in_(mine))


def _apply_filters(stmt: Select, filters: AuditFilters) -> Select:
    if filters.date_field not in DATE_FIELDS:
        raise BadRequestError(f"Invalid dateField '{filters.date_field}'. Allowed: {', '.join(DATE_FIELDS)}")
    if filters.trade_id:
        stmt = stmt.where(AuditLog.trade_id.ilike(filters.trade_id.strip()))
    if filters.user_id is not None:
        stmt = stmt.where(AuditLog.actor_user_id == filters.user_id)
    if filters.action:
        stmt = stmt.where(AuditLog.action.in_(filters.action))
    if filters.date_field == "eventTime":  # whole days in the app time zone
        zone = ZoneInfo(get_settings().app_timezone)
        if filters.date_from:
            stmt = stmt.where(AuditLog.event_time >= datetime.combine(filters.date_from, time.min, tzinfo=zone))
        if filters.date_to:
            stmt = stmt.where(AuditLog.event_time < datetime.combine(filters.date_to + timedelta(days=1), time.min, tzinfo=zone))
    else:
        column = AuditLog.trade_date if filters.date_field == "tradeDate" else AuditLog.settlement_date
        if filters.date_from:
            stmt = stmt.where(column >= filters.date_from)
        if filters.date_to:
            stmt = stmt.where(column <= filters.date_to)
    return stmt


def to_event(row: AuditLog) -> AuditEventOut:
    return AuditEventOut(
        id=row.id, event_time=row.event_time, actor_username=row.actor_username, actor_role=row.actor_role,
        action=row.action, entity_type=row.entity_type, trade_id=row.trade_id, queue_id=row.queue_id,
        risk_score=None if row.risk_score is None else float(row.risk_score), status_after=row.status_after,
        likely_reason=row.likely_reason, recommended_fix=row.recommended_fix, applied_fix=row.applied_fix,
        details=row.details)


def list_events(db: Session, user: User, filters: AuditFilters, page: int, page_size: int) -> tuple[list[AuditEventOut], int]:
    """Newest first."""
    total = db.scalar(_apply_filters(_scope(select(func.count()).select_from(AuditLog), user), filters)) or 0
    stmt = _apply_filters(_scope(select(AuditLog), user), filters)
    rows = db.scalars(stmt.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return [to_event(row) for row in rows], total


def timeline_rows(db: Session, trade_id: str, user: User) -> list[AuditLog]:
    """Every event of one trade, oldest first (FR-AT-001). 404 if there is nothing this user may see."""
    rows = db.scalars(_scope(select(AuditLog), user).where(AuditLog.trade_id == trade_id).order_by(AuditLog.id)).all()
    if not rows:
        raise NotFoundError(f"No audit events found for trade {trade_id}")
    return list(rows)


def timeline(db: Session, trade_id: str, user: User) -> list[AuditEventOut]:
    return [to_event(row) for row in timeline_rows(db, trade_id, user)]


def export_rows(db: Session, user: User, filters: AuditFilters, detailed: bool):
    stmt = _apply_filters(_scope(select(AuditLog), user), filters).order_by(AuditLog.id)
    for row in db.scalars(stmt.execution_options(yield_per=2000)):
        fixed = row.applied_fix is not None
        cells = [row.trade_id, row.queue_id, None if row.risk_score is None else f"{row.risk_score:.4f}",
                 row.likely_reason, row.recommended_fix, row.applied_fix, row.actor_username if fixed else None,
                 row.event_time.strftime("%Y-%m-%dT%H:%M:%SZ") if fixed else None, row.status_after]
        if detailed:
            cells += [row.action, row.actor_username, row.event_time.strftime("%Y-%m-%dT%H:%M:%SZ")]
        yield cells


def export_csv(db: Session, user: User, filters: AuditFilters, detailed: bool, ip_address: str | None) -> tuple[list[str], str]:
    from app.services import export_service  # local import: export_service is not needed for plain reads

    count = 0

    def rows():
        nonlocal count
        for cells in export_rows(db, user, filters, detailed):
            count += 1
            yield cells

    header = EXPORT_COLUMNS + (DETAIL_COLUMNS if detailed else [])
    chunks = ["﻿"] + list(export_service.to_csv_chunks(header, rows()))
    audit_service.log(db, "EXPORT_CSV", "AUDIT", actor=user, ip_address=ip_address,
                      details={"rows": count, "filters": filters.as_dict(), "export": "audit"})
    db.commit()
    return chunks, export_service.export_file_name("audit")


def verify(db: Session) -> tuple[bool, int | None]:
    return audit_service.verify_chain(db)
