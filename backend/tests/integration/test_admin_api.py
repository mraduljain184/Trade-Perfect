"""BE-6: admin (users, system configuration, holidays) and notifications - endpoints 48-51."""
from datetime import date

import pytest
from sqlalchemy import select

from app.models import AuditLog, Notification, SystemConfig, User
from app.services import engine_adapter
from tests.conftest import TEST_PASSWORD, assert_error_shape

USERS, CONFIG, HOLIDAYS, NOTES = "/api/v1/admin/users", "/api/v1/admin/config", "/api/v1/admin/holidays", "/api/v1/notifications"
ADMIN, MANAGER = "sys.admin", "ops.manager"
NEW_USER = {"username": "Grace.Hopper", "fullName": "Grace Hopper", "email": "Grace.Hopper@TradePerfect.local",
            "password": "Compiler#1952", "role": "trader"}


def try_login(client, username, password):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


@pytest.fixture(scope="module")
def grace(client, login):
    response = client.post(USERS, json=NEW_USER, headers=login(ADMIN))
    assert response.status_code == 201, response.text
    yield response.json()
    client.put(f"{USERS}/{response.json()['id']}", json={"isActive": False}, headers=login(ADMIN))   # other files list active users


# ---------------------------------------------------------------- users (48)
def test_create_user_and_log_in_with_it(client, login, grace, db):
    assert set(grace) == {"id", "username", "fullName", "email", "role", "roleName", "isActive", "createdAt", "lastLoginAt"}
    assert (grace["username"], grace["email"], grace["role"], grace["roleName"], grace["isActive"]) == (
        "grace.hopper", "grace.hopper@tradeperfect.local", "TRADER", "Trader", True)        # normalised to lower case
    stored = db.scalar(select(User).where(User.username == "grace.hopper"))
    assert stored.password_hash.startswith("$2") and "Compiler" not in stored.password_hash   # bcrypt, never plain text
    session = try_login(client, "grace.hopper", "Compiler#1952")
    assert session.status_code == 200 and "TRADE_SUBMIT_SINGLE" in session.json()["user"]["permissions"]
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "USER_CREATED").order_by(AuditLog.id.desc()))
    assert audit.actor_username == ADMIN and audit.after_state["username"] == "grace.hopper" and "password" not in str(audit.after_state).lower()


def test_list_users_never_returns_password_data(client, login, grace):
    response = client.get(USERS, headers=login(ADMIN))
    users = response.json()
    assert len(users) >= 22 and "password" not in response.text.lower() and "$2b$" not in response.text
    assert [u["username"] for u in users] == sorted(u["username"] for u in users)
    assert next(u for u in users if u["username"] == "ops.manager")["role"] == "OPS_MANAGER"


@pytest.mark.parametrize("change, status, code", [
    ({"username": "grace.hopper"}, 409, "CONFLICT"), ({"username": "g.h2", "email": "grace.hopper@tradeperfect.local"}, 409, "CONFLICT"),
    ({"username": "g.h3", "email": "g3@x.io", "role": "WIZARD"}, 400, "BAD_REQUEST"),
    ({"username": "g.h4", "email": "g4@x.io", "password": "short"}, 422, "VALIDATION_ERROR"),
    ({"username": "x", "email": "g5@x.io"}, 422, "VALIDATION_ERROR"), ({"username": "bad name", "email": "g6@x.io"}, 422, "VALIDATION_ERROR"),
    ({"username": "g.h7", "email": "not-an-email"}, 422, "VALIDATION_ERROR"),
])
def test_create_user_validation(client, login, grace, change, status, code):
    assert_error_shape(client.post(USERS, json={**NEW_USER, **change}, headers=login(ADMIN)), status, code)


