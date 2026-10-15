"""BE-4: repair queue workflows + team performance (endpoints 20-29)."""
import csv
import io
import itertools

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Notification, RepairQueueItem, SystemConfig, Trade, TradeVersion, User
from app.services import audit_service, engine_adapter
from tests.conftest import assert_error_shape

QUEUE, TRADES, TEAM = "/api/v1/repair-queue", "/api/v1/trades", "/api/v1/team/performance"
UPLOADER, OPS1, OPS2, OPS3, MANAGER, ADMIN = "robert.taylor", "ops.user1", "ops.user2", "ops.user3", "ops.manager", "sys.admin"

# Clean trade (score 0). The three variants below give one item of each priority (business date 2026-09-28).
BASE = {"tradeType": "BUY", "symbol": "AAPL", "quantity": 1000, "price": 10250.5, "counterparty": "Jefferies",
        "tradeDate": "2026-09-29", "settlementDate": "2026-10-01", "accountId": "ACC1000",
        "ssiMatched": True, "currencyMatched": True}
KINDS = {
    "HIGH": {"counterparty": "BlackRock", "ssiMatched": False, "currencyMatched": False},  # 0.25+0.35+0.15 = 0.75
    "MEDIUM": {"counterparty": "BlackRock", "ssiMatched": False},                          # 0.25+0.35      = 0.60
    "LOW": {"quantity": 9000},                                                             # 0.10
}
HEADER = "tradeId,tradeType,symbol,quantity,price,counterparty,tradeDate,settlementDate,accountId,ssiMatched,currencyMatched,traderName"
_numbers = itertools.count(1)


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


@pytest.fixture()
def make(client, login):
    """make("HIGH", "LOW") -> one new queue item per priority: [{"queueId", "tradeId", "payload"}, ...].
    The trades come in through bulk upload, so no UI trade IDs are used up."""

    def _make(*kinds):
        made, lines = [], [HEADER]
        for kind in kinds:
            payload = {**BASE, **KINDS[kind]}
            trade_id = f"RQT{next(_numbers):05d}"
            cells = [trade_id, *[("YES" if v else "NO") if isinstance(v, bool) else str(v) for v in payload.values()],
                     "Robert Taylor"]
            lines.append(",".join(cells))
            made.append({"tradeId": trade_id, "payload": payload, "kind": kind})
        response = client.post(f"{TRADES}/bulk", files={"file": ("queue.csv", "\r\n".join(lines).encode(), "text/csv")},
                               headers=login(UPLOADER))
        assert response.status_code == 202, response.text
        for entry in made:
            found = client.get(f"{QUEUE}?search={entry['tradeId']}", headers=login(MANAGER)).json()["items"]
            assert len(found) == 1 and found[0]["priority"] == entry["kind"], found
            entry["queueId"] = found[0]["queueId"]
        return made

    return _make


@pytest.fixture()
def business_date(db):
    """business_date("2026-10-05") moves 'today'; it is put back after the test."""
    def change(value):
        db.get(SystemConfig, "business_date_override").value = value
        db.commit()
        engine_adapter.invalidate_cache()

    yield change
    change("2026-09-28")


def user_id(db, username):
    return db.scalar(select(User.id).where(User.username == username))


def assign(client, login, queue_ids, assignee_id, by=MANAGER):
    return client.post(f"{QUEUE}/assign", json={"queueIds": queue_ids, "assigneeId": assignee_id}, headers=login(by))


def item(client, login, queue_id, by=MANAGER):
    response = client.get(f"{QUEUE}/{queue_id}", headers=login(by))
    assert response.status_code == 200, response.text
    return response.json()


def audit_actions(db, queue_id):
    return db.scalars(select(AuditLog.action).where(AuditLog.queue_id == queue_id).order_by(AuditLog.id)).all()


# ---------------------------------------------------------------- reading (20-22)
def test_queue_item_shape_and_server_side_flags(client, login, make):
    high, medium, low = make("HIGH", "MEDIUM", "LOW")
    body = item(client, login, high["queueId"])
    assert set(body) == {"queueId", "tradeId", "trade", "priority", "status", "riskScore", "suggestedFixes", "assignedTo",
                         "assignedAt", "appliedFix", "appliedBy", "appliedAt", "canResubmit", "canReject",
                         "createdAt", "updatedAt"}
    assert (body["status"], body["priority"], body["riskScore"], body["tradeId"]) == ("OPEN", "HIGH", 0.75, high["tradeId"])
    assert body["assignedTo"] is None and body["appliedFix"] is None
    assert body["trade"]["tradeId"] == high["tradeId"] and body["trade"]["queueId"] == high["queueId"]
    assert {h["ruleId"] for h in body["trade"]["ruleHits"]} == {"S-SSI", "S-CCY", "S-CP-TIER"}
    assert body["suggestedFixes"] and set(body["suggestedFixes"][0]) == {"factor", "fix"}
    assert (body["canResubmit"], body["canReject"]) == (False, True)          # HIGH: fix or reject
    for other in (medium, low):
        flags = item(client, login, other["queueId"])
        assert (flags["canResubmit"], flags["canReject"]) == (True, False)    # MEDIUM / LOW: re-submit


