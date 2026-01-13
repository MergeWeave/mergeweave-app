"""
Dashboard Authentication Middleware.

Validates authentication tokens for Dashboard API requests.
Integrates with the existing Public API authentication system.

This middleware:
1. Extracts bearer tokens from Authorization headers
2. Validates tokens against the Public API user session
3. Attaches user context to requests for downstream handlers

NOTE: Uses pure ASGI middleware pattern (not BaseHTTPMiddleware) to ensure
request.state modifications persist through the middleware chain. This is
required when multiple middlewares use BaseHTTPMiddleware due to a known
Starlette issue where request.state doesn't propagate correctly.
"""

import json
import logging
import time
from typing import Optional, Callable, Any
from uuid import UUID

import httpx
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from starlette.types import ASGIApp, Receive, Send, Scope

from app.config import get_config
from app.cache.redis_client import get_redis_client

logger = logging.getLogger(__name__)


def _safe_token_preview(token: str, prefix_len: int = 20) -> str:
    """Return a safe preview of a token for logging (first N chars + length)."""
    if not token:
        return "<empty>"
    if len(token) <= prefix_len:
        return f"{token[:8]}...[TRUNCATED] (len={len(token)})"
    return f"{token[:prefix_len]}...[TRUNCATED] (len={len(token)})"


