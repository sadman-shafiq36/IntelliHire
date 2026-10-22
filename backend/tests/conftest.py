# tests run on in-memory sqlite, so pytest needs nothing running

import importlib
import itertools
import os
import tempfile
import uuid
from collections.abc import Iterator

import pytest

# settings are read at import time, set the env before importing app
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-not-used-anywhere-real")
os.environ.setdefault("APP_ENV", "development")
# forced, not setdefault: the container's .env must never leak into tests
os.environ["USE_FAKE_AI"] = "true"
# fake embeddings score lower than real ones
os.environ["POLICY_MIN_SCORE"] = "0.05"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="intellihire-test-")
# a public deployment turns this off; the tests sign people up
os.environ["ALLOW_REGISTRATION"] = "true"
os.environ["LIVEKIT_URL"] = "http://livekit:7880"
os.environ["LIVEKIT_PUBLIC_URL"] = "ws://localhost:7880"
# real integration credentials must never reach a test, the providers fixture sets fakes
for _name in (
    "LINKEDIN_CLIENT_ID",
    "LINKEDIN_CLIENT_SECRET",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_FORM_TEMPLATE_ID",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
):
    os.environ[_name] = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.rag.policy_store import PolicyStore, set_policy_store  # noqa: E402


@pytest.fixture
def db_session() -> Iterator[Session]:
    # StaticPool keeps one connection, otherwise each checkout gets a new empty db
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, future=True)

    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    app = create_app()

    def override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def interviewer_token(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "interviewer@example.com",
            "password": "correct-horse-battery",
            "full_name": "Test Interviewer",
            "role": "interviewer",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


@pytest.fixture
def candidate_token(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "candidate@example.com",
            "password": "correct-horse-battery",
            "full_name": "Test Candidate",
            "role": "candidate",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def policy_store() -> Iterator[PolicyStore]:
    import chromadb

    # fresh collection per test, the ephemeral client shares state in-process
    store = PolicyStore(chromadb.EphemeralClient(), collection_name=f"test_{uuid.uuid4().hex}")
    set_policy_store(store)
    yield store
    set_policy_store(None)


@pytest.fixture
def inline_jobs(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> list[dict]:
    # runs background jobs straight away against the test db instead of queueing them
    calls: list[dict] = []
    counter = itertools.count(1)

    def fake_enqueue(func_path: str, kind: str, **kwargs):
        module_path, func_name = func_path.rsplit(".", 1)
        func = getattr(importlib.import_module(module_path), func_name)
        job_id = f"inline-{next(counter)}"
        try:
            result = func(**kwargs)
            calls.append({"job_id": job_id, "kind": kind, "result": result, "error": None})
        except Exception as exc:
            calls.append({"job_id": job_id, "kind": kind, "result": None, "error": exc})
        return job_id

    monkeypatch.setattr("app.worker.queue.enqueue", fake_enqueue)
    monkeypatch.setattr(
        "app.worker.tasks.get_session_factory", lambda: lambda: _NonClosingSession(db_session)
    )
    return calls


class _NonClosingSession:
    # lets a task use "with factory() as db" without closing the shared test session
    def __init__(self, session: Session) -> None:
        self._session = session

    def __enter__(self) -> Session:
        return self._session

    def __exit__(self, *exc) -> None:
        return None


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch):
    # configured linkedin + google pointing at the in-memory fake
    from app.config import settings
    from app.tools import http
    from tests.fake_providers import FakeProviders

    fake = FakeProviders()
    for name, value in {
        "linkedin_client_id": "li-client",
        "linkedin_client_secret": "li-secret",
        "google_oauth_client_id": "g-client",
        "google_oauth_client_secret": "g-secret",
        "google_form_template_id": fake.template_id,
    }.items():
        monkeypatch.setattr(settings, name, value)
    http.set_transport(fake.transport())
    yield fake
    http.set_transport(None)


def connect(client: TestClient, token: str, provider: str) -> None:
    # runs the oauth round trip against the fake provider
    from urllib.parse import parse_qs, urlparse

    url = client.post(
        f"/api/v1/integrations/{provider}/connect", headers=auth_header(token)
    ).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    response = client.get(
        f"/api/v1/integrations/{provider}/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 307, response.text
    assert f"connected={provider}" in response.headers["location"]
