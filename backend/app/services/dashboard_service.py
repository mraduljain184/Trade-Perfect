"""Trade dashboard numbers (endpoints 18-19). Everything is a SQL GROUP BY - no rows are loaded.

A Trader only gets numbers for their own trades (same row-level rule as GET /trades).
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Trade, User
from app.schemas.dashboard import (
    DashboardCharts, DashboardSummary, HistogramBucket, StatusByDate, StatusByName, StatusCounts, StatusPct,
)
from app.services import trade_service

TOP_N = 10
BUCKETS = 10


def _counts(by_status: dict[str, int]) -> StatusCounts:
    total = sum(by_status.values())
    numbers = {name: by_status.get(name.upper(), 0) for name in ("pass", "monitor", "exception", "rejected")}
    pct = {name: round(100 * value / total, 1) if total else 0.0 for name, value in numbers.items()}
    return StatusCounts(total=total, pass_=numbers["pass"], monitor=numbers["monitor"],
                        exception=numbers["exception"], rejected=numbers["rejected"],
                        pct=StatusPct(pass_=pct["pass"], monitor=pct["monitor"],
                                      exception=pct["exception"], rejected=pct["rejected"]))


def summary(db: Session, user: User, day: date | None) -> DashboardSummary:
    """`today` = trades booked on `day` (created_at, in the app time zone); `historical` = all trades."""
    zone = ZoneInfo(get_settings().app_timezone)
    day = day or datetime.now(zone).date()
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = start + timedelta(days=1)
    booked_that_day = func.count().filter(Trade.created_at >= start, Trade.created_at < end)
    stmt = trade_service._scope(select(Trade.status, func.count(), booked_that_day), user).group_by(Trade.status)
    historical, today = {}, {}
    for status, total, on_day in db.execute(stmt):
        historical[status], today[status] = total, on_day
    return DashboardSummary(today=_counts(today), historical=_counts(historical))


def _top_by_exception(db: Session, user: User, column) -> list[StatusByName]:
    stmt = trade_service._scope(select(column, Trade.status, func.count()), user).where(column.is_not(None))
    groups: dict[str, dict[str, int]] = defaultdict(dict)
    for name, status, count in db.execute(stmt.group_by(column, Trade.status)):
        groups[name][status] = count
    rows = [StatusByName(name=name, total=sum(c.values()), pass_=c.get("PASS", 0), monitor=c.get("MONITOR", 0),
                         exception=c.get("EXCEPTION", 0), rejected=c.get("REJECTED", 0)) for name, c in groups.items()]
    rows.sort(key=lambda r: (-r.exception, -r.total, r.name))
    return rows[:TOP_N]


def charts(db: Session, user: User) -> DashboardCharts:
    by_date: dict[date, dict[str, int]] = defaultdict(dict)
    stmt = trade_service._scope(select(Trade.trade_date, Trade.status, func.count()), user)
    for trade_date, status, count in db.execute(stmt.where(Trade.trade_date.is_not(None))
                                                .group_by(Trade.trade_date, Trade.status)):
        by_date[trade_date][status] = count
    over_time = [StatusByDate(date=d, pass_=c.get("PASS", 0), monitor=c.get("MONITOR", 0),
                              exception=c.get("EXCEPTION", 0), rejected=c.get("REJECTED", 0))
                 for d, c in sorted(by_date.items())]

    # REJECTED trades are left out: they all carry the fixed score 1.0 and would hide the real spread.
    bucket = func.least(func.width_bucket(Trade.final_score, 0, 1, BUCKETS), BUCKETS)
    stmt = trade_service._scope(select(bucket, func.count()), user).where(Trade.status != "REJECTED").group_by(bucket)
    filled = dict(db.execute(stmt).all())
    histogram = [HistogramBucket(bucket=f"{(i - 1) / BUCKETS:.1f}-{i / BUCKETS:.1f}", min=(i - 1) / BUCKETS,
                                 max=i / BUCKETS, count=filled.get(i, 0)) for i in range(1, BUCKETS + 1)]

    return DashboardCharts(by_status_over_time=over_time,
                           by_counterparty=_top_by_exception(db, user, Trade.counterparty),
                           by_symbol=_top_by_exception(db, user, Trade.symbol),
                           risk_score_histogram=histogram)
