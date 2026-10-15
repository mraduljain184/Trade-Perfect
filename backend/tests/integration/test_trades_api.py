"""BE-2: booking, amending, listing, history and export of single trades (rules-only engine)."""
import csv
import io

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditLog, MLPrediction, RepairQueueItem, RuleEvaluation, SystemConfig, Trade, TradeVersion, User,
)
from app.services import audit_service, engine_adapter
from tests.conftest import assert_error_shape

API = "/api/v1/trades"
TRADER, OTHER_TRADER, OPS, MANAGER, ADMIN = "priya.sharma", "emma.davis", "ops.user1", "ops.manager", "sys.admin"

# Clean trade: Tier-1 counterparty, everything matched, weekday settlement -> score 0.
CLEAN = {
    "tradeType": "BUY", "symbol": "AAPL", "quantity": 1000, "price": 10250.5, "counterparty": "Jefferies",
    "tradeDate": "2026-09-29", "settlementDate": "2026-10-01", "accountId": "ACC1000",
    "ssiMatched": True, "currencyMatched": True,
}
# Tier 4 (0.25) + SSI (0.35) + currency (0.15) = 0.75 -> EXCEPTION / HIGH
RISKY = {**CLEAN, "counterparty": "BlackRock", "ssiMatched": False, "currencyMatched": False}


@pytest.fixture(autouse=True, scope="module")
def _fixed_business_date(_database):
    """Pin 'today' to 2026-09-28 so scores do not depend on the day the tests run."""
    from app.core.database import SessionLocal

    with SessionLocal() as session:
        session.get(SystemConfig, "business_date_override").value = "2026-09-28"
        session.commit()
    engine_adapter.invalidate_cache()
    yield
    with SessionLocal() as session:
        session.get(SystemConfig, "business_date_override").value = None
        session.commit()
    engine_adapter.invalidate_cache()


def book(client, login, payload=CLEAN, user=TRADER):
    response = client.post(API, json=payload, headers=login(user))
    assert response.status_code == 201, response.text
    return response.json()


def audit_actions(db, trade_id):
    return db.scalars(select(AuditLog.action).where(AuditLog.trade_id == trade_id).order_by(AuditLog.id)).all()


# ---------------------------------------------------------------- validate
def test_validate_ok_writes_nothing(client, login, db):
    before = db.scalar(select(func.count()).select_from(Trade))
    response = client.post(f"{API}/validate", json=CLEAN, headers=login(TRADER))
    assert response.status_code == 200
    assert response.json() == {"valid": True, "fields": [], "warnings": []}
    assert db.scalar(select(func.count()).select_from(Trade)) == before


def test_validate_reports_every_problem_with_rule_ids(client, login):
    bad = {**CLEAN, "symbol": "ISRG", "accountId": "ACC1011", "quantity": -5, "counterparty": "ABC Bank",
           "tradeType": "HOLD", "settlementDate": "2026-09-01"}
    body = client.post(f"{API}/validate", json=bad, headers=login(TRADER)).json()
    assert body["valid"] is False
    by_field = {f["field"]: f["ruleId"] for f in body["fields"]}
    assert by_field == {"tradeType": "R-002", "quantity": "R-003", "symbol": "R-005", "counterparty": "R-006",
                        "accountId": "R-007", "settlementDate": "R-008"}
    assert all(f["message"] for f in body["fields"])


def test_validate_returns_soft_warnings(client, login):
    weekend = {**CLEAN, "settlementDate": "2026-10-03", "quantity": 9000}  # Saturday + over the limit
    body = client.post(f"{API}/validate", json=weekend, headers=login(TRADER)).json()
    assert body["valid"] is True and len(body["warnings"]) == 2


def test_validate_allowed_for_ops_but_not_ml_admin(client, login):
    assert client.post(f"{API}/validate", json=CLEAN, headers=login(OPS)).status_code == 200
    assert_error_shape(client.post(f"{API}/validate", json=CLEAN, headers=login("ml.admin")), 403, "FORBIDDEN")