def test_list_default_order_paging_and_filters(client, login, make, db):
    high, medium, low = make("HIGH", "MEDIUM", "LOW")
    headers = login(MANAGER)
    page = client.get(f"{QUEUE}?pageSize=500", headers=headers).json()
    assert set(page) == {"items", "total", "page", "pageSize"} and len(page["items"]) == min(page["total"], 500) >= 3
    ranks = [("HIGH", "MEDIUM", "LOW").index(i["priority"]) for i in page["items"]]
    assert ranks == sorted(ranks)                                             # HIGH first ...
    scores = [i["riskScore"] for i in page["items"] if i["priority"] == "HIGH"]
    assert scores == sorted(scores, reverse=True)                             # ... then the riskiest
    assert client.get(QUEUE, headers=headers).json()["pageSize"] == 50
    assert len(client.get(f"{QUEUE}?pageSize=2&page=2", headers=headers).json()["items"]) == 2

    def ids(query):
        # only this file's trades (RQT...), so one page always holds them all; a later search= in `query` wins
        response = client.get(f"{QUEUE}?pageSize=500&search=RQT&{query}", headers=headers)
        assert response.status_code == 200, response.text
        return {i["queueId"] for i in response.json()["items"]}

    mine ={high["queueId"], medium["queueId"], low["queueId"]}
    assert ids("priority=HIGH") & mine == {high["queueId"]}
    assert ids("priority=medium,low") & mine == {medium["queueId"], low["queueId"]}
    assert ids("status=OPEN") >= mine and not ids("status=REJECTED") & mine
    assert ids(f"search={high['tradeId']}") == {high["queueId"]} and ids(f"search={low['queueId']}") == {low["queueId"]}
    assign(client, login, [medium["queueId"]], user_id(db, OPS2))
    assert ids(f"assignedTo={user_id(db, OPS2)}") & mine == {medium["queueId"]}
    assert ids("assignedTo=unassigned") & mine == {high["queueId"], low["queueId"]}
    assert not ids("assignedTo=me") & mine
    asc = [i["riskScore"] for i in client.get(f"{QUEUE}?sort=riskScore:asc&pageSize=500", headers=headers).json()["items"]]
    assert asc == sorted(asc)


@pytest.mark.parametrize("query, status, code", [
    ("status=DONE", 400, "BAD_REQUEST"), ("priority=URGENT", 400, "BAD_REQUEST"), ("assignedTo=bob", 400, "BAD_REQUEST"),
    ("sort=password:asc", 400, "BAD_REQUEST"), ("pageSize=501", 422, "VALIDATION_ERROR"), ("page=0", 422, "VALIDATION_ERROR"),
])
def test_list_rejects_bad_query(client, login, query, status, code):
    assert_error_shape(client.get(f"{QUEUE}?{query}", headers=login(MANAGER)), status, code)


def test_summary_matches_sql(client, login, make, db):
    make("HIGH", "LOW")
    body = client.get(f"{QUEUE}/summary", headers=login(MANAGER)).json()
    assert set(body) == {"total", "byStatus", "byPriority"}
    by_status = dict(db.execute(select(RepairQueueItem.status, func.count()).group_by(RepairQueueItem.status)).all())
    by_priority = dict(db.execute(select(RepairQueueItem.priority, func.count()).group_by(RepairQueueItem.priority)).all())
    assert body["byStatus"] == {s: by_status.get(s, 0) for s in ("OPEN", "IN_PROGRESS", "RESOLVED", "REJECTED")}
    assert body["byPriority"] == {p: by_priority.get(p, 0) for p in ("HIGH", "MEDIUM", "LOW")}
    assert body["total"] == sum(by_status.values()) == sum(by_priority.values())


