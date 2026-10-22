from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.enums import IntegrationProvider
from app.models.integration import IntegrationAccount
from app.services import integrations
from app.tools.http import ProviderError
from tests.conftest import auth_header, connect


def _status(client: TestClient, token: str) -> dict[str, dict]:
    rows = client.get("/api/v1/integrations", headers=auth_header(token)).json()
    return {row["provider"]: row for row in rows}


def test_unconfigured_providers_say_what_to_do(client: TestClient, interviewer_token: str):
    status = _status(client, interviewer_token)
    assert status["linkedin"]["configured"] is False
    assert status["google"]["connected"] is False

    response = client.post(
        "/api/v1/integrations/linkedin/connect", headers=auth_header(interviewer_token)
    )
    assert response.status_code == 422
    assert "integrations-setup.md" in response.json()["error"]["message"]


def test_connect_url_asks_for_the_right_permissions(
    client: TestClient, interviewer_token: str, providers
):
    url = client.post(
        "/api/v1/integrations/google/connect", headers=auth_header(interviewer_token)
    ).json()["authorize_url"]
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["g-client"]
    assert query["access_type"] == ["offline"]
    assert "https://www.googleapis.com/auth/forms.body" in query["scope"][0]
    assert query["redirect_uri"] == ["http://localhost:8000/api/v1/integrations/google/callback"]

    url = client.post(
        "/api/v1/integrations/linkedin/connect", headers=auth_header(interviewer_token)
    ).json()["authorize_url"]
    assert "w_member_social" in parse_qs(urlparse(url).query)["scope"][0]


def test_connecting_stores_an_encrypted_token(
    client: TestClient, interviewer_token: str, providers, db_session
):
    connect(client, interviewer_token, "linkedin")
    connect(client, interviewer_token, "google")

    status = _status(client, interviewer_token)
    assert status["linkedin"]["connected"] is True
    assert status["linkedin"]["account_name"] == "Test Member"
    assert status["google"]["account_name"] == "owner@example.com"
    # tokens never leave the server
    assert "token" not in str(status).lower().replace("expires", "")

    account = db_session.scalar(
        select(IntegrationAccount).where(
            IntegrationAccount.provider == IntegrationProvider.LINKEDIN
        )
    )
    assert account.account_id == "member-42"
    assert "li-token" not in account.access_token_enc
    assert integrations.decrypt(account.access_token_enc) == "li-token"


def test_a_forged_or_expired_state_is_refused(client: TestClient, providers):
    response = client.get(
        "/api/v1/integrations/linkedin/callback",
        params={"code": "x", "state": "not-a-real-state"},
        follow_redirects=False,
    )
    assert response.status_code == 307
    assert "error=" in response.headers["location"]
    assert "connected" not in response.headers["location"]


def test_a_state_for_one_provider_cannot_complete_another(
    client: TestClient, interviewer_token: str, providers
):
    url = client.post(
        "/api/v1/integrations/linkedin/connect", headers=auth_header(interviewer_token)
    ).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    response = client.get(
        "/api/v1/integrations/google/callback",
        params={"code": "x", "state": state},
        follow_redirects=False,
    )
    assert "error=" in response.headers["location"]


def test_declined_consent_comes_back_with_an_error(client: TestClient, providers):
    response = client.get(
        "/api/v1/integrations/google/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert "error=declined" in response.headers["location"]


def test_an_expiring_google_token_is_refreshed(
    client: TestClient, interviewer_token: str, providers, db_session
):
    connect(client, interviewer_token, "google")
    account = db_session.scalar(
        select(IntegrationAccount).where(IntegrationAccount.provider == IntegrationProvider.GOOGLE)
    )
    account.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    token, _ = integrations.access_token(db_session, IntegrationProvider.GOOGLE)
    assert token == "g-token"
    assert providers.google_refreshes == 1


def test_a_revoked_google_refresh_token_asks_to_reconnect(
    client: TestClient, interviewer_token: str, providers, db_session
):
    connect(client, interviewer_token, "google")
    account = db_session.scalar(
        select(IntegrationAccount).where(IntegrationAccount.provider == IntegrationProvider.GOOGLE)
    )
    account.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    providers.google_refresh_revoked = True

    with pytest.raises(ProviderError, match="Reconnect Google"):
        integrations.access_token(db_session, IntegrationProvider.GOOGLE)


def test_an_expired_linkedin_connection_asks_to_reconnect(
    client: TestClient, interviewer_token: str, providers, db_session
):
    connect(client, interviewer_token, "linkedin")
    account = db_session.scalar(
        select(IntegrationAccount).where(
            IntegrationAccount.provider == IntegrationProvider.LINKEDIN
        )
    )
    account.expires_at = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()

    assert _status(client, interviewer_token)["linkedin"]["expired"] is True
    try:
        integrations.access_token(db_session, IntegrationProvider.LINKEDIN)
    except Exception as exc:
        assert "Reconnect LinkedIn" in exc.message
    else:
        raise AssertionError("expected a reconnect error")


def test_disconnect(client: TestClient, interviewer_token: str, providers):
    connect(client, interviewer_token, "linkedin")
    response = client.delete(
        "/api/v1/integrations/linkedin", headers=auth_header(interviewer_token)
    )
    assert response.status_code == 204
    assert _status(client, interviewer_token)["linkedin"]["connected"] is False


def test_unknown_provider_is_404(client: TestClient, interviewer_token: str):
    response = client.post(
        "/api/v1/integrations/twitter/connect", headers=auth_header(interviewer_token)
    )
    assert response.status_code == 404


def test_candidates_cannot_manage_integrations(client: TestClient, candidate_token: str):
    assert (
        client.get("/api/v1/integrations", headers=auth_header(candidate_token)).status_code == 403
    )
    response = client.post(
        "/api/v1/integrations/google/connect", headers=auth_header(candidate_token)
    )
    assert response.status_code == 403


def test_a_provider_error_is_explained_not_called_declined(client: TestClient, providers):
    response = client.get(
        "/api/v1/integrations/linkedin/callback",
        params={
            "error": "unauthorized_scope_error",
            "error_description": 'Scope "w_member_social" is not authorized for your application',
        },
        follow_redirects=False,
    )
    location = response.headers["location"]
    assert "LinkedIn+refused+the+connection" in location
    assert "w_member_social" in location
