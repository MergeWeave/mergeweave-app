"""
Health Check Endpoints for MergeWeave GitHub App

Provides health check endpoints for monitoring, load balancing, and orchestration:

- /health - Fast health check for load balancers (< 10ms)
- /health/detailed - Comprehensive dependency checks (database, Redis, GitHub API)
- /health/readiness - Kubernetes readiness probe
- /health/liveness - Kubernetes liveness probe
"""

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.database.session import get_db
from app.cache.redis_client import get_redis_client, RedisClient
from datetime import datetime, timezone
import logging
import httpx

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health_check():
    """
    Basic health check (fast, for load balancers).

    Returns 200 OK if service is running. Does not check dependencies.
    Includes version information for debugging.

    Response time: < 10ms
    Use case: Load balancer health checks, high-frequency polling

    Returns:
        {
            "status": "ok",
            "service": "github-app",
            "version": "1.0.0",
            "versions": {...},
            "timestamp": "2025-11-17T10:30:00Z"
        }
    """
    try:
        from app.services.version_manifest import GITHUB_APP_VERSION
    except Exception:
        GITHUB_APP_VERSION = "unknown"

    # Get CRE version info (non-blocking - don't fail if unavailable)
    cre_version = "unknown"
    __io_contract_version__ = "1.0.0"
    try:
        from mergeweave_cre import __version__ as cre_version, __io_contract_version__
    except ImportError:
        pass  # CRE module not available, use defaults

    # Get schema registry version (non-blocking)
    SCHEMA_REGISTRY_VERSION = "1.0.0"
    try:
        from config.schema_versions import SCHEMA_REGISTRY_VERSION
    except ImportError:
        pass  # Schema registry not available, use default

    return {
        "status": "ok",
        "service": "github-app",
        "version": GITHUB_APP_VERSION,
        "versions": {
            "github_app": GITHUB_APP_VERSION,
            "cre_engine": cre_version,
            "io_contract": __io_contract_version__,
            "schema_registry": SCHEMA_REGISTRY_VERSION
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/health/detailed")
async def detailed_health_check(
    db: AsyncSession = Depends(get_db),
    redis: RedisClient = Depends(get_redis_client)
):
    """
    Detailed health check (checks all dependencies).

    Checks:
    1. Database connectivity (PostgreSQL)
    2. Cache connectivity (Redis)
    3. GitHub API reachability

    Response time: < 5s (includes network timeouts)
    Use case: Monitoring dashboards, operational health verification

    Returns:
        {
            "status": "healthy" | "degraded" | "unhealthy",
            "checks": {
                "database": {...},
                "redis": {...},
                "github_api": {...}
            },
            "timestamp": "2025-11-17T10:30:00Z"
        }

    Status Codes:
    - 200: healthy or degraded
    - 503: unhealthy (database down)

    Status Levels:
    - healthy: All dependencies operational
    - degraded: Non-critical dependency issue (Redis or GitHub API down)
    - unhealthy: Critical dependency issue (database down)
    """
    checks = {}
    overall_status = "healthy"

    # ========================================================================
    # Check Database (CRITICAL)
    # ========================================================================
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar()
        checks["database"] = {
            "status": "healthy",
            "message": "Database connection OK",
            "critical": True
        }
    except Exception as e:
        logger.error(f"Database health check failed: {str(e)}", exc_info=True)
        checks["database"] = {
            "status": "unhealthy",
            "message": f"Database error: {str(e)}",
            "critical": True
        }
        overall_status = "unhealthy"  # Database failure = unhealthy

    # ========================================================================
    # Check Redis (NON-CRITICAL - degrades gracefully)
    # ========================================================================
    try:
        if await redis.ping():
            checks["redis"] = {
                "status": "healthy",
                "message": "Redis connection OK",
                "critical": False
            }
        else:
            checks["redis"] = {
                "status": "unhealthy",
                "message": "Redis ping failed",
                "critical": False
            }
            if overall_status == "healthy":
                overall_status = "degraded"  # Redis failure = degraded (not critical)
    except Exception as e:
        logger.error(f"Redis health check failed: {str(e)}", exc_info=True)
        checks["redis"] = {
            "status": "unhealthy",
            "message": f"Redis error: {str(e)}",
            "critical": False
        }
        if overall_status == "healthy":
            overall_status = "degraded"  # Redis failure = degraded

    # ========================================================================
    # Check GitHub API (NON-CRITICAL - degrades gracefully)
    # ========================================================================
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                "https://api.github.com/",
                headers={"Accept": "application/vnd.github.v3+json"}
            )

            if response.status_code == 200:
                checks["github_api"] = {
                    "status": "healthy",
                    "message": "GitHub API reachable",
                    "critical": False
                }
            else:
                checks["github_api"] = {
                    "status": "degraded",
                    "message": f"GitHub API returned {response.status_code}",
                    "critical": False
                }
                if overall_status == "healthy":
                    overall_status = "degraded"

    except httpx.TimeoutException:
        logger.warning("GitHub API health check timed out")
        checks["github_api"] = {
            "status": "degraded",
            "message": "GitHub API timeout (network issue or high latency)",
            "critical": False
        }
        if overall_status == "healthy":
            overall_status = "degraded"

    except Exception as e:
        logger.error(f"GitHub API health check failed: {str(e)}", exc_info=True)
        checks["github_api"] = {
            "status": "unhealthy",
            "message": f"GitHub API unreachable: {str(e)}",
            "critical": False
        }
        if overall_status == "healthy":
            overall_status = "degraded"  # GitHub API failure = degraded (can use cached data)

    # ========================================================================
    # Compile Response
    # ========================================================================
    from app.services.version_manifest import GITHUB_APP_VERSION

    # Get CRE version info
    try:
        from mergeweave_cre import __version__ as cre_version, __io_contract_version__
    except ImportError:
        cre_version = "unknown"
        __io_contract_version__ = "1.0.0"

    response_data = {
        "status": overall_status,
        "service": "github-app",
        "version": GITHUB_APP_VERSION,
        "versions": {
            "github_app": GITHUB_APP_VERSION,
            "cre_engine": cre_version,
            "io_contract": __io_contract_version__
        },
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    # Return appropriate status code
    if overall_status == "unhealthy":
        # 503 Service Unavailable - critical dependency (database) is down
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response_data
        )

    # 200 OK - healthy or degraded (can still operate)
    return response_data


@router.get("/health/readiness")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """
    Readiness check (for Kubernetes readiness probe).

    Checks if the service is ready to accept traffic.
    Only checks critical dependencies (database).

    Response time: < 1s
    Use case: Kubernetes readiness probe, traffic routing decisions

    Returns 200 if ready to serve traffic, 503 if not.

    Kubernetes Configuration:
        readinessProbe:
          httpGet:
            path: /github-app/health/readiness
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 10
          failureThreshold: 3

    Returns:
        200: Service ready to accept traffic
        503: Service not ready (don't route traffic)
    """
    try:
        # Check database (critical for serving traffic)
        await db.execute(text("SELECT 1"))

        return {
            "status": "ready",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Readiness check failed: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "not_ready",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )


@router.get("/health/liveness")
async def liveness_check():
    """
    Liveness check (for Kubernetes liveness probe).

    Checks if the service process is alive and responsive.
    Does not check dependencies - only verifies the process can respond.

    Response time: < 10ms
    Use case: Kubernetes liveness probe, deadlock detection

    If this endpoint fails, Kubernetes will restart the pod.

    Kubernetes Configuration:
        livenessProbe:
          httpGet:
            path: /github-app/health/liveness
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 30
          failureThreshold: 3

    Returns:
        {
            "status": "alive",
            "timestamp": "2025-11-17T10:30:00Z"
        }
    """
    return {
        "status": "alive",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
