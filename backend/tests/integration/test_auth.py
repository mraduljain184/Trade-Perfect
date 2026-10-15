import pytest
from sqlalchemy import func, select

from app.models import AuditLog, User
from scripts import seed_data
from tests.conftest import ROLE_USERS, TEST_PASSWORD, assert_error_shape

EXPECTED_PERMISSIONS = {
    code: sorted(c for c, _, grants in seed_data.PERMISSIONS if role_id in grants)
    for role_id, code in seed_data.ROLE_CODES.items()
}


def _count(db, action, username):
    return db.scalar(select(func.count()).select_from(AuditLog)
                     .where(AuditLog.action == action, AuditLog.actor_username == username))


@pytest.mark.parametrize("role, username", ROLE_USERS.items())
def test_every_role_can_log_in_and_me_returns_its_permissions(client, role, username):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"accessToken", "tokenType", "expiresIn", "user"}
    assert body["tokenType"] == "bearer" and body["expiresIn"] == 8 * 3600

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['accessToken']}"})
    assert me.status_code == 200
    user = me.json()
    assert user == body["user"]
    assert set(user) == {"id", "username", "fullName", "email", "role", "roleName", "permissions"}
    assert user["username"] == username and user["role"] == role
    assert user["email"] == f"{username}@tradeperfect.local"
    assert user["permissions"] == EXPECTED_PERMISSIONS[role]


def test_trader_full_name_matches_trader_name_in_data(client, login):
    assert client.get("/api/v1/auth/me", headers=login("priya.sharma")).json()["fullName"] == "Priya Sharma"


def test_username_is_case_insensitive_and_trimmed(client):
    response = client.post("/api/v1/auth/login", json={"username": "  Priya.Sharma ", "password": TEST_PASSWORD})
    assert response.status_code == 200


def test_wrong_password_is_401_and_audited(client, db):
    before = _count(db, "LOGIN_FAILED", "emma.davis")
    response = client.post("/api/v1/auth/login", json={"username": "emma.davis", "password": "wrong-password"})
    error = assert_error_shape(response, 401, "UNAUTHORIZED")
    assert error["message"] == "Invalid username or password"
    assert _count(db, "LOGIN_FAILED", "emma.davis") == before + 1


def test_unknown_user_gets_the_same_401_and_is_audited(client, db):
    response = client.post("/api/v1/auth/login", json={"username": "nobody.here", "password": TEST_PASSWORD})
    assert assert_error_shape(response, 401, "UNAUTHORIZED")["message"] == "Invalid username or password"
    row = db.scalar(select(AuditLog).where(AuditLog.actor_username == "nobody.here").order_by(AuditLog.id.desc()))
    assert row.action == "LOGIN_FAILED" and row.actor_user_id is None and row.details == {"reason": "unknown_user"}


def test_inactive_user_cannot_log_in(client, db):
    user = db.scalar(select(User).where(User.username == "wei.zhang"))
    user.is_active = False
    db.commit()
    try:
        response = client.post("/api/v1/auth/login", json={"username": "wei.zhang", "password": TEST_PASSWORD})
        assert_error_shape(response, 401, "UNAUTHORIZED")
    finally:
        user.is_active = True
        db.commit()


def test_login_and_logout_are_audited_and_last_login_set(client, db):
    logins = _count(db, "LOGIN", "tom.richardson")
    response = client.post("/api/v1/auth/login", json={"username": "tom.richardson", "password": TEST_PASSWORD})
    headers = {"Authorization": f"Bearer {response.json()['accessToken']}"}
    assert _count(db, "LOGIN", "tom.richardson") == logins + 1

    out = client.post("/api/v1/auth/logout", headers=headers)
    assert out.status_code == 200 and out.json() == {"message": "Logged out"}
    row = db.scalar(select(AuditLog).where(AuditLog.action == "LOGOUT", AuditLog.actor_username == "tom.richardson"))
    assert row.actor_role == "TRADER" and row.entity_type == "AUTH" and len(row.hash) == 64
    assert db.scalar(select(User.last_login_at).where(User.username == "tom.richardson")) is not None


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-token"}, {"Authorization": "Basic abc"}])
def test_me_without_valid_token_is_401(client, headers):
    assert_error_shape(client.get("/api/v1/auth/me", headers=headers), 401, "UNAUTHORIZED")


def test_logout_without_token_is_401(client):
    assert_error_shape(client.post("/api/v1/auth/logout"), 401, "UNAUTHORIZED")


def test_password_hash_never_returned_and_stored_as_bcrypt(client, db):
    response = client.post("/api/v1/auth/login", json={"username": "ml.admin", "password": TEST_PASSWORD})
    assert "password" not in response.text.lower().replace("accesstoken", "")
    hashes = db.scalars(select(User.password_hash)).all()
    assert all(h.startswith("$2") and TEST_PASSWORD not in h for h in hashes)