def test_update_user_details_password_role_and_active_flag(client, login, grace, db):
    url, headers = f"{USERS}/{grace['id']}", login(ADMIN)
    renamed = client.put(url, json={"fullName": "Grace B. Hopper", "email": "g.hopper@tradeperfect.local"}, headers=headers).json()
    assert (renamed["fullName"], renamed["email"]) == ("Grace B. Hopper", "g.hopper@tradeperfect.local")

    assert client.put(url, json={"password": "Cobol#1959-ok"}, headers=headers).status_code == 200
    assert try_login(client, "grace.hopper", "Compiler#1952").status_code == 401
    assert try_login(client, "grace.hopper", "Cobol#1959-ok").status_code == 200

    promoted = client.put(url, json={"role": "ML_ADMIN"}, headers=headers).json()
    assert (promoted["role"], promoted["roleName"]) == ("ML_ADMIN", "ML Model Administrator")
    assert "MODEL_RETRAIN" in try_login(client, "grace.hopper", "Cobol#1959-ok").json()["user"]["permissions"]
    last_two = db.scalars(select(AuditLog.action).where(AuditLog.entity_type == "USER").order_by(AuditLog.id.desc()).limit(2)).all()
    assert set(last_two) == {"USER_UPDATED", "ROLE_CHANGED"}
    role_audit = db.scalar(select(AuditLog).where(AuditLog.action == "ROLE_CHANGED").order_by(AuditLog.id.desc()))
    assert (role_audit.before_state, role_audit.after_state) == ({"role": "TRADER"}, {"role": "ML_ADMIN"})

    assert client.put(url, json={"isActive": False}, headers=headers).json()["isActive"] is False
    assert try_login(client, "grace.hopper", "Cobol#1959-ok").status_code == 401              # deactivated users cannot log in
    assert client.put(url, json={"isActive": True, "role": "TRADER"}, headers=headers).json()["role"] == "TRADER"


def test_update_user_refusals(client, login, grace, db):
    headers = login(ADMIN)
    me = db.scalar(select(User.id).where(User.username == ADMIN))
    assert_error_shape(client.put(f"{USERS}/{me}", json={"isActive": False}, headers=headers), 409, "CONFLICT")   # no lock-out
    assert_error_shape(client.put(f"{USERS}/{me}", json={"role": "TRADER"}, headers=headers), 409, "CONFLICT")
    assert_error_shape(client.put(f"{USERS}/{grace['id']}", json={}, headers=headers), 400, "BAD_REQUEST")
    assert_error_shape(client.put(f"{USERS}/{grace['id']}", json={"role": "WIZARD"}, headers=headers), 400, "BAD_REQUEST")
    assert_error_shape(client.put(f"{USERS}/{grace['id']}", json={"email": "ops.manager@tradeperfect.local"}, headers=headers), 409, "CONFLICT")
    assert_error_shape(client.put(f"{USERS}/999999", json={"fullName": "Nobody Here"}, headers=headers), 404, "NOT_FOUND")
    assert client.get("/api/v1/auth/me", headers=headers).json()["role"] == "SYS_ADMIN"


# ---------------------------------------------------------------- system configuration (49)
def test_config_list_and_single_key(client, login):
    items = client.get(CONFIG, headers=login(ADMIN)).json()
    assert len(items) == 14 and set(items[0]) == {"key", "value", "description", "editableBy", "updatedAt"}
    one = client.get(f"{CONFIG}/quantity_limits", headers=login(ADMIN)).json()
    assert one["value"] == {"min": 100, "max": 5000} and one["editableBy"] == "SYS_ADMIN"
    assert_error_shape(client.get(f"{CONFIG}/no_such_key", headers=login(ADMIN)), 404, "NOT_FOUND")
    assert_error_shape(client.put(f"{CONFIG}/no_such_key", json={"value": 1}, headers=login(ADMIN)), 404, "NOT_FOUND")


