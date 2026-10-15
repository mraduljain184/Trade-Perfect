"""BE-5: audit endpoints 30-36, PDF report and e-mail outbox."""
import csv
import io
import itertools
import re
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import AuditLog, EmailOutbox, SystemConfig, User
from app.services import audit_service, email_service, engine_adapter, report_service, upload_service
from tests.conftest import assert_error_shape

AUDIT, TRADES, QUEUE = "/api/v1/audit", "/api/v1/trades", "/api/v1/repair-queue"
UPLOADER, OTHER_TRADER, OPS1, OPS2, MANAGER, ADMIN = "aisha.khan", "nina.petrova", "ops.user1", "ops.user2", "ops.manager", "sys.admin"
HEADER = "tradeId,tradeType,symbol,quantity,price,counterparty,tradeDate,settlementDate,accountId,ssiMatched,currencyMatched,traderName"
RISKY = "BUY,AAPL,1000,10250.5,BlackRock,2026-09-29,2026-10-01,ACC1000,NO,NO,Aisha Khan"   # 0.75 EXCEPTION / HIGH
_numbers = itertools.count(1)


@pytest.fixture(autouse=True, scope="module")
def _setup(_database, tmp_path_factory):
    """Fixed business date (2026-09-28), and reports written to a temp folder instead of the repository."""
    from app.core.database import SessionLocal

    def business_date(value):
        with SessionLocal() as session:
            session.get(SystemConfig, "business_date_override").value = value
            session.commit()
        engine_adapter.invalidate_cache()

    business_date("2026-09-28")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(upload_service, "REPORT_DIR", tmp_path_factory.mktemp("reports"))
        patch.setattr(report_service, "PAGE_COMPRESSION", 0)
        yield
    business_date(None)


@pytest.fixture()
def make_trade(client, login):
    """A new EXCEPTION trade with a queue item, booked through bulk upload: {"tradeId", "queueId"}."""
    def _make():
        trade_id = f"AUD{next(_numbers):05d}"
        text = f"{HEADER}\r\n{trade_id},{RISKY}\r\n"
        assert client.post(f"{TRADES}/bulk", files={"file": ("a.csv", text.encode(), "text/csv")},
                           headers=login(UPLOADER)).status_code == 202
        found = client.get(f"{QUEUE}?search={trade_id}", headers=login(MANAGER)).json()["items"]
        return {"tradeId": trade_id, "queueId": found[0]["queueId"]}

    return _make


@pytest.fixture(scope="module")
def story(client, login, _setup):
    """One trade with a full life: booked, assigned, fixed, re-submitted."""
    from app.core.database import SessionLocal

    trade_id = "AUDSTORY1"
    text = f"{HEADER}\r\n{trade_id},{RISKY}\r\n"
    assert client.post(f"{TRADES}/bulk", files={"file": ("s.csv", text.encode(), "text/csv")}, headers=login(UPLOADER)).status_code == 202
    queue_id = client.get(f"{QUEUE}?search={trade_id}", headers=login(MANAGER)).json()["items"][0]["queueId"]
    with SessionLocal() as session:
        ops1 = session.scalar(select(User.id).where(User.username == OPS1))
    client.post(f"{QUEUE}/assign", json={"queueIds": [queue_id], "assigneeId": ops1}, headers=login(MANAGER))
    fixed = {"tradeType": "BUY", "symbol": "AAPL", "quantity": 1000, "price": 10250.5, "counterparty": "BlackRock",
             "tradeDate": "2026-09-29", "settlementDate": "2026-10-01", "accountId": "ACC1000", "ssiMatched": True,
             "currencyMatched": True, "queueId": queue_id, "appliedFix": "SSI and currency confirmed with BlackRock"}
    assert client.put(f"{TRADES}/{trade_id}", json=fixed, headers=login(OPS1)).status_code == 200
    assert client.post(f"{QUEUE}/{queue_id}/resubmit", headers=login(OPS1)).status_code == 200
    return {"tradeId": trade_id, "queueId": queue_id}


STORY_ACTIONS = ["TRADE_SUBMITTED", "QUEUE_ITEM_CREATED", "QUEUE_ASSIGNED", "FIX_APPLIED", "TRADE_RESUBMITTED", "QUEUE_RESOLVED"]


