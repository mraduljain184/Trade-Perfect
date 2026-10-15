"""BE-8: the whole RBAC matrix, generated from PROJECT_CONTEXT section 5.2 (scripts/seed_data.PERMISSIONS).

For EVERY API route and EVERY role: a role that has (one of) the route's permission(s) must NOT
get 403, and a role without it MUST get 403. Requests are chosen so that an allowed call changes
nothing (unknown IDs -> 404, empty bodies -> 422).

The same table proves that every route is permission-guarded and documented in Swagger: a route
that is missing from ROUTES fails `test_every_api_route_is_in_the_matrix`.
"""
import pytest

from app.main import app
from app.services import ml_service
from scripts import seed_data
from tests.conftest import ROLE_USERS, assert_error_shape

API = "/api/v1"
ANY = "ANY"  # any logged-in user
PUBLIC = {("POST", "/auth/login"), ("GET", "/health")}
REPORT = "audit_X_20260101_000000000.pdf"

# (method, route template, concrete path, body, permission codes - any one of them is enough)
ROUTES = [
    ("GET", "/auth/me", "/auth/me", None, ANY),
    ("POST", "/auth/logout", "/auth/logout", None, ANY),
    ("GET", "/reference/symbols", "/reference/symbols", None, ANY),
    ("GET", "/reference/counterparties", "/reference/counterparties", None, ANY),
    ("GET", "/reference/accounts", "/reference/accounts", None, ANY),
    ("GET", "/reference/users", "/reference/users", None, ("QUEUE_ASSIGN", "AUDIT_VIEW")),
    ("POST", "/trades/validate", "/trades/validate", {}, ("TRADE_SUBMIT_SINGLE", "TRADE_AMEND")),
    ("POST", "/trades", "/trades", {}, ("TRADE_SUBMIT_SINGLE",)),
    ("GET", "/trades", "/trades?pageSize=1", None, ("TRADE_VIEW",)),
    ("GET", "/trades/export", "/trades/export?search=NOPE-NOPE", None, ("TRADE_VIEW",)),
    ("GET", "/trades/{trade_id}", "/trades/NOPE1", None, ("TRADE_VIEW",)),
    ("GET", "/trades/{trade_id}/history", "/trades/NOPE1/history", None, ("TRADE_VIEW",)),
    ("PUT", "/trades/{trade_id}", "/trades/NOPE1", {}, ("TRADE_AMEND",)),
    ("POST", "/trades/bulk", "/trades/bulk", None, ("TRADE_SUBMIT_BULK",)),
    ("GET", "/uploads/{batch_id}", "/uploads/BATCH-19990101-0001", None, ("TRADE_SUBMIT_BULK",)),
    ("GET", "/uploads/{batch_id}/rejections", "/uploads/BATCH-19990101-0001/rejections", None, ("TRADE_SUBMIT_BULK",)),
    ("GET", "/dashboard/summary", "/dashboard/summary", None, ("EXCEPTION_DASHBOARD_VIEW", "TRADE_VIEW")),
    ("GET", "/dashboard/charts", "/dashboard/charts", None, ("EXCEPTION_DASHBOARD_VIEW", "TRADE_VIEW")),
    ("GET", "/repair-queue", "/repair-queue?pageSize=1", None, ("REPAIR_QUEUE_MANAGE",)),
    ("GET", "/repair-queue/summary", "/repair-queue/summary", None, ("REPAIR_QUEUE_MANAGE",)),
    ("GET", "/repair-queue/export", "/repair-queue/export?search=NOPE-NOPE", None, ("REPAIR_QUEUE_MANAGE",)),
    ("GET", "/repair-queue/{queue_id}", "/repair-queue/RQ-999999", None, ("REPAIR_QUEUE_MANAGE",)),
    ("POST", "/repair-queue/assign", "/repair-queue/assign", {"queueIds": ["RQ-999999"], "assigneeId": "<me>"}, ("QUEUE_ASSIGN",)),
    ("POST", "/repair-queue/bulk-resubmit", "/repair-queue/bulk-resubmit", {"queueIds": ["RQ-999999"]}, ("QUEUE_BULK_OPS",)),
    ("POST", "/repair-queue/{queue_id}/resubmit", "/repair-queue/RQ-999999/resubmit", None, ("FIX_APPLY",)),
    ("POST", "/repair-queue/{queue_id}/reject", "/repair-queue/RQ-999999/reject", {"reason": "matrix test"}, ("FIX_APPLY",)),
    ("POST", "/repair-queue/{queue_id}/approve", "/repair-queue/RQ-999999/approve", {"note": "matrix test"}, ("HIGH_RESOLUTION_APPROVE",)),
    ("GET", "/team/performance", "/team/performance", None, ("TEAM_METRICS_VIEW",)),
    ("GET", "/audit", "/audit?pageSize=1", None, ("AUDIT_VIEW",)),
    ("GET", "/audit/export", "/audit/export?tradeId=NOPE1", None, ("AUDIT_VIEW",)),
    ("GET", "/audit/verify", "/audit/verify", None, ("SYSTEM_CONFIG",)),
    ("GET", "/audit/reports/{file_name}", f"/audit/reports/{REPORT}", None, ("AUDIT_VIEW",)),
    ("GET", "/audit/trades/{trade_id}/timeline", "/audit/trades/NOPE1/timeline", None, ("AUDIT_VIEW",)),
    ("GET", "/audit/trades/{trade_id}/latest", "/audit/trades/NOPE1/latest", None, ("AUDIT_VIEW", "TRADE_VIEW")),
    ("POST", "/audit/trades/{trade_id}/report", "/audit/trades/NOPE1/report", None, ("AUDIT_VIEW",)),
    ("GET", "/ml/models", "/ml/models", None, ("MODEL_METRICS_VIEW",)),
    ("GET", "/ml/models/production", "/ml/models/production", None, ("MODEL_METRICS_VIEW",)),
    ("GET", "/ml/metrics/history", "/ml/metrics/history", None, ("MODEL_METRICS_VIEW",)),
    ("POST", "/ml/retrain", "/ml/retrain", None, ("MODEL_RETRAIN",)),
    ("GET", "/ml/retrain/{event_id}", "/ml/retrain/999999", None, ("MODEL_RETRAIN",)),
    ("POST", "/ml/models/{version}/promote", "/ml/models/v999/promote", None, ("MODEL_PROMOTE",)),
    ("GET", "/ml/drift/latest", "/ml/drift/latest", None, ("DRIFT_MONITOR",)),
    ("POST", "/ml/drift/run", "/ml/drift/run", None, ("DRIFT_MONITOR",)),
    ("GET", "/ml/config", "/ml/config", None, ("HYBRID_WEIGHTS_CONFIG",)),
    ("PUT", "/ml/config", "/ml/config", {}, ("HYBRID_WEIGHTS_CONFIG",)),
    ("GET", "/ml/feature-importance", "/ml/feature-importance", None, ("FEATURE_IMPORTANCE_VIEW",)),
    ("POST", "/ml/predict", "/ml/predict", {}, ("MODEL_METRICS_VIEW", "TRADE_SUBMIT_SINGLE")),
    ("POST", "/ml/predict/batch", "/ml/predict/batch", {"trades": [{}]}, ("MODEL_METRICS_VIEW", "TRADE_SUBMIT_BULK")),
    ("GET", "/admin/users", "/admin/users", None, ("USER_MANAGE",)),
    ("POST", "/admin/users", "/admin/users", {}, ("USER_MANAGE",)),
    ("PUT", "/admin/users/{user_id}", "/admin/users/999999", {"fullName": "Matrix Test"}, ("USER_MANAGE",)),
    ("GET", "/admin/config", "/admin/config", None, ("SYSTEM_CONFIG", "BUSINESS_RULES_CONFIG")),
    ("GET", "/admin/config/{key}", "/admin/config/batch_size", None, ("SYSTEM_CONFIG", "BUSINESS_RULES_CONFIG")),
    ("PUT", "/admin/config/{key}", "/admin/config/no_such_key", {"value": 1}, ("SYSTEM_CONFIG", "BUSINESS_RULES_CONFIG")),
    ("GET", "/admin/holidays", "/admin/holidays", None, ("SYSTEM_CONFIG",)),
    ("POST", "/admin/holidays", "/admin/holidays", {}, ("SYSTEM_CONFIG",)),
    ("DELETE", "/admin/holidays/{day}", "/admin/holidays/1999-01-01", None, ("SYSTEM_CONFIG",)),
    ("GET", "/notifications", "/notifications", None, ANY),
    ("POST", "/notifications/{notification_id}/read", "/notifications/999999999/read", None, ANY),
    ("POST", "/chat", "/chat", {"sessionId": "matrix", "message": "hello"}, ("CHATBOT_USE",)),
]

