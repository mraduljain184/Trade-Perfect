from tests.conftest import assert_error_shape


def test_health_ok(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "model": None}  # no model registered yet
    assert response.headers["X-Request-ID"]


def test_health_alias_and_request_id_echo(client):
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.status_code == 200 and response.headers["X-Request-ID"] == "abc-123"


def test_swagger_and_openapi_load(client):
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/api/v1/auth/login", "/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/health"):
        assert path in paths
    for path, methods in paths.items():
        for method, spec in methods.items():
            assert spec.get("summary") and spec.get("tags"), f"{method} {path} needs summary + tags"


def test_unknown_route_uses_error_shape(client):
    assert_error_shape(client.get("/api/v1/does-not-exist"), 404, "NOT_FOUND")


def test_validation_error_uses_error_shape(client):
    error = assert_error_shape(client.post("/api/v1/auth/login", json={"username": "x"}), 422, "VALIDATION_ERROR")
    assert error["fields"] == [{"field": "password", "ruleId": None, "message": "Field required"}]


def test_unexpected_error_is_500_without_stack_trace(client):
    from app.main import app

    @app.get("/api/v1/_boom", include_in_schema=False)
    def _boom():
        raise RuntimeError("secret internal detail")

    response = client.get("/api/v1/_boom")
    assert_error_shape(response, 500, "INTERNAL_ERROR")
    assert "secret internal detail" not in response.text and "Traceback" not in response.text


def test_cors_allows_the_frontend_origin(client):
    response = client.options("/api/v1/auth/login", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