# ---------------------------------------------------------------- timeline + latest (31, 32)
def test_timeline_is_the_trade_life_in_order(client, login, story):
    events = client.get(f"{AUDIT}/trades/{story['tradeId']}/timeline", headers=login(MANAGER)).json()
    assert [e["action"] for e in events] == STORY_ACTIONS
    assert [e["id"] for e in events] == sorted(e["id"] for e in events)
    first = events[0]
    assert set(first) == {"id", "eventTime", "actorUsername", "actorRole", "action", "entityType", "tradeId", "queueId",
                          "riskScore", "statusAfter", "likelyReason", "recommendedFix", "appliedFix", "details"}
    assert (first["actorUsername"], first["actorRole"], first["riskScore"], first["statusAfter"]) == (UPLOADER, "TRADER", 0.75, "EXCEPTION")
    fix = events[3]
    assert fix["appliedFix"] == "SSI and currency confirmed with BlackRock" and fix["actorUsername"] == OPS1
    assert fix["riskScore"] == 0.25 and fix["queueId"] == story["queueId"]
    assert_error_shape(client.get(f"{AUDIT}/trades/NOPE123/timeline", headers=login(MANAGER)), 404, "NOT_FOUND")


def test_latest_audit_box(client, login, story):
    box = client.get(f"{AUDIT}/trades/{story['tradeId']}/latest", headers=login(MANAGER)).json()
    assert set(box) == {"tradeId", "queueId", "riskScore", "likelyReason", "recommendedFix", "appliedFix", "appliedBy",
                        "appliedTimestamp", "status"}
    assert (box["tradeId"], box["queueId"], box["riskScore"], box["status"]) == (story["tradeId"], story["queueId"], 0.25, "PASS")
    assert box["appliedFix"] == "SSI and currency confirmed with BlackRock" and box["appliedBy"] == "Ops User One"
    assert box["appliedTimestamp"]
    assert client.get(f"{AUDIT}/trades/{story['tradeId']}/latest", headers=login(UPLOADER)).status_code == 200   # own trade
    assert_error_shape(client.get(f"{AUDIT}/trades/{story['tradeId']}/latest", headers=login(OTHER_TRADER)), 404, "NOT_FOUND")
    assert_error_shape(client.get(f"{AUDIT}/trades/{story['tradeId']}/latest", headers=login("ml.admin")), 403, "FORBIDDEN")


# ---------------------------------------------------------------- search (30)
def test_search_filters(client, login, story, db):
    headers = login(MANAGER)

    def search(query):
        response = client.get(f"{AUDIT}?pageSize=500&{query}", headers=headers)
        assert response.status_code == 200, response.text
        return response.json()

    body = search(f"tradeId={story['tradeId']}")
    assert set(body) == {"items", "total", "page", "pageSize"} and body["total"] == 6
    assert [e["action"] for e in body["items"]] == STORY_ACTIONS[::-1]                 # newest first
    assert [e["action"] for e in search(f"tradeId={story['tradeId']}&action=FIX_APPLIED,QUEUE_RESOLVED")["items"]] == [
        "QUEUE_RESOLVED", "FIX_APPLIED"]
    ops1 = db.scalar(select(User.id).where(User.username == OPS1))
    assert {e["action"] for e in search(f"tradeId={story['tradeId']}&userId={ops1}")["items"]} == {
        "FIX_APPLIED", "TRADE_RESUBMITTED", "QUEUE_RESOLVED"}
    assert search(f"tradeId={story['tradeId']}&dateField=tradeDate&dateFrom=2026-09-29&dateTo=2026-09-29")["total"] == 6
    assert search(f"tradeId={story['tradeId']}&dateField=tradeDate&dateFrom=2026-09-30")["total"] == 0
    assert search(f"tradeId={story['tradeId']}&dateField=settlementDate&dateTo=2026-10-01")["total"] == 6
    assert search(f"tradeId={story['tradeId']}&dateFrom=2020-01-01&dateTo=2020-01-02")["total"] == 0   # event time
    logins = search("action=LOGIN&pageSize=5")
    assert logins["total"] >= 1 and all(e["action"] == "LOGIN" and e["tradeId"] is None for e in logins["items"])
    assert search("pageSize=2&page=2")["page"] == 2
    assert search("pageSize=1")["total"] == db.scalar(select(func.count()).select_from(AuditLog))