# ---------------------------------------------------------------- create
def test_book_clean_trade_scores_zero_and_has_no_queue_item(client, login, db):
    trade = book(client, login)
    assert trade["tradeId"].startswith("T2") and int(trade["tradeId"][1:]) >= 200001
    assert (trade["status"], trade["priority"], trade["finalScore"], trade["ruleScore"]) == ("PASS", "LOW", 0.0, 0.0)
    assert trade["mlScore"] is None and trade["modelVersion"] is None  # rules-only mode
    assert trade["queueId"] is None and trade["queueStatus"] is None and trade["riskFactors"] == []
    assert (trade["source"], trade["version"], trade["isResolved"], trade["createdBy"]) == ("UI", 1, False, TRADER)
    assert trade["traderName"] == "Priya Sharma" and trade["rejectionReasons"] is None
    assert trade["createdAt"].endswith("Z") or trade["createdAt"].endswith("+00:00")
    assert audit_actions(db, trade["tradeId"]) == ["TRADE_SUBMITTED"]
    assert db.scalar(select(func.count()).select_from(RepairQueueItem).join(Trade).where(Trade.trade_id == trade["tradeId"])) == 0


def test_book_risky_trade_goes_to_repair_queue(client, login, db):
    trade = book(client, login, RISKY)
    assert (trade["status"], trade["priority"], trade["finalScore"]) == ("EXCEPTION", "HIGH", 0.75)
    assert trade["queueId"].startswith("RQ-") and trade["queueStatus"] == "OPEN" and trade["assignedTo"] is None
    factors = trade["riskFactors"]
    assert [f["factor"] for f in factors] == ["SSI", "COUNTERPARTY", "CURRENCY"]
    assert round(sum(f["pct"] for f in factors)) == 100
    assert trade["likelyReason"] == factors[0]["detail"] and trade["suggestedAction"] and trade["tradeId"] in trade["explanation"]
    triggered = {h["ruleId"] for h in trade["ruleHits"] if h["triggered"]}
    assert triggered == {"S-SSI", "S-CCY", "S-CP-TIER"} and len(trade["ruleHits"]) > 3  # engine returns ALL rules

    row = db.scalar(select(Trade).where(Trade.trade_id == trade["tradeId"]))
    stored = db.scalars(select(RuleEvaluation).where(RuleEvaluation.trade_pk == row.id)).all()
    assert {r.rule_id for r in stored} == triggered and all(r.triggered and r.trade_version == 1 for r in stored)
    item = db.scalar(select(RepairQueueItem).where(RepairQueueItem.trade_pk == row.id))
    assert (item.queue_id, item.priority, float(item.risk_score_at_entry)) == (trade["queueId"], "HIGH", 0.75)
    assert item.suggested_fixes and {"factor", "fix"} <= set(item.suggested_fixes[0])
    version = db.scalar(select(TradeVersion).where(TradeVersion.trade_pk == row.id))
    assert (version.version, version.change_type) == (1, "CREATED") and version.snapshot["tradeId"] == trade["tradeId"]
    assert db.scalar(select(func.count()).select_from(MLPrediction).where(MLPrediction.trade_pk == row.id)) == 0
    assert audit_actions(db, trade["tradeId"]) == ["TRADE_SUBMITTED", "QUEUE_ITEM_CREATED"]
    audit = db.scalar(select(AuditLog).where(AuditLog.trade_id == trade["tradeId"], AuditLog.action == "TRADE_SUBMITTED"))
    assert (audit.status_after, float(audit.risk_score), audit.queue_id) == ("EXCEPTION", 0.75, trade["queueId"])
    assert audit_service.verify_chain(db) == (True, None)


def test_monitor_band(client, login):
    trade = book(client, login, {**CLEAN, "counterparty": "BlackRock", "ssiMatched": False})  # 0.25 + 0.35
    assert (trade["status"], trade["priority"], trade["finalScore"]) == ("MONITOR", "MEDIUM", 0.6)


