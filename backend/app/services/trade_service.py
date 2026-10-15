"""Single-trade workflows: validate, book, amend, list, detail, history (endpoints 8-13).

Rules of this module:
  * Scoring/validation is NEVER done here - it always calls the engine (app.engine).
  * Each write is ONE transaction: trade + version + rule evaluations + prediction + queue item + audit.
  * Row-level scoping lives here: a Trader only sees / amends their own trades.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, case, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.core.errors import AppError, BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.engine import EngineConfig, ScoredTrade, ValidationResult, score_trades, validate_trade
from app.models import (
    MLPrediction, ModelVersion, RepairQueueItem, RuleEvaluation, Trade, TradeVersion, UploadBatch, User,
)
from app.schemas.trade import AuditBoxOut, TradeDetail, TradeInput, TradeResult, TradeVersionOut
from app.services import audit_service, engine_adapter

logger = logging.getLogger("app.trades")

STATUSES = ("PASS", "MONITOR", "EXCEPTION", "REJECTED")
PRIORITIES = ("HIGH", "MEDIUM", "LOW")
CLOSED_QUEUE_STATUSES = ("RESOLVED", "REJECTED")

# API field name -> Trade attribute, for the 10 fields a user can edit
INPUT_FIELDS = {
    "tradeType": "trade_type", "symbol": "symbol", "quantity": "quantity", "price": "price",
    "counterparty": "counterparty", "tradeDate": "trade_date", "settlementDate": "settlement_date",
    "accountId": "account_id", "ssiMatched": "ssi_matched", "currencyMatched": "currency_matched",
}
SORT_COLUMNS = {
    "tradeId": Trade.trade_id, "finalScore": Trade.final_score, "ruleScore": Trade.rule_score,
    "mlScore": Trade.ml_score, "status": Trade.status, "priority": Trade.priority,
    "tradeDate": Trade.trade_date, "settlementDate": Trade.settlement_date, "symbol": Trade.symbol,
    "counterparty": Trade.counterparty, "quantity": Trade.quantity, "price": Trade.price,
    "traderName": Trade.trader_name, "createdAt": Trade.created_at, "updatedAt": Trade.updated_at,
}

_Assignee = aliased(User)
_Creator = aliased(User)


@dataclass
class TradeFilters:
    status: list[str] = field(default_factory=list)
    priority: list[str] = field(default_factory=list)
    trade_date_from: date | None = None
    trade_date_to: date | None = None
    settlement_date_from: date | None = None
    settlement_date_to: date | None = None
    search: str | None = None
    trader_name: str | None = None
    counterparty: str | None = None
    batch_id: str | None = None
    mine: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], False)}


def parse_multi(value: str | None, allowed: tuple[str, ...], name: str) -> list[str]:
    """'EXCEPTION,MONITOR' -> ['EXCEPTION', 'MONITOR']; unknown values -> 400."""
    if not value:
        return []
    items = [part.strip().upper() for part in value.split(",") if part.strip()]
    bad = [item for item in items if item not in allowed]
    if bad:
        raise BadRequestError(f"Invalid {name}: {', '.join(bad)}. Allowed: {', '.join(allowed)}")
    return items


# ------------------------------------------------------------------ helpers
def _float(value) -> float | None:
    return float(value) if value is not None else None


def _validation_error(errors) -> AppError:
    fields = [{"field": e.field, "ruleId": e.rule_id, "message": e.message} for e in errors]
    return AppError(422, "Trade validation failed", fields=fields)


def _engine_context(db: Session, existing_ids: set[str] | None = None):
    return (engine_adapter.get_reference_data(db, existing_ids), engine_adapter.get_engine_config(db),
            engine_adapter.get_business_date(db))


def _base_select() -> Select:
    return (
        select(Trade, RepairQueueItem, _Assignee.full_name, _Creator.username, ModelVersion.version)
        .outerjoin(RepairQueueItem, RepairQueueItem.trade_pk == Trade.id)
        .outerjoin(_Assignee, _Assignee.id == RepairQueueItem.assigned_to)
        .outerjoin(_Creator, _Creator.id == Trade.created_by)
        .outerjoin(ModelVersion, ModelVersion.id == Trade.model_version_id)
    )


def _own_only(user: User, permission: str) -> bool:
    return user.permission_scope(permission) == "OWN"


def _is_owner(trade: Trade, user: User) -> bool:
    return user.id in (trade.trader_user_id, trade.created_by)


def _scope(stmt: Select, user: User) -> Select:
    if _own_only(user, "TRADE_VIEW"):
        stmt = stmt.where(or_(Trade.trader_user_id == user.id, Trade.created_by == user.id))
    return stmt


def _apply_filters(stmt: Select, filters: TradeFilters, user: User) -> Select:
    if filters.status:
        stmt = stmt.where(Trade.status.in_(filters.status))
    if filters.priority:
        stmt = stmt.where(Trade.priority.in_(filters.priority))
    if filters.trade_date_from:
        stmt = stmt.where(Trade.trade_date >= filters.trade_date_from)
    if filters.trade_date_to:
        stmt = stmt.where(Trade.trade_date <= filters.trade_date_to)
    if filters.settlement_date_from:
        stmt = stmt.where(Trade.settlement_date >= filters.settlement_date_from)
    if filters.settlement_date_to:
        stmt = stmt.where(Trade.settlement_date <= filters.settlement_date_to)
    if filters.search:
        pattern = f"%{filters.search.strip()}%"
        stmt = stmt.where(or_(Trade.trade_id.ilike(pattern), Trade.symbol.ilike(pattern)))
    if filters.trader_name:
        stmt = stmt.where(Trade.trader_name.ilike(filters.trader_name.strip()))
    if filters.counterparty:
        stmt = stmt.where(Trade.counterparty.ilike(filters.counterparty.strip()))
    if filters.batch_id:
        stmt = stmt.where(Trade.batch_id.in_(select(UploadBatch.id).where(UploadBatch.batch_id == filters.batch_id)))
    if filters.mine:
        stmt = stmt.where(or_(Trade.trader_user_id == user.id, Trade.created_by == user.id))
    return stmt


def _order_by(sort: str | None) -> list:
    clauses = []
    for part in (sort or "createdAt:desc").split(","):
        name, _, direction = part.strip().partition(":")
        direction = (direction or "asc").lower()
        if name not in SORT_COLUMNS or direction not in ("asc", "desc"):
            raise BadRequestError(f"Invalid sort '{part.strip()}'. Use field:asc|desc with one of: {', '.join(SORT_COLUMNS)}")
        column = SORT_COLUMNS[name]
        clauses.append((column.desc() if direction == "desc" else column.asc()).nulls_last())
    clauses.append(Trade.id.asc())  # stable order between pages
    return clauses


def _to_result(row, *, rule_hits=None, shap_values=None, warnings=None) -> TradeResult:
    trade, item, assignee_name, creator, model_version = row
    return TradeResult(
        id=str(trade.id), trade_id=trade.trade_id, trade_type=trade.trade_type, symbol=trade.symbol,
        quantity=trade.quantity, price=_float(trade.price), counterparty=trade.counterparty,
        trade_date=trade.trade_date, settlement_date=trade.settlement_date, account_id=trade.account_id,
        ssi_matched=trade.ssi_matched, currency_matched=trade.currency_matched, trader_name=trade.trader_name,
        source=trade.source, status=trade.status, priority=trade.priority,
        rule_score=_float(trade.rule_score), ml_score=_float(trade.ml_score), final_score=float(trade.final_score),
        risk_factors=trade.risk_factors or [], likely_reason=trade.likely_reason,
        suggested_action=trade.suggested_action, explanation=trade.explanation,
        rejection_reasons=trade.rejection_reasons, rule_hits=rule_hits, shap_values=shap_values, warnings=warnings,
        queue_id=item.queue_id if item else None, queue_status=item.status if item else None,
        assigned_to=assignee_name, is_resolved=trade.is_resolved, version=trade.version,
        model_version=model_version, created_at=trade.created_at, updated_at=trade.updated_at, created_by=creator,
    )


def _load_row(db: Session, trade_pk):
    return db.execute(_base_select().where(Trade.id == trade_pk)).one()


def _engine_hits(scored: ScoredTrade) -> list[dict]:
    return [{"rule_id": h.rule_id, "category": h.category, "triggered": h.triggered,
             "contribution": h.contribution, "message": h.message} for h in scored.rule_hits]


def _apply_scores(trade: Trade, scored: ScoredTrade, model_version_id: int | None) -> None:
    trade.trade_type, trade.symbol = scored.trade_type, scored.symbol
    trade.quantity, trade.price = scored.quantity, Decimal(str(scored.price))
    trade.counterparty, trade.account_id = scored.counterparty, scored.account_id
    trade.trade_date, trade.settlement_date = scored.trade_date, scored.settlement_date
    trade.ssi_matched, trade.currency_matched = scored.ssi_matched, scored.currency_matched
    trade.raw_payload = audit_service._plain_json(scored.raw)
    trade.status, trade.priority = scored.status, scored.priority
    trade.rule_score, trade.ml_score, trade.final_score = scored.rule_score, scored.ml_score, scored.final_score
    trade.risk_factors = scored.risk_factors
    trade.likely_reason, trade.suggested_action = scored.likely_reason, scored.suggested_action
    trade.explanation = scored.explanation
    trade.rejection_reasons = None
    trade.model_version_id = model_version_id


def _model_version_id(db: Session, version: str | None) -> int | None:
    if not version:
        return None
    found = db.scalar(select(ModelVersion.id).where(ModelVersion.version == version))
    if found is None:
        logger.warning("Model version %s is not registered - prediction row not stored", version)
    return found


def _save_scoring_rows(db: Session, trade: Trade, scored: ScoredTrade, cfg: EngineConfig,
                       model_version_id: int | None) -> bool:
    """rule_evaluations (triggered rules only) + one ml_predictions row when a model scored it."""
    hits = [h for h in scored.rule_hits if h.triggered]
    if hits:
        db.add_all(RuleEvaluation(trade_pk=trade.id, trade_version=trade.version, rule_id=h.rule_id,
                                  rule_category=h.category, triggered=True,
                                  score_contribution=h.contribution, message=h.message) for h in hits)
    if scored.ml_score is None or model_version_id is None:
        return False
    db.add(MLPrediction(
        trade_pk=trade.id, trade_version=trade.version, model_version_id=model_version_id,
        features=scored.features or {}, rule_score=scored.rule_score, ml_score=scored.ml_score,
        final_score=scored.final_score, alpha=cfg.alpha, beta=cfg.beta, classification=scored.status,
        shap_values=scored.shap_values))
    return True


def _audit_fields(trade: Trade, item: RepairQueueItem | None) -> dict[str, Any]:
    return dict(
        entity_id=trade.trade_id, trade_pk=trade.id, trade_id=trade.trade_id,
        queue_id=item.queue_id if item else None, trade_date=trade.trade_date,
        settlement_date=trade.settlement_date, risk_score=trade.final_score, status_after=trade.status,
        likely_reason=trade.likely_reason, recommended_fix=trade.suggested_action,
    )


def _state(trade: Trade) -> dict[str, Any]:
    state = {api: getattr(trade, attr) for api, attr in INPUT_FIELDS.items()}
    state.update(status=trade.status, priority=trade.priority, finalScore=_float(trade.final_score), version=trade.version)
    return state


def _new_queue_item(db: Session, trade: Trade, scored: ScoredTrade) -> RepairQueueItem:
    item = RepairQueueItem(trade_pk=trade.id, priority=scored.priority, status="OPEN",
                           risk_score_at_entry=scored.final_score, suggested_fixes=scored.suggested_fixes)
    db.add(item)
    db.flush()
    db.refresh(item)  # queue_id (RQ-000001) is generated by the database
    return item


def _add_version(db: Session, trade: Trade, snapshot: TradeResult, change_type: str, user: User,
                 changed_fields: dict | None = None, reason: str | None = None) -> None:
    db.add(TradeVersion(
        trade_pk=trade.id, version=trade.version, snapshot=snapshot.model_dump(mode="json", by_alias=True),
        change_type=change_type, changed_fields=audit_service._plain_json(changed_fields),
        change_reason=reason, changed_by=user.id))


# ------------------------------------------------------------------ validate / create
def validate(db: Session, payload: TradeInput, user: User) -> ValidationResult:
    """Dry run for the booking form: no database write."""
    record = payload.engine_record()
    record["traderName"] = user.full_name
    refs, cfg, business_date = _engine_context(db)
    return validate_trade(record, refs, cfg, business_date)


def create_trade(db: Session, payload: TradeInput, user: User, ip_address: str | None) -> TradeResult:
    record = payload.engine_record()
    record["traderName"] = user.full_name  # FR-TB-004: never trust a trader name sent by the client
    refs, cfg, business_date = _engine_context(db)

    check = validate_trade(record, refs, cfg, business_date)
    if not check.valid:
        raise _validation_error(check.errors)  # 422, nothing saved

    trade_id = f"T{db.scalar(text('SELECT nextval(:seq)'), {'seq': 'trade_id_seq'})}"
    record["tradeId"] = trade_id
    refs.existing_trade_ids = engine_adapter.existing_active_trade_ids(db, [trade_id])
    scored = score_trades([record], refs, cfg, business_date, engine_adapter.get_predictor())[0]
    if scored.status == "REJECTED":
        raise _validation_error(scored.rejection_reasons)

    model_version_id = _model_version_id(db, scored.model_version)
    trade = Trade(trade_id=trade_id, trader_name=user.full_name, trader_user_id=user.id, source="UI",
                  version=1, created_by=user.id, updated_by=user.id)
    _apply_scores(trade, scored, model_version_id)
    db.add(trade)
    db.flush()

    has_prediction = _save_scoring_rows(db, trade, scored, cfg, model_version_id)
    item = _new_queue_item(db, trade, scored) if scored.final_score > 0 else None

    result = _to_result(_load_row(db, trade.id), rule_hits=_engine_hits(scored),
                        shap_values=scored.shap_values, warnings=scored.warnings)
    _add_version(db, trade, result, "CREATED", user)

    common = _audit_fields(trade, item)
    entries = [dict(action="TRADE_SUBMITTED", entity_type="TRADE", actor=user, ip_address=ip_address,
                    after_state=_state(trade), details={"source": "UI"}, **common)]
    if has_prediction:
        entries.append(dict(action="ML_PREDICTION", entity_type="TRADE", actor=user, **common,
                            details={"mlScore": scored.ml_score, "modelVersion": scored.model_version}))
    if item:
        entries.append(dict(action="QUEUE_ITEM_CREATED", entity_type="REPAIR_QUEUE", actor=user,
                            **{**common, "entity_id": item.queue_id}, details={"priority": item.priority}))
    audit_service.log_many(db, entries)
    db.commit()
    logger.info("Trade %s booked by %s -> %s %.4f", trade_id, user.username, scored.status, scored.final_score)
    return result


# ------------------------------------------------------------------ amend
def amend_trade(db: Session, trade_id: str, payload: TradeInput, user: User, ip_address: str | None) -> TradeResult:
    trade = db.scalar(select(Trade).where(Trade.trade_id == trade_id, Trade.status != "REJECTED").with_for_update())
    if trade is None:
        if db.scalar(select(func.count()).select_from(Trade).where(Trade.trade_id == trade_id)):
            raise ConflictError(f"Trade {trade_id} is REJECTED and cannot be amended - correct it in the upstream system")
        raise NotFoundError(f"Trade {trade_id} not found")
    if _own_only(user, "TRADE_AMEND") and not _is_owner(trade, user):
        raise NotFoundError(f"Trade {trade_id} not found")

    item = db.scalar(select(RepairQueueItem).where(RepairQueueItem.trade_pk == trade.id).with_for_update())
    is_fix = payload.queue_id is not None
    if is_fix:
        if "FIX_APPLY" not in user.permission_codes:
            raise ForbiddenError("Permission required: FIX_APPLY")
        if item is None or item.queue_id != payload.queue_id:
            raise BadRequestError(f"Queue item {payload.queue_id} does not belong to trade {trade_id}")
        if item.status in CLOSED_QUEUE_STATUSES:
            raise ConflictError(f"Queue item {item.queue_id} is already {item.status}")

    record = payload.engine_record()
    record["tradeId"] = trade.trade_id
    record["traderName"] = trade.trader_name  # the trader of record never changes on amend
    refs, cfg, business_date = _engine_context(db)  # existing ids empty: the trade's own id must not count as a duplicate
    scored = score_trades([record], refs, cfg, business_date, engine_adapter.get_predictor())[0]
    if scored.status == "REJECTED":
        raise _validation_error(scored.rejection_reasons)

    before = _state(trade)
    new_values = {api: getattr(scored, attr) for api, attr in INPUT_FIELDS.items()}
    changed = {}
    for api, new in new_values.items():
        old = before[api]
        same = float(old) == float(new) if api == "price" and old is not None else old == new
        if not same:
            changed[api] = {"old": _float(old) if api == "price" else old, "new": new}
    if not changed:
        raise BadRequestError("Nothing was changed - edit at least one field before updating the trade")

    model_version_id = _model_version_id(db, scored.model_version)
    _apply_scores(trade, scored, model_version_id)
    trade.version += 1
    trade.updated_by, trade.updated_at = user.id, datetime.now(timezone.utc)
    has_prediction = _save_scoring_rows(db, trade, scored, cfg, model_version_id)

    entries: list[dict] = []
    resolved_now = False
    applied_fix = (payload.applied_fix or f"Updated {', '.join(changed)}") if is_fix else None
    if item is None and scored.final_score > 0:
        item = _new_queue_item(db, trade, scored)
        entries.append(dict(action="QUEUE_ITEM_CREATED", entity_type="REPAIR_QUEUE", actor=user,
                            details={"priority": item.priority, "reason": "risk appeared after amendment"}))
    elif item is not None and item.status not in CLOSED_QUEUE_STATUSES:
        item.priority = scored.priority
        item.suggested_fixes = scored.suggested_fixes
        item.updated_at = trade.updated_at
        if is_fix:
            item.applied_fix, item.applied_by, item.applied_at = applied_fix, user.id, trade.updated_at
        if scored.final_score == 0:  # nothing left to repair
            item.status, item.resolved_by, item.resolved_at = "RESOLVED", user.id, trade.updated_at
            item.resolution_note = "Risk score is 0 after the update"
            trade.is_resolved = resolved_now = True
    elif item is not None and item.status == "RESOLVED" and scored.final_score > 0:
        # One queue item per trade: a resolved trade that is amended and still carries risk
        # gets its item back to OPEN instead of a second item.
        from app.services import repair_queue_service  # local import: that module imports this one

        repair_queue_service.reopen(item, scored.priority, scored.suggested_fixes, trade.updated_at)
        trade.is_resolved = False
        entries.append(dict(action="QUEUE_ITEM_CREATED", entity_type="REPAIR_QUEUE", actor=user,
                            details={"priority": item.priority, "reason": "re-opened: risk remains after an amendment"}))
    db.flush()

    result = _to_result(_load_row(db, trade.id), rule_hits=_engine_hits(scored),
                        shap_values=scored.shap_values, warnings=scored.warnings)
    change_type = "FIX_APPLIED" if is_fix else "AMENDED"
    _add_version(db, trade, result, change_type, user, changed, payload.change_reason)

    common = _audit_fields(trade, item)
    main = dict(action="FIX_APPLIED" if is_fix else "TRADE_AMENDED", entity_type="TRADE", actor=user,
                ip_address=ip_address, before_state=before, after_state=_state(trade), applied_fix=applied_fix,
                details={"changedFields": changed, "changeReason": payload.change_reason}, **common)
    queue_common = {**common, "entity_id": item.queue_id} if item else common
    entries = [main] + [{**queue_common, **e} for e in entries]
    if has_prediction:
        entries.append(dict(action="ML_PREDICTION", entity_type="TRADE", actor=user, **common,
                            details={"mlScore": scored.ml_score, "modelVersion": scored.model_version}))
    if resolved_now:
        entries.append(dict(action="QUEUE_RESOLVED", entity_type="REPAIR_QUEUE", actor=user, **queue_common,
                            details={"reason": "score 0 after update"}))
    audit_service.log_many(db, entries)
    db.commit()
    logger.info("Trade %s %s by %s -> v%d %s %.4f", trade_id, change_type, user.username, trade.version,
                scored.status, scored.final_score)
    return result


# ------------------------------------------------------------------ read
def list_trades(db: Session, user: User, filters: TradeFilters, page: int, page_size: int,
                sort: str | None) -> tuple[list[TradeResult], int]:
    order = _order_by(sort)
    count_stmt = _apply_filters(_scope(select(func.count()).select_from(Trade), user), filters, user)
    total = db.scalar(count_stmt) or 0
    stmt = _apply_filters(_scope(_base_select(), user), filters, user)
    rows = db.execute(stmt.order_by(*order).offset((page - 1) * page_size).limit(page_size)).all()
    return [_to_result(row) for row in rows], total


def iter_trades(db: Session, user: User, filters: TradeFilters, sort: str | None):
    """Every trade matching the filters (no paging) - used by the CSV export."""
    stmt = _apply_filters(_scope(_base_select(), user), filters, user).order_by(*_order_by(sort))
    for row in db.execute(stmt.execution_options(yield_per=1000)):
        yield _to_result(row)


def _find_row(db: Session, trade_id: str, user: User):
    """The non-rejected trade with this ID first; otherwise the latest rejected one."""
    stmt = (_scope(_base_select(), user).where(Trade.trade_id == trade_id)
            .order_by(case((Trade.status == "REJECTED", 1), else_=0), Trade.created_at.desc()).limit(1))
    row = db.execute(stmt).first()
    if row is None:
        raise NotFoundError(f"Trade {trade_id} not found")
    return row


def get_trade(db: Session, trade_id: str, user: User) -> TradeDetail:
    row = _find_row(db, trade_id, user)
    trade, item = row[0], row[1]
    hits = db.scalars(select(RuleEvaluation).where(
        RuleEvaluation.trade_pk == trade.id, RuleEvaluation.trade_version == trade.version).order_by(RuleEvaluation.id)).all()
    rule_hits = [{"rule_id": h.rule_id, "category": h.rule_category, "triggered": h.triggered,
                  "contribution": float(h.score_contribution), "message": h.message} for h in hits]
    shap = db.scalar(select(MLPrediction.shap_values).where(MLPrediction.trade_pk == trade.id)
                     .order_by(MLPrediction.id.desc()).limit(1))
    versions = db.scalar(select(func.count()).select_from(TradeVersion).where(TradeVersion.trade_pk == trade.id)) or 0
    applied_by = db.scalar(select(User.full_name).where(User.id == item.applied_by)) if item and item.applied_by else None
    audit_box = AuditBoxOut(
        trade_id=trade.trade_id, queue_id=item.queue_id if item else None, risk_score=float(trade.final_score),
        likely_reason=trade.likely_reason, recommended_fix=trade.suggested_action,
        applied_fix=item.applied_fix if item else None, applied_by=applied_by,
        applied_timestamp=item.applied_at if item else None, status=trade.status)
    base = _to_result(row, rule_hits=rule_hits, shap_values=shap)
    return TradeDetail(**base.model_dump(), versions_count=versions, audit_box=audit_box)


def get_history(db: Session, trade_id: str, user: User) -> list[TradeVersionOut]:
    trade = _find_row(db, trade_id, user)[0]
    rows = db.execute(
        select(TradeVersion, User.username).outerjoin(User, User.id == TradeVersion.changed_by)
        .where(TradeVersion.trade_pk == trade.id).order_by(TradeVersion.version.desc())).all()
    return [TradeVersionOut(version=v.version, change_type=v.change_type, changed_fields=v.changed_fields,
                            change_reason=v.change_reason, changed_by=username, changed_at=v.changed_at,
                            snapshot=v.snapshot) for v, username in rows]