@pytest.mark.parametrize("query, status, code", [
    ("dateField=createdAt", 400, "BAD_REQUEST"), ("action=HACKED", 400, "BAD_REQUEST"),
    ("dateFrom=10/10/2026", 422, "VALIDATION_ERROR"), ("userId=abc", 422, "VALIDATION_ERROR"),
    ("pageSize=501", 422, "VALIDATION_ERROR"),
])
def test_search_rejects_bad_query(client, login, query, status, code):
    assert_error_shape(client.get(f"{AUDIT}?{query}", headers=login(MANAGER)), status, code)


# ---------------------------------------------------------------- export (33)
def test_export_has_exactly_the_fr_at_006_columns(client, login, story, db):
    response = client.get(f"{AUDIT}/export?tradeId={story['tradeId']}", headers=login(MANAGER))
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert re.fullmatch(r'attachment; filename="audit_\d{8}\.csv"', response.headers["content-disposition"])
    rows = list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert rows[0] == ["Trade Id", "Queue ID", "Risk Score", "Likely Reason", "Recommended Fix", "Applied Fix",
                       "Applied By", "Applied Timestamp", "Status"]
    assert len(rows) == 7
    fix = dict(zip(rows[0], rows[4]))                                                  # the FIX_APPLIED event
    assert (fix["Trade Id"], fix["Queue ID"], fix["Risk Score"], fix["Status"]) == (story["tradeId"], story["queueId"], "0.2500", "PASS")
    assert fix["Applied Fix"] == "SSI and currency confirmed with BlackRock" and fix["Applied By"] == OPS1
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", fix["Applied Timestamp"])
    assert dict(zip(rows[0], rows[1]))["Applied By"] == ""                             # no fix on the booking event

    detailed = list(csv.reader(io.StringIO(client.get(f"{AUDIT}/export?tradeId={story['tradeId']}&detailed=true",
                                                      headers=login(MANAGER)).text.lstrip("\ufeff"))))
    assert detailed[0][9:] == ["Action", "User", "Event Time"] and [r[9] for r in detailed[1:]] == STORY_ACTIONS
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "EXPORT_CSV").order_by(AuditLog.id.desc()))
    assert audit.entity_type == "AUDIT" and audit.details["rows"] == 6 and audit.details["export"] == "audit"


# ---------------------------------------------------------------- PDF report + e-mail outbox (34, 35)
def test_report_pdf_contains_the_full_timeline_and_is_emailed(client, login, story, db):
    response = client.post(f"{AUDIT}/trades/{story['tradeId']}/report", headers=login(MANAGER))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"reportUrl", "emailStatus", "fileName", "emailTo"}
    assert re.fullmatch(rf"audit_{story['tradeId']}_\d{{8}}_\d{{9}}\.pdf", body["fileName"])
    assert body["reportUrl"] == f"/api/v1/audit/reports/{body['fileName']}"
    assert body["emailTo"] == ["compliance@tradeperfect.local"]                        # default from the config
    assert body["emailStatus"] == ("PENDING" if get_settings().smtp_host else "LOGGED_ONLY")

    pdf = client.get(body["reportUrl"], headers=login(MANAGER))
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF") and len(pdf.content) > 2000
    text = pdf.content.decode("latin-1")
    for expected in ["Audit Report", story["tradeId"], story["queueId"], "Audit box", *STORY_ACTIONS, "ops.user1",
                     "VALID", "Ops Manager"]:
        assert expected in text, expected

    mail = db.scalar(select(EmailOutbox).where(EmailOutbox.attachment_path == f"reports/{body['fileName']}"))
    assert mail.purpose == "AUDIT_REPORT" and mail.to_addresses == "compliance@tradeperfect.local"
    assert story["tradeId"] in mail.subject and mail.status == body["emailStatus"]
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "AUDIT_REPORT_GENERATED").order_by(AuditLog.id.desc()))
    assert audit.trade_id == story["tradeId"] and audit.details["fileName"] == body["fileName"] and audit.details["events"] == 6
    events = client.get(f"{AUDIT}/trades/{story['tradeId']}/timeline", headers=login(MANAGER)).json()
    assert events[-1]["action"] == "AUDIT_REPORT_GENERATED"                           # the report itself is audited