def test_invalid_trade_is_422_with_all_errors_and_nothing_saved(client, login, db):
    before = db.scalar(select(func.count()).select_from(Trade))
    audits = db.scalar(select(func.count()).select_from(AuditLog))
    bad = {**CLEAN, "symbol": "ISRG", "accountId": "ACC1011", "price": 0}
    error = assert_error_shape(client.post(API, json=bad, headers=login(TRADER)), 422, "VALIDATION_ERROR")
    assert error["message"] == "Trade validation failed"
    assert {(f["field"], f["ruleId"]) for f in error["fields"]} == {("symbol", "R-005"), ("accountId", "R-007"), ("price", "R-004")}
    assert db.scalar(select(func.count()).select_from(Trade)) == before
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audits


def test_missing_and_wrongly_typed_fields_are_engine_errors_not_crashes(client, login):
    error = assert_error_shape(client.post(API, json={"symbol": "AAPL", "quantity": "lots"}, headers=login(TRADER)),
                               422, "VALIDATION_ERROR")
    fields = {f["field"] for f in error["fields"]}
    assert {"tradeType", "price", "counterparty", "tradeDate", "settlementDate", "accountId",
            "ssiMatched", "currencyMatched", "quantity"} <= fields
    assert all(f["ruleId"] for f in error["fields"])


def test_client_cannot_choose_trade_id_or_trader_name(client, login):
    trade = book(client, login, {**CLEAN, "tradeId": "T999999", "traderName": "Somebody Else"})
    assert trade["tradeId"] != "T999999" and trade["traderName"] == "Priya Sharma"


def test_trade_ids_are_unique_and_increasing(client, login):
    first, second = book(client, login), book(client, login)
    assert int(second["tradeId"][1:]) > int(first["tradeId"][1:])


@pytest.mark.parametrize("user", [OPS, MANAGER, "ml.admin"])
def test_only_traders_and_admin_can_book(client, login, user):
    assert_error_shape(client.post(API, json=CLEAN, headers=login(user)), 403, "FORBIDDEN")


def test_sys_admin_can_book_and_no_token_is_401(client, login):
    assert book(client, login, user=ADMIN)["traderName"] == "System Admin"
    assert_error_shape(client.post(API, json=CLEAN), 401, "UNAUTHORIZED")


def test_business_date_drives_past_settlement_rule(client, login, db):
    """Same trade, later 'today' -> settlement is now in the past -> +0.15."""
    db.get(SystemConfig, "business_date_override").value = "2026-10-05"
    db.commit()
    engine_adapter.invalidate_cache()
    try:
        trade = book(client, login)
        assert trade["finalScore"] == 0.15 and trade["riskFactors"][0]["factor"] == "SETTLEMENT_DATE"
        assert trade["queueId"] is not None and trade["priority"] == "LOW"
    finally:
        db.get(SystemConfig, "business_date_override").value = "2026-09-28"
        db.commit()
        engine_adapter.invalidate_cache()


