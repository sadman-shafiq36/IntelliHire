# tests run on in-memory sqlite, so pytest needs nothing running

import os
from collections.abc import Iterator

import pytest

# settings are read at import time, set the env before importing app
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-not-used-anywhere-real")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("USE_FAKE_AI", "true")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402


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