class DashboardAuthMiddleware:
    """
    Authentication middleware for Dashboard API endpoints.

    Validates bearer tokens and attaches authenticated user context
    to requests. Only applies to /api/dashboard/* routes.

    Uses pure ASGI middleware pattern to ensure request.state persists
    correctly through middleware chain.

    Authentication flow:
    1. Check for Authorization header with Bearer token
    2. Validate token against Public API or cached session
    3. Attach user_id and permissions to request.state
    4. Continue to handler if valid, return 401 if not

    Skipped routes:
    - Health check endpoints
    - Webhook endpoints
    - OAuth endpoints
    """

    # Routes that don't require authentication
    # NOTE: Do NOT add "/" here - it would match all paths via startswith()!
    SKIP_AUTH_PATHS = {
        "/health",
        "/health/detailed",
        "/health/readiness",
        "/health/liveness",
        "/webhooks",
        "/oauth",
        "/docs",
        "/redoc",
        "/openapi.json",
    }

    # Cache TTL for validated tokens (5 minutes)
    TOKEN_CACHE_TTL = 300

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

        path = scope["path"]

        # Skip auth for non-dashboard routes
        if not path.startswith("/api/dashboard"):
            await self.app(scope, receive, send)
            return

        # Skip if path is in skip list
        for skip_path in self.SKIP_AUTH_PATHS:
            if path.startswith(skip_path):
                await self.app(scope, receive, send)
                return

        # Extract Authorization header from scope
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8")

        if not auth_header:
            logger.warning(
                "Dashboard auth failed: missing Authorization header",
                extra={"path": path}
            )
            await self._send_unauthorized_response(send, "Missing Authorization header")
            return

        if not auth_header.startswith("Bearer "):
            logger.warning(
                "Dashboard auth failed: invalid Authorization header format",
                extra={
                    "path": path,
                    "auth_header_preview": auth_header[:20] if auth_header else "<empty>"
                }
            )
            await self._send_unauthorized_response(send, "Invalid Authorization header format")
            return

        token = auth_header[7:]  # Strip "Bearer "

        if not token:
            logger.warning(
                "Dashboard auth failed: empty token after Bearer prefix",
                extra={"path": path}
            )
            await self._send_unauthorized_response(send, "Empty token")
            return

        # Log token validation attempt
        logger.debug(
            "Dashboard auth: validating token",
            extra={
                "path": path,
                "token_preview": _safe_token_preview(token)
            }
        )

        # Validate token
        start_time = time.time()
        user_context = await self._validate_token(token)
        validation_time_ms = (time.time() - start_time) * 1000

        if not user_context:
            logger.warning(
                "Dashboard auth failed: token validation returned no user context",
                extra={
                    "path": path,
                    "token_preview": _safe_token_preview(token),
                    "validation_time_ms": round(validation_time_ms, 2)
                }
            )
            await self._send_unauthorized_response(send, "Invalid or expired token")
            return

        # Initialize state dict in scope if not present
        if "state" not in scope:
            scope["state"] = {}

        # Attach user context directly to scope["state"]
        # This is what request.state reads from
        scope["state"]["user_id"] = user_context.get("user_id")
        scope["state"]["user_email"] = user_context.get("email")
        scope["state"]["permissions"] = user_context.get("permissions", [])
        scope["state"]["token"] = token

        logger.info(
            "Dashboard request authenticated",
            extra={
                "user_id": str(user_context.get("user_id")),
                "path": path
            }
        )

        # Continue to next middleware/handler
        await self.app(scope, receive, send)

    async def _send_unauthorized_response(self, send: Send, message: str) -> None:
        """
        Send a 401 Unauthorized response using ASGI send.

        Args:
            send: ASGI send callable
            message: Error message to include in response
        """
        body = json.dumps({
            "error": True,
            "error_code": "unauthorized",
            "message": message,
            "details": None
        }).encode("utf-8")

        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                [b"content-type", b"application/json"],
                [b"www-authenticate", b'Bearer realm="dashboard"'],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })

    async def _validate_token(self, token: str) -> Optional[dict]:
        """
        Validate a bearer token.

        First checks Redis cache for recently validated tokens,
        then falls back to Public API validation.

        Args:
            token: Bearer token to validate

        Returns:
            User context dict if valid, None if invalid
        """
        # Check cache first
        cache_key = f"dashboard:token:{token[:16]}"  # Use prefix to avoid storing full token

        try:
            redis = await get_redis_client()
            cached = await redis.get(cache_key)

            if cached:
                logger.info(
                    "Dashboard auth: token validated from cache",
                    extra={
                        "cache_key": cache_key,
                        "user_id": str(cached.get("user_id")) if cached.get("user_id") else None
                    }
                )
                return cached

            logger.info(
                "Dashboard auth: cache miss, validating with Public API",
                extra={"cache_key": cache_key}
            )
        except Exception as e:
            logger.warning(
                "Dashboard auth: Redis cache check failed, falling back to API",
                extra={"error": str(e), "error_type": type(e).__name__}
            )

        # Validate against Public API
        user_context = await self._validate_with_public_api(token)

        if user_context:
            # Cache successful validation
            try:
                await redis.set(cache_key, user_context, ttl=self.TOKEN_CACHE_TTL)
                logger.info(
                    "Dashboard auth: cached successful validation",
                    extra={"cache_key": cache_key, "ttl": self.TOKEN_CACHE_TTL}
                )
            except Exception as e:
                logger.warning(
                    "Dashboard auth: failed to cache token validation",
                    extra={"error": str(e)}
                )

        return user_context

    async def _validate_with_public_api(self, token: str) -> Optional[dict]:
        """
        Validate token with Public API's /v1/auth/verify endpoint.

        The session token from OTP login is actually an API key, so we use
        the existing /verify endpoint which validates API keys.

        Args:
            token: Bearer token (API key) to validate

        Returns:
            User context dict if valid, None if invalid
        """
        config = get_config()
        # Use the existing /v1/auth/verify endpoint (GET with Authorization header)
        validate_url = f"{config.public_api_url}/v1/auth/verify"

        # Log the URL being called (critical for debugging Docker networking issues)
        logger.info(
            "Dashboard auth: calling Public API for token validation",
            extra={
                "validate_url": validate_url,
                "public_api_url_config": config.public_api_url,
                "token_preview": _safe_token_preview(token)
            }
        )

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    validate_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                    }
                )

                # Log response details at INFO level for production debugging
                logger.info(
                    "Dashboard auth: Public API response received",
                    extra={
                        "status_code": response.status_code,
                        "response_headers": dict(response.headers),
                        "validate_url": validate_url
                    }
                )

                if response.status_code == 200:
                    data = response.json()

                    # Log the response body structure (without sensitive data)
                    logger.info(
                        "Dashboard auth: Public API returned 200",
                        extra={
                            "valid": data.get("valid"),
                            "has_user_id": bool(data.get("user_id")),
                            "has_api_key_id": bool(data.get("api_key_id")),
                            "has_email": bool(data.get("email")),
                            "scopes": data.get("scopes", []),
                            "response_keys": list(data.keys())
                        }
                    )

                    # The /verify endpoint returns {valid, api_key_id, scopes, rate_limit}
                    # We need to extract user_id from the API key info
                    if data.get("valid"):
                        # For now, use api_key_id as a placeholder
                        # The actual user_id should come from the API key's user association
                        user_id = data.get("user_id")
                        if not user_id:
                            # Fallback: parse user_id from extended response if available
                            user_id = data.get("api_key_id")
                            logger.info(
                                "Dashboard auth: user_id not in response, falling back to api_key_id",
                                extra={"api_key_id": user_id}
                            )

                        user_context = {
                            "user_id": UUID(user_id) if isinstance(user_id, str) and self._is_valid_uuid(user_id) else None,
                            "api_key_id": data.get("api_key_id"),
                            "email": data.get("email"),
                            "permissions": data.get("scopes", []),
                            "validated_at": None
                        }

                        logger.info(
                            "Dashboard auth: token validated successfully",
                            extra={
                                "user_id": str(user_context["user_id"]) if user_context["user_id"] else None,
                                "has_email": bool(user_context["email"]),
                                "permissions": user_context["permissions"]
                            }
                        )
                        return user_context

                    logger.warning(
                        "Dashboard auth: Public API returned valid=false",
                        extra={"response_data": {k: v for k, v in data.items() if k not in ['api_key']}}
                    )
                    return None

                elif response.status_code == 401:
                    # Try to get error details from response
                    try:
                        error_data = response.json()
                        error_detail = error_data.get("detail", error_data)
                    except Exception:
                        error_detail = response.text[:200]

                    logger.warning(
                        "Dashboard auth: token rejected by Public API (401)",
                        extra={
                            "validate_url": validate_url,
                            "error_detail": error_detail,
                            "token_preview": _safe_token_preview(token)
                        }
                    )
                    return None

                else:
                    # Try to get error details from response
                    try:
                        error_data = response.json()
                    except Exception:
                        error_data = response.text[:500]

                    logger.warning(
                        "Dashboard auth: unexpected response from Public API",
                        extra={
                            "status_code": response.status_code,
                            "validate_url": validate_url,
                            "response_body": error_data
                        }
                    )
                    return None

        except httpx.TimeoutException as e:
            logger.error(
                "Dashboard auth: Public API validation timed out",
                extra={
                    "validate_url": validate_url,
                    "timeout": 5.0,
                    "error": str(e)
                }
            )
            return None

        except httpx.ConnectError as e:
            logger.error(
                "Dashboard auth: failed to connect to Public API",
                extra={
                    "validate_url": validate_url,
                    "error": str(e),
                    "error_type": type(e).__name__
                }
            )
            return None

        except Exception as e:
            logger.error(
                "Dashboard auth: unexpected error validating token",
                extra={
                    "validate_url": validate_url,
                    "error": str(e),
                    "error_type": type(e).__name__
                },
                exc_info=True
            )
            return None

    def _is_valid_uuid(self, value: str) -> bool:
        """Check if a string is a valid UUID."""
        try:
            UUID(value)
            return True
        except (ValueError, AttributeError):
            return False


def get_current_user(request: Request) -> Optional[UUID]:
    """
    FastAPI dependency to get the authenticated user ID.

    Usage:
        @router.get("/my-endpoint")
        async def my_endpoint(user_id: UUID = Depends(get_current_user)):
            ...

    Returns:
        User UUID if authenticated, None otherwise
    """
    return getattr(request.state, "user_id", None)


def require_auth(request: Request) -> UUID:
    """
    FastAPI dependency that requires authentication.

    Raises HTTPException if user is not authenticated.

    Usage:
        @router.get("/protected")
        async def protected_endpoint(user_id: UUID = Depends(require_auth)):
            ...

    Returns:
        User UUID

    Raises:
        HTTPException: 401 if not authenticated
    """
    from fastapi import HTTPException, status

    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Bearer realm="dashboard"'}
        )
    return user_id