# ---------------------------------------------------------------- amend
def test_amend_creates_version_2_with_changed_fields(client, login, db):
    trade = book(client, login, RISKY)
    update = {**RISKY, "ssiMatched": True, "quantity": 1500, "changeReason": "SSI confirmed by phone"}
    response = client.put(f"{API}/{trade['tradeId']}", json=update, headers=login(TRADER))
    assert response.status_code == 200, response.text
    amended = response.json()
    assert (amended["version"], amended["finalScore"], amended["status"], amended["priority"]) == (2, 0.4, "PASS", "LOW")
    assert amended["queueId"] == trade["queueId"] and amended["queueStatus"] == "OPEN"

    history = client.get(f"{API}/{trade['tradeId']}/history", headers=login(TRADER)).json()
    assert [(v["version"], v["changeType"]) for v in history] == [(2, "AMENDED"), (1, "CREATED")]
    assert history[0]["changedFields"] == {"ssiMatched": {"old": False, "new": True}, "quantity": {"old": 1000, "new": 1500}}
    assert history[0]["changeReason"] == "SSI confirmed by phone" and history[0]["changedBy"] == TRADER
    assert history[0]["snapshot"]["finalScore"] == 0.4 and history[1]["snapshot"]["finalScore"] == 0.75
    assert history[1]["changedFields"] is None

    row = db.scalar(select(Trade).where(Trade.trade_id == trade["tradeId"]))
    item = db.scalar(select(RepairQueueItem).where(RepairQueueItem.trade_pk == row.id))
    assert item.priority == "LOW" and float(item.risk_score_at_entry) == 0.75 and item.applied_fix is None
    versions = {r.trade_version for r in db.scalars(select(RuleEvaluation).where(RuleEvaluation.trade_pk == row.id))}
    assert versions == {1, 2}
    assert audit_actions(db, trade["tradeId"]) == ["TRADE_SUBMITTED", "QUEUE_ITEM_CREATED", "TRADE_AMENDED"]
    audit = db.scalar(select(AuditLog).where(AuditLog.trade_id == trade["tradeId"], AuditLog.action == "TRADE_AMENDED"))
    assert audit.before_state["ssiMatched"] is False and audit.after_state["ssiMatched"] is True
    assert audit.before_state["finalScore"] == 0.75 and audit.after_state["finalScore"] == 0.4


def test_apply_fix_records_fix_and_resolves_when_score_reaches_zero(client, login, db):
    trade = book(client, login, RISKY)
    fix = {**CLEAN, "queueId": trade["queueId"], "appliedFix": "Re-confirmed SSI and currency; moved to Jefferies"}
    response = client.put(f"{API}/{trade['tradeId']}", json=fix, headers=login(OPS))
    assert response.status_code == 200, response.text
    fixed = response.json()
    assert (fixed["finalScore"], fixed["status"], fixed["queueStatus"], fixed["isResolved"]) == (0.0, "PASS", "RESOLVED", True)
    assert fixed["traderName"] == "Priya Sharma"  # the original trader stays on the trade

    row = db.scalar(select(Trade).where(Trade.trade_id == trade["tradeId"]))
    item = db.scalar(select(RepairQueueItem).where(RepairQueueItem.trade_pk == row.id))
    assert item.applied_fix == fix["appliedFix"] and item.applied_by is not None and item.applied_at is not None
    assert item.status == "RESOLVED" and item.resolved_at is not None
    assert audit_actions(db, trade["tradeId"])[-2:] == ["FIX_APPLIED", "QUEUE_RESOLVED"]
    audit = db.scalar(select(AuditLog).where(AuditLog.trade_id == trade["tradeId"], AuditLog.action == "FIX_APPLIED"))
    assert audit.applied_fix == fix["appliedFix"] and audit.actor_username == OPS and audit.queue_id == trade["queueId"]

    detail = client.get(f"{API}/{trade['tradeId']}", headers=login(OPS)).json()
    assert detail["versionsCount"] == 2
    assert detail["auditBox"]["appliedFix"] == fix["appliedFix"] and detail["auditBox"]["appliedBy"] == "Ops User One"
    assert detail["auditBox"]["queueId"] == trade["queueId"] and detail["auditBox"]["status"] == "PASS"
    history = client.get(f"{API}/{trade['tradeId']}/history", headers=login(OPS)).json()
    assert history[0]["changeType"] == "FIX_APPLIED" and history[0]["changedBy"] == OPS

    # the item is closed now: a second fix on it is refused
    again = {**RISKY, "queueId": trade["queueId"]}
    assert_error_shape(client.put(f"{API}/{trade['tradeId']}", json=again, headers=login(OPS)), 409, "CONFLICT")


