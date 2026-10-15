"""BE-3: dashboard numbers (endpoints 18-19) must equal a plain SQL count."""
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, or_, select

from app.core.config import get_settings
from app.models import SystemConfig, Trade, User
from app.services import engine_adapter, upload_service
from tests.conftest import assert_error_shape

SUMMARY, CHARTS = "/api/v1/dashboard/summary", "/api/v1/dashboard/charts"
TRADER, OPS, MANAGER, ADMIN = "michael.brown", "ops.user1", "ops.manager", "sys.admin"
STATUSES = ("pass", "monitor", "exception", "rejected")
SAMPLE = (
    "Trade ID,Trade Type,Symbol,Quantity,Price,Counterparty,Trade Date,Settlement Date,Account ID,"
    "SSI Matched,Currency Matched,Trader Name\r\n"
    "DSH0001,BUY,MSFT,1000,10250.5,Jefferies,9/29/2026,10/1/2026,ACC1000,YES,YES,Michael Brown\r\n"
    "DSH0002,BUY,MSFT,1000,10250.5,BlackRock,9/29/2026,10/1/2026,ACC1000,NO,YES,Michael Brown\r\n"
    "DSH0003,BUY,MSFT,1000,10250.5,BlackRock,9/29/2026,10/1/2026,ACC1000,NO,NO,Michael Brown\r\n"
    "DSH0004,BUY,MSFT,1000,10250.5,BlackRock,9/29/2026,10/1/2026,ACC1011,NO,NO,Michael Brown\r\n"
)


@pytest.fixture(autouse=True, scope="module")
def _some_trades(client, login, _database):
    """Upload one trade of every status: PASS 0.00, MONITOR 0.60, EXCEPTION 0.75 and a REJECTED one.
    'Today' is pinned to 2026-09-28 meanwhile so the scores do not depend on the real date."""
    from app.core.database import SessionLocal

    def business_date(value):
        with SessionLocal() as session:
            session.get(SystemConfig, "business_date_override").value = value
            session.commit()
        engine_adapter.invalidate_cache()

    business_date("2026-09-28")
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(upload_service, "REPORT_DIR", Path(tempfile.mkdtemp()))
            response = client.post("/api/v1/trades/bulk", files={"file": ("dash.csv", SAMPLE.encode(), "text/csv")},
                                   headers=login(TRADER))
        assert response.status_code == 202, response.text
    finally:
        business_date(None)


def day_bounds(day):
    zone = ZoneInfo(get_settings().app_timezone)
    start = datetime.combine(day, time.min, tzinfo=zone)
    return Trade.created_at >= start, Trade.created_at < start + timedelta(days=1)


def sql_counts(db, *where):
    rows = dict(db.execute(select(Trade.status, func.count()).where(*where).group_by(Trade.status)).all())
    return {"total": sum(rows.values()), **{name: rows.get(name.upper(), 0) for name in STATUSES}}


def without_pct(block):
    return {key: value for key, value in block.items() if key != "pct"}


def test_summary_shape_and_numbers_match_sql(client, login, db):
    today = datetime.now(ZoneInfo(get_settings().app_timezone)).date()
    body = client.get(f"{SUMMARY}?date={today}", headers=login(MANAGER)).json()
    assert set(body) == {"today", "historical"}
    for block in body.values():
        assert set(block) == {"total", "pass", "monitor", "exception", "rejected", "pct"}
        assert set(block["pct"]) == set(STATUSES)
    expected = sql_counts(db)
    assert without_pct(body["historical"]) == expected and all(expected[name] >= 1 for name in STATUSES)
    booked_today = sql_counts(db, *day_bounds(today))
    assert without_pct(body["today"]) == booked_today and booked_today["total"] >= 1
    for name in STATUSES:
        assert body["historical"]["pct"][name] == round(100 * expected[name] / expected["total"], 1)
    assert client.get(SUMMARY, headers=login(MANAGER)).json() == body  # date defaults to today


