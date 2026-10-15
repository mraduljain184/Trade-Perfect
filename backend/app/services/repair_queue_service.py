"""Repair queue workflows (endpoints 20-29, PROJECT_CONTEXT section 7.1).

Rules of this module:
  * The status machine is ONE table (`TRANSITIONS`). Anything not in it is refused with 409.
  * Row-level scoping lives here: an Ops User sees items assigned to them, unassigned OPEN items
    (so they can pick them up) and items they closed; Ops Manager / Sys Admin see everything.
  * Re-Submit re-scores through the engine (app.engine) - scoring is never done here.
  * Every state change writes audit rows in the same transaction.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Select, and_, case, extract, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.engine import score_trades
from app.models import ModelVersion, Notification, RepairQueueItem, Role, RuleEvaluation, Trade, User
from app.schemas.repair_queue import (
    AssigneeOut, BulkFailure, BulkResult, QueueItemOut, QueueSummary, TeamMemberPerformance,
)
from app.services import audit_service, engine_adapter, trade_service

logger = logging.getLogger("app.repair_queue")

OPEN, IN_PROGRESS, RESOLVED, REJECTED = "OPEN", "IN_PROGRESS", "RESOLVED", "REJECTED"
STATUSES = (OPEN, IN_PROGRESS, RESOLVED, REJECTED)
PRIORITIES = ("HIGH", "MEDIUM", "LOW")

# (current status, action) -> next status.  Section 7.1.  A pair that is missing is forbidden.
TRANSITIONS: dict[tuple[str, str], str] = {
    (OPEN, "ASSIGN"): IN_PROGRESS,            # manager assigns / ops user picks it up
    (IN_PROGRESS, "ASSIGN"): IN_PROGRESS,     # manager reassigns to someone else
    (IN_PROGRESS, "UNASSIGN"): OPEN,          # manager takes it back
    (OPEN, "RESUBMIT"): RESOLVED,
    (IN_PROGRESS, "RESUBMIT"): RESOLVED,
    (OPEN, "BULK_RESUBMIT"): RESOLVED,
    (IN_PROGRESS, "BULK_RESUBMIT"): RESOLVED,
    (OPEN, "APPROVE"): RESOLVED,              # manager approves a HIGH resolution
    (IN_PROGRESS, "APPROVE"): RESOLVED,
    (OPEN, "REJECT"): REJECTED,
    (IN_PROGRESS, "REJECT"): REJECTED,
    (RESOLVED, "REOPEN"): OPEN,               # a later amendment made the trade risky again
}
# Some actions are only allowed for certain priorities.
ALLOWED_PRIORITIES: dict[str, tuple[str, ...]] = {
    "RESUBMIT": ("MEDIUM", "LOW"),
    "BULK_RESUBMIT": ("LOW",),
    "REJECT": ("HIGH",),
    "APPROVE": ("HIGH",),
}
_ACTION_LABELS = {"ASSIGN": "Assign", "UNASSIGN": "Unassign", "RESUBMIT": "Re-Submit", "BULK_RESUBMIT": "Bulk Re-Submit",
                  "APPROVE": "Approve", "REJECT": "Reject", "REOPEN": "Re-open"}
_PRIORITY_HINTS = {
    "RESUBMIT": "Re-Submit is only for MEDIUM and LOW items - apply a fix or reject it",
    "BULK_RESUBMIT": "Bulk Re-Submit only accepts LOW items",
    "REJECT": "only HIGH items can be rejected",
    "APPROVE": "only HIGH items need a manager's approval",
}
NOT_FOUND = "Queue item not found"

_Assignee = aliased(User)
_Creator = aliased(User)
_Applier = aliased(User)
_PRIORITY_RANK = case((RepairQueueItem.priority == "HIGH", 0), (RepairQueueItem.priority == "MEDIUM", 1), else_=2)
SORT_COLUMNS = {
    "queueId": RepairQueueItem.queue_id, "priority": _PRIORITY_RANK, "status": RepairQueueItem.status,
    "riskScore": Trade.final_score, "tradeId": Trade.trade_id, "assignedAt": RepairQueueItem.assigned_at,
    "createdAt": RepairQueueItem.created_at, "updatedAt": RepairQueueItem.updated_at,
}


@dataclass
class QueueFilters:
    status: list[str] = field(default_factory=list)
    priority: list[str] = field(default_factory=list)
    assigned_to: str | None = None   # a user id, "me" or "unassigned"
    search: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [])}


# ------------------------------------------------------------------ state machine
def why_not(item: RepairQueueItem, action: str) -> str | None:
    """None if `action` is allowed for this item right now, else a sentence that says why not."""
    if (item.status, action) not in TRANSITIONS:
        return f"{_ACTION_LABELS[action]} is not allowed: queue item {item.queue_id} is {item.status}"
    allowed = ALLOWED_PRIORITIES.get(action)
    if allowed and item.priority not in allowed:
        return f"Queue item {item.queue_id} is {item.priority} priority: {_PRIORITY_HINTS[action]}"
    return None


def next_status(item: RepairQueueItem, action: str) -> str:
    """The status after `action`, or 409 if the transition is forbidden."""
    reason = why_not(item, action)
    if reason:
        raise ConflictError(reason)
    return TRANSITIONS[(item.status, action)]


def reopen(item: RepairQueueItem, priority: str, suggested_fixes: list[dict] | None, now: datetime) -> None:
    """A trade can have only ONE queue item, so when a resolved trade becomes risky again the same
    item goes back to OPEN (unassigned) instead of a second item being created."""
    item.status = next_status(item, "REOPEN")
    item.priority, item.suggested_fixes, item.updated_at = priority, suggested_fixes, now
    item.assigned_to = item.assigned_by = item.assigned_at = None
    item.applied_fix = item.applied_by = item.applied_at = None
    item.resolution_note = item.resolved_by = item.resolved_at = None


# ------------------------------------------------------------------ reading
def _sees_all(user: User) -> bool:
    return user.permission_scope("REPAIR_QUEUE_MANAGE") == "ALL"


def _scope(stmt: Select, user: User) -> Select:
    if _sees_all(user):
        return stmt
    # own items + unassigned OPEN items (to pick up) + items this user closed without being assigned
    return stmt.where(or_(RepairQueueItem.assigned_to == user.id, RepairQueueItem.resolved_by == user.id,
                          and_(RepairQueueItem.status == OPEN, RepairQueueItem.assigned_to.is_(None))))


def _select() -> Select:
    return (
        select(RepairQueueItem, Trade, _Assignee, _Creator.username, ModelVersion.version, _Applier.full_name)
        .join(Trade, Trade.id == RepairQueueItem.trade_pk)
        .outerjoin(_Assignee, _Assignee.id == RepairQueueItem.assigned_to)
        .outerjoin(_Creator, _Creator.id == Trade.created_by)
        .outerjoin(ModelVersion, ModelVersion.id == Trade.model_version_id)
        .outerjoin(_Applier, _Applier.id == RepairQueueItem.applied_by)
    )


def _apply_filters(stmt: Select, filters: QueueFilters, user: User) -> Select:
    if filters.status:
        stmt = stmt.where(RepairQueueItem.status.in_(filters.status))
    if filters.priority:
        stmt = stmt.where(RepairQueueItem.priority.in_(filters.priority))
    if filters.assigned_to:
        value = filters.assigned_to.strip().lower()
        if value in ("unassigned", "none"):
            stmt = stmt.where(RepairQueueItem.assigned_to.is_(None))
        elif value == "me":
            stmt = stmt.where(RepairQueueItem.assigned_to == user.id)
        elif value.isdigit():
            stmt = stmt.where(RepairQueueItem.assigned_to == int(value))
        else:
            raise BadRequestError("Invalid assignedTo: use a user id, 'me' or 'unassigned'")
    if filters.search:
        pattern = f"%{filters.search.strip()}%"
        stmt = stmt.where(or_(RepairQueueItem.queue_id.ilike(pattern), Trade.trade_id.ilike(pattern),
                              Trade.symbol.ilike(pattern)))
    return stmt


def _order_by(sort: str | None) -> list:
    if not sort:  # section 7.1: HIGH -> MEDIUM -> LOW, then the riskiest first
        return [_PRIORITY_RANK.asc(), Trade.final_score.desc(), RepairQueueItem.id.asc()]
    clauses = []
    for part in sort.split(","):
        name, _, direction = part.strip().partition(":")
        direction = (direction or "asc").lower()
        if name not in SORT_COLUMNS or direction not in ("asc", "desc"):
            raise BadRequestError(f"Invalid sort '{part.strip()}'. Use field:asc|desc with one of: {', '.join(SORT_COLUMNS)}")
        column = SORT_COLUMNS[name]
        clauses.append((column.desc() if direction == "desc" else column.asc()).nulls_last())
    clauses.append(RepairQueueItem.id.asc())  # stable order between pages
    return clauses


def _to_out(row, user: User, rule_hits: list[dict] | None = None) -> QueueItemOut:
    item, trade, assignee, creator, model_version, applier_name = row
    result = trade_service._to_result((trade, item, assignee.full_name if assignee else None, creator, model_version),
                                      rule_hits=rule_hits)
    may_fix = "FIX_APPLY" in user.permission_codes
    return QueueItemOut(
        queue_id=item.queue_id, trade_id=trade.trade_id, trade=result, priority=item.priority, status=item.status,
        risk_score=float(trade.final_score), suggested_fixes=item.suggested_fixes or [],
        assigned_to=AssigneeOut(id=assignee.id, full_name=assignee.full_name) if assignee else None,
        assigned_at=item.assigned_at, applied_fix=item.applied_fix, applied_by=applier_name, applied_at=item.applied_at,
        can_resubmit=may_fix and why_not(item, "RESUBMIT") is None,
        can_reject=may_fix and why_not(item, "REJECT") is None,
        created_at=item.created_at, updated_at=item.updated_at)


def list_items(db: Session, user: User, filters: QueueFilters, page: int, page_size: int,
               sort: str | None) -> tuple[list[QueueItemOut], int]:
    order = _order_by(sort)
    count_stmt = select(func.count()).select_from(RepairQueueItem).join(Trade, Trade.id == RepairQueueItem.trade_pk)
    total = db.scalar(_apply_filters(_scope(count_stmt, user), filters, user)) or 0
    stmt = _apply_filters(_scope(_select(), user), filters, user)
    rows = db.execute(stmt.order_by(*order).offset((page - 1) * page_size).limit(page_size)).all()
    return [_to_out(row, user) for row in rows], total


def iter_items(db: Session, user: User, filters: QueueFilters, sort: str | None):
    """Every item matching the filters (no paging) - used by the CSV export."""
    stmt = _apply_filters(_scope(_select(), user), filters, user).order_by(*_order_by(sort))
    for row in db.execute(stmt.execution_options(yield_per=1000)):
        yield _to_out(row, user)


def summary(db: Session, user: User) -> QueueSummary:
    """Counts for the top bar, limited to what this user may see."""
    by_status = dict(db.execute(_scope(select(RepairQueueItem.status, func.count()), user)
                                .group_by(RepairQueueItem.status)).all())
    by_priority = dict(db.execute(_scope(select(RepairQueueItem.priority, func.count()), user)
                                  .group_by(RepairQueueItem.priority)).all())
    return QueueSummary(total=sum(by_status.values()), by_status={s: by_status.get(s, 0) for s in STATUSES},
                        by_priority={p: by_priority.get(p, 0) for p in PRIORITIES})


def get_item(db: Session, queue_id: str, user: User) -> QueueItemOut:
    row = db.execute(_scope(_select(), user).where(RepairQueueItem.queue_id == queue_id)).first()
    if row is None:
        raise NotFoundError(f"Queue item {queue_id} not found")
    trade = row[1]
    hits = db.scalars(select(RuleEvaluation).where(
        RuleEvaluation.trade_pk == trade.id, RuleEvaluation.trade_version == trade.version).order_by(RuleEvaluation.id)).all()
    rule_hits = [{"rule_id": h.rule_id, "category": h.rule_category, "triggered": h.triggered,
                  "contribution": float(h.score_contribution), "message": h.message} for h in hits]
    return _to_out(row, user, rule_hits=rule_hits)


# ------------------------------------------------------------------ writing: helpers
def _unique(queue_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(q.strip() for q in queue_ids if q and q.strip()))


def _lock(db: Session, queue_ids: list[str], user: User) -> dict[str, tuple[RepairQueueItem, Trade]]:
    """The visible items among `queue_ids`, locked until the transaction ends."""
    stmt = (select(RepairQueueItem, Trade).join(Trade, Trade.id == RepairQueueItem.trade_pk)
            .where(RepairQueueItem.queue_id.in_(queue_ids)).with_for_update())
    return {item.queue_id: (item, trade) for item, trade in db.execute(_scope(stmt, user))}


def _lock_one(db: Session, queue_id: str, user: User) -> tuple[RepairQueueItem, Trade]:
    rows = _lock(db, [queue_id], user)
    if queue_id not in rows:
        raise NotFoundError(f"Queue item {queue_id} not found")
    return rows[queue_id]


def _audit(action: str, entity_type: str, trade: Trade, item: RepairQueueItem, user: User,
           ip_address: str | None = None, **extra: Any) -> dict[str, Any]:
    fields = trade_service._audit_fields(trade, item)
    if entity_type == "REPAIR_QUEUE":
        fields["entity_id"] = item.queue_id
    details = {"queueStatus": item.status, "priority": item.priority, **(extra.pop("details", None) or {})}
    return dict(action=action, entity_type=entity_type, actor=user, ip_address=ip_address, details=details,
                **fields, **extra)


def _result(succeeded: list[str], failed: dict[str, str], order: list[str]) -> BulkResult:
    return BulkResult(succeeded=succeeded, failed=[BulkFailure(id=q, reason=failed[q]) for q in order if q in failed])


# ------------------------------------------------------------------ assign / unassign (endpoint 23)
def assign(db: Session, user: User, queue_ids: list[str], assignee_id: int | None, ip_address: str | None) -> BulkResult:
    """Never all-or-nothing: each item succeeds or is listed in `failed` with the reason."""
    order = _unique(queue_ids)
    may_reassign = "QUEUE_REASSIGN" in user.permission_codes
    if not may_reassign and assignee_id != user.id:
        raise ForbiddenError("Permission required: QUEUE_REASSIGN (you can only pick up unassigned items yourself)")
    assignee = None
    if assignee_id is not None:
        assignee = db.get(User, assignee_id)
        if assignee is None or not assignee.is_active or "FIX_APPLY" not in assignee.permission_codes:
            raise BadRequestError(f"User {assignee_id} cannot be given repair queue items")

    rows = _lock(db, order, user)
    now = datetime.now(timezone.utc)
    succeeded, failed, audits = [], {}, []
    names = dict(db.execute(select(User.id, User.full_name).where(
        User.id.in_({item.assigned_to for item, _ in rows.values() if item.assigned_to}))).all())
    for queue_id in order:
        if queue_id not in rows:
            failed[queue_id] = NOT_FOUND
            continue
        item, trade = rows[queue_id]
        action = "ASSIGN" if assignee else "UNASSIGN"
        reason = why_not(item, action)
        previous = item.assigned_to
        if not reason and assignee and previous == assignee.id:
            reason = f"Already assigned to {assignee.full_name}"
        if not reason and not may_reassign and previous is not None:
            reason = f"Already assigned to {names.get(previous, 'another user')} - only a manager can reassign it"
        if reason:
            failed[queue_id] = reason
            continue
        item.status, item.updated_at = TRANSITIONS[(item.status, action)], now
        if assignee:
            item.assigned_to, item.assigned_by, item.assigned_at = assignee.id, user.id, now
            audits.append(_audit("QUEUE_REASSIGNED" if previous else "QUEUE_ASSIGNED", "REPAIR_QUEUE", trade, item, user,
                                 ip_address, details={"assigneeId": assignee.id, "assignee": assignee.username,
                                                      "previousAssigneeId": previous}))
        else:
            item.assigned_to = item.assigned_by = item.assigned_at = None
            audits.append(_audit("QUEUE_UNASSIGNED", "REPAIR_QUEUE", trade, item, user, ip_address,
                                 details={"previousAssigneeId": previous}))
        succeeded.append(queue_id)

    if succeeded and assignee and assignee.id != user.id:  # nobody needs a message about their own pick-up
        one = len(succeeded) == 1
        db.add(Notification(
            user_id=assignee.id, type="ASSIGNMENT", severity="INFO",
            title=(f"Repair queue item {succeeded[0]} assigned to you" if one
                   else f"{len(succeeded)} repair queue items assigned to you"),
            message=f"{user.full_name} assigned you: {', '.join(succeeded[:10])}{' ...' if len(succeeded) > 10 else ''}",
            link=f"/repair-queue?queueId={succeeded[0]}" if one else "/repair-queue?assignedTo=me"))
    audit_service.log_many(db, audits)
    db.commit()
    logger.info("Queue %s by %s: %d ok, %d failed", "assign" if assignee else "unassign", user.username,
                len(succeeded), len(failed))
    return _result(succeeded, failed, order)


# ------------------------------------------------------------------ re-submit (endpoints 24-25)
def _engine_record(trade: Trade) -> dict[str, Any]:
    return {"tradeId": trade.trade_id, "tradeType": trade.trade_type, "symbol": trade.symbol, "quantity": trade.quantity,
            "price": float(trade.price), "counterparty": trade.counterparty, "tradeDate": trade.trade_date,
            "settlementDate": trade.settlement_date, "accountId": trade.account_id, "ssiMatched": trade.ssi_matched,
            "currencyMatched": trade.currency_matched, "traderName": trade.trader_name}


def _resubmit(db: Session, user: User, order: list[str], action: str, ip_address: str | None) -> tuple[list[str], dict[str, str]]:
    """Re-score the eligible trades with ONE engine call; resolve the ones that are still allowed.
    A trade whose new score is too high for this action is NOT resolved: its item gets the new
    (higher) priority and the reason is reported."""
    rows = _lock(db, order, user)
    succeeded, failed, eligible = [], {}, []
    for queue_id in order:
        if queue_id not in rows:
            failed[queue_id] = NOT_FOUND
        elif reason := why_not(rows[queue_id][0], action):
            failed[queue_id] = reason
        else:
            eligible.append(queue_id)
    if not eligible:
        return succeeded, failed

    refs, cfg, business_date = trade_service._engine_context(db)  # no existing ids: a trade is not its own duplicate
    scored_list = score_trades([_engine_record(rows[q][1]) for q in eligible], refs, cfg, business_date,
                               engine_adapter.get_predictor())
    now = datetime.now(timezone.utc)
    audits = []
    for queue_id, scored in zip(eligible, scored_list):
        item, trade = rows[queue_id]
        if scored.status == "REJECTED":  # e.g. the account was deactivated after booking
            failed[queue_id] = ("Trade no longer passes validation: "
                                + "; ".join(e.message for e in scored.rejection_reasons))
            continue
        before, raw_payload = trade_service._state(trade), trade.raw_payload
        model_version_id = trade_service._model_version_id(db, scored.model_version)
        trade_service._apply_scores(trade, scored, model_version_id)
        trade.raw_payload = raw_payload  # keep what was originally received
        trade.version += 1
        trade.updated_by, trade.updated_at = user.id, now
        trade_service._save_scoring_rows(db, trade, scored, cfg, model_version_id)
        item.priority, item.suggested_fixes, item.updated_at = scored.priority, scored.suggested_fixes, now

        escalated = scored.priority not in ALLOWED_PRIORITIES[action]
        if escalated:
            reason = (f"Re-scoring gave {scored.status} ({scored.final_score:.2f}): queue item {queue_id} is now "
                      f"{scored.priority} priority and was not resolved")
            failed[queue_id] = reason
        else:
            item.status = TRANSITIONS[(item.status, action)]
            item.resolved_by, item.resolved_at, item.resolution_note = user.id, now, "Re-submitted"
            trade.is_resolved = True
            reason = "Re-submitted from the repair queue"
            succeeded.append(queue_id)
        db.flush()
        snapshot = trade_service._to_result(trade_service._load_row(db, trade.id), rule_hits=trade_service._engine_hits(scored),
                                            shap_values=scored.shap_values, warnings=scored.warnings)
        trade_service._add_version(db, trade, snapshot, "RESUBMITTED", user, None, reason)
        state = dict(before_state=before, after_state=trade_service._state(trade))
        if escalated:
            audits.append(_audit("TRADE_SCORED", "TRADE", trade, item, user, ip_address, **state,
                                 details={"reason": "re-submit refused: priority raised by re-scoring"}))
        else:
            audits.append(_audit("TRADE_RESUBMITTED", "TRADE", trade, item, user, ip_address, **state))
            audits.append(_audit("QUEUE_RESOLVED", "REPAIR_QUEUE", trade, item, user, details={"reason": "re-submitted"}))
    audit_service.log_many(db, audits)
    db.commit()
    logger.info("Queue %s by %s: %d resolved, %d not", action, user.username, len(succeeded), len(failed))
    return succeeded, failed


def resubmit(db: Session, user: User, queue_id: str, ip_address: str | None) -> QueueItemOut:
    _, failed = _resubmit(db, user, [queue_id], "RESUBMIT", ip_address)
    if queue_id in failed:
        if failed[queue_id] == NOT_FOUND:
            raise NotFoundError(f"Queue item {queue_id} not found")
        raise ConflictError(failed[queue_id])
    return get_item(db, queue_id, user)


def bulk_resubmit(db: Session, user: User, queue_ids: list[str], ip_address: str | None) -> BulkResult:
    order = _unique(queue_ids)
    succeeded, failed = _resubmit(db, user, order, "BULK_RESUBMIT", ip_address)
    return _result(succeeded, failed, order)


# ------------------------------------------------------------------ reject / approve (endpoints 26-27)
def reject(db: Session, user: User, queue_id: str, reason: str, ip_address: str | None) -> QueueItemOut:
    item, trade = _lock_one(db, queue_id, user)
    item.status = next_status(item, "REJECT")
    now = datetime.now(timezone.utc)
    before, old_version = trade_service._state(trade), trade.version
    item.resolved_by, item.resolved_at, item.resolution_note, item.updated_at = user.id, now, reason, now

    trade.status, trade.priority = "REJECTED", None
    trade.rejection_reasons = [{"ruleId": "MANUAL", "field": None, "message": reason}]
    trade.version += 1
    trade.updated_by, trade.updated_at = user.id, now
    # carry the triggered rules over to the new version, so the trade page still shows them
    for hit in db.scalars(select(RuleEvaluation).where(RuleEvaluation.trade_pk == trade.id,
                                                       RuleEvaluation.trade_version == old_version)).all():
        db.add(RuleEvaluation(trade_pk=trade.id, trade_version=trade.version, rule_id=hit.rule_id,
                              rule_category=hit.rule_category, triggered=hit.triggered,
                              score_contribution=hit.score_contribution, message=hit.message))
    db.flush()
    snapshot = trade_service._to_result(trade_service._load_row(db, trade.id))
    trade_service._add_version(db, trade, snapshot, "REJECTED", user,
                               {"status": {"old": before["status"], "new": "REJECTED"}}, reason)
    state = dict(before_state=before, after_state=trade_service._state(trade))
    audit_service.log_many(db, [
        _audit("QUEUE_REJECTED", "REPAIR_QUEUE", trade, item, user, ip_address, details={"reason": reason}),
        _audit("TRADE_REJECTED", "TRADE", trade, item, user, **state, details={"reason": reason, "source": "REPAIR_QUEUE"}),
    ])
    db.commit()
    logger.info("Queue item %s rejected by %s", queue_id, user.username)
    return get_item(db, queue_id, user)


def approve(db: Session, user: User, queue_id: str, note: str, ip_address: str | None) -> QueueItemOut:
    item, trade = _lock_one(db, queue_id, user)
    item.status = next_status(item, "APPROVE")
    now = datetime.now(timezone.utc)
    item.resolved_by, item.resolved_at, item.resolution_note, item.updated_at = user.id, now, note, now
    trade.is_resolved, trade.updated_by, trade.updated_at = True, user.id, now
    audit_service.log_many(db, [
        _audit("HIGH_RESOLUTION_APPROVED", "REPAIR_QUEUE", trade, item, user, ip_address, details={"note": note}),
        _audit("QUEUE_RESOLVED", "REPAIR_QUEUE", trade, item, user, details={"reason": "approved by manager"}),
    ])
    db.commit()
    logger.info("Queue item %s approved by %s", queue_id, user.username)
    return get_item(db, queue_id, user)


# ------------------------------------------------------------------ team performance (endpoint 29)
def team_performance(db: Session) -> list[TeamMemberPerformance]:
    """One row per Ops User. avgResolutionHours = resolved_at - assigned_at over the items they resolved."""
    members = db.execute(select(User.id, User.username, User.full_name).join(Role, Role.id == User.role_id)
                         .where(Role.code == "OPS_USER").order_by(User.full_name)).all()
    assigned = {user_id: (total, in_progress) for user_id, total, in_progress in db.execute(
        select(RepairQueueItem.assigned_to, func.count(),
               func.count().filter(RepairQueueItem.status == IN_PROGRESS))
        .where(RepairQueueItem.assigned_to.is_not(None)).group_by(RepairQueueItem.assigned_to))}
    seconds = extract("epoch", RepairQueueItem.resolved_at - RepairQueueItem.assigned_at)
    resolved = {user_id: (total, avg) for user_id, total, avg in db.execute(
        select(RepairQueueItem.resolved_by, func.count(), func.avg(seconds))
        .where(RepairQueueItem.status == RESOLVED, RepairQueueItem.resolved_by.is_not(None))
        .group_by(RepairQueueItem.resolved_by))}
    rows = []
    for user_id, username, full_name in members:
        total, in_progress = assigned.get(user_id, (0, 0))
        done, avg = resolved.get(user_id, (0, None))
        rows.append(TeamMemberPerformance(
            user_id=user_id, username=username, full_name=full_name, assigned=total, in_progress=in_progress,
            resolved=done, avg_resolution_hours=None if avg is None else round(float(avg) / 3600, 2)))
    return rows