def test_unknown_queue_item_is_404(client, login):
    for call in (client.get(f"{QUEUE}/RQ-999999", headers=login(MANAGER)),
                 client.post(f"{QUEUE}/RQ-999999/resubmit", headers=login(MANAGER)),
                 client.post(f"{QUEUE}/RQ-999999/reject", json={"reason": "not needed"}, headers=login(MANAGER)),
                 client.post(f"{QUEUE}/RQ-999999/approve", json={"note": "looks fine"}, headers=login(MANAGER))):
        assert_error_shape(call, 404, "NOT_FOUND")


# ---------------------------------------------------------------- assign / unassign (23)
def test_manager_assigns_reassigns_and_unassigns(client, login, make, db):
    (one,) = make("HIGH")
    ops1, ops2 = user_id(db, OPS1), user_id(db, OPS2)

    assert assign(client, login, [one["queueId"]], ops1).json() == {"succeeded": [one["queueId"]], "failed": []}
    body = item(client, login, one["queueId"])
    assert body["status"] == "IN_PROGRESS" and body["assignedTo"] == {"id": ops1, "fullName": "Ops User One"}
    assert body["assignedAt"] and body["trade"]["assignedTo"] == "Ops User One" and body["trade"]["queueStatus"] == "IN_PROGRESS"
    note = db.scalar(select(Notification).where(Notification.user_id == ops1, Notification.title.contains(one["queueId"])))
    assert note.type == "ASSIGNMENT" and "Ops Manager" in note.message and one["queueId"] in note.link

    again = assign(client, login, [one["queueId"]], ops1).json()                     # same person: nothing to do
    assert again["succeeded"] == [] and "Already assigned to Ops User One" in again["failed"][0]["reason"]

    assert assign(client, login, [one["queueId"]], ops2).json()["succeeded"] == [one["queueId"]]   # IN_PROGRESS -> IN_PROGRESS
    assert item(client, login, one["queueId"])["assignedTo"]["id"] == ops2

    assert assign(client, login, [one["queueId"]], None).json()["succeeded"] == [one["queueId"]]   # IN_PROGRESS -> OPEN
    body = item(client, login, one["queueId"])
    assert body["status"] == "OPEN" and body["assignedTo"] is None and body["assignedAt"] is None

    not_assigned = assign(client, login, [one["queueId"]], None).json()               # OPEN -> unassign: forbidden
    assert not_assigned["succeeded"] == [] and "OPEN" in not_assigned["failed"][0]["reason"]
    assert audit_actions(db, one["queueId"])[-3:] == ["QUEUE_ASSIGNED", "QUEUE_REASSIGNED", "QUEUE_UNASSIGNED"]
    row = db.scalar(select(AuditLog).where(AuditLog.queue_id == one["queueId"], AuditLog.action == "QUEUE_REASSIGNED"))
    assert row.actor_username == MANAGER and row.entity_type == "REPAIR_QUEUE" and row.entity_id == one["queueId"]
    assert row.details["assigneeId"] == ops2 and row.details["previousAssigneeId"] == ops1


def test_bulk_assign_is_not_all_or_nothing(client, login, make, db):
    first, second = make("LOW", "MEDIUM")
    result = assign(client, login, [first["queueId"], "RQ-999999", second["queueId"], first["queueId"]], user_id(db, OPS1)).json()
    assert result["succeeded"] == [first["queueId"], second["queueId"]]               # the repeated id counts once
    assert result["failed"] == [{"id": "RQ-999999", "reason": "Queue item not found"}]
    notes = db.scalars(select(Notification).where(Notification.user_id == user_id(db, OPS1),
                                                  Notification.message.contains(first["queueId"]))).all()
    assert len(notes) == 1 and notes[0].title == "2 repair queue items assigned to you"   # one message for the whole call


def test_assign_refuses_bad_assignees_and_bad_bodies(client, login, make, db):
    (one,) = make("LOW")
    for bad in (user_id(db, UPLOADER), user_id(db, "ml.admin"), 999999):
        assert_error_shape(assign(client, login, [one["queueId"]], bad), 400, "BAD_REQUEST")
    assert_error_shape(client.post(f"{QUEUE}/assign", json={"queueIds": []}, headers=login(MANAGER)), 422, "VALIDATION_ERROR")
    assert_error_shape(client.post(f"{QUEUE}/assign", json={"assigneeId": 1}, headers=login(MANAGER)), 422, "VALIDATION_ERROR")
    assert item(client, login, one["queueId"])["status"] == "OPEN"