def test_config_change_is_audited_and_reaches_the_engine_at_once(client, login, db):
    headers = login(ADMIN)
    try:
        assert engine_adapter.get_engine_config(db).qty_max == 5000                  # fills the cache
        changed = client.put(f"{CONFIG}/quantity_limits", json={"value": {"min": 50, "max": 8000}}, headers=headers)
        assert changed.status_code == 200 and changed.json()["value"] == {"min": 50, "max": 8000}
        assert engine_adapter.get_engine_config(db).qty_max == 8000                  # no 60 s wait: cache cleared
        assert client.put(f"{CONFIG}/rule_weights", json={"value": {"ssi": 0.4}}, headers=headers).status_code == 200
        assert engine_adapter.get_engine_config(db).rule_weights["ssi"] == 0.4
        assert client.put(f"{CONFIG}/business_date_override", json={"value": "2026-09-28"}, headers=headers).status_code == 200
        assert engine_adapter.get_business_date(db) == date(2026, 9, 28)
        assert client.put(f"{CONFIG}/batch_size", json={"value": 2500}, headers=headers).json()["value"] == 2500
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "CONFIG_CHANGED", AuditLog.entity_id == "quantity_limits")
                          .order_by(AuditLog.id.desc()))
        assert audit.before_state == {"value": {"min": 100, "max": 5000}} and audit.after_state == {"value": {"min": 50, "max": 8000}}
    finally:
        for key, value in (("quantity_limits", {"min": 100, "max": 5000}), ("business_date_override", None), ("batch_size", 10000)):
            assert client.put(f"{CONFIG}/{key}", json={"value": value}, headers=headers).status_code == 200
        defaults = db.get(SystemConfig, "rule_weights")
        from scripts import seed_data
        assert client.put(f"{CONFIG}/rule_weights", json={"value": seed_data.SYSTEM_CONFIG["rule_weights"][0]}, headers=headers).status_code == 200
        db.refresh(defaults)
    assert engine_adapter.get_engine_config(db).rule_weights["ssi"] == 0.35 and engine_adapter.get_engine_config(db).qty_max == 5000


@pytest.mark.parametrize("key, value", [
    ("quantity_limits", {"min": 9000, "max": 5000}), ("quantity_limits", None), ("rule_weights", {"made_up": 0.5}),
    ("rule_weights", {"ssi": 3}), ("hybrid_weights", {"alpha": 0.5, "beta": 0.6}), ("classification_thresholds", {"pass": 0.8, "exception": 0.7}),
    ("counterparty_tier_thresholds", {"tier4": 100, "tier3": 1000, "tier2": 500}), ("settlement_far_days", 0),
    ("business_date_override", "28/09/2026"), ("business_date_override", 20260928), ("batch_size", 0), ("batch_size", 20000),
    ("batch_size", "many"), ("max_upload_rows", -5), ("drift_window_days", 1.5), ("psi_threshold", 0), ("psi_threshold", "high"),
    ("rejection_report_recipients", ["not-an-address"]), ("audit_report_recipients", "compliance@bank.example"),
])
def test_config_refuses_values_that_would_break_the_app(client, login, db, key, value):
    before = db.get(SystemConfig, key).value
    assert_error_shape(client.put(f"{CONFIG}/{key}", json={"value": value}, headers=login(ADMIN)), 400, "BAD_REQUEST")
    db.expire_all()
    assert db.get(SystemConfig, key).value == before


# ---------------------------------------------------------------- holidays (50)
def test_holidays_add_list_delete(client, login, db):
    headers = login(ADMIN)
    assert len(client.get(HOLIDAYS, headers=headers).json()) >= 3
    engine_adapter.get_reference_data(db)                                            # fills the cache
    added = client.post(HOLIDAYS, json={"date": "2026-10-02", "market": "global", "description": "Gandhi Jayanti"}, headers=headers)
    assert added.status_code == 201 and added.json() == {"date": "2026-10-02", "market": "GLOBAL", "description": "Gandhi Jayanti"}
    assert date(2026, 10, 2) in engine_adapter.get_reference_data(db).holidays       # the engine sees it at once
    assert {"date": "2026-10-02", "market": "GLOBAL", "description": "Gandhi Jayanti"} in client.get(HOLIDAYS, headers=headers).json()
    assert_error_shape(client.post(HOLIDAYS, json={"date": "2026-10-02"}, headers=headers), 409, "CONFLICT")
    assert_error_shape(client.post(HOLIDAYS, json={"date": "02-10-2026"}, headers=headers), 422, "VALIDATION_ERROR")
    assert client.delete(f"{HOLIDAYS}/2026-10-02", headers=headers).status_code == 204
    assert date(2026, 10, 2) not in engine_adapter.get_reference_data(db).holidays
    assert_error_shape(client.delete(f"{HOLIDAYS}/2026-10-02", headers=headers), 404, "NOT_FOUND")
    holiday_audits = db.scalars(select(AuditLog.before_state).where(AuditLog.entity_id == "holiday:2026-10-02").order_by(AuditLog.id)).all()
    assert holiday_audits[0] is None and holiday_audits[1]["description"] == "Gandhi Jayanti"


