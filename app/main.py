"""
MergeWeave GitHub App - Main Application

FastAPI application entry point with middleware, routing, and lifecycle management.
Implements Workstream 1 infrastructure foundation.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from contextlib import asynccontextmanager
import logging

from app.config import config
from app.monitoring.logging_config import setup_logging
from app.database.session import init_db, close_db
from app.cache.redis_client import init_redis, close_redis
from app.middleware.correlation_id import CorrelationIDMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from app.middleware.version_headers import VersionHeadersMiddleware
from app.middleware.dashboard_auth import DashboardAuthMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.error_handler import (
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler
)

# Import routers
from app.api.health import router as health_router
from app.webhooks.router import router as webhook_router
from app.monitoring.metrics import router as metrics_router
from app.api.oauth import router as oauth_router
from app.api.dashboard import router as dashboard_router
from app.api.websocket import router as websocket_router
from app.api.config import router as config_router

# Import webhook handlers to register them
from app.webhooks import handlers  # noqa: F401

# Setup logging
setup_logging(config.log_level, structured=(config.environment == "production"))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan events.

    Handles startup and shutdown operations for database and Redis connections.
    """
    # Startup
    logger.info("=" * 80)
    logger.info("Starting GitHub App service...")
    logger.info(f"Environment: {config.environment}")
    logger.info(f"Debug mode: {config.debug}")
    logger.info(f"Port: {config.port}")

    # Log version information
    from app.services.version_manifest import GITHUB_APP_VERSION
    logger.info(f"GitHub App Version: {GITHUB_APP_VERSION}")
    try:
        from mergeweave_cre import __version__ as cre_version, __io_contract_version__
        logger.info(f"CRE Version: {cre_version}")
        logger.info(f"IO Contract Version: {__io_contract_version__}")
    except ImportError as e:
        logger.warning(f"CRE module not available for version logging: {e}")
        # Don't fail startup if CRE module not available

    logger.info("=" * 80)

    # Test database connection (don't create tables in production)
    try:
        from app.database.session import engine
        from sqlalchemy import text
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            result.scalar()  # Verify we got a result
        logger.info("✓ Database connection verified")
    except Exception as e:
        logger.error(f"✗ Database connection failed: {e}")
        raise
    
    # Initialize database tables (skip in production - use Alembic migrations instead)
    if config.environment != "production":
        try:
            await init_db()
            logger.info("✓ Database tables initialized")
        except Exception as e:
            logger.warning(f"Database table creation skipped (may already exist): {e}")
            # Don't raise - allow app to start even if init_db fails
    else:
        logger.info("✓ Database table creation skipped (production - using Alembic migrations)")

    # Initialize Redis
    try:
        await init_redis()
        logger.info("✓ Redis initialized")
    except Exception as e:
        logger.error(f"✗ Redis initialization failed: {e}")
        raise

    logger.info("=" * 80)
    logger.info("🚀 GitHub App service started successfully")
    logger.info("=" * 80)

    yield

    # Shutdown
    logger.info("=" * 80)
    logger.info("Shutting down GitHub App service...")
    logger.info("=" * 80)

    await close_redis()
    logger.info("✓ Redis connections closed")

    await close_db()
    logger.info("✓ Database connections closed")

    logger.info("✓ GitHub App service stopped")


# Create FastAPI app
app = FastAPI(
    title="MergeWeave GitHub App",
    description="GitHub App for proactive conflict detection and resolution",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Add middleware (order matters: first added = outermost wrapper)
# SecurityHeaders → RateLimit → VersionHeaders → CorrelationID → DashboardAuth → CORS → Application
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, enabled=(config.environment == "production"))
app.add_middleware(VersionHeadersMiddleware)
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(DashboardAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if config.debug else [
        "https://mergeweave.cloud",
        "https://api.mergeweave.cloud",
        "https://webhook.mergeweave.cloud",
        "https://preview.mergeweave.dev",  # Web dashboard preview
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add exception handlers
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# Include routers (no prefix per full spec)
app.include_router(health_router, tags=["health"])
app.include_router(webhook_router, tags=["webhooks"])
app.include_router(metrics_router, tags=["monitoring"])
app.include_router(oauth_router, tags=["oauth"])
app.include_router(dashboard_router)  # Already has /api/dashboard prefix
app.include_router(websocket_router)  # WebSocket for real-time updates
app.include_router(config_router)  # Already has /api/dashboard/config prefix

logger.info("✓ Routers registered: health, webhooks, metrics, oauth, dashboard, websocket, config")


@app.get("/")
async def root():
    """
    Root endpoint.

    Returns basic service information with version details.
    """
    from app.services.version_manifest import GITHUB_APP_VERSION

    # Get CRE version info
    try:
        from mergeweave_cre import __version__ as cre_version, __io_contract_version__
    except ImportError:
        cre_version = "unknown"
        __io_contract_version__ = "1.0.0"

    return {
        "service": "MergeWeave GitHub App",
        "version": GITHUB_APP_VERSION,
        "status": "running",
        "environment": config.environment,
        "versions": {
            "github_app": GITHUB_APP_VERSION,
            "cre_engine": cre_version,
            "io_contract": __io_contract_version__
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=config.host,
        port=config.port,
        reload=config.debug,
        log_level=config.log_level.lower()
    )
