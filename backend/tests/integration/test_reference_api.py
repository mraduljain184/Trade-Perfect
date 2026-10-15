import pytest

from tests.conftest import ROLE_USERS, assert_error_shape

BASE = "/api/v1/reference"


def test_symbols(client, login):
    body = client.get(f"{BASE}/symbols", headers=login("priya.sharma")).json()
    assert len(body) == 140 and set(body[0]) == {"symbol"}
    symbols = [row["symbol"] for row in body]
    assert symbols == sorted(symbols) and "ISRG1" in symbols and "ISRG" not in symbols


def test_counterparties_carry_tier(client, login):
    body = client.get(f"{BASE}/counterparties", headers=login("ml.admin")).json()
    assert len(body) == 38 and set(body[0]) == {"name", "riskTier", "tierLabel"}
    blackrock = next(row for row in body if row["name"] == "BlackRock")
    assert blackrock == {"name": "BlackRock", "riskTier": 4, "tierLabel": "Tier4_HighestRisk"}


def test_accounts_all_and_active_only(client, login):
    headers = login("priya.sharma")
    everything = client.get(f"{BASE}/accounts", headers=headers).json()
    active = client.get(f"{BASE}/accounts?active=true", headers=headers).json()
    inactive = client.get(f"{BASE}/accounts?active=false", headers=headers).json()
    assert (len(everything), len(active), len(inactive)) == (100, 97, 3)
    assert set(everything[0]) == {"accountId", "isActive"}
    assert {row["accountId"] for row in inactive} == {"ACC1011", "ACC1063", "ACC1093"}


@pytest.mark.parametrize("path", ["symbols", "counterparties", "accounts", "users"])
def test_reference_needs_login(client, path):
    assert_error_shape(client.get(f"{BASE}/{path}"), 401, "UNAUTHORIZED")


def test_users_by_role(client, login):
    body = client.get(f"{BASE}/users?role=ops_user", headers=login("ops.manager")).json()
    assert [row["username"] for row in body] == ["ops.user1", "ops.user3", "ops.user2"]  # by full name
    assert set(body[0]) == {"id", "username", "fullName", "role"} and body[0]["role"] == "OPS_USER"
    assert len(client.get(f"{BASE}/users", headers=login("ops.user1")).json()) == 21


@pytest.mark.parametrize("role", ["TRADER", "ML_ADMIN"])
def test_users_forbidden_without_assign_or_audit_permission(client, login, role):
    assert_error_shape(client.get(f"{BASE}/users", headers=login(ROLE_USERS[role])), 403, "FORBIDDEN")


def test_users_unknown_role_is_400(client, login):
    assert_error_shape(client.get(f"{BASE}/users?role=WIZARD", headers=login("sys.admin")), 400, "BAD_REQUEST")