def test_ops_user_can_only_pick_up_unassigned_items_for_themselves(client, login, make, db):
    free, taken = make("LOW", "LOW")
    ops1, ops2 = user_id(db, OPS1), user_id(db, OPS2)
    assign(client, login, [taken["queueId"]], ops2)                                   # the manager gives one to ops.user2

    assert assign(client, login, [free["queueId"]], ops1, by=OPS1).json()["succeeded"] == [free["queueId"]]   # self pick-up
    mine = item(client, login, free["queueId"], by=OPS1)
    assert mine["status"] == "IN_PROGRESS" and mine["assignedTo"]["id"] == ops1
    assert db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == ops1, Notification.title.contains(free["queueId"]))) == 0   # no message to yourself

    error = assert_error_shape(assign(client, login, [free["queueId"]], ops2, by=OPS1), 403, "FORBIDDEN")   # to someone else
    assert "QUEUE_REASSIGN" in error["message"]
    assert_error_shape(assign(client, login, [free["queueId"]], None, by=OPS1), 403, "FORBIDDEN")           # unassign
    stolen = assign(client, login, [taken["queueId"]], ops1, by=OPS1).json()          # somebody else's item is invisible
    assert stolen == {"succeeded": [], "failed": [{"id": taken["queueId"], "reason": "Queue item not found"}]}
    assert item(client, login, taken["queueId"])["assignedTo"]["id"] == ops2


# ---------------------------------------------------------------- re-submit (24)
@pytest.mark.parametrize("kind", ["MEDIUM", "LOW"])
def test_resubmit_resolves_medium_and_low_items(client, login, make, db, kind):
    (one,) = make(kind)
    response = client.post(f"{QUEUE}/{one['queueId']}/resubmit", headers=login(OPS1))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["priority"], body["canResubmit"], body["canReject"]) == ("RESOLVED", kind, False, False)
    assert body["trade"]["isResolved"] is True and body["trade"]["version"] == 2 and body["trade"]["queueStatus"] == "RESOLVED"
    trade = db.scalar(select(Trade).where(Trade.trade_id == one["tradeId"]))
    assert trade.raw_payload["tradeId"] == one["tradeId"]                             # the original input is kept
    versions = db.execute(select(TradeVersion.version, TradeVersion.change_type).where(TradeVersion.trade_pk == trade.id)
                          .order_by(TradeVersion.version)).all()
    assert [tuple(v) for v in versions] == [(1, "CREATED"), (2, "RESUBMITTED")]
    assert audit_actions(db, one["queueId"])[-2:] == ["TRADE_RESUBMITTED", "QUEUE_RESOLVED"]
    row = db.scalar(select(RepairQueueItem).where(RepairQueueItem.queue_id == one["queueId"]))
    assert row.resolved_by == user_id(db, OPS1) and row.resolved_at is not None
    detail = client.get(f"{TRADES}/{one['tradeId']}", headers=login(MANAGER)).json()
    assert detail["ruleHits"] and detail["versionsCount"] == 2                        # rules re-stored for version 2


def test_resubmit_is_refused_for_high_items(client, login, make):
    (one,) = make("HIGH")
    error = assert_error_shape(client.post(f"{QUEUE}/{one['queueId']}/resubmit", headers=login(MANAGER)), 409, "CONFLICT")
    assert "HIGH" in error["message"] and "Re-Submit" in error["message"]
    assert item(client, login, one["queueId"])["status"] == "OPEN"


def test_resubmit_that_rescored_higher_is_refused_and_escalates(client, login, make, business_date, db):
    (one,) = make("MEDIUM")                       # 0.60 while 'today' is before the settlement date
    business_date("2026-10-05")                   # now the settlement date is in the past: +0.15 -> 0.75 EXCEPTION
    error = assert_error_shape(client.post(f"{QUEUE}/{one['queueId']}/resubmit", headers=login(OPS1)), 409, "CONFLICT")
    assert "EXCEPTION" in error["message"] and "HIGH" in error["message"]
    body = item(client, login, one["queueId"])
    assert (body["status"], body["priority"], body["riskScore"]) == ("OPEN", "HIGH", 0.75)
    assert (body["canResubmit"], body["canReject"]) == (False, True) and body["trade"]["isResolved"] is False
    assert audit_actions(db, one["queueId"])[-1] == "TRADE_SCORED"


