"""Bulk upload (endpoints 15-17, FR-TB-002/007/009).

Flow:  POST /trades/bulk saves the file + creates an upload_batches row and answers 202 at once.
`process_batch` then runs as a background task with ITS OWN database session:

    read CSV -> for each chunk of `batch_size` rows: ONE engine call (score_trades) + bulk inserts
    (trades, versions, rule evaluations, predictions, queue items, audit) + ONE commit
    -> summary, rejection report, e-mail outbox row, notification.

Rules of this module:
  * Scoring/validation is NEVER done here - it always calls the engine (app.engine).
  * A chunk is all-or-nothing. If one fails, the batch becomes FAILED; earlier chunks stay saved.
"""
import logging
import os
import tempfile
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR, get_settings
from app.core.database import SessionLocal
from app.core.errors import BadRequestError, NotFoundError
from app.engine import EngineConfig, ScoredTrade, score_trades
from app.models import (
    EmailOutbox, MLPrediction, Notification, RepairQueueItem, RuleEvaluation, Trade, TradeVersion, UploadBatch, User,
)
from app.schemas.trade import TradeResult
from app.schemas.upload import UploadBatchOut, UploadSummary
from app.services import audit_service, email_service, engine_adapter, export_service, trade_service

logger = logging.getLogger("app.uploads")

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_BATCH_SIZE = 10_000                  # FR-TB-007: at most 10K rows are processed per chunk
REPORT_DIR = BACKEND_DIR / "reports"     # rejection reports attached to e-mails (not committed)
BATCH_ID_LOCK_KEY = 44                   # advisory locks: 42 = audit chain, 43 = one chunk at a time
CHUNK_LOCK_KEY = 43
OPEN_STATUSES = ("PENDING", "PROCESSING")

REJECTION_COLUMNS = [
    "File Line", "Trade ID", "Rule IDs", "Rejection Reasons", "Trade Type", "Symbol", "Quantity", "Price",
    "Counterparty", "Trade Date", "Settlement Date", "Account ID", "SSI Matched", "Currency Matched", "Trader Name",
]
_RAW_KEYS = ("tradetype", "symbol", "quantity", "price", "counterparty", "tradedate", "settlementdate",
             "accountid", "ssimatched", "currencymatched", "tradername")


class UploadError(Exception):
    """A problem with the file itself; the message is safe to show to the user."""


# ------------------------------------------------------------------ request side (endpoint 15)
def save_upload(file_name: str | None, stream: BinaryIO) -> tuple[str, Path]:
    """Copy the uploaded file to a temp file (max 20 MB). Returns (clean file name, temp path)."""
    name = Path(file_name or "").name.strip()
    if not name.lower().endswith(".csv"):
        raise BadRequestError("Only .csv files are supported")
    handle, temp_name = tempfile.mkstemp(prefix="tp_upload_", suffix=".csv")
    size = 0
    try:
        with os.fdopen(handle, "wb") as out:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise BadRequestError("File is larger than the 20 MB limit")
                out.write(chunk)
        if size == 0:
            raise BadRequestError("The file is empty")
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return name[:255], Path(temp_name)


def create_batch(db: Session, file_name: str, user: User, ip_address: str | None) -> UploadBatch:
    """New upload_batches row with the next BATCH-YYYYMMDD-NNNN id of the day."""
    today = datetime.now(ZoneInfo(get_settings().app_timezone))
    prefix = f"BATCH-{today:%Y%m%d}-"
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": BATCH_ID_LOCK_KEY})
    last = db.scalar(select(func.max(UploadBatch.batch_id)).where(UploadBatch.batch_id.like(f"{prefix}%")))
    number = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    batch = UploadBatch(batch_id=f"{prefix}{number:04d}", file_name=file_name, uploaded_by=user.id, status="PENDING")
    db.add(batch)
    db.flush()
    db.refresh(batch)
    audit_service.log(db, "BULK_UPLOAD_STARTED", "BATCH", actor=user, entity_id=batch.batch_id,
                      ip_address=ip_address, details={"fileName": file_name})
    db.commit()
    logger.info("Upload %s created by %s (%s)", batch.batch_id, user.username, file_name)
    return batch


