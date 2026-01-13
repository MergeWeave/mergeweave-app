"""
Version Headers Middleware.

Adds version information to all HTTP responses for debugging and tracing.

NOTE: Uses pure ASGI middleware pattern (not BaseHTTPMiddleware) to ensure
consistency with other middlewares and proper scope["state"] propagation.

See: https://github.com/encode/starlette/issues/1012
"""

import logging
from starlette.types import ASGIApp, Receive, Send, Scope, Message

from app.services.version_manifest import GITHUB_APP_VERSION

logger = logging.getLogger(__name__)


class VersionHeadersMiddleware:
    """
    Pure ASGI middleware that adds MergeWeave version headers to all responses.

    Headers added:
    - X-MergeWeave-Service: Service name (github-app)
    - X-MergeWeave-Version: Service version
    - X-MergeWeave-CRE-Version: CRE engine version (if available)
    - X-MergeWeave-IO-Contract: IO Contract version
    """

    def __init__(
        self,
        app: ASGIApp,
        cre_version: str = None,
        io_contract_version: str = "1.0.0"
    ):
        """
        Initialize middleware.

        Args:
            app: ASGI app to wrap
            cre_version: Optional CRE version override
            io_contract_version: IO contract version (default "1.0.0")
        """
        self.app = app
        self._cre_version = cre_version
        self._io_contract_version = io_contract_version
        self._load_cre_version()
        self._build_headers()

    def _load_cre_version(self):
        """Load CRE version if not provided."""
        if self._cre_version is None:
            try:
                from mergeweave_cre import __version__ as cre_version
                self._cre_version = cre_version
            except ImportError:
                logger.warning("CRE module not available for version header")
                self._cre_version = "unknown"

        try:
            from mergeweave_cre import __io_contract_version__
            self._io_contract_version = __io_contract_version__
        except (ImportError, AttributeError):
            pass

    def _build_headers(self):
        """Pre-build headers list for efficiency."""
        self._version_headers = [
            (b"x-mergeweave-service", b"github-app"),
            (b"x-mergeweave-version", GITHUB_APP_VERSION.encode("utf-8")),
            (b"x-mergeweave-io-contract", self._io_contract_version.encode("utf-8")),
        ]
        if self._cre_version:
            self._version_headers.append(
                (b"x-mergeweave-cre-version", self._cre_version.encode("utf-8"))
            )

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

        # Wrap send to add version headers to response
        async def send_with_version_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self._version_headers)
                message = {**message, "headers": headers}

            await send(message)

        await self.app(scope, receive, send_with_version_headers)