# ---------------------------------------------------------------- permissions
@pytest.mark.parametrize("username", ["priya.sharma", "ops.user1", MANAGER, "ml.admin"])
def test_only_sys_admin_reaches_admin_endpoints(client, login, username):
    headers = login(username)
    for call in (client.get(USERS, headers=headers), client.post(USERS, json=NEW_USER, headers=headers),
                 client.put(f"{USERS}/1", json={"fullName": "Someone Else"}, headers=headers),
                 client.get(CONFIG, headers=headers), client.get(f"{CONFIG}/batch_size", headers=headers),
                 client.put(f"{CONFIG}/batch_size", json={"value": 1}, headers=headers),
                 client.get(HOLIDAYS, headers=headers), client.post(HOLIDAYS, json={"date": "2026-11-11"}, headers=headers),
                 client.delete(f"{HOLIDAYS}/2026-12-25", headers=headers)):
        assert_error_shape(call, 403, "FORBIDDEN")


def test_no_token_is_401(client):
    for call in (client.get(USERS), client.post(USERS, json=NEW_USER), client.get(CONFIG),
                 client.put(f"{CONFIG}/batch_size", json={"value": 1}), client.get(HOLIDAYS), client.get(NOTES),
                 client.post(f"{NOTES}/1/read")):
        assert_error_shape(call, 401, "UNAUTHORIZED")


# ---------------------------------------------------------------- notifications (51)
def test_notifications_are_mine_plus_my_role(client, login, db):
    ops1 = db.scalar(select(User.id).where(User.username == "ops.user1"))
    ops2 = db.scalar(select(User.id).where(User.username == "ops.user2"))
    mine = Notification(user_id=ops1, type="ASSIGNMENT", title="Item RQ-TEST assigned to you", message="m", link="/repair-queue")
    for_role = Notification(role_code="OPS_USER", type="INFO_TEST", severity="WARNING", title="For every Ops User", message="m")
    not_mine = Notification(user_id=ops2, type="ASSIGNMENT", title="Private to ops.user2", message="m")
    other_role = Notification(role_code="ML_ADMIN", type="DRIFT_ALERT", severity="CRITICAL", title="For ML admins", message="m")
    db.add_all([mine, for_role, not_mine, other_role])
    db.commit()

    listed = client.get(f"{NOTES}?limit=100", headers=login("ops.user1")).json()
    assert set(listed[0]) == {"id", "type", "severity", "title", "message", "link", "isRead", "createdAt"}
    titles = [n["title"] for n in listed]
    assert "Item RQ-TEST assigned to you" in titles and "For every Ops User" in titles
    assert "Private to ops.user2" not in titles and "For ML admins" not in titles
    assert [n["id"] for n in listed] == sorted((n["id"] for n in listed), reverse=True)   # newest first

    read = client.post(f"{NOTES}/{mine.id}/read", headers=login("ops.user1"))
    assert read.status_code == 200 and read.json()["isRead"] is True
    unread = {n["id"] for n in client.get(f"{NOTES}?unreadOnly=true&limit=100", headers=login("ops.user1")).json()}
    assert mine.id not in unread and for_role.id in unread
    assert client.post(f"{NOTES}/{for_role.id}/read", headers=login("ops.user1")).json()["isRead"] is True
    assert_error_shape(client.post(f"{NOTES}/{not_mine.id}/read", headers=login("ops.user1")), 404, "NOT_FOUND")
    assert_error_shape(client.post(f"{NOTES}/{other_role.id}/read", headers=login("ops.user1")), 404, "NOT_FOUND")
    assert_error_shape(client.post(f"{NOTES}/999999999/read", headers=login("ops.user1")), 404, "NOT_FOUND")
    assert_error_shape(client.get(f"{NOTES}?limit=1000", headers=login("ops.user1")), 422, "VALIDATION_ERROR")
    assert TEST_PASSWORD  # (import used by helpers above)