# ---------------------------------------------------------------- bulk re-submit (25)
def test_bulk_resubmit_takes_low_items_and_explains_the_rest(client, login, make):
    low1, low2, medium, high = make("LOW", "LOW", "MEDIUM", "HIGH")
    client.post(f"{QUEUE}/{low2['queueId']}/resubmit", headers=login(OPS1))           # already resolved
    ids = [low1["queueId"], low2["queueId"], medium["queueId"], high["queueId"], "RQ-999999"]
    response = client.post(f"{QUEUE}/bulk-resubmit", json={"queueIds": ids}, headers=login(OPS1))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["succeeded"] == [low1["queueId"]]
    reasons = {f["id"]: f["reason"] for f in body["failed"]}
    assert set(reasons) == set(ids[1:])
    assert "RESOLVED" in reasons[low2["queueId"]] and "only accepts LOW" in reasons[medium["queueId"]]
    assert "only accepts LOW" in reasons[high["queueId"]] and reasons["RQ-999999"] == "Queue item not found"
    assert item(client, login, low1["queueId"])["status"] == "RESOLVED"
    assert item(client, login, medium["queueId"])["status"] == "OPEN"
    assert_error_shape(client.post(f"{QUEUE}/bulk-resubmit", json={"queueIds": []}, headers=login(OPS1)), 422, "VALIDATION_ERROR")


# ---------------------------------------------------------------- reject (26)
def test_reject_closes_the_item_and_rejects_the_trade(client, login, make, db):
    (one,) = make("HIGH")
    response = client.post(f"{QUEUE}/{one['queueId']}/reject", json={"reason": "  Counterparty cancelled the trade  "},
                           headers=login(OPS1))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["canResubmit"], body["canReject"]) == ("REJECTED", False, False)
    trade = body["trade"]
    assert (trade["status"], trade["priority"], trade["version"], trade["queueStatus"]) == ("REJECTED", None, 2, "REJECTED")
    assert trade["rejectionReasons"] == [{"ruleId": "MANUAL", "field": None, "message": "Counterparty cancelled the trade"}]
    assert trade["finalScore"] == 0.75                                                # the risk score is kept for the record
    assert audit_actions(db, one["queueId"])[-2:] == ["QUEUE_REJECTED", "TRADE_REJECTED"]
    version = db.scalar(select(TradeVersion).join(Trade, Trade.id == TradeVersion.trade_pk)
                        .where(Trade.trade_id == one["tradeId"], TradeVersion.version == 2))
    assert version.change_type == "REJECTED" and version.change_reason == "Counterparty cancelled the trade"
    detail = client.get(f"{TRADES}/{one['tradeId']}", headers=login(MANAGER)).json()
    assert detail["status"] == "REJECTED" and {h["ruleId"] for h in detail["ruleHits"]} == {"S-SSI", "S-CCY", "S-CP-TIER"}
    amend = client.put(f"{TRADES}/{one['tradeId']}", json={**one["payload"], "ssiMatched": True}, headers=login(MANAGER))
    assert_error_shape(amend, 409, "CONFLICT")                                        # rejected trades are fixed upstream


@pytest.mark.parametrize("kind", ["MEDIUM", "LOW"])
def test_reject_is_only_for_high_items(client, login, make, kind):
    (one,) = make(kind)
    error = assert_error_shape(client.post(f"{QUEUE}/{one['queueId']}/reject", json={"reason": "not wanted"},
                                           headers=login(MANAGER)), 409, "CONFLICT")
    assert "only HIGH" in error["message"] and item(client, login, one["queueId"])["status"] == "OPEN"


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}, {"reason": "no"}])
def test_reject_needs_a_reason(client, login, make, body):
    (one,) = make("HIGH")
    assert_error_shape(client.post(f"{QUEUE}/{one['queueId']}/reject", json=body, headers=login(MANAGER)), 422, "VALIDATION_ERROR")
    assert item(client, login, one["queueId"])["status"] == "OPEN"


# ---------------------------------------------------------------- approve (27)
def test_manager_approves_a_high_item(client, login, make, db):
    (one,) = make("HIGH")
    assign(client, login, [one["queueId"]], user_id(db, OPS1))
    response = client.post(f"{QUEUE}/{one['queueId']}/approve", json={"note": "Confirmed with the counterparty by phone"},
                           headers=login(MANAGER))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["priority"]) == ("RESOLVED", "HIGH") and body["trade"]["isResolved"] is True
    assert body["trade"]["status"] == "EXCEPTION" and body["trade"]["version"] == 1    # the trade itself is unchanged
    assert audit_actions(db, one["queueId"])[-2:] == ["HIGH_RESOLUTION_APPROVED", "QUEUE_RESOLVED"]
    row = db.scalar(select(RepairQueueItem).where(RepairQueueItem.queue_id == one["queueId"]))
    assert row.resolution_note == "Confirmed with the counterparty by phone" and row.resolved_by == user_id(db, MANAGER)