# Section 5.2 codes that guard no route on their own, and why.
NOT_A_ROUTE_GUARD = {
    "RISK_SCORE_VIEW": "the score is part of every trade response; there is no separate endpoint",
    "COMPLIANCE_REPORT": "compliance exports are the audit CSV / PDF endpoints (AUDIT_VIEW)",
    "ROLE_ASSIGN": "checked INSIDE PUT /admin/users/{id} when the role changes",
    "QUEUE_REASSIGN": "checked INSIDE POST /repair-queue/assign when assigning to someone else",
    "API_KEYS_MANAGE": "keys live in environment variables; there is no endpoint",
    "MONITORING_ALERTS_CONFIG": "alert thresholds are system_config keys (SYSTEM_CONFIG / HYBRID_WEIGHTS_CONFIG)",
}
ROLE_IDS = {code: role_id for role_id, code in seed_data.ROLE_CODES.items()}
GRANTS = {code: set(grants) for code, _, grants in seed_data.PERMISSIONS}


def allowed(role: str, permissions) -> bool:
    return permissions == ANY or any(ROLE_IDS[role] in GRANTS[code] for code in permissions)


@pytest.fixture(autouse=True, scope="module")
def _no_real_training(_database):
    """POST /ml/retrain is in the matrix: accept it, but do not train a model here."""
    from app.core.database import SessionLocal

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ml_service, "run_retrain", lambda event_id: None)
        yield
    with SessionLocal() as session:
        ml_service.fail_interrupted_retrains(session)


