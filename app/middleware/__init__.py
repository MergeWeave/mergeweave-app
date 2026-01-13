"""
Middleware package for FastAPI application.

Exports all middleware classes for easy import.
"""

from app.middleware.correlation_id import CorrelationIDMiddleware, get_correlation_id
from app.middleware.security import SecurityHeadersMiddleware
from app.middleware.version_headers import VersionHeadersMiddleware
from app.middleware.dashboard_auth import (
    DashboardAuthMiddleware,
    get_current_user,
    require_auth,
)
from app.middleware.error_handler import (
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler,
)
from app.middleware.rate_limit import (
    RateLimitMiddleware,
    RateLimitByInstallation,
    RateLimitConfig,
    get_rate_limit_status,
)

__all__ = [
    "CorrelationIDMiddleware",
    "SecurityHeadersMiddleware",
    "VersionHeadersMiddleware",
    "DashboardAuthMiddleware",
    "RateLimitMiddleware",
    "RateLimitByInstallation",
    "RateLimitConfig",
    "get_correlation_id",
    "get_current_user",
    "require_auth",
    "get_rate_limit_status",
    "http_exception_handler",
    "validation_exception_handler",
    "general_exception_handler",
]