def test_approve_rules(client, login, make):
    high, medium = make("HIGH", "MEDIUM")
    url = f"{QUEUE}/{high['queueId']}/approve"
    assert_error_shape(client.post(url, json={"note": "fine by me"}, headers=login(OPS1)), 403, "FORBIDDEN")   # managers only
    assert_error_shape(client.post(url, json={}, headers=login(MANAGER)), 422, "VALIDATION_ERROR")            # note required
    error = assert_error_shape(client.post(f"{QUEUE}/{medium['queueId']}/approve", json={"note": "fine by me"},
                                           headers=login(MANAGER)), 409, "CONFLICT")
    assert "only HIGH" in error["message"]
    assert item(client, login, high["queueId"])["status"] == "OPEN"


# ---------------------------------------------------------------- closed items: every action is forbidden
@pytest.mark.parametrize("closed_as", ["RESOLVED", "REJECTED"])
def test_nothing_can_be_done_to_a_closed_item(client, login, make, db, closed_as):
    (one,) = make("HIGH")
    headers, queue_id = login(MANAGER), one["queueId"]
    if closed_as == "RESOLVED":
        assert client.post(f"{QUEUE}/{queue_id}/approve", json={"note": "checked and fine"}, headers=headers).status_code == 200
    else:
        assert client.post(f"{QUEUE}/{queue_id}/reject", json={"reason": "cancelled"}, headers=headers).status_code == 200

    for call in (client.post(f"{QUEUE}/{queue_id}/resubmit", headers=headers),
                 client.post(f"{QUEUE}/{queue_id}/reject", json={"reason": "again"}, headers=headers),
                 client.post(f"{QUEUE}/{queue_id}/approve", json={"note": "again"}, headers=headers)):
        assert closed_as in assert_error_shape(call, 409, "CONFLICT")["message"]
    for assignee in (user_id(db, OPS1), None):
        result = assign(client, login, [queue_id], assignee).json()
        assert result["succeeded"] == [] and closed_as in result["failed"][0]["reason"]
    bulk = client.post(f"{QUEUE}/bulk-resubmit", json={"queueIds": [queue_id]}, headers=headers).json()
    assert bulk["succeeded"] == [] and closed_as in bulk["failed"][0]["reason"]
    fix = client.put(f"{TRADES}/{one['tradeId']}", json={**one["payload"], "ssiMatched": True, "queueId": queue_id},
                     headers=headers)
    assert_error_shape(fix, 409, "CONFLICT")                                           # Apply Fix on a closed item
    assert item(client, login, queue_id)["status"] == closed_as


# ---------------------------------------------------------------- apply fix, then re-submit (section 7.1)
def test_apply_fix_lowers_the_priority_and_then_resubmit_resolves(client, login, make, db):
    (one,) = make("HIGH")
    queue_id, ops1 = one["queueId"], user_id(db, OPS1)
    assign(client, login, [queue_id], ops1)
    fixed = {**one["payload"], "ssiMatched": True, "currencyMatched": True, "queueId": queue_id,
             "appliedFix": "SSI and currency re-confirmed with BlackRock"}
    trade = client.put(f"{TRADES}/{one['tradeId']}", json=fixed, headers=login(OPS1))
    assert trade.status_code == 200, trade.text
    assert (trade.json()["status"], trade.json()["finalScore"], trade.json()["queueStatus"]) == ("PASS", 0.25, "IN_PROGRESS")

    body = item(client, login, queue_id, by=OPS1)
    assert (body["status"], body["priority"], body["riskScore"]) == ("IN_PROGRESS", "LOW", 0.25)
    assert (body["canResubmit"], body["canReject"]) == (True, False)                   # Re-Submit is now enabled
    assert body["appliedFix"] == "SSI and currency re-confirmed with BlackRock" and body["appliedBy"] == "Ops User One"
    assert body["appliedAt"]

    done = client.post(f"{QUEUE}/{queue_id}/resubmit", headers=login(OPS1)).json()
    assert done["status"] == "RESOLVED" and done["trade"]["isResolved"] is True and done["trade"]["version"] == 3
    assert audit_actions(db, queue_id) == ["TRADE_SUBMITTED", "QUEUE_ITEM_CREATED", "QUEUE_ASSIGNED", "FIX_APPLIED",
                                           "TRADE_RESUBMITTED", "QUEUE_RESOLVED"]