def test_summary_for_a_day_without_trades_is_all_zero(client, login, db):
    body = client.get(f"{SUMMARY}?date=2020-01-01", headers=login(OPS)).json()
    assert body["today"] == {"total": 0, "pass": 0, "monitor": 0, "exception": 0, "rejected": 0,
                             "pct": {"pass": 0.0, "monitor": 0.0, "exception": 0.0, "rejected": 0.0}}
    assert body["historical"]["total"] == sql_counts(db)["total"]


def test_trader_only_gets_numbers_for_own_trades(client, login, db):
    me = db.scalar(select(User.id).where(User.username == TRADER))
    mine = sql_counts(db, or_(Trade.trader_user_id == me, Trade.created_by == me))
    body = client.get(SUMMARY, headers=login(TRADER)).json()
    assert without_pct(body["historical"]) == mine == {"total": 4, "pass": 1, "monitor": 1, "exception": 1, "rejected": 1}
    assert body["historical"]["pct"] == {"pass": 25.0, "monitor": 25.0, "exception": 25.0, "rejected": 25.0}
    other = client.get(SUMMARY, headers=login("nina.petrova")).json()
    assert other["historical"]["total"] < sql_counts(db)["total"]
    charts = client.get(CHARTS, headers=login(TRADER)).json()
    assert sum(b["count"] for b in charts["riskScoreHistogram"]) == mine["total"] - mine["rejected"]


def test_charts_match_sql(client, login, db):
    body = client.get(CHARTS, headers=login(ADMIN)).json()
    assert set(body) == {"byStatusOverTime", "byCounterparty", "bySymbol", "riskScoreHistogram"}
    expected = sql_counts(db)

    histogram = body["riskScoreHistogram"]
    assert [b["bucket"] for b in histogram] == ["0.0-0.1", "0.1-0.2", "0.2-0.3", "0.3-0.4", "0.4-0.5", "0.5-0.6",
                                                "0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0"]
    assert sum(b["count"] for b in histogram) == expected["total"] - expected["rejected"]
    in_bucket = db.scalar(select(func.count()).select_from(Trade).where(
        Trade.status != "REJECTED", Trade.final_score >= 0.7, Trade.final_score < 0.8))
    assert histogram[7]["count"] == in_bucket and in_bucket >= 1  # the 0.75 trade booked above

    days = body["byStatusOverTime"]
    assert [d["date"] for d in days] == sorted(d["date"] for d in days)
    dated = sql_counts(db, Trade.trade_date.is_not(None))
    for name in STATUSES:
        assert sum(d[name] for d in days) == dated[name]
    one_day = sql_counts(db, Trade.trade_date == date.fromisoformat(days[0]["date"]))
    assert {name: days[0][name] for name in STATUSES} == {name: one_day[name] for name in STATUSES}

    for key, column in (("byCounterparty", Trade.counterparty), ("bySymbol", Trade.symbol)):
        bars = body[key]
        assert 1 <= len(bars) <= 10
        assert [b["exception"] for b in bars] == sorted((b["exception"] for b in bars), reverse=True)
        top = bars[0]
        assert sql_counts(db, column == top["name"]) == {k: top[k] for k in ("total", *STATUSES)}


@pytest.mark.parametrize("path", [SUMMARY, CHARTS])
def test_dashboard_permissions(client, login, path):
    for username in (TRADER, OPS, MANAGER, ADMIN):
        assert client.get(path, headers=login(username)).status_code == 200, username
    assert_error_shape(client.get(path, headers=login("ml.admin")), 403, "FORBIDDEN")
    assert_error_shape(client.get(path), 401, "UNAUTHORIZED")


def test_summary_rejects_a_badly_formatted_date(client, login):
    assert_error_shape(client.get(f"{SUMMARY}?date=10/10/2026", headers=login(MANAGER)), 422, "VALIDATION_ERROR")
