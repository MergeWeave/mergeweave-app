"""
Database Session Management

Provides async database session factory and dependency injection for FastAPI.
Uses SQLAlchemy 2.0 async patterns with asyncpg driver.
"""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.config import get_config

# Get configuration
config = get_config()

# Create async engine
# Note: DATABASE_URL should be in format: postgresql+asyncpg://user:password@host:port/database
engine = create_async_engine(
    config.database_url,
    echo=config.environment == "development",  # Log SQL queries in development
    pool_pre_ping=True,  # Verify connections before using
    pool_size=10,  # Maximum number of connections in the pool (per spec)
    max_overflow=20,  # Maximum number of connections that can be created beyond pool_size (per spec)
    pool_recycle=3600,  # Recycle connections after 1 hour
    poolclass=NullPool if config.environment == "test" else None,  # Disable pooling in tests
)

# Create session factory
# Using AsyncSessionLocal name per Workstream 1 spec for consistency
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Don't expire objects after commit (allows access after commit)
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides database sessions.

    Yields an async database session and ensures proper cleanup.
    Use this as a dependency in FastAPI route handlers.

    Usage:
        @router.get("/users")
        async def get_users(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(User))
            return result.scalars().all()

    Yields:
        AsyncSession: SQLAlchemy async session
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """
    Initialize database (create all tables).

    This should only be used in development/testing.
    In production, use Alembic migrations instead.
    
    Note: This function may fail if tables already exist or if there are
    permission issues. The caller should handle exceptions appropriately.
    """
    from app.models.base import Base

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        # Log but don't raise - allows graceful handling by caller
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Database table creation failed (may already exist): {e}")
        raise


async def close_db():
    """
    Close database connections.

    Call this on application shutdown to properly close all connections.
    """
    await engine.dispose()
