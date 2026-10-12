from fastapi.testclient import TestClient


def test_health_reports_ok(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_needs_no_authentication(client: TestClient):
    # docker's healthcheck has no token
    assert "Authorization" not in client.headers
    assert client.get("/health").status_code == 200


def test_openapi_document_is_generated(client: TestClient):
    response = client.get("/openapi.json")
    assert response.status_code == 200

    paths = response.json()["paths"]
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/register" in paths
    assert "/health" in paths


def test_unknown_route_returns_the_standard_error_shape(client: TestClient):
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert "error" in response.json()
    assert "code" in response.json()["error"]