def test_apply_fix_that_still_leaves_high_risk_keeps_item_open(client, login):
    trade = book(client, login, RISKY)
    fix = {**RISKY, "quantity": 9000, "queueId": trade["queueId"]}  # no appliedFix text -> auto summary
    fixed = client.put(f"{API}/{trade['tradeId']}", json=fix, headers=login(OPS)).json()
    assert fixed["priority"] == "HIGH" and fixed["queueStatus"] == "OPEN" and fixed["isResolved"] is False
    assert client.get(f"{API}/{trade['tradeId']}", headers=login(OPS)).json()["auditBox"]["appliedFix"] == "Updated quantity"


def test_amend_clean_trade_into_risk_creates_queue_item(client, login, db):
    trade = book(client, login)
    amended = client.put(f"{API}/{trade['tradeId']}", json={**CLEAN, "ssiMatched": False}, headers=login(TRADER)).json()
    assert amended["finalScore"] == 0.35 and amended["queueId"] is not None and amended["queueStatus"] == "OPEN"
    assert audit_actions(db, trade["tradeId"]) == ["TRADE_SUBMITTED", "TRADE_AMENDED", "QUEUE_ITEM_CREATED"]


def test_amend_with_invalid_data_is_422_and_changes_nothing(client, login):
    trade = book(client, login, RISKY)
    error = assert_error_shape(client.put(f"{API}/{trade['tradeId']}", json={**RISKY, "symbol": "HD12"}, headers=login(TRADER)),
                               422, "VALIDATION_ERROR")
    assert error["fields"][0]["ruleId"] == "R-005"
    assert client.get(f"{API}/{trade['tradeId']}", headers=login(TRADER)).json()["version"] == 1


def test_amend_without_changes_is_400(client, login):
    trade = book(client, login)
    assert_error_shape(client.put(f"{API}/{trade['tradeId']}", json=CLEAN, headers=login(TRADER)), 400, "BAD_REQUEST")


def test_amend_unknown_trade_is_404(client, login):
    assert_error_shape(client.put(f"{API}/T000000", json=CLEAN, headers=login(TRADER)), 404, "NOT_FOUND")


def test_rejected_trade_cannot_be_amended_but_can_be_read(client, login, db):
    creator = db.scalar(select(User.id).where(User.username == ADMIN))
    db.add(Trade(trade_id="TREJ0001", symbol="ISRG", status="REJECTED", final_score=1, source="FILE", created_by=creator,
                 rejection_reasons=[{"ruleId": "R-005", "field": "symbol", "message": "Symbol 'ISRG' not found in master data"}]))
    db.commit()
    assert_error_shape(client.put(f"{API}/TREJ0001", json=CLEAN, headers=login(ADMIN)), 409, "CONFLICT")
    detail = client.get(f"{API}/TREJ0001", headers=login(ADMIN)).json()
    assert detail["status"] == "REJECTED" and detail["finalScore"] == 1.0 and detail["queueId"] is None
    assert detail["rejectionReasons"] == [{"ruleId": "R-005", "field": "symbol", "message": "Symbol 'ISRG' not found in master data"}]


def test_trader_cannot_use_queue_id_and_queue_id_must_match(client, login):
    trade, other = book(client, login, RISKY), book(client, login, RISKY)
    body = {**CLEAN, "queueId": trade["queueId"]}
    assert_error_shape(client.put(f"{API}/{trade['tradeId']}", json=body, headers=login(TRADER)), 403, "FORBIDDEN")
    wrong = {**CLEAN, "queueId": other["queueId"]}
    assert_error_shape(client.put(f"{API}/{trade['tradeId']}", json=wrong, headers=login(OPS)), 400, "BAD_REQUEST")


def test_ml_admin_cannot_amend(client, login):
    trade = book(client, login)
    assert_error_shape(client.put(f"{API}/{trade['tradeId']}", json=RISKY, headers=login("ml.admin")), 403, "FORBIDDEN")


