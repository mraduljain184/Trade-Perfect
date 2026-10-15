"""require_permission: the backend - not the UI - decides who may call what."""
import pytest

from tests.conftest import ROLE_USERS, assert_error_shape

ML_ENDPOINT = "/api/v1/ml/config"  # needs HYBRID_WEIGHTS_CONFIG (ML_ADMIN, SYS_ADMIN)


@pytest.mark.parametrize("role", ["TRADER", "OPS_USER", "OPS_MANAGER"])
def test_roles_without_permission_get_403_on_ml_endpoint(client, login, role):
    error = assert_error_shape(client.get(ML_ENDPOINT, headers=login(ROLE_USERS[role])), 403, "FORBIDDEN")
    assert "HYBRID_WEIGHTS_CONFIG" in error["message"]


@pytest.mark.parametrize("role", ["ML_ADMIN", "SYS_ADMIN"])
def test_roles_with_permission_get_200(client, login, role):
    response = client.get(ML_ENDPOINT, headers=login(ROLE_USERS[role]))
    assert response.status_code == 200
    assert response.json() == {"alpha": 0.3, "beta": 0.7, "passThreshold": 0.5, "exceptionThreshold": 0.7, "psiThreshold": 0.2}


def test_no_token_is_401_not_403(client):
    assert_error_shape(client.get(ML_ENDPOINT), 401, "UNAUTHORIZED")


def test_permission_scopes(db):
    from sqlalchemy import select

    from app.models import User

    def scope(username, code):
        return db.scalar(select(User).where(User.username == username)).permission_scope(code)

    assert scope("priya.sharma", "TRADE_VIEW") == "OWN"
    assert scope("ops.user1", "AUDIT_VIEW") == "ASSIGNED"
    assert scope("ops.manager", "AUDIT_VIEW") == "ALL"
    assert scope("priya.sharma", "AUDIT_VIEW") is None
