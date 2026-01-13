"""
Security Headers Middleware.

Adds security-related HTTP headers to all responses to protect against
common web vulnerabilities.

Aligned with Workstream 1 v3.0 spec.

NOTE: Uses pure ASGI middleware pattern (not BaseHTTPMiddleware) to ensure
consistency with other middlewares and proper scope["state"] propagation.

See: https://github.com/encode/starlette/issues/1012
"""

import logging
from starlette.types import ASGIApp, Receive, Send, Scope, Message

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware:
    """
    Pure ASGI middleware to add security headers to all responses.

    Implements OWASP security best practices for HTTP headers.
    """

    # Security headers to add (header name as bytes, value as bytes)
    SECURITY_HEADERS = [
        # Prevent MIME type sniffing - forces browsers to respect declared Content-Type
        (b"x-content-type-options", b"nosniff"),
        # Prevent clickjacking attacks - prevents page from being embedded in iframes
        (b"x-frame-options", b"DENY"),
        # Enable XSS protection in older browsers (modern browsers use CSP)
        (b"x-xss-protection", b"1; mode=block"),
        # Referrer policy - control information sent in Referer header
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        # Permissions policy - disable potentially dangerous browser features
        (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
    ]

    # HSTS header for HTTPS connections
    HSTS_HEADER = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")

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

        # Check if this is an HTTPS request
        is_https = scope.get("scheme") == "https"

        # Wrap send to add security headers to response
        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Add security headers
                headers = list(message.get("headers", []))
                headers.extend(self.SECURITY_HEADERS)

                # Add HSTS header for HTTPS connections
                if is_https:
                    headers.append(self.HSTS_HEADER)

                message = {**message, "headers": headers}

            await send(message)

        await self.app(scope, receive, send_with_security_headers)
