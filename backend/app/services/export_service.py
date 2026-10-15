"""CSV exports. Columns follow the Trade Dashboard grid (FR-RQ-008) plus the optional extras."""
import csv
import io
from collections.abc import Iterable, Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import User
from app.schemas.trade import TradeResult
from app.services import audit_service, trade_service
from app.services.trade_service import TradeFilters

TRADE_COLUMNS = [
    "Trade ID", "Queue ID", "Risk Score", "Status", "Likely Reason", "Suggested Action", "Priority",
    "Risk Factors %", "Explanation", "Symbol", "Counterparty", "Trade Type", "Quantity", "Price",
    "Trade Date", "Settlement Date", "Account ID", "SSI Matched", "Currency Matched", "Trader Name",
    "Rule Score", "ML Score", "Queue Status", "Assigned To", "Version", "Source",
]
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    """Text for one CSV cell. Text starting with = + - @ gets a leading apostrophe so Excel
    shows it as text instead of running it as a formula."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _risk_factors(trade: TradeResult) -> str:
    return "; ".join(f"{f.factor} {f.pct:g}%" for f in trade.risk_factors)


def trade_row(t: TradeResult) -> list:
    return [
        t.trade_id, t.queue_id, f"{t.final_score:.4f}", t.status, t.likely_reason, t.suggested_action, t.priority,
        _risk_factors(t), t.explanation, t.symbol, t.counterparty, t.trade_type, t.quantity, t.price,
        t.trade_date.isoformat() if t.trade_date else None,
        t.settlement_date.isoformat() if t.settlement_date else None,
        t.account_id, t.ssi_matched, t.currency_matched, t.trader_name,
        None if t.rule_score is None else f"{t.rule_score:.4f}", None if t.ml_score is None else f"{t.ml_score:.4f}",
        t.queue_status, t.assigned_to, t.version, t.source,
    ]


def to_csv_chunks(header: list[str], rows: Iterable[list], chunk_rows: int = 500) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    pending = 0
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
        pending += 1
        if pending >= chunk_rows:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
            pending = 0
    yield buffer.getvalue()


QUEUE_COLUMNS = [
    "Queue ID", "Trade ID", "Priority", "Status", "Risk Score", "Likely Reason", "Suggested Fix", "Assigned To",
    "Assigned At", "Applied Fix", "Applied By", "Applied At", "Trade Status", "Symbol", "Counterparty",
    "Trade Date", "Settlement Date", "Trader Name", "Created At", "Updated At",
]


def _stamp(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def queue_row(q) -> list:
    t = q.trade
    return [
        q.queue_id, q.trade_id, q.priority, q.status, f"{q.risk_score:.4f}", t.likely_reason,
        " | ".join(f.fix for f in q.suggested_fixes) or t.suggested_action,
        q.assigned_to.full_name if q.assigned_to else None, _stamp(q.assigned_at), q.applied_fix, q.applied_by,
        _stamp(q.applied_at), t.status, t.symbol, t.counterparty,
        t.trade_date.isoformat() if t.trade_date else None,
        t.settlement_date.isoformat() if t.settlement_date else None,
        t.trader_name, _stamp(q.created_at), _stamp(q.updated_at),
    ]


def export_file_name(prefix: str) -> str:
    today = datetime.now(ZoneInfo(get_settings().app_timezone))
    return f"{prefix}_{today:%Y%m%d}.csv"


def export_trades(db: Session, user: User, filters: TradeFilters, sort: str | None,
                  ip_address: str | None) -> tuple[list[str], str]:
    """Returns (csv text chunks, file name). The same filters as GET /trades, without paging."""
    count = 0

    def rows():
        nonlocal count
        for trade in trade_service.iter_trades(db, user, filters, sort):
            count += 1
            yield trade_row(trade)

    # Built completely while the DB session is open; "\ufeff" (BOM) makes Excel read UTF-8 correctly.
    chunks = ["\ufeff"] + list(to_csv_chunks(TRADE_COLUMNS, rows()))
    audit_service.log(db, "EXPORT_CSV", "TRADE", actor=user, ip_address=ip_address,
                      details={"rows": count, "filters": filters.as_dict(), "export": "trades"})
    db.commit()
    return chunks, export_file_name("trades")


def export_queue(db: Session, user: User, filters, sort: str | None, ip_address: str | None) -> tuple[list[str], str]:
    """Repair queue as CSV (FR-RQ-015): the same filters and row scoping as GET /repair-queue, without paging."""
    from app.services import repair_queue_service  # local import: that module is not needed for trade exports

    count = 0

    def rows():
        nonlocal count
        for item in repair_queue_service.iter_items(db, user, filters, sort):
            count += 1
            yield queue_row(item)

    chunks = ["\ufeff"] + list(to_csv_chunks(QUEUE_COLUMNS, rows()))
    audit_service.log(db, "EXPORT_CSV", "REPAIR_QUEUE", actor=user, ip_address=ip_address,
                      details={"rows": count, "filters": filters.as_dict(), "export": "repair-queue"})
    db.commit()
    return chunks, export_file_name("repair_queue")