# ------------------------------------------------------------------ read side (endpoints 16-17)
def get_batch(db: Session, batch_id: str, user: User) -> UploadBatch:
    """Uploaders see their own batches; users who may view ALL trades (Sys Admin) see every batch."""
    batch = db.scalar(select(UploadBatch).where(UploadBatch.batch_id == batch_id))
    if batch is None or (batch.uploaded_by != user.id and user.permission_scope("TRADE_VIEW") != "ALL"):
        raise NotFoundError(f"Upload batch {batch_id} not found")
    return batch


def to_out(db: Session, batch: UploadBatch) -> UploadBatchOut:
    counts = dict(db.execute(select(Trade.status, func.count()).where(Trade.batch_id == batch.id)
                             .group_by(Trade.status)).all())
    summary = UploadSummary(
        accepted=sum(n for status, n in counts.items() if status != "REJECTED"), pass_=counts.get("PASS", 0),
        monitor=counts.get("MONITOR", 0), exception=counts.get("EXCEPTION", 0), rejected=counts.get("REJECTED", 0))
    return UploadBatchOut(
        batch_id=batch.batch_id, file_name=batch.file_name, status=batch.status, total_rows=batch.total_rows,
        processed_rows=batch.processed_rows, summary=summary, started_at=batch.started_at,
        completed_at=batch.completed_at, error_message=batch.error_message)


def _rejection_rows(db: Session, batch_pk: int) -> Iterator[list]:
    stmt = (select(Trade.batch_row_number, Trade.trade_id, Trade.rejection_reasons, Trade.raw_payload)
            .where(Trade.batch_id == batch_pk, Trade.status == "REJECTED").order_by(Trade.batch_row_number))
    for row_number, trade_id, reasons, raw in db.execute(stmt.execution_options(yield_per=2000)):
        # the file may use "Trade ID" or "tradeId": compare header names without spaces / case
        original = {"".join(ch for ch in str(key).lower() if ch.isalnum()): value for key, value in (raw or {}).items()}
        reasons = reasons or []
        yield [
            (row_number or 0) + 1,  # +1 for the header line, so it matches the line number in the file
            original.get("tradeid") or trade_id,
            ", ".join(dict.fromkeys(r.get("ruleId") or "" for r in reasons)),
            "; ".join(r.get("message") or "" for r in reasons),
            *[original.get(key) for key in _RAW_KEYS],
        ]


def rejection_report(db: Session, batch: UploadBatch) -> tuple[list[str], str]:
    """(csv text chunks, file name) with every REJECTED row of the batch and why it was rejected."""
    chunks = ["﻿"] + list(export_service.to_csv_chunks(REJECTION_COLUMNS, _rejection_rows(db, batch.id)))
    return chunks, f"rejections_{batch.batch_id}.csv"


# ------------------------------------------------------------------ background task
def _read_records(path: Path) -> list[dict[str, str]]:
    """Every cell as text (the ENGINE parses numbers/dates/booleans, so bad values become rejections)."""
    for encoding in ("utf-8-sig", "cp1252"):  # utf-8-sig also removes the BOM Excel adds
        try:
            frame = pd.read_csv(path, dtype=str, encoding=encoding, keep_default_na=False, na_filter=False,
                                skip_blank_lines=True)
        except UnicodeDecodeError:
            continue
        except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
            raise UploadError(f"The file could not be read as CSV: {exc}") from exc
        frame.columns = [str(column).strip() for column in frame.columns]
        return frame.to_dict("records")
    raise UploadError("The file is not valid UTF-8 text - save it as 'CSV UTF-8' and upload it again")


def _text(value: Any, limit: int) -> str | None:
    return None if value is None else str(value)[:limit]


