"""
Global Error Handling Middleware and Exception Handlers.

Provides consistent error responses with correlation IDs and structured logging.

Aligned with Workstream 1 v3.0 spec.
"""

import logging
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """
    Handle HTTP exceptions with correlation ID.

    Args:
        request: FastAPI Request object
        exc: HTTP exception

    Returns:
        JSON response with error details and correlation ID
    """
    correlation_id = getattr(request.state, "correlation_id", "unknown")

    logger.warning(
        "HTTP exception",
        extra={
            "correlation_id": correlation_id,
            "status_code": exc.status_code,
            "detail": exc.detail,
            "path": request.url.path,
        },
    )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "correlation_id": correlation_id,
            "path": str(request.url.path),
        },
        headers={"X-Correlation-ID": correlation_id},
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Handle request validation errors with detailed field information.

    Args:
        request: FastAPI Request object
        exc: Validation error exception

    Returns:
        JSON response with validation error details
    """
    correlation_id = getattr(request.state, "correlation_id", "unknown")

    logger.warning(
        "Validation error",
        extra={
            "correlation_id": correlation_id,
            "errors": exc.errors(),
            "path": request.url.path,
        },
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "Validation error",
            "details": exc.errors(),
            "correlation_id": correlation_id,
            "path": str(request.url.path),
        },
        headers={"X-Correlation-ID": correlation_id},
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle unexpected exceptions with error logging.

    Args:
        request: FastAPI Request object
        exc: Any unhandled exception

    Returns:
        JSON response with generic error message (don't leak internals)
    """
    correlation_id = getattr(request.state, "correlation_id", "unknown")

    logger.error(
        "Unhandled exception",
        exc_info=True,
        extra={
            "correlation_id": correlation_id,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "path": request.url.path,
            "method": request.method,
        },
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "message": "An unexpected error occurred. Please contact support if the issue persists.",
            "correlation_id": correlation_id,
            "path": str(request.url.path),
        },
        headers={"X-Correlation-ID": correlation_id},
    )
