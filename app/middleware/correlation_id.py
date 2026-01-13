"""
Correlation ID Middleware for Request Tracking.

Generates or extracts correlation IDs for every request and adds them to:
- Request state (accessible in route handlers via scope["state"])
- Response headers (for client tracking)
- Logging context (for distributed tracing)

Aligned with Workstream 1 v3.0 spec.

NOTE: Uses pure ASGI middleware pattern (not BaseHTTPMiddleware) to ensure
scope["state"] modifications persist through the middleware chain. This is
required when multiple middlewares need to share state, due to a known
Starlette issue where BaseHTTPMiddleware creates separate Request objects.

See: https://github.com/encode/starlette/issues/1012
"""

import uuid
import logging
from typing import Callable, Awaitable

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Send, Scope, Message

logger = logging.getLogger(__name__)


class CorrelationIDMiddleware:
    """
    Pure ASGI middleware for correlation ID generation and propagation.

    Correlation IDs enable request tracing across distributed systems.
    Stores correlation_id in scope["state"] for downstream access.
    """

    CORRELATION_ID_HEADER = b"x-correlation-id"
    CORRELATION_ID_HEADER_STR = "X-Correlation-ID"

    def __init__(self, app: ASGIApp):
        """Initialize middleware with the ASGI app."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        ASGI middleware entry point.

        Args:
            scope: ASGI connection scope
            receive: ASGI receive callable
            send: ASGI send callable
        """
        # Only process HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract correlation ID from request headers or generate new one
        headers_dict = {k: v for k, v in scope.get("headers", [])}
        existing_id = headers_dict.get(self.CORRELATION_ID_HEADER, b"").decode("utf-8")
        correlation_id = existing_id if existing_id else str(uuid.uuid4())

        # Initialize state dict in scope if not present
        if "state" not in scope:
            scope["state"] = {}

        # Store correlation ID in scope state (accessible via request.state)
        scope["state"]["correlation_id"] = correlation_id

        # Extract path and method for logging
        path = scope.get("path", "unknown")
        method = scope.get("method", "unknown")

        logger.debug(
            "Request started",
            extra={
                "correlation_id": correlation_id,
                "method": method,
                "path": path,
            },
        )

        # Track response status for logging
        response_status = [None]

        # Wrap send to add correlation ID to response headers
        async def send_with_correlation_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Capture status code for logging
                response_status[0] = message.get("status")

                # Add correlation ID to response headers
                headers = list(message.get("headers", []))
                headers.append(
                    (self.CORRELATION_ID_HEADER, correlation_id.encode("utf-8"))
                )
                message = {**message, "headers": headers}

            await send(message)

        # Call next middleware/handler
        try:
            await self.app(scope, receive, send_with_correlation_id)
        except Exception as e:
            logger.error(
                "Request failed",
                exc_info=True,
                extra={
                    "correlation_id": correlation_id,
                    "error": str(e),
                },
            )
            raise

        logger.debug(
            "Request completed",
            extra={
                "correlation_id": correlation_id,
                "status_code": response_status[0],
            },
        )


def get_correlation_id(request: Request) -> str:
    """
    Helper function to get correlation ID from request state.

    Usage in route handlers:
        @app.get("/endpoint")
        async def endpoint(request: Request):
            correlation_id = get_correlation_id(request)
            logger.info("Processing request", extra={"correlation_id": correlation_id})

    Args:
        request: FastAPI Request object

    Returns:
        Correlation ID string
    """
    return getattr(request.state, "correlation_id", "unknown")