def _trade_columns(scored: ScoredTrade) -> dict[str, Any]:
    """Typed trade columns. A REJECTED row may hold anything, so values that cannot be stored in
    the column are left empty - the original text is always kept in raw_payload."""
    quantity = scored.quantity
    if isinstance(quantity, bool) or not isinstance(quantity, int) or abs(quantity) >= 2 ** 63:
        quantity = None
    try:
        price = Decimal(str(scored.price)) if scored.price is not None else None
        if price is not None and (not price.is_finite() or abs(price) >= Decimal(10) ** 14):
            price = None
    except InvalidOperation:
        price = None

    def as_date(value):
        if isinstance(value, datetime):
            return value.date()
        return value if isinstance(value, date) else None

    return {
        "trade_id": _text(scored.trade_id, 30),
        "trade_type": scored.trade_type if scored.trade_type in ("BUY", "SELL") else None,
        "symbol": _text(scored.symbol, 20), "quantity": quantity, "price": price,
        "counterparty": _text(scored.counterparty, 100),
        "trade_date": as_date(scored.trade_date), "settlement_date": as_date(scored.settlement_date),
        "account_id": _text(scored.account_id, 20), "trader_name": _text(scored.trader_name, 100),
        "ssi_matched": scored.ssi_matched if isinstance(scored.ssi_matched, bool) else None,
        "currency_matched": scored.currency_matched if isinstance(scored.currency_matched, bool) else None,
    }