def test_report_recipients_can_be_chosen_and_are_validated(client, login, story, db):
    url = f"{AUDIT}/trades/{story['tradeId']}/report"
    body = client.post(url, json={"emailTo": [" auditor@bank.example ", "trader@bank.example"]}, headers=login(MANAGER)).json()
    assert body["emailTo"] == ["auditor@bank.example", "trader@bank.example"]
    mail = db.scalar(select(EmailOutbox).where(EmailOutbox.attachment_path == f"reports/{body['fileName']}"))
    assert mail.to_addresses == "auditor@bank.example, trader@bank.example"
    for bad in ({"emailTo": ["not-an-address"]}, {"emailTo": "someone@bank.example"}, {"emailTo": ["a@b.c"] * 11}):
        assert_error_shape(client.post(url, json=bad, headers=login(MANAGER)), 422, "VALIDATION_ERROR")
    assert_error_shape(client.post(f"{AUDIT}/trades/NOPE123/report", headers=login(MANAGER)), 404, "NOT_FOUND")


@pytest.mark.parametrize("name", ["audit_NOPE_20260101_000000000.pdf", "notes.pdf", "audit_x.txt", "..%2F..%2F.env",
                                  "audit_..%2Fsecret_20260101_000000000.pdf"])
def test_only_generated_reports_can_be_downloaded(client, login, name):
    assert_error_shape(client.get(f"{AUDIT}/reports/{name}", headers=login(ADMIN)), 404, "NOT_FOUND")


# ---------------------------------------------------------------- verify (36)
def test_verify_chain_endpoint(client, login):
    assert client.get(f"{AUDIT}/verify", headers=login(ADMIN)).json() == {"valid": True, "brokenAtId": None}
    for username in (MANAGER, OPS1):
        assert_error_shape(client.get(f"{AUDIT}/verify", headers=login(username)), 403, "FORBIDDEN")


# ---------------------------------------------------------------- scoping + permissions
def test_ops_user_only_sees_audit_of_trades_assigned_to_them(client, login, make_trade, db):
    mine, theirs = make_trade(), make_trade()
    ops1, ops2 = (db.scalar(select(User.id).where(User.username == name)) for name in (OPS1, OPS2))
    client.post(f"{QUEUE}/assign", json={"queueIds": [mine["queueId"]], "assigneeId": ops1}, headers=login(MANAGER))
    client.post(f"{QUEUE}/assign", json={"queueIds": [theirs["queueId"]], "assigneeId": ops2}, headers=login(MANAGER))

    seen = client.get(f"{AUDIT}?pageSize=500", headers=login(OPS1)).json()
    trade_ids = {e["tradeId"] for e in seen["items"]}
    assert mine["tradeId"] in trade_ids and theirs["tradeId"] not in trade_ids and None not in trade_ids   # no logins etc.
    assert seen["total"] < client.get(f"{AUDIT}?pageSize=1", headers=login(MANAGER)).json()["total"]
    assert client.get(f"{AUDIT}/trades/{mine['tradeId']}/timeline", headers=login(OPS1)).status_code == 200
    assert_error_shape(client.get(f"{AUDIT}/trades/{theirs['tradeId']}/timeline", headers=login(OPS1)), 404, "NOT_FOUND")
    assert_error_shape(client.post(f"{AUDIT}/trades/{theirs['tradeId']}/report", headers=login(OPS1)), 404, "NOT_FOUND")
    exported = client.get(f"{AUDIT}/export", headers=login(OPS1)).text
    assert mine["tradeId"] in exported and theirs["tradeId"] not in exported

    report = client.post(f"{AUDIT}/trades/{theirs['tradeId']}/report", headers=login(OPS2)).json()
    assert client.get(report["reportUrl"], headers=login(OPS2)).status_code == 200
    assert_error_shape(client.get(report["reportUrl"], headers=login(OPS1)), 404, "NOT_FOUND")   # someone else's trade


