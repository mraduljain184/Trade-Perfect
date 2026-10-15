"""BE-3: bulk upload (endpoints 15-17). The TestClient runs the background task before POST returns,
so the batch is already finished when the next line of a test runs."""
import csv
import io
import re

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import (
    AuditLog, EmailOutbox, Notification, RepairQueueItem, RuleEvaluation, SystemConfig, Trade, TradeVersion,
    UploadBatch, User,
)
from app.services import audit_service, engine_adapter, upload_service
from tests.conftest import assert_error_shape

BULK, UPLOADS, TRADES = "/api/v1/trades/bulk", "/api/v1/uploads", "/api/v1/trades"
TRADER, OTHER_TRADER, ADMIN = "john.miller", "carlos.rodriguez", "sys.admin"
HEADER = ("Trade ID,Trade Type,Symbol,Quantity,Price,Counterparty,Trade Date,Settlement Date,Account ID,"
          "SSI Matched,Currency Matched,Trader Name")


def line(trade_id, **changes) -> str:
    """One CSV line. Default = a clean trade (Tier-1 counterparty, all matched, weekday) -> score 0."""
    values = {"type": "BUY", "symbol": "AAPL", "qty": "1000", "price": "10250.5", "cp": "Jefferies",
              "trade": "9/29/2026", "settle": "10/1/2026", "account": "ACC1000", "ssi": "YES", "ccy": "YES",
              "trader": "John Miller", **changes}
    return ",".join([trade_id, values["type"], values["symbol"], values["qty"], values["price"], values["cp"],
                     values["trade"], values["settle"], values["account"], values["ssi"], values["ccy"], values["trader"]])


def csv_text(*lines: str) -> str:
    return "\r\n".join([HEADER, *lines]) + "\r\n"


# Every trap of PROJECT_CONTEXT section 2.1 in one small file.
TRAPS = csv_text(
    line("BLK0001"),                                              # 2  clean                    PASS 0.00
    line("BLK0002", cp="BlackRock", ssi="NO", ccy="NO"),          # 3  0.25 + 0.35 + 0.15       EXCEPTION
    line("BLK0003", cp="BlackRock", ssi="NO"),                    # 4  0.25 + 0.35              MONITOR
    line(""),                                                     # 5  empty Trade ID           R-001
    line("BLK0005", type=""),                                     # 6  empty Trade Type         R-002
    line("BLK0006", symbol="ISRG"),                               # 7  symbol not in master     R-005
    line("BLK0007", account="ACC1011"),                           # 8  inactive account         R-007
    line("BLK0008", qty="123444297"),                             # 9  absurd quantity          PASS 0.10
    line("BLK0009", settle="10/3/2026", trader="Carlos Rodriguez"),     # 10 Saturday settlement      PASS 0.10
    line("BLK0001"),                                              # 11 duplicate inside file    R-001
)


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