def _save_chunk(db: Session, batch: UploadBatch, user: User, results: list[ScoredTrade], offset: int,
                cfg: EngineConfig, trader_ids: dict[str, int]) -> Counter:
    """Bulk-insert everything one chunk produced. The caller commits."""
    now = datetime.now(timezone.utc)
    model_ids: dict[str | None, int | None] = {None: None}
    trades, hits, predictions, queue_rows, versions, audits = [], [], [], [], [], []
    snapshots: list[tuple[dict, ScoredTrade]] = []

    for scored in results:
        if scored.model_version not in model_ids:
            model_ids[scored.model_version] = trade_service._model_version_id(db, scored.model_version)
        model_version_id = model_ids[scored.model_version]
        rejected = scored.status == "REJECTED"
        reasons = [{"ruleId": e.rule_id, "field": e.field, "message": e.message} for e in scored.rejection_reasons]
        row = {
            "id": uuid.uuid4(), **_trade_columns(scored),
            "trader_user_id": trader_ids.get((scored.trader_name or "").strip().lower()),
            "raw_payload": audit_service._plain_json(scored.raw),
            "source": "FILE", "batch_id": batch.id, "batch_row_number": offset + scored.row_index + 1,
            "status": scored.status, "priority": scored.priority, "rule_score": scored.rule_score,
            "ml_score": scored.ml_score, "final_score": scored.final_score,
            "risk_factors": scored.risk_factors or [], "likely_reason": scored.likely_reason,
            "suggested_action": scored.suggested_action, "explanation": scored.explanation,
            "rejection_reasons": reasons if rejected else None, "model_version_id": model_version_id,
            "version": 1, "is_resolved": False, "created_by": user.id, "created_at": now,
            "updated_by": user.id, "updated_at": now,
        }
        trades.append(row)
        snapshots.append((row, scored))

        if rejected:  # failed hard gates are stored as triggered rules too (section 6.3)
            hits += [{"trade_pk": row["id"], "trade_version": 1, "rule_id": e.rule_id[:10], "rule_category": "HARD_GATE",
                      "triggered": True, "score_contribution": 0, "message": e.message} for e in scored.rejection_reasons]
            continue
        hits += [{"trade_pk": row["id"], "trade_version": 1, "rule_id": h.rule_id, "rule_category": h.category,
                  "triggered": True, "score_contribution": h.contribution, "message": h.message}
                 for h in scored.rule_hits if h.triggered]
        if scored.ml_score is not None and model_version_id is not None:
            predictions.append({
                "trade_pk": row["id"], "trade_version": 1, "model_version_id": model_version_id,
                "features": scored.features or {}, "rule_score": scored.rule_score, "ml_score": scored.ml_score,
                "final_score": scored.final_score, "alpha": cfg.alpha, "beta": cfg.beta,
                "classification": scored.status, "shap_values": scored.shap_values})
        if scored.final_score > 0:
            queue_rows.append({"trade_pk": row["id"], "priority": scored.priority, "status": "OPEN",
                               "risk_score_at_entry": scored.final_score, "suggested_fixes": scored.suggested_fixes,
                               "created_at": now, "updated_at": now})

    db.execute(insert(Trade), trades)
    if hits:
        db.execute(insert(RuleEvaluation), hits)
    if predictions:
        db.execute(insert(MLPrediction), predictions)
    queue_ids: dict[uuid.UUID, str] = {}
    if queue_rows:  # queue_id (RQ-000001) is generated by the database, so ask for it back
        queue_ids = dict(db.execute(
            insert(RepairQueueItem).returning(RepairQueueItem.trade_pk, RepairQueueItem.queue_id), queue_rows).all())

    for row, scored in snapshots:
        rejected = row["status"] == "REJECTED"
        queue_id = queue_ids.get(row["id"])
        snapshot = TradeResult(
            id=str(row["id"]), trade_id=row["trade_id"], trade_type=row["trade_type"], symbol=row["symbol"],
            quantity=row["quantity"], price=None if row["price"] is None else float(row["price"]),
            counterparty=row["counterparty"], trade_date=row["trade_date"], settlement_date=row["settlement_date"],
            account_id=row["account_id"], ssi_matched=row["ssi_matched"], currency_matched=row["currency_matched"],
            trader_name=row["trader_name"], source="FILE", status=row["status"], priority=row["priority"],
            rule_score=row["rule_score"], ml_score=row["ml_score"], final_score=row["final_score"],
            risk_factors=row["risk_factors"], likely_reason=row["likely_reason"],
            suggested_action=row["suggested_action"], explanation=row["explanation"],
            rejection_reasons=row["rejection_reasons"], shap_values=scored.shap_values,
            warnings=scored.warnings or None, queue_id=queue_id, queue_status="OPEN" if queue_id else None,
            is_resolved=False, version=1, model_version=scored.model_version if row["model_version_id"] else None,
            created_at=now, updated_at=now, created_by=user.username)
        versions.append({"trade_pk": row["id"], "version": 1, "snapshot": snapshot.model_dump(mode="json", by_alias=True),
                         "change_type": "REJECTED" if rejected else "CREATED", "changed_fields": None,
                         "change_reason": None, "changed_by": user.id, "changed_at": now})
        common = dict(
            actor=user, event_time=now, entity_id=row["trade_id"], trade_pk=row["id"], trade_id=row["trade_id"],
            queue_id=queue_id, trade_date=row["trade_date"], settlement_date=row["settlement_date"],
            risk_score=row["final_score"], status_after=row["status"], likely_reason=row["likely_reason"],
            recommended_fix=row["suggested_action"])
        details = {"source": "FILE", "batchId": batch.batch_id, "row": row["batch_row_number"]}
        if rejected:
            details["ruleIds"] = [r["ruleId"] for r in row["rejection_reasons"]]
        audits.append(dict(action="TRADE_REJECTED" if rejected else "TRADE_SUBMITTED", entity_type="TRADE",
                           details=details, **common))
        if queue_id:
            audits.append(dict(action="QUEUE_ITEM_CREATED", entity_type="REPAIR_QUEUE",
                               details={"priority": row["priority"], "batchId": batch.batch_id},
                               **{**common, "entity_id": queue_id}))

    db.execute(insert(TradeVersion), versions)
    audit_service.log_many(db, audits)
    return Counter(row["status"] for row in trades)