def test_amending_a_resolved_trade_that_is_still_risky_reopens_its_item(client, login, make, db):
    (one,) = make("MEDIUM")
    queue_id = one["queueId"]
    assign(client, login, [queue_id], user_id(db, OPS1))
    assert client.post(f"{QUEUE}/{queue_id}/resubmit", headers=login(OPS1)).json()["status"] == "RESOLVED"

    amended = client.put(f"{TRADES}/{one['tradeId']}", json={**one["payload"], "currencyMatched": False},
                         headers=login(UPLOADER))                                      # 0.60 + 0.15 = 0.75
    assert amended.status_code == 200, amended.text
    assert (amended.json()["status"], amended.json()["isResolved"], amended.json()["queueId"]) == ("EXCEPTION", False, queue_id)
    body = item(client, login, queue_id)
    assert (body["status"], body["priority"], body["assignedTo"], body["appliedFix"]) == ("OPEN", "HIGH", None, None)
    assert db.scalar(select(func.count()).select_from(RepairQueueItem).join(Trade, Trade.id == RepairQueueItem.trade_pk)
                     .where(Trade.trade_id == one["tradeId"])) == 1                    # still ONE item per trade
    last = db.scalar(select(AuditLog).where(AuditLog.queue_id == queue_id).order_by(AuditLog.id.desc()))
    assert last.action == "QUEUE_ITEM_CREATED" and "re-opened" in last.details["reason"]


# ---------------------------------------------------------------- row scoping
def test_ops_user_sees_own_and_unassigned_open_items_only(client, login, make, db):
    mine, theirs, free, done_by_other, closed_unassigned = make("LOW", "LOW", "LOW", "LOW", "LOW")
    assign(client, login, [mine["queueId"]], user_id(db, OPS1))
    assign(client, login, [theirs["queueId"], done_by_other["queueId"]], user_id(db, OPS2))
    client.post(f"{QUEUE}/{done_by_other['queueId']}/resubmit", headers=login(OPS2))
    client.post(f"{QUEUE}/{closed_unassigned['queueId']}/resubmit", headers=login(OPS2))   # closed without picking it up
    assert item(client, login, closed_unassigned["queueId"], by=OPS2)["status"] == "RESOLVED"   # still visible to who closed it
    assert_error_shape(client.get(f"{QUEUE}/{closed_unassigned['queueId']}", headers=login(OPS1)), 404, "NOT_FOUND")

    def visible(username):
        items = client.get(f"{QUEUE}?pageSize=500&search=RQT", headers=login(username)).json()["items"]
        return {i["queueId"] for i in items}

    ours = {mine["queueId"], theirs["queueId"], free["queueId"], done_by_other["queueId"]}
    assert visible(OPS1) & ours == {mine["queueId"], free["queueId"]}
    assert visible(OPS2) & ours == {theirs["queueId"], free["queueId"], done_by_other["queueId"]}
    assert visible(MANAGER) >= ours and visible(ADMIN) >= ours

    other = theirs["queueId"]
    assert_error_shape(client.get(f"{QUEUE}/{other}", headers=login(OPS1)), 404, "NOT_FOUND")
    assert_error_shape(client.post(f"{QUEUE}/{other}/resubmit", headers=login(OPS1)), 404, "NOT_FOUND")
    assert_error_shape(client.post(f"{QUEUE}/{other}/reject", json={"reason": "not mine"}, headers=login(OPS1)), 404, "NOT_FOUND")
    bulk = client.post(f"{QUEUE}/bulk-resubmit", json={"queueIds": [other]}, headers=login(OPS1)).json()
    assert bulk == {"succeeded": [], "failed": [{"id": other, "reason": "Queue item not found"}]}
    assert item(client, login, other)["status"] == "IN_PROGRESS"                       # untouched

    own_total = client.get(f"{QUEUE}/summary", headers=login(OPS1)).json()["total"]
    assert own_total == client.get(f"{QUEUE}?pageSize=1", headers=login(OPS1)).json()["total"]
    assert own_total < client.get(f"{QUEUE}/summary", headers=login(MANAGER)).json()["total"]
    exported = client.get(f"{QUEUE}/export?search=RQT", headers=login(OPS1)).text
    assert mine["queueId"] in exported and other not in exported


# ---------------------------------------------------------------- permissions
@pytest.mark.parametrize("username", [UPLOADER, "ml.admin"])
def test_traders_and_ml_admin_have_no_access(client, login, username):
    headers = login(username)
    calls = [client.get(QUEUE, headers=headers), client.get(f"{QUEUE}/summary", headers=headers),
             client.get(f"{QUEUE}/export", headers=headers), client.get(f"{QUEUE}/RQ-000001", headers=headers),
             client.post(f"{QUEUE}/assign", json={"queueIds": ["RQ-000001"], "assigneeId": 1}, headers=headers),
             client.post(f"{QUEUE}/RQ-000001/resubmit", headers=headers),
             client.post(f"{QUEUE}/bulk-resubmit", json={"queueIds": ["RQ-000001"]}, headers=headers),
             client.post(f"{QUEUE}/RQ-000001/reject", json={"reason": "no access"}, headers=headers),
             client.post(f"{QUEUE}/RQ-000001/approve", json={"note": "no access"}, headers=headers),
             client.get(TEAM, headers=headers)]
    for call in calls:
        assert_error_shape(call, 403, "FORBIDDEN")