@pytest.mark.parametrize("username", [UPLOADER, "ml.admin"])
def test_traders_and_ml_admin_cannot_read_the_audit_trail(client, login, story, username):
    headers = login(username)
    for call in (client.get(AUDIT, headers=headers), client.get(f"{AUDIT}/export", headers=headers),
                 client.get(f"{AUDIT}/verify", headers=headers),
                 client.get(f"{AUDIT}/trades/{story['tradeId']}/timeline", headers=headers),
                 client.post(f"{AUDIT}/trades/{story['tradeId']}/report", headers=headers),
                 client.get(f"{AUDIT}/reports/audit_X_20260101_000000000.pdf", headers=headers)):
        assert_error_shape(call, 403, "FORBIDDEN")


def test_no_token_is_401(client, story):
    for call in (client.get(AUDIT), client.get(f"{AUDIT}/export"), client.get(f"{AUDIT}/verify"),
                 client.get(f"{AUDIT}/trades/{story['tradeId']}/timeline"),
                 client.get(f"{AUDIT}/trades/{story['tradeId']}/latest"),
                 client.post(f"{AUDIT}/trades/{story['tradeId']}/report")):
        assert_error_shape(call, 401, "UNAUTHORIZED")


# ---------------------------------------------------------------- e-mail sending
class FakeSmtp:
    sent, fail_for = [], None

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def ehlo(self):
        pass

    def has_extn(self, name):
        return False

    def login(self, user, password):
        FakeSmtp.logged_in = (user, password)

    def send_message(self, message):
        if FakeSmtp.fail_for and FakeSmtp.fail_for in message["To"]:
            raise OSError("mailbox unavailable")
        FakeSmtp.sent.append(message)


def test_without_smtp_emails_are_only_logged_and_nothing_is_sent(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_host", "")
    row = email_service.queue_email(db, ["a@bank.example"], "Subject", "Body", "AUDIT_REPORT")
    db.commit()
    assert row.status == "LOGGED_ONLY" and email_service.send_pending() == 0


def test_with_smtp_pending_emails_are_sent_with_the_attachment(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.bank.example")
    monkeypatch.setattr(settings, "smtp_user", "mailer")
    monkeypatch.setattr(settings, "smtp_password", "not-a-real-password")
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSmtp)
    FakeSmtp.sent, FakeSmtp.fail_for = [], "broken@"
    (Path(upload_service.REPORT_DIR) / "audit_MAIL_20260101_000000000.pdf").write_bytes(b"%PDF-1.4 test")

    good = email_service.queue_email(db, ["auditor@bank.example"], "Audit report", "See attachment", "AUDIT_REPORT",
                                     attachment_path="reports/audit_MAIL_20260101_000000000.pdf")
    bad = email_service.queue_email(db, ["broken@bank.example"], "Audit report", "See attachment", "AUDIT_REPORT")
    db.commit()
    assert (good.status, bad.status) == ("PENDING", "PENDING")

    assert email_service.send_pending() == 1                                          # one failure does not stop the other
    db.refresh(good)
    db.refresh(bad)
    assert good.status == "SENT" and good.sent_at is not None
    assert bad.status == "FAILED" and "mailbox unavailable" in bad.error_message
    message = FakeSmtp.sent[0]
    assert message["To"] == "auditor@bank.example" and message["From"] == settings.smtp_from
    attachment = next(message.iter_attachments())
    assert attachment.get_filename() == "audit_MAIL_20260101_000000000.pdf" and attachment.get_content_type() == "application/pdf"
    assert FakeSmtp.logged_in == ("mailer", "not-a-real-password")
    assert email_service.send_pending() == 0                                          # nothing left to send


def test_a_dead_mail_server_never_raises(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.bank.example")

    def boom(*args, **kwargs):
        raise ConnectionRefusedError("no server")

    monkeypatch.setattr(email_service.smtplib, "SMTP", boom)
    row = email_service.queue_email(db, ["x@bank.example"], "S", "B", "AUDIT_REPORT")
    db.commit()
    assert email_service.send_pending() == 0
    db.refresh(row)
    assert row.status == "FAILED" and "no server" in row.error_message


def test_audit_chain_is_valid_after_this_module(db):
    assert audit_service.verify_chain(db) == (True, None)