# ---------------------------------------------------------------- scoping
def test_trader_sees_and_amends_only_own_trades(client, login):
    mine, theirs = book(client, login, user=TRADER), book(client, login, user=OTHER_TRADER)
    assert client.get(f"{API}/{mine['tradeId']}", headers=login(TRADER)).status_code == 200
    for call in (lambda: client.get(f"{API}/{theirs['tradeId']}", headers=login(TRADER)),
                 lambda: client.get(f"{API}/{theirs['tradeId']}/history", headers=login(TRADER)),
                 lambda: client.put(f"{API}/{theirs['tradeId']}", json=RISKY, headers=login(TRADER))):
        assert_error_shape(call(), 404, "NOT_FOUND")
    listed = client.get(f"{API}?pageSize=500", headers=login(TRADER)).json()
    assert listed["total"] == len(listed["items"]) > 0
    assert {t["createdBy"] for t in listed["items"]} == {TRADER}
    # Ops and managers see everyone's trades
    assert client.get(f"{API}/{theirs['tradeId']}", headers=login(OPS)).status_code == 200
    everyone = client.get(f"{API}?pageSize=500", headers=login(MANAGER)).json()
    assert {TRADER, OTHER_TRADER} <= {t["createdBy"] for t in everyone["items"]}


def test_ml_admin_cannot_list_trades(client, login):
    assert_error_shape(client.get(API, headers=login("ml.admin")), 403, "FORBIDDEN")


# ---------------------------------------------------------------- list
def test_list_paging_shape_and_default_page_size(client, login):
    for _ in range(3):
        book(client, login)
    body = client.get(API, headers=login(MANAGER)).json()
    assert set(body) == {"items", "total", "page", "pageSize"} and (body["page"], body["pageSize"]) == (1, 50)
    page1 = client.get(f"{API}?page=1&pageSize=2", headers=login(MANAGER)).json()
    page2 = client.get(f"{API}?page=2&pageSize=2", headers=login(MANAGER)).json()
    assert len(page1["items"]) == 2 and page1["total"] == body["total"]
    assert not {t["id"] for t in page1["items"]} & {t["id"] for t in page2["items"]}
    assert page1["items"][0]["createdAt"] >= page1["items"][1]["createdAt"]  # newest first by default


def test_list_filters(client, login):
    sell = book(client, login, {**RISKY, "symbol": "QCOM", "tradeType": "SELL", "tradeDate": "2026-09-30",
                                "settlementDate": "2026-10-02"}, user="wei.zhang")
    headers = login(MANAGER)

    def ids(query):
        response = client.get(f"{API}?pageSize=500&{query}", headers=headers)
        assert response.status_code == 200, response.text
        return {t["tradeId"] for t in response.json()["items"]}

    assert sell["tradeId"] in ids("status=EXCEPTION") and sell["tradeId"] not in ids("status=PASS")
    assert sell["tradeId"] in ids("status=pass,exception&priority=HIGH")
    assert ids(f"search={sell['tradeId'][2:]}") >= {sell["tradeId"]} and sell["tradeId"] in ids("search=qcom")
    assert sell["tradeId"] in ids("traderName=wei zhang") and sell["tradeId"] not in ids("traderName=priya sharma")
    assert sell["tradeId"] in ids("counterparty=blackrock") and sell["tradeId"] not in ids("counterparty=Jefferies")
    assert sell["tradeId"] in ids("tradeDateFrom=2026-09-30&tradeDateTo=2026-09-30")
    assert sell["tradeId"] not in ids("tradeDateTo=2026-09-29")
    assert sell["tradeId"] in ids("settlementDateFrom=2026-10-02&settlementDateTo=2026-10-02")
    assert ids("batchId=BATCH-19990101-0001") == set()
    assert ids("mine=true") == set()  # the manager has booked nothing


