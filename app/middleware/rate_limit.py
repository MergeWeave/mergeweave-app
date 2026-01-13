"""
Rate Limiting Middleware.

Implements distributed rate limiting using Redis sliding window algorithm.
Provides per-IP and per-installation rate limits with configurable policies.

Phase 4 - Configuration & Production Readiness.

NOTE: Uses pure ASGI middleware pattern (not BaseHTTPMiddleware) to ensure
consistency with other middlewares and proper scope["state"] propagation.

See: https://github.com/encode/starlette/issues/1012
"""

import json
import logging
import time
from typing import Optional, Callable
from dataclasses import dataclass

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Send, Scope, Message

from app.cache.redis_client import get_redis_client
from app.config import get_config

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Rate limit configuration for an endpoint pattern."""
    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    burst_limit: int = 10


# Endpoint-specific rate limits
RATE_LIMIT_CONFIGS: dict[str, RateLimitConfig] = {
    # Webhook endpoints - high limits for GitHub callbacks
    "/webhooks": RateLimitConfig(
        requests_per_minute=120,
        requests_per_hour=5000,
        burst_limit=20,
    ),
    # Dashboard API - moderate limits
    "/api/dashboard": RateLimitConfig(
        requests_per_minute=60,
        requests_per_hour=1000,
        burst_limit=10,
    ),
    # Config API - lower limits (config changes should be infrequent)
    "/api/dashboard/config": RateLimitConfig(
        requests_per_minute=30,
        requests_per_hour=500,
        burst_limit=5,
    ),
    # Health endpoints - exempt from strict limits
    "/health": RateLimitConfig(
        requests_per_minute=300,
        requests_per_hour=10000,
        burst_limit=50,
    ),
    # Metrics - exempt from strict limits
    "/metrics": RateLimitConfig(
        requests_per_minute=300,
        requests_per_hour=10000,
        burst_limit=50,
    ),
}

# Default rate limit for unmatched endpoints
DEFAULT_RATE_LIMIT = RateLimitConfig(
    requests_per_minute=60,
    requests_per_hour=1000,
    burst_limit=10,
)


class RateLimitMiddleware:
    """
    Pure ASGI rate limiting middleware using Redis.

    Features:
    - Per-IP rate limiting for anonymous requests
    - Per-user/installation rate limiting for authenticated requests
    - Sliding window algorithm for smooth rate limiting
    - Configurable limits per endpoint pattern
    - Graceful degradation if Redis unavailable

    Rate limit headers in response:
    - X-RateLimit-Limit: Maximum requests allowed
    - X-RateLimit-Remaining: Requests remaining in window
    - X-RateLimit-Reset: Unix timestamp when limit resets
    - Retry-After: Seconds to wait (only on 429 responses)
    """

    # Paths to exclude from rate limiting
    EXCLUDED_PATHS = frozenset([
        "/",
        "/docs",
        "/redoc",
        "/openapi.json",
    ])

    def __init__(self, app: ASGIApp, enabled: bool = True):
        """
        Initialize middleware.

        Args:
            app: ASGI app to wrap
            enabled: Whether rate limiting is enabled (default True)
        """
        self.app = app
        self.enabled = enabled
        self._config = get_config()

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

        path = scope.get("path", "")

        # Skip if disabled or excluded path
        if not self.enabled or path in self.EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        # Skip rate limiting in debug mode if configured
        if self._config.debug and not getattr(self._config, "rate_limit_in_debug", False):
            await self.app(scope, receive, send)
            return

        # Get rate limit config for this endpoint
        rate_config = self._get_rate_config(path)

        # Get client identifier (IP or installation ID)
        client_id = self._get_client_id(scope)

        # Check rate limit
        try:
            allowed, remaining, reset_at = await self._check_rate_limit(
                client_id=client_id,
                path_prefix=self._get_path_prefix(path),
                config=rate_config,
            )
        except Exception as e:
            # Graceful degradation - allow request if Redis fails
            logger.warning(f"Rate limit check failed, allowing request: {e}")
            await self.app(scope, receive, send)
            return

        if not allowed:
            retry_after = max(1, int(reset_at - time.time()))
            logger.warning(
                f"Rate limit exceeded",
                extra={
                    "client_id": client_id,
                    "path": path,
                    "retry_after": retry_after,
                }
            )
            await self._send_rate_limit_response(send, rate_config, reset_at, retry_after)
            return

        # Wrap send to add rate limit headers to response
        async def send_with_rate_limit_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-ratelimit-limit", str(rate_config.requests_per_minute).encode()),
                    (b"x-ratelimit-remaining", str(remaining).encode()),
                    (b"x-ratelimit-reset", str(int(reset_at)).encode()),
                ])
                message = {**message, "headers": headers}

            await send(message)

        await self.app(scope, receive, send_with_rate_limit_headers)

    async def _send_rate_limit_response(
        self,
        send: Send,
        config: RateLimitConfig,
        reset_at: float,
        retry_after: int
    ) -> None:
        """Send a 429 Too Many Requests response."""
        body = json.dumps({
            "error": "Too Many Requests",
            "message": "Rate limit exceeded. Please slow down.",
            "retry_after": retry_after,
        }).encode("utf-8")

        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"x-ratelimit-limit", str(config.requests_per_minute).encode()),
                (b"x-ratelimit-remaining", b"0"),
                (b"x-ratelimit-reset", str(int(reset_at)).encode()),
                (b"retry-after", str(retry_after).encode()),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })

    def _get_rate_config(self, path: str) -> RateLimitConfig:
        """Get rate limit config for a path."""
        for prefix, config in RATE_LIMIT_CONFIGS.items():
            if path.startswith(prefix):
                return config
        return DEFAULT_RATE_LIMIT

    def _get_path_prefix(self, path: str) -> str:
        """Get path prefix for rate limit key grouping."""
        for prefix in RATE_LIMIT_CONFIGS.keys():
            if path.startswith(prefix):
                return prefix
        return "default"

    def _get_client_id(self, scope: Scope) -> str:
        """
        Get unique client identifier for rate limiting.

        Priority:
        1. Installation ID from auth context (for authenticated requests)
        2. User ID from auth context (for dashboard requests)
        3. X-Forwarded-For header (for proxied requests)
        4. Client IP address
        """
        state = scope.get("state", {})

        # Check for installation ID in scope state (set by auth middleware)
        if state.get("installation_id"):
            return f"installation:{state['installation_id']}"

        # Check for user ID in scope state
        if state.get("user_id"):
            return f"user:{state['user_id']}"

        # Get IP address from headers or client info
        headers_dict = {k: v for k, v in scope.get("headers", [])}
        forwarded_for = headers_dict.get(b"x-forwarded-for", b"").decode("utf-8")

        if forwarded_for:
            # Take first IP in chain (original client)
            client_ip = forwarded_for.split(",")[0].strip()
        else:
            # Get from client tuple
            client = scope.get("client")
            client_ip = client[0] if client else "unknown"

        return f"ip:{client_ip}"

    async def _check_rate_limit(
        self,
        client_id: str,
        path_prefix: str,
        config: RateLimitConfig,
    ) -> tuple[bool, int, float]:
        """
        Check if request is within rate limits using sliding window.

        Uses individual Redis commands instead of pipeline for compatibility
        with the RedisClient wrapper.

        Args:
            client_id: Unique client identifier
            path_prefix: Endpoint prefix for grouping
            config: Rate limit configuration

        Returns:
            Tuple of (allowed, remaining, reset_timestamp)
        """
        redis = await get_redis_client()
        if redis is None:
            # No Redis - allow request (fail open)
            return (True, config.requests_per_minute, time.time() + 60)

        now = time.time()
        window_start = now - 60  # 1 minute window

        # Redis key for this client + endpoint
        key = f"ratelimit:{path_prefix}:{client_id}"

        try:
            # Use individual commands instead of pipeline
            # (RedisClient wrapper doesn't support pipeline)

            # Remove old entries outside the window
            await redis.zremrangebyscore(key, 0, window_start)

            # Count current entries in window
            current_count = await redis.zcard(key)

            # Check if within limit BEFORE adding
            limit = config.requests_per_minute
            reset_at = now + 60

            if current_count >= limit:
                # Rate limited - don't add this request
                return (False, 0, reset_at)

            # Add current request
            await redis.zadd(key, {str(now): now})

            # Set TTL on key (cleanup)
            await redis.expire(key, 120)  # 2 minute TTL

            remaining = max(0, limit - current_count - 1)  # -1 for current request
            return (True, remaining, reset_at)

        except Exception as e:
            logger.warning(f"Redis rate limit operation failed: {e}")
            # Fail open - allow request
            return (True, config.requests_per_minute, time.time() + 60)


class RateLimitByInstallation:
    """
    Decorator for per-installation rate limiting on specific routes.

    Usage:
        @router.post("/endpoint")
        @RateLimitByInstallation(requests_per_minute=10)
        async def endpoint(request: Request):
            ...
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
    ):
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour

    def __call__(self, func: Callable) -> Callable:
        async def wrapper(request: Request, *args, **kwargs):
            # Get installation ID from request
            installation_id = getattr(request.state, "installation_id", None)
            if not installation_id:
                # No installation ID - use IP-based limiting
                client_id = request.client.host if request.client else "unknown"
            else:
                client_id = f"installation:{installation_id}"

            # Check rate limit
            redis = await get_redis_client()
            if redis:
                key = f"ratelimit:route:{func.__name__}:{client_id}"
                now = time.time()
                window_start = now - 60

                # Sliding window check
                await redis.zremrangebyscore(key, 0, window_start)
                count = await redis.zcard(key)

                if count >= self.requests_per_minute:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "Too Many Requests",
                            "message": f"Rate limit of {self.requests_per_minute}/min exceeded",
                        },
                        headers={"Retry-After": "60"},
                    )

                await redis.zadd(key, {str(now): now})
                await redis.expire(key, 120)

            return await func(request, *args, **kwargs)

        # Preserve function metadata
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper


async def get_rate_limit_status(client_id: str) -> dict:
    """
    Get current rate limit status for a client.

    Useful for debugging and monitoring.

    Args:
        client_id: Client identifier

    Returns:
        Dict with rate limit status per endpoint
    """
    redis = await get_redis_client()
    if redis is None:
        return {"error": "Redis not available"}

    now = time.time()
    window_start = now - 60

    status = {}
    for prefix, config in RATE_LIMIT_CONFIGS.items():
        key = f"ratelimit:{prefix}:{client_id}"

        # Count requests in current window
        count = await redis.zcount(key, window_start, now)

        status[prefix] = {
            "limit": config.requests_per_minute,
            "used": count,
            "remaining": max(0, config.requests_per_minute - count),
        }

    return status