def _finish(db: Session, batch: UploadBatch, user: User, seconds: float) -> None:
    """Summary + rejection report + e-mail outbox row + notification, in one transaction."""
    out = to_out(db, batch)
    summary = out.summary.model_dump(by_alias=True)
    batch.status, batch.completed_at = "COMPLETED", datetime.now(timezone.utc)

    if batch.rejected_count:
        chunks, file_name = rejection_report(db, batch)
        attachment = None
        try:  # the report file is a convenience for the e-mail; it must never fail the upload
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            (REPORT_DIR / file_name).write_text("".join(chunks), encoding="utf-8", newline="")
            attachment = f"reports/{file_name}"
        except OSError:
            logger.exception("Could not save rejection report %s", file_name)
        recipients = engine_adapter.get_setting(db, "rejection_report_recipients", []) or []
        if isinstance(recipients, str):
            recipients = [recipients]
        if recipients:
            smtp_ready = bool(get_settings().smtp_host)
            db.add(EmailOutbox(
                to_addresses=", ".join(recipients), purpose="REJECTION_REPORT", attachment_path=attachment,
                subject=f"Trade Perfect: {batch.rejected_count} rejected trades in {batch.batch_id}"[:255],
                body=(f"Upload {batch.batch_id} ({batch.file_name}) by {user.full_name}: {batch.total_rows} rows, "
                      f"{batch.accepted_count} accepted, {batch.rejected_count} rejected.\n"
                      "Rejected trades must be corrected in the upstream system and uploaded again.\n"
                      f"Report: GET /api/v1/uploads/{batch.batch_id}/rejections"),
                status="PENDING" if smtp_ready else "LOGGED_ONLY", created_by=user.id))
            audit_service.log(db, "REJECTION_REPORT_SENT", "BATCH", actor=user, entity_id=batch.batch_id,
                              details={"recipients": recipients, "rejected": batch.rejected_count,
                                       "emailStatus": "PENDING" if smtp_ready else "LOGGED_ONLY"})

    audit_service.log(db, "BULK_UPLOAD_COMPLETED", "BATCH", actor=user, entity_id=batch.batch_id,
                      details={"status": "COMPLETED", "totalRows": batch.total_rows, "summary": summary,
                               "seconds": round(seconds, 1)})
    db.add(Notification(
        user_id=user.id, type="REJECTION_REPORT" if batch.rejected_count else "UPLOAD_COMPLETED",
        severity="WARNING" if batch.rejected_count else "INFO", title=f"Upload {batch.batch_id} finished"[:150],
        message=(f"{batch.file_name}: {batch.total_rows} rows - {summary['pass']} PASS, {summary['monitor']} MONITOR, "
                 f"{summary['exception']} EXCEPTION, {summary['rejected']} REJECTED."),
        link=f"/trades/upload?batchId={batch.batch_id}"))
    db.commit()
    logger.info("Upload %s COMPLETED: %d rows in %.1f s (%s)", batch.batch_id, batch.total_rows, seconds, summary)