@pytest.fixture(autouse=True, scope="module")
def _reports_in_tmp(tmp_path_factory):
    """Rejection reports go to a temp folder, not into the repository."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(upload_service, "REPORT_DIR", tmp_path_factory.mktemp("reports"))
        yield


@pytest.fixture()
def config(db):
    """Temporarily change system_config values:  config(batch_size=3)."""
    original = {}

    def change(**values):
        for key, value in values.items():
            row = db.get(SystemConfig, key)
            original.setdefault(key, row.value)
            row.value = value
        db.commit()
        engine_adapter.invalidate_cache()

    yield change
    for key, value in original.items():
        db.get(SystemConfig, key).value = value
    db.commit()
    engine_adapter.invalidate_cache()


def upload(client, login, text, user=TRADER, name="trades.csv"):
    response = client.post(BULK, files={"file": (name, text.encode("utf-8-sig"), "text/csv")}, headers=login(user))
    assert response.status_code == 202, response.text
    return response.json()["batchId"]


def progress(client, login, batch_id, user=TRADER):
    response = client.get(f"{UPLOADS}/{batch_id}", headers=login(user))
    assert response.status_code == 200, response.text
    return response.json()


def batch_trades(db, batch_id):
    pk = db.scalar(select(UploadBatch.id).where(UploadBatch.batch_id == batch_id))
    rows = db.scalars(select(Trade).where(Trade.batch_id == pk).order_by(Trade.batch_row_number)).all()
    return {t.batch_row_number: t for t in rows}


@pytest.fixture(scope="module")
def traps(client, login):
    """The trap file, uploaded once for the tests that only read the result."""
    return upload(client, login, TRAPS)


# ---------------------------------------------------------------- happy path
def test_upload_answers_202_and_summary_counts_every_status(client, login, traps):
    assert re.fullmatch(r"BATCH-\d{8}-\d{4}", traps)
    body = progress(client, login, traps)
    assert set(body) == {"batchId", "fileName", "status", "totalRows", "processedRows", "summary", "startedAt",
                         "completedAt", "errorMessage"}
    assert (body["status"], body["totalRows"], body["processedRows"]) == ("COMPLETED", 10, 10)
    assert body["summary"] == {"accepted": 5, "pass": 3, "monitor": 1, "exception": 1, "rejected": 5}
    assert body["fileName"] == "trades.csv" and body["completedAt"] and body["errorMessage"] is None


def test_accepted_rows_are_scored_and_routed_like_single_trades(db, traps):
    trades = batch_trades(db, traps)
    clean, risky, monitor, big, weekend = trades[1], trades[2], trades[3], trades[8], trades[9]
    assert [(t.status, t.priority, float(t.final_score)) for t in (clean, risky, monitor, big, weekend)] == [
        ("PASS", "LOW", 0.0), ("EXCEPTION", "HIGH", 0.75), ("MONITOR", "MEDIUM", 0.6), ("PASS", "LOW", 0.1),
        ("PASS", "LOW", 0.1)]
    assert all(t.source == "FILE" and t.version == 1 and t.ml_score is None for t in trades.values())
    assert big.quantity == 123444297 and str(risky.trade_date) == "2026-09-29"  # M/D/YYYY parsed
    assert risky.risk_factors and risky.explanation and risky.suggested_action

    uploader = db.scalar(select(User.id).where(User.username == TRADER))
    other = db.scalar(select(User.id).where(User.username == OTHER_TRADER))
    assert clean.trader_user_id == uploader and weekend.trader_user_id == other  # matched by Trader Name
    assert {t.created_by for t in trades.values()} == {uploader}

    queued = dict(db.execute(select(RepairQueueItem.trade_pk, RepairQueueItem.priority)
                             .where(RepairQueueItem.trade_pk.in_([t.id for t in trades.values()]))).all())
    assert queued == {risky.id: "HIGH", monitor.id: "MEDIUM", big.id: "LOW", weekend.id: "LOW"}  # score 0 -> no item
    rules = set(db.scalars(select(RuleEvaluation.rule_id).where(RuleEvaluation.trade_pk == risky.id)).all())
    assert rules == {"S-SSI", "S-CCY", "S-CP-TIER"}


def test_rejected_rows_are_stored_with_reasons_and_no_queue_item(db, traps):
    trades = batch_trades(db, traps)
    expected = {4: "R-001", 5: "R-002", 6: "R-005", 7: "R-007", 10: "R-001"}
    for row_number, rule_id in expected.items():
        trade = trades[row_number]
        assert (trade.status, trade.priority, float(trade.final_score)) == ("REJECTED", None, 1.0), row_number
        assert rule_id in {r["ruleId"] for r in trade.rejection_reasons}, row_number
        assert trade.raw_payload["Symbol"] in ("AAPL", "ISRG")
        assert db.scalar(select(func.count()).select_from(RepairQueueItem).where(RepairQueueItem.trade_pk == trade.id)) == 0
        stored = db.execute(select(RuleEvaluation.rule_id, RuleEvaluation.rule_category)
                            .where(RuleEvaluation.trade_pk == trade.id)).all()
        assert (rule_id, "HARD_GATE") in stored
    assert trades[4].trade_id is None and trades[10].trade_id == "BLK0001"
    assert trades[1].status == "PASS"  # the FIRST occurrence of a duplicated ID wins


def test_every_row_has_a_version_and_audit_rows_and_the_chain_is_valid(db, traps):
    trades = batch_trades(db, traps)
    kinds = db.execute(select(TradeVersion.change_type, func.count())
                       .where(TradeVersion.trade_pk.in_([t.id for t in trades.values()]))
                       .group_by(TradeVersion.change_type)).all()
    assert dict(kinds) == {"CREATED": 5, "REJECTED": 5}
    snapshot = db.scalar(select(TradeVersion.snapshot).where(TradeVersion.trade_pk == trades[2].id))
    assert snapshot["tradeId"] == "BLK0002" and snapshot["queueId"].startswith("RQ-") and snapshot["finalScore"] == 0.75

    per_trade = dict(db.execute(select(AuditLog.action, func.count())
                                .where(AuditLog.trade_pk.in_([t.id for t in trades.values()]))
                                .group_by(AuditLog.action)).all())
    assert per_trade == {"TRADE_SUBMITTED": 5, "TRADE_REJECTED": 5, "QUEUE_ITEM_CREATED": 4}
    batch_actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == traps).order_by(AuditLog.id)).all()
    assert batch_actions == ["BULK_UPLOAD_STARTED", "REJECTION_REPORT_SENT", "BULK_UPLOAD_COMPLETED"]
    done = db.scalar(select(AuditLog).where(AuditLog.entity_id == traps, AuditLog.action == "BULK_UPLOAD_COMPLETED"))
    assert done.actor_username == TRADER and done.details["summary"]["rejected"] == 5
    assert audit_service.verify_chain(db) == (True, None)


def test_rejection_report_email_and_notification(db, traps, client, login):
    mail = db.scalar(select(EmailOutbox).where(EmailOutbox.subject.contains(traps)))
    assert mail.purpose == "REJECTION_REPORT" and mail.to_addresses == "trader-group@tradeperfect.local"
    assert mail.status == ("PENDING" if get_settings().smtp_host else "LOGGED_ONLY")
    assert mail.attachment_path == f"reports/rejections_{traps}.csv"
    note = db.scalar(select(Notification).where(Notification.title.contains(traps)))
    assert note.type == "REJECTION_REPORT" and "5 REJECTED" in note.message
    assert note.user_id == db.scalar(select(User.id).where(User.username == TRADER))


def test_rejections_csv_lists_each_rejected_row_with_its_reason(client, login, traps):
    response = client.get(f"{UPLOADS}/{traps}/rejections", headers=login(TRADER))
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == f'attachment; filename="rejections_{traps}.csv"'
    rows = list(csv.DictReader(io.StringIO(response.text.lstrip("﻿"))))
    assert [r["File Line"] for r in rows] == ["5", "6", "7", "8", "11"]  # line numbers as seen in the file
    by_line = {r["File Line"]: r for r in rows}
    assert by_line["7"]["Symbol"] == "ISRG" and "R-005" in by_line["7"]["Rule IDs"] and "ISRG" in by_line["7"]["Rejection Reasons"]
    assert by_line["8"]["Account ID"] == "ACC1011" and by_line["11"]["Trade ID"] == "BLK0001"
    assert by_line["5"]["Trade ID"] == "" and by_line["5"]["Trader Name"] == "John Miller"


def test_uploaded_trades_show_up_in_the_trade_list_by_batch(client, login, traps):
    mine = client.get(f"{TRADES}?batchId={traps}&pageSize=500", headers=login(TRADER)).json()
    assert mine["total"] == 10 and {t["source"] for t in mine["items"]} == {"FILE"}
    other = client.get(f"{TRADES}?batchId={traps}", headers=login(OTHER_TRADER)).json()
    assert [t["tradeId"] for t in other["items"]] == ["BLK0009"]  # only the row that carries their name
    detail = client.get(f"{TRADES}/BLK0002", headers=login(TRADER)).json()
    assert detail["queueId"].startswith("RQ-") and detail["versionsCount"] == 1 and detail["status"] == "EXCEPTION"


def test_uploading_the_same_file_again_rejects_every_row_as_duplicate(client, login, db, traps):
    again = upload(client, login, TRAPS)
    body = progress(client, login, again)
    assert again != traps and body["status"] == "COMPLETED"
    assert body["summary"] == {"accepted": 0, "pass": 0, "monitor": 0, "exception": 0, "rejected": 10}
    trades = batch_trades(db, again)
    for row_number in (1, 2, 3, 8, 9):  # accepted the first time -> now duplicates of trades in the database
        assert "R-001" in {r["ruleId"] for r in trades[row_number].rejection_reasons}
    assert db.scalar(select(func.count()).select_from(Trade).where(Trade.trade_id == "BLK0002", Trade.status != "REJECTED")) == 1


# ---------------------------------------------------------------- chunks
def test_file_is_processed_in_chunks_with_one_engine_call_each(client, login, db, config, monkeypatch):
    config(batch_size=3)
    sizes = []
    real = upload_service.score_trades

    def counting(records, *args, **kwargs):
        sizes.append(len(records))
        return real(records, *args, **kwargs)

    monkeypatch.setattr(upload_service, "score_trades", counting)
    lines = [line(f"CHK{n:04d}") for n in range(1, 8)]
    lines[5] = line("CHK0001", cp="BlackRock")  # same ID as a row in an EARLIER chunk
    batch_id = upload(client, login, csv_text(*lines))

    assert sizes == [3, 3, 1]
    body = progress(client, login, batch_id)
    assert (body["status"], body["processedRows"]) == ("COMPLETED", 7)
    assert body["summary"] == {"accepted": 6, "pass": 6, "monitor": 0, "exception": 0, "rejected": 1}
    trades = batch_trades(db, batch_id)
    assert sorted(trades) == [1, 2, 3, 4, 5, 6, 7]
    assert trades[6].status == "REJECTED" and trades[6].rejection_reasons[0]["ruleId"] == "R-001"


def test_error_in_a_later_chunk_fails_the_batch_but_keeps_earlier_chunks(client, login, db, config, monkeypatch):
    config(batch_size=2)
    real, calls = upload_service._save_chunk, []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("secret database detail")
        return real(*args, **kwargs)

    monkeypatch.setattr(upload_service, "_save_chunk", flaky)
    batch_id = upload(client, login, csv_text(*[line(f"ERR{n:04d}") for n in range(1, 6)]))

    body = progress(client, login, batch_id)
    assert (body["status"], body["totalRows"], body["processedRows"]) == ("FAILED", 5, 2)
    assert body["summary"]["accepted"] == 2 and body["completedAt"]
    assert "unexpected error" in body["errorMessage"] and "secret" not in body["errorMessage"]
    assert sorted(batch_trades(db, batch_id)) == [1, 2]
    note = db.scalar(select(Notification).where(Notification.title.contains(batch_id)))
    assert (note.type, note.severity) == ("UPLOAD_FAILED", "CRITICAL")
    done = db.scalar(select(AuditLog).where(AuditLog.entity_id == batch_id, AuditLog.action == "BULK_UPLOAD_COMPLETED"))
    assert done.details["status"] == "FAILED"


def test_file_with_more_rows_than_the_limit_fails_and_saves_nothing(client, login, db, config):
    config(max_upload_rows=2)
    batch_id = upload(client, login, csv_text(*[line(f"MAX{n:04d}") for n in range(1, 4)]))
    body = progress(client, login, batch_id)
    assert body["status"] == "FAILED" and "maximum is 2" in body["errorMessage"]
    assert body["processedRows"] == 0 and batch_trades(db, batch_id) == {}


# ---------------------------------------------------------------- file formats
def test_camel_case_headers_iso_dates_and_other_boolean_spellings(client, login, db):
    text = ("tradeId,tradeType,symbol,quantity,price,counterparty,tradeDate,settlementDate,accountId,"
            "ssiMatched,currencyMatched,traderName\r\n"
            "FMT0001, sell ,aapl,100.0,99.5,jefferies,2026-09-29,2026-10-01,ACC1000, true ,Y,John Miller\r\n")
    batch_id = upload(client, login, text)
    trade = batch_trades(db, batch_id)[1]
    assert (trade.status, trade.trade_type, trade.symbol, trade.quantity, trade.counterparty) == (
        "PASS", "SELL", "AAPL", 100, "Jefferies")
    assert trade.ssi_matched is True and trade.currency_matched is True and str(trade.settlement_date) == "2026-10-01"


def test_garbage_values_are_rejected_rows_not_a_crash(client, login, db):
    batch_id = upload(client, login, csv_text(
        line("GARBAGE-" + "9" * 60, type="HOLD", symbol="S" * 40, qty="lots", price="1e30", cp="C" * 150,
             trade="yesterday", settle="13/45/2026", account="A" * 40, ssi="maybe", ccy="", trader="T" * 150),
        line("GAR0002", qty="-5", price="0", settle="9/1/2026"),
        line("=GAR0003", symbol="=1+1")))
    body = progress(client, login, batch_id)
    assert body["status"] == "COMPLETED" and body["summary"]["rejected"] == 3
    trades = batch_trades(db, batch_id)
    first = trades[1]
    assert first.trade_type is None and first.quantity is None and first.trade_date is None and len(first.symbol) == 20
    assert first.raw_payload["Trade Type"] == "HOLD" and len(first.rejection_reasons) >= 5
    assert {r["ruleId"] for r in trades[2].rejection_reasons} >= {"R-003", "R-004", "R-008"}
    report = client.get(f"{UPLOADS}/{batch_id}/rejections", headers=login(TRADER)).text
    assert "'=1+1" in report  # formula text is neutralised for Excel


def test_header_only_file_becomes_a_failed_batch(client, login):
    body = progress(client, login, upload(client, login, HEADER + "\r\n"))
    assert body["status"] == "FAILED" and "no data rows" in body["errorMessage"] and body["totalRows"] == 0


def test_bad_files_are_refused_before_a_batch_is_created(client, login, db, monkeypatch):
    before = db.scalar(select(func.count()).select_from(UploadBatch))
    headers = login(TRADER)

    def post(name, content):
        return client.post(BULK, files={"file": (name, content, "text/csv")}, headers=headers)

    assert "Only .csv" in assert_error_shape(post("trades.xlsx", b"PK"), 400, "BAD_REQUEST")["message"]
    assert "empty" in assert_error_shape(post("trades.csv", b""), 400, "BAD_REQUEST")["message"]
    monkeypatch.setattr(upload_service, "MAX_FILE_BYTES", 20)
    assert "20 MB" in assert_error_shape(post("trades.csv", TRAPS.encode()), 400, "BAD_REQUEST")["message"]
    assert_error_shape(client.post(BULK, headers=headers), 422, "VALIDATION_ERROR")  # no file part at all
    assert db.scalar(select(func.count()).select_from(UploadBatch)) == before


# ---------------------------------------------------------------- permissions
@pytest.mark.parametrize("username", ["ops.user1", "ops.manager", "ml.admin"])
def test_only_traders_and_admin_can_upload(client, login, username, traps):
    files = {"file": ("trades.csv", TRAPS.encode(), "text/csv")}
    assert_error_shape(client.post(BULK, files=files, headers=login(username)), 403, "FORBIDDEN")
    assert_error_shape(client.get(f"{UPLOADS}/{traps}", headers=login(username)), 403, "FORBIDDEN")
    assert_error_shape(client.get(f"{UPLOADS}/{traps}/rejections", headers=login(username)), 403, "FORBIDDEN")


def test_no_token_is_401(client, traps):
    assert_error_shape(client.post(BULK, files={"file": ("t.csv", b"x", "text/csv")}), 401, "UNAUTHORIZED")
    assert_error_shape(client.get(f"{UPLOADS}/{traps}"), 401, "UNAUTHORIZED")
    assert_error_shape(client.get(f"{UPLOADS}/{traps}/rejections"), 401, "UNAUTHORIZED")


def test_batches_are_private_to_the_uploader_except_for_sys_admin(client, login, traps):
    assert_error_shape(client.get(f"{UPLOADS}/{traps}", headers=login(OTHER_TRADER)), 404, "NOT_FOUND")
    assert_error_shape(client.get(f"{UPLOADS}/{traps}/rejections", headers=login(OTHER_TRADER)), 404, "NOT_FOUND")
    assert_error_shape(client.get(f"{UPLOADS}/BATCH-19990101-0001", headers=login(TRADER)), 404, "NOT_FOUND")
    assert progress(client, login, traps, user=ADMIN)["batchId"] == traps
    admin_batch = upload(client, login, csv_text(line("ADM0001")), user=ADMIN)
    assert progress(client, login, admin_batch, user=ADMIN)["summary"]["accepted"] == 1
    assert_error_shape(client.get(f"{UPLOADS}/{admin_batch}", headers=login(TRADER)), 404, "NOT_FOUND")


# ---------------------------------------------------------------- restart safety
def test_batches_cut_off_by_a_restart_are_marked_failed(db):
    uploader = db.scalar(select(User.id).where(User.username == TRADER))
    stuck = UploadBatch(batch_id="BATCH-20000101-0001", file_name="old.csv", uploaded_by=uploader,
                        status="PROCESSING", total_rows=10, processed_rows=4)
    db.add(stuck)
    db.commit()
    assert upload_service.fail_interrupted_batches(db) == 1
    db.refresh(stuck)
    assert stuck.status == "FAILED" and "restarted" in stuck.error_message and stuck.completed_at
    assert upload_service.fail_interrupted_batches(db) == 0  # finished batches are never touched