def test_list_sorting(client, login):
    book(client, login, RISKY)
    scores = [t["finalScore"] for t in client.get(f"{API}?sort=finalScore:desc&pageSize=500", headers=login(MANAGER)).json()["items"]]
    assert scores == sorted(scores, reverse=True) and scores[0] >= 0.75
    scores = [t["finalScore"] for t in client.get(f"{API}?sort=finalScore:asc,tradeId:desc&pageSize=500", headers=login(MANAGER)).json()["items"]]
    assert scores == sorted(scores)


@pytest.mark.parametrize("query, status, code", [
    ("status=BROKEN", 400, "BAD_REQUEST"), ("priority=URGENT", 400, "BAD_REQUEST"),
    ("sort=password:asc", 400, "BAD_REQUEST"), ("sort=finalScore:sideways", 400, "BAD_REQUEST"),
    ("pageSize=501", 422, "VALIDATION_ERROR"), ("page=0", 422, "VALIDATION_ERROR"),
    ("tradeDateFrom=10/01/2026", 422, "VALIDATION_ERROR"),
])
def test_list_rejects_bad_query(client, login, query, status, code):
    assert_error_shape(client.get(f"{API}?{query}", headers=login(MANAGER)), status, code)


def test_search_text_is_not_executed_as_sql(client, login):
    response = client.get(f"{API}?search=' OR 1=1 --", headers=login(MANAGER))
    assert response.status_code == 200 and response.json()["total"] == 0


def test_get_unknown_trade_is_404(client, login):
    assert_error_shape(client.get(f"{API}/T000000", headers=login(MANAGER)), 404, "NOT_FOUND")
    assert_error_shape(client.get(f"{API}/T000000/history", headers=login(MANAGER)), 404, "NOT_FOUND")


def test_detail_returns_stored_rule_hits(client, login):
    trade = book(client, login, RISKY)
    detail = client.get(f"{API}/{trade['tradeId']}", headers=login(TRADER)).json()
    assert {h["ruleId"] for h in detail["ruleHits"]} == {"S-SSI", "S-CCY", "S-CP-TIER"}
    assert detail["versionsCount"] == 1 and detail["auditBox"]["riskScore"] == 0.75
    assert detail["auditBox"]["recommendedFix"] == trade["suggestedAction"] and detail["auditBox"]["appliedFix"] is None


# ---------------------------------------------------------------- export
def test_export_csv_respects_filters_and_is_audited(client, login, db):
    trade = book(client, login, {**RISKY, "symbol": "NVDA"}, user="tom.richardson")
    response = client.get(f"{API}/export?traderName=Tom Richardson&status=EXCEPTION", headers=login(MANAGER))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="trades_') and disposition.endswith('.csv"')
    rows = list(csv.reader(io.StringIO(response.text.lstrip("﻿"))))
    assert rows[0][:9] == ["Trade ID", "Queue ID", "Risk Score", "Status", "Likely Reason", "Suggested Action",
                           "Priority", "Risk Factors %", "Explanation"]
    record = dict(zip(rows[0], rows[1]))
    assert len(rows) == 2 and record["Trade ID"] == trade["tradeId"] and record["Queue ID"] == trade["queueId"]
    assert (record["Risk Score"], record["Status"], record["Priority"]) == ("0.7500", "EXCEPTION", "HIGH")
    assert record["Trade Date"] == "2026-09-29" and record["SSI Matched"] == "NO" and "SSI" in record["Risk Factors %"]
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "EXPORT_CSV").order_by(AuditLog.id.desc()))
    assert audit.actor_username == MANAGER and audit.details["rows"] == 1


def test_export_is_scoped_for_traders_and_forbidden_for_ml_admin(client, login):
    book(client, login, user=OTHER_TRADER)
    rows = list(csv.DictReader(io.StringIO(client.get(f"{API}/export", headers=login(TRADER)).text.lstrip("﻿"))))
    assert rows and {r["Trader Name"] for r in rows} == {"Priya Sharma"}
    assert_error_shape(client.get(f"{API}/export", headers=login("ml.admin")), 403, "FORBIDDEN")
