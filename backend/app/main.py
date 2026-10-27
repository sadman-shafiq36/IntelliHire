from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    assessment,
    auth,
    background_jobs,
    candidate_feedback,
    candidates,
    copilot,
    health,
    identity,
    integrations,
    interview_analysis,
    interview_reviews,
    interview_room,
    interviews,
    jobs,
    overview,
    policies,
    postings,
    scoring,
    screening,
    usage,
)
from app.config import settings
from app.errors import register_exception_handlers
from app.logging_config import configure_logging, get_logger

log = get_logger(__name__)

API_V1_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # keep startup work here so importing the module in tests touches nothing
    configure_logging()
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    log.info(
        "application_startup",
        environment=settings.app_env,
        storage_dir=settings.storage_dir,
    )
    yield
    log.info("application_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="IntelliHire API",
        description=(
            "AI-powered recruitment and interview intelligence platform. "
            "Screens CVs against job requirements, runs the live interview, "
            "and produces an evidence-backed consolidated assessment."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    # wildcard origins don't work with credentials, so they come from config
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    # health stays unversioned so container healthchecks don't depend on it
    app.include_router(health.router)

    app.include_router(auth.router, prefix=API_V1_PREFIX)
    app.include_router(background_jobs.router, prefix=API_V1_PREFIX)
    app.include_router(policies.router, prefix=API_V1_PREFIX)
    app.include_router(policies.references_router, prefix=API_V1_PREFIX)
    app.include_router(jobs.router, prefix=API_V1_PREFIX)
    app.include_router(postings.router, prefix=API_V1_PREFIX)
    app.include_router(integrations.router, prefix=API_V1_PREFIX)
    app.include_router(overview.router, prefix=API_V1_PREFIX)
    app.include_router(screening.router, prefix=API_V1_PREFIX)
    app.include_router(scoring.router, prefix=API_V1_PREFIX)
    app.include_router(candidates.router, prefix=API_V1_PREFIX)
    app.include_router(interviews.router, prefix=API_V1_PREFIX)
    app.include_router(interview_room.router, prefix=API_V1_PREFIX)
    app.include_router(copilot.router, prefix=API_V1_PREFIX)
    app.include_router(interview_analysis.router, prefix=API_V1_PREFIX)
    app.include_router(assessment.router, prefix=API_V1_PREFIX)
    app.include_router(interview_reviews.router, prefix=API_V1_PREFIX)
    app.include_router(identity.router, prefix=API_V1_PREFIX)
    app.include_router(candidate_feedback.router, prefix=API_V1_PREFIX)
    app.include_router(usage.router, prefix=API_V1_PREFIX)

    return app


app = create_app()
