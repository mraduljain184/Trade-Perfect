"""Audit trail - the ONLY place that writes to audit_logs (PROJECT_CONTEXT section 9).

Every row stores  hash = sha256(prev_hash + canonical_json(row)).  Changing or removing any old
row breaks every hash after it, which `verify_chain` detects.  Writers take a Postgres advisory
lock (released automatically at commit/rollback) so the chain stays in one strict order.

`log` / `log_many` do NOT commit: the audit row is saved in the same transaction as the business
change it describes (both happen or neither does).
"""
import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from app.models import AuditLog, User

logger = logging.getLogger("app.audit")

AUDIT_LOCK_KEY = 42

# Columns covered by the hash (everything except id, prev_hash, hash).
HASHED_FIELDS = (
    "event_time", "actor_user_id", "actor_username", "actor_role", "action", "entity_type", "entity_id",
    "trade_pk", "trade_id", "queue_id", "trade_date", "settlement_date", "risk_score", "status_after",
    "likely_reason", "recommended_fix", "applied_fix", "before_state", "after_state", "details", "ip_address",
)

ACTIONS = frozenset({
    "LOGIN", "LOGIN_FAILED", "LOGOUT", "TRADE_SUBMITTED", "TRADE_REJECTED", "TRADE_SCORED", "ML_PREDICTION",
    "TRADE_AMENDED", "BULK_UPLOAD_STARTED", "BULK_UPLOAD_COMPLETED", "QUEUE_ITEM_CREATED", "QUEUE_ASSIGNED",
    "QUEUE_REASSIGNED", "QUEUE_UNASSIGNED", "FIX_APPLIED", "TRADE_RESUBMITTED", "QUEUE_RESOLVED",
    "QUEUE_REJECTED", "HIGH_RESOLUTION_APPROVED", "EXPORT_CSV", "AUDIT_REPORT_GENERATED",
    "REJECTION_REPORT_SENT", "RETRAIN_STARTED", "RETRAIN_COMPLETED", "RETRAIN_FAILED", "MODEL_PROMOTED",
    "DRIFT_CHECK_RUN", "DRIFT_ALERT", "CONFIG_CHANGED", "USER_CREATED", "USER_UPDATED", "ROLE_CHANGED",
    "CHAT_QUERY",
})


def _plain_json(value: Any) -> Any:
    """Convert dates/Decimals/UUIDs inside a dict to plain JSON types, exactly as they will be stored."""
    if value is None:
        return None
    return json.loads(json.dumps(value, default=str))


def canonical_row(row: dict[str, Any]) -> dict[str, Any]:
    """Same output whether `row` comes from Python (before insert) or from the DB (when verifying)."""
    out: dict[str, Any] = {}
    for field in HASHED_FIELDS:
        value = row.get(field)
        if value is None:
            out[field] = None
        elif field == "event_time":
            out[field] = value.astimezone(timezone.utc).isoformat(timespec="microseconds")
        elif field == "risk_score":
            out[field] = f"{Decimal(str(value)):.4f}"
        elif isinstance(value, (date, datetime)):
            out[field] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            out[field] = str(value)
        else:
            out[field] = value
    return out


def compute_hash(prev_hash: str | None, row: dict[str, Any]) -> str:
    payload = (prev_hash or "") + json.dumps(canonical_row(row), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _prepare(entry: dict[str, Any]) -> dict[str, Any]:
    entry = dict(entry)
    action = entry.get("action")
    if action not in ACTIONS:
        raise ValueError(f"Unknown audit action: {action!r}")
    if not entry.get("entity_type"):
        raise ValueError("entity_type is required for an audit row")

    actor: User | None = entry.pop("actor", None)
    if actor is not None:
        defaults = {"actor_user_id": actor.id, "actor_username": actor.username,
                    "actor_role": actor.role.code if actor.role else None}
        for key, value in defaults.items():
            if entry.get(key) is None:
                entry[key] = value
    entry["actor_username"] = (entry.get("actor_username") or "SYSTEM")[:50]

    unknown = set(entry) - set(HASHED_FIELDS)
    if unknown:
        raise ValueError(f"Unknown audit fields: {sorted(unknown)}")

    entry.setdefault("event_time", datetime.now(timezone.utc))
    if entry.get("entity_id") is not None:
        entry["entity_id"] = str(entry["entity_id"])[:60]
    if entry.get("risk_score") is not None:
        entry["risk_score"] = round(Decimal(str(entry["risk_score"])), 4)
    for json_field in ("before_state", "after_state", "details"):
        entry[json_field] = _plain_json(entry.get(json_field))
    return {field: entry.get(field) for field in HASHED_FIELDS}


def log_many(db: Session, entries: Iterable[dict[str, Any]]) -> int:
    """Append many audit rows with ONE lock and ONE bulk insert (used by bulk upload). Returns the count."""
    rows = [_prepare(entry) for entry in entries]
    if not rows:
        return 0
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": AUDIT_LOCK_KEY})
    prev_hash = db.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1))
    for row in rows:
        row["prev_hash"] = prev_hash
        row["hash"] = compute_hash(prev_hash, row)
        prev_hash = row["hash"]
    db.execute(insert(AuditLog), rows)
    return len(rows)


def log(db: Session, action: str, entity_type: str, *, actor: User | None = None, **fields: Any) -> None:
    """Append one audit row. Example:

        audit_service.log(db, "LOGIN", "AUTH", actor=user, entity_id=user.id, ip_address=ip)

    Optional fields: actor_username, actor_role, entity_id, trade_pk, trade_id, queue_id, trade_date,
    settlement_date, risk_score, status_after, likely_reason, recommended_fix, applied_fix,
    before_state, after_state, details, ip_address.
    """
    log_many(db, [{"action": action, "entity_type": entity_type, "actor": actor, **fields}])


def verify_chain(db: Session) -> tuple[bool, int | None]:
    """Re-compute every hash in order. Returns (True, None) or (False, id of the first bad row)."""
    prev_hash: str | None = None
    stmt = select(AuditLog).order_by(AuditLog.id).execution_options(yield_per=2000)
    for record in db.scalars(stmt):
        row = {field: getattr(record, field) for field in HASHED_FIELDS}
        if record.prev_hash != prev_hash or record.hash != compute_hash(prev_hash, row):
            logger.error("Audit hash chain broken at id=%s", record.id)
            return False, record.id
        prev_hash = record.hash
    return True, None
