#!/usr/bin/env python3
"""
Redis Connection Management for GitHub App Service.

Provides async Redis client with connection pooling, JSON serialization,
and health checks. Shared infrastructure with Public API.

Aligned with Workstream 1 v3.0 spec.
"""

import json
import logging
from typing import Optional, Any
import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool

logger = logging.getLogger(__name__)


class RedisClient:
    """
    Async Redis client wrapper with JSON serialization.

    Provides high-level operations for caching with automatic
    JSON encoding/decoding of Python objects.
    """

    def __init__(self, redis_client: redis.Redis):
        """
        Initialize Redis client wrapper.

        Args:
            redis_client: Underlying redis.asyncio.Redis client
        """
        self.client = redis_client

    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache with automatic JSON deserialization.

        Args:
            key: Cache key

        Returns:
            Deserialized value or None if key doesn't exist or error occurs
        """
        try:
            value = await self.client.get(key)
            if value:
                return json.loads(value)
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"JSON decode error for key '{key}': {e}")
            return None
        except Exception as e:
            logger.warning(f"Redis GET error for key '{key}': {e}")
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Set value in cache with automatic JSON serialization.

        Args:
            key: Cache key
            value: Python object to serialize and store
            ttl: Optional time-to-live in seconds

        Returns:
            True if successful, False otherwise
        """
        try:
            serialized = json.dumps(value)
            if ttl:
                await self.client.setex(key, ttl, serialized)
            else:
                await self.client.set(key, serialized)
            return True
        except (TypeError, ValueError) as e:
            logger.warning(f"JSON encode error for key '{key}': {e}")
            return False
        except Exception as e:
            logger.warning(f"Redis SET error for key '{key}': {e}")
            return False

    async def delete(self, *keys: str) -> int:
        """
        Delete one or more keys from cache.

        Args:
            *keys: Variable number of cache keys to delete

        Returns:
            Number of keys that were deleted
        """
        try:
            if not keys:
                return 0
            return await self.client.delete(*keys)
        except Exception as e:
            logger.warning(f"Redis DELETE error: {e}")
            return 0

    async def exists(self, key: str) -> bool:
        """
        Check if key exists in cache.

        Args:
            key: Cache key

        Returns:
            True if key exists, False otherwise
        """
        try:
            return await self.client.exists(key) > 0
        except Exception as e:
            logger.warning(f"Redis EXISTS error for key '{key}': {e}")
            return False

    async def ping(self) -> bool:
        """
        Check Redis connection health.

        Returns:
            True if Redis is responding, False otherwise
        """
        try:
            await self.client.ping()
            return True
        except Exception as e:
            logger.warning(f"Redis PING error: {e}")
            return False

    async def ttl(self, key: str) -> int:
        """
        Get time-to-live for a key.

        Args:
            key: Cache key

        Returns:
            TTL in seconds, -1 if key exists but has no expiry, -2 if key doesn't exist
        """
        try:
            return await self.client.ttl(key)
        except Exception as e:
            logger.warning(f"Redis TTL error for key '{key}': {e}")
            return -2

    async def close(self):
        """Close the underlying Redis connection."""
        try:
            await self.client.close()
            logger.info("Redis client connection closed")
        except Exception as e:
            logger.warning(f"Error closing Redis client: {e}")


# Global connection pool and client (reused across requests)
_redis_pool: Optional[ConnectionPool] = None
_redis_wrapper: Optional[RedisClient] = None


async def get_redis_pool(redis_url: str, max_connections: int = 50) -> ConnectionPool:
    """
    Get or create Redis connection pool.

    Connection pool is shared across all requests for efficiency.

    Args:
        redis_url: Redis connection URL
        max_connections: Maximum number of connections in pool (default: 50 per spec)

    Returns:
        ConnectionPool: Redis connection pool
    """
    global _redis_pool

    if _redis_pool is None:
        logger.info(f"Creating Redis connection pool: {redis_url}")
        _redis_pool = ConnectionPool.from_url(
            redis_url,
            max_connections=max_connections,
            decode_responses=True,  # Auto-decode bytes to strings for JSON
            socket_keepalive=True,
            socket_connect_timeout=5,
            retry_on_timeout=True
        )
        logger.info("✓ Redis connection pool created")

    return _redis_pool


async def get_redis_client() -> RedisClient:
    """
    Get Redis client instance (FastAPI dependency injection compatible).

    Client uses connection pool for efficiency and provides JSON serialization.
    Safe to call multiple times (singleton pattern).

    Usage:
        @app.get("/data")
        async def get_data(redis: RedisClient = Depends(get_redis_client)):
            value = await redis.get("key")
            await redis.set("key", {"data": "value"}, ttl=300)

    Returns:
        RedisClient: High-level Redis client wrapper
    """
    global _redis_wrapper

    if _redis_wrapper is None:
        from app.config import get_config
        config = get_config()
        pool = await get_redis_pool(config.redis_url)
        raw_client = redis.Redis(connection_pool=pool)
        _redis_wrapper = RedisClient(raw_client)
        logger.info("✓ Redis client initialized")

    return _redis_wrapper


async def init_redis():
    """
    Initialize Redis connection on application startup.

    Called by FastAPI lifespan manager.
    """
    global _redis_wrapper

    if _redis_wrapper is None:
        _redis_wrapper = await get_redis_client()
        # Verify connection
        if not await _redis_wrapper.ping():
            raise ConnectionError("Failed to connect to Redis")
        logger.info("✓ Redis initialized and verified")


async def close_redis():
    """
    Close Redis connections on application shutdown.

    Called by FastAPI lifespan manager.
    """
    global _redis_pool, _redis_wrapper

    if _redis_wrapper:
        await _redis_wrapper.close()
        _redis_wrapper = None
        logger.info("✓ Redis client closed")

    if _redis_pool:
        await _redis_pool.disconnect()
        _redis_pool = None
        logger.info("✓ Redis connection pool closed")


async def health_check() -> bool:
    """
    Check Redis health for monitoring endpoints.

    Returns:
        bool: True if Redis is healthy, False otherwise
    """
    try:
        client = await get_redis_client()
        return await client.ping()
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        return False
