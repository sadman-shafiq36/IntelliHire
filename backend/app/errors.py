# every error response has the shape
#   {"error": {"code": "...", "message": "...", "details": {...}}}
# tracebacks only go to the log, never to the client

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_config import get_logger

# starlette renamed the 422 constant, so just use the number
HTTP_422_UNPROCESSABLE = 422

log = get_logger(__name__)


class AppError(Exception):
    # message is shown to the user as-is, anything else becomes a generic 500

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class InvalidInputError(AppError):
    status_code = HTTP_422_UNPROCESSABLE
    code = "validation_error"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_failed"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class ExternalServiceError(AppError):
    # upstream message is not forwarded, it can contain prompts or credentials

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "external_service_unavailable"


def _error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        log.warning(
            "app_error",
            code=exc.code,
            status_code=exc.status_code,
            detail=exc.details,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # fastapi's default echoes the input back, which could leak cv data
        problems = [
            {
                "field": ".".join(str(part) for part in err["loc"][1:]),
                "reason": err["msg"],
            }
            for err in exc.errors()
        ]
        log.info("request_validation_failed", problem_count=len(problems))
        return JSONResponse(
            status_code=HTTP_422_UNPROCESSABLE,
            content=_error_body(
                "validation_error",
                "The request was not valid.",
                {"problems": problems},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body("http_error", str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(
                "internal_error",
                "Something went wrong on our side. The incident has been logged.",
            ),
        )