@pytest.mark.parametrize("role", list(ROLE_USERS))
@pytest.mark.parametrize("method, template, path, body, permissions", ROUTES, ids=[f"{r[0]} {r[1]}" for r in ROUTES])
def test_role_gets_403_exactly_when_it_lacks_the_permission(client, login, role, method, template, path, body, permissions):
    headers = login(ROLE_USERS[role])
    if body and body.get("assigneeId") == "<me>":
        body = {**body, "assigneeId": client.get(f"{API}/auth/me", headers=headers).json()["id"]}
    response = client.request(method, f"{API}{path}", json=body, headers=headers)
    if allowed(role, permissions):
        assert response.status_code not in (401, 403), f"{role} has the permission but got {response.status_code}: {response.text[:200]}"
        assert response.status_code < 500, response.text[:300]
    else:
        error = assert_error_shape(response, 403, "FORBIDDEN")
        assert error["message"] == f"Permission required: {' or '.join(permissions)}"


@pytest.mark.parametrize("method, template, path, body, permissions", ROUTES, ids=[f"{r[0]} {r[1]}" for r in ROUTES])
def test_without_a_token_every_route_is_401(client, method, template, path, body, permissions):
    assert_error_shape(client.request(method, f"{API}{path}", json=body), 401, "UNAUTHORIZED")


def _api_routes():
    """(METHOD, path below /api/v1, OpenAPI operation) for every route the app really serves."""
    for path, operations in app.openapi()["paths"].items():
        if path.startswith(API + "/"):
            for method, operation in operations.items():
                yield method.upper(), path[len(API):], operation


def test_every_api_route_is_in_the_matrix():
    real = {(method, path) for method, path, _ in _api_routes()}
    listed = {(method, template) for method, template, *_ in ROUTES}
    assert real - listed - PUBLIC == set(), "new route(s): add them to ROUTES with their permission"
    assert listed - real == set(), "ROUTES lists route(s) that no longer exist"
    assert PUBLIC <= real


def test_every_permission_code_is_used_or_explained():
    used = {code for *_, permissions in ROUTES if permissions != ANY for code in permissions}
    assert used | set(NOT_A_ROUTE_GUARD) == set(GRANTS), "section 5.2 and this matrix disagree"
    assert used & set(NOT_A_ROUTE_GUARD) == set()


def test_every_route_documents_itself_in_swagger():
    """Definition of Done: summary, tag and a described response for every endpoint."""
    for method, path, operation in _api_routes():
        assert operation.get("summary"), f"{method} {path} has no summary"
        assert operation.get("tags"), f"{method} {path} has no tag"
        assert operation.get("responses"), f"{method} {path} documents no response"
    document = app.openapi()
    assert len(document["paths"]) >= 50 and document["info"]["title"] == "Trade Perfect API"