def test_no_token_is_401(client):
    for call in (client.get(QUEUE), client.get(f"{QUEUE}/summary"), client.get(f"{QUEUE}/export"),
                 client.post(f"{QUEUE}/assign", json={"queueIds": ["RQ-000001"], "assigneeId": 1}),
                 client.post(f"{QUEUE}/RQ-000001/resubmit"), client.get(TEAM)):
        assert_error_shape(call, 401, "UNAUTHORIZED")


def test_sys_admin_can_do_everything_an_ops_manager_can(client, login, make, db):
    high, low = make("HIGH", "LOW")
    assert assign(client, login, [high["queueId"]], user_id(db, OPS1), by=ADMIN).json()["succeeded"] == [high["queueId"]]
    assert client.post(f"{QUEUE}/{high['queueId']}/approve", json={"note": "approved by admin"}, headers=login(ADMIN)).status_code == 200
    assert client.post(f"{QUEUE}/bulk-resubmit", json={"queueIds": [low["queueId"]]}, headers=login(ADMIN)).json()["succeeded"]
    assert client.get(TEAM, headers=login(ADMIN)).status_code == 200


# ---------------------------------------------------------------- export (28)
def test_export_csv_matches_the_filters_and_is_audited(client, login, make, db):
    high, low = make("HIGH", "LOW")
    assign(client, login, [high["queueId"]], user_id(db, OPS1))
    response = client.get(f"{QUEUE}/export?priority=HIGH&search={high['tradeId']}", headers=login(MANAGER))
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="repair_queue_') and disposition.endswith('.csv"')
    rows = list(csv.DictReader(io.StringIO(response.text.lstrip("﻿"))))
    assert len(rows) == 1
    row = rows[0]
    assert list(row)[:8] == ["Queue ID", "Trade ID", "Priority", "Status", "Risk Score", "Likely Reason", "Suggested Fix",
                             "Assigned To"]
    assert (row["Queue ID"], row["Trade ID"], row["Priority"], row["Status"]) == (high["queueId"], high["tradeId"], "HIGH", "IN_PROGRESS")
    assert (row["Risk Score"], row["Assigned To"], row["Counterparty"]) == ("0.7500", "Ops User One", "BlackRock")
    assert row["Suggested Fix"] and row["Likely Reason"]
    both = client.get(f"{QUEUE}/export?search=RQT", headers=login(MANAGER)).text
    assert high["queueId"] in both and low["queueId"] in both
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "EXPORT_CSV").order_by(AuditLog.id.desc()))
    assert audit.actor_username == MANAGER and audit.entity_type == "REPAIR_QUEUE" and audit.details["export"] == "repair-queue"


# ---------------------------------------------------------------- team performance (29)
def test_team_performance_counts_per_ops_user(client, login, make, db):
    def row_for(username):
        rows = client.get(TEAM, headers=login(MANAGER)).json()
        assert [r["username"] for r in rows] == ["ops.user1", "ops.user3", "ops.user2"]   # Ops Users only, by full name
        assert all(set(r) == {"userId", "username", "fullName", "assigned", "inProgress", "resolved", "avgResolutionHours"}
                   for r in rows)
        return next(r for r in rows if r["username"] == username)

    before = row_for(OPS3)
    first, second, third = make("LOW", "MEDIUM", "HIGH")
    assign(client, login, [first["queueId"], second["queueId"], third["queueId"]], user_id(db, OPS3))
    assert client.post(f"{QUEUE}/{first['queueId']}/resubmit", headers=login(OPS3)).status_code == 200
    after = row_for(OPS3)
    assert after["assigned"] == before["assigned"] + 3
    assert after["inProgress"] == before["inProgress"] + 2
    assert after["resolved"] == before["resolved"] + 1
    assert after["avgResolutionHours"] is not None and 0 <= after["avgResolutionHours"] < 1
    assert_error_shape(client.get(TEAM, headers=login(OPS1)), 403, "FORBIDDEN")        # managers and admin only


def test_audit_chain_is_still_valid_after_all_queue_work(db):
    assert audit_service.verify_chain(db) == (True, None)
