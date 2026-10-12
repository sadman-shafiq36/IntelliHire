from fastapi.testclient import TestClient

from tests.conftest import auth_header


def test_register_returns_a_token_and_the_user(client: TestClient):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "New.Person@Example.COM",
            "password": "correct-horse-battery",
            "full_name": "New Person",
            "role": "interviewer",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "interviewer"
    # emails are normalised so casing can't create a duplicate
    assert body["user"]["email"] == "new.person@example.com"


def test_response_never_contains_the_password_hash(client: TestClient):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "person@example.com",
            "password": "correct-horse-battery",
            "full_name": "Person",
            "role": "interviewer",
        },
    )
    assert "password_hash" not in response.text
    assert "correct-horse-battery" not in response.text


def test_duplicate_email_is_a_conflict(client: TestClient):
    payload = {
        "email": "dup@example.com",
        "password": "correct-horse-battery",
        "full_name": "Dup",
        "role": "interviewer",
    }
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201

    second = client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


def test_short_password_is_rejected_before_reaching_the_database(client: TestClient):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "short@example.com",
            "password": "abc",
            "full_name": "Short",
            "role": "interviewer",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_validation_error_does_not_echo_the_submitted_password(client: TestClient):
    # error response must not echo what was sent
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "not-an-email",
            "password": "secret-value-xyz",
            "full_name": "X",
            "role": "interviewer",
        },
    )
    assert response.status_code == 422
    assert "secret-value-xyz" not in response.text


def test_login_succeeds_with_correct_credentials(client: TestClient):
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "login@example.com",
            "password": "correct-horse-battery",
            "full_name": "Login",
            "role": "interviewer",
        },
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "login@example.com", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_login_failure_does_not_reveal_whether_the_account_exists(client: TestClient):
    # identical messages, otherwise emails can be enumerated
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "known@example.com",
            "password": "correct-horse-battery",
            "full_name": "Known",
            "role": "interviewer",
        },
    )
    wrong_password = client.post(
        "/api/v1/auth/login",
        json={"email": "known@example.com", "password": "wrong-password"},
    )
    unknown_account = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "wrong-password"},
    )
    assert wrong_password.status_code == unknown_account.status_code == 401
    assert wrong_password.json() == unknown_account.json()


def test_me_requires_a_token(client: TestClient):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_returns_the_signed_in_user(client: TestClient, interviewer_token: str):
    response = client.get("/api/v1/auth/me", headers=auth_header(interviewer_token))
    assert response.status_code == 200
    assert response.json()["role"] == "interviewer"


def test_garbage_token_is_rejected(client: TestClient):
    response = client.get("/api/v1/auth/me", headers=auth_header("not.a.jwt"))
    assert response.status_code == 401


# --- candidates must not reach interviewer-only data ----------------------


def test_candidate_cannot_poll_background_jobs(client: TestClient, candidate_token: str):
    response = client.get("/api/v1/background-jobs/any-id", headers=auth_header(candidate_token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_interviewer_passes_the_guard(client: TestClient, interviewer_token: str, monkeypatch):
    # redis isn't running under pytest so the lookup is patched.
    # a 404 means the guard let the request through
    monkeypatch.setattr("app.api.v1.background_jobs.read_progress", lambda job_id: None)
    response = client.get(
        "/api/v1/background-jobs/unknown-id", headers=auth_header(interviewer_token)
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