def _run(db: Session, batch_pk: int, path: Path) -> None:
    started = time.perf_counter()
    batch = db.get(UploadBatch, batch_pk)
    user = db.get(User, batch.uploaded_by)

    records = _read_records(path)
    if not records:
        raise UploadError("The file has a header but no data rows")
    max_rows = int(engine_adapter.get_setting(db, "max_upload_rows", 100_000))
    if len(records) > max_rows:
        raise UploadError(f"The file has {len(records):,} rows; the maximum is {max_rows:,}")
    batch_size = min(max(int(engine_adapter.get_setting(db, "batch_size", MAX_BATCH_SIZE)), 1), MAX_BATCH_SIZE)

    batch.status, batch.total_rows = "PROCESSING", len(records)
    db.commit()

    cfg = engine_adapter.get_engine_config(db)
    business_date = engine_adapter.get_business_date(db)
    trader_ids = {name.strip().lower(): user_id for user_id, name in db.execute(select(User.id, User.full_name))}

    # the header may be "Trade ID", "tradeId" or "trade_id": compare without spaces / case
    id_columns = [key for key in records[0] if "".join(ch for ch in str(key).lower() if ch.isalnum()) == "tradeid"]

    for offset in range(0, len(records), batch_size):
        chunk = records[offset:offset + batch_size]
        chunk_started = time.perf_counter()
        # One chunk at a time across all uploads, so two files with the same Trade ID cannot both be accepted.
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CHUNK_LOCK_KEY})
        ids = [str(record[key]).strip() for record in chunk for key in id_columns if str(record[key]).strip()]
        # IDs accepted by earlier chunks are already committed, so this one query covers them as well.
        refs = engine_adapter.get_reference_data(db, engine_adapter.existing_active_trade_ids(db, ids))
        results = score_trades(chunk, refs, cfg, business_date, engine_adapter.get_predictor())
        counts = _save_chunk(db, batch, user, results, offset, cfg, trader_ids)
        batch.processed_rows += len(chunk)
        batch.rejected_count += counts.get("REJECTED", 0)
        batch.accepted_count += len(chunk) - counts.get("REJECTED", 0)
        db.commit()
        logger.info("Upload %s: rows %d-%d done in %.1f s (%s)", batch.batch_id, offset + 1, offset + len(chunk),
                    time.perf_counter() - chunk_started, dict(counts))

    _finish(db, batch, user, time.perf_counter() - started)


def _mark_failed(batch_pk: int, message: str) -> None:
    """Uses a fresh session: the one that failed may be unusable."""
    try:
        with SessionLocal() as db:
            batch = db.get(UploadBatch, batch_pk)
            if batch is None or batch.status not in OPEN_STATUSES:
                return
            user = db.get(User, batch.uploaded_by)
            batch.status, batch.error_message = "FAILED", message[:2000]
            batch.completed_at = datetime.now(timezone.utc)
            audit_service.log(db, "BULK_UPLOAD_COMPLETED", "BATCH", actor=user, entity_id=batch.batch_id,
                              details={"status": "FAILED", "error": message[:500], "processedRows": batch.processed_rows})
            db.add(Notification(user_id=batch.uploaded_by, type="UPLOAD_FAILED", severity="CRITICAL",
                                title=f"Upload {batch.batch_id} failed"[:150], message=message[:2000],
                                link=f"/trades/upload?batchId={batch.batch_id}"))
            db.commit()
    except Exception:  # noqa: BLE001 - nothing more we can do from a background task
        logger.exception("Could not mark upload batch %s as FAILED", batch_pk)


def process_batch(batch_pk: int, path: Path) -> None:
    """Background task entry point. Never raises."""
    try:
        with SessionLocal() as db:
            _run(db, batch_pk, path)
    except UploadError as exc:
        logger.warning("Upload batch %s rejected: %s", batch_pk, exc)
        _mark_failed(batch_pk, str(exc))
    except Exception:  # noqa: BLE001 - the details go to the log, the user gets a short message
        logger.exception("Upload batch %s failed", batch_pk)
        _mark_failed(batch_pk, "The upload stopped because of an unexpected error. Rows processed before the "
                               "error are saved; fix the file or contact support, then upload the rest again.")
    finally:
        Path(path).unlink(missing_ok=True)
        email_service.send_pending()  # no-op without SMTP; never raises


def fail_interrupted_batches(db: Session) -> int:
    """Called at startup: a batch still PENDING/PROCESSING was cut off by a restart and will never finish."""
    result = db.execute(update(UploadBatch).where(UploadBatch.status.in_(OPEN_STATUSES)).values(
        status="FAILED", completed_at=datetime.now(timezone.utc),
        error_message="The server restarted while this upload was running. Rows processed so far are saved."))
    db.commit()
    return result.rowcount or 0
