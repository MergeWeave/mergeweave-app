"""
Cache Invalidation Utilities.

Provides structured cache invalidation strategies for lifecycle events.
Ensures cache consistency when installations are deleted, repositories
are removed, or other state changes occur.
"""

import logging
from typing import List, Optional, Union
from uuid import UUID
from redis.asyncio import Redis

from app.cache.keys import CacheKeys

logger = logging.getLogger(__name__)


class CacheInvalidator:
    """
    Manages cache invalidation for lifecycle events.

    Provides methods to invalidate specific cache entries or groups of
    related entries when state changes occur.
    """

    def __init__(self, redis_client: Redis):
        """
        Initialize cache invalidator.

        Args:
            redis_client: Async Redis client
        """
        self.redis = redis_client

    async def on_installation_deleted(
        self,
        installation_id: int,
        user_id: Optional[Union[UUID, str]] = None
    ) -> int:
        """
        Invalidate all cache entries for a deleted installation.

        Removes:
        - Installation token
        - Rate limit data
        - Installation repositories list
        - User-installation mapping (if user_id provided)

        Args:
            installation_id: GitHub installation ID
            user_id: Optional user ID (UUID) for user-installation mapping

        Returns:
            Number of cache keys deleted
        """
        keys_to_delete = [
            CacheKeys.installation_token(installation_id),
            CacheKeys.rate_limit("core", installation_id),
            CacheKeys.rate_limit("search", installation_id),
            CacheKeys.rate_limit("graphql", installation_id),
            CacheKeys.installation_repositories(installation_id),
        ]

        if user_id is not None:
            keys_to_delete.append(CacheKeys.user_installation(user_id))

        deleted_count = await self.redis.delete(*keys_to_delete)

        logger.info(
            f"Invalidated cache for deleted installation",
            extra={
                "installation_id": installation_id,
                "user_id": user_id,
                "deleted_count": deleted_count
            }
        )

        return deleted_count

    async def on_installation_suspended(self, installation_id: int) -> int:
        """
        Invalidate cache entries for a suspended installation.

        Removes installation token to force re-authentication on resume.

        Args:
            installation_id: GitHub installation ID

        Returns:
            Number of cache keys deleted
        """
        keys_to_delete = [
            CacheKeys.installation_token(installation_id),
        ]

        deleted_count = await self.redis.delete(*keys_to_delete)

        logger.info(
            f"Invalidated cache for suspended installation",
            extra={
                "installation_id": installation_id,
                "deleted_count": deleted_count
            }
        )

        return deleted_count

    async def on_repositories_changed(
        self,
        installation_id: int,
        owner: Optional[str] = None,
        repo: Optional[str] = None
    ) -> int:
        """
        Invalidate cache entries when repositories are added/removed.

        Removes installation repositories list and optionally specific
        repository metadata.

        Args:
            installation_id: GitHub installation ID
            owner: Optional repository owner
            repo: Optional repository name

        Returns:
            Number of cache keys deleted
        """
        keys_to_delete = [
            CacheKeys.installation_repositories(installation_id),
        ]

        if owner and repo:
            keys_to_delete.extend([
                CacheKeys.repo_default_branch(owner, repo),
                CacheKeys.repo_metadata(owner, repo),
            ])

        deleted_count = await self.redis.delete(*keys_to_delete)

        logger.info(
            f"Invalidated cache for repository changes",
            extra={
                "installation_id": installation_id,
                "owner": owner,
                "repo": repo,
                "deleted_count": deleted_count
            }
        )

        return deleted_count

    async def invalidate_pattern(self, pattern: str) -> int:
        """
        Invalidate all cache keys matching a pattern.

        WARNING: Use with caution. Pattern matching with SCAN can be slow
        on large datasets.

        Args:
            pattern: Redis key pattern (e.g., "github:user:*")

        Returns:
            Number of cache keys deleted
        """
        deleted_count = 0
        cursor = 0

        while True:
            cursor, keys = await self.redis.scan(
                cursor=cursor,
                match=pattern,
                count=100
            )

            if keys:
                deleted_count += await self.redis.delete(*keys)

            if cursor == 0:
                break

        logger.warning(
            f"Invalidated cache keys by pattern",
            extra={
                "pattern": pattern,
                "deleted_count": deleted_count
            }
        )

        return deleted_count

    async def clear_rate_limits(self, installation_id: int) -> int:
        """
        Clear all rate limit cache entries for an installation.

        Useful for testing or when rate limit data becomes stale.

        Args:
            installation_id: GitHub installation ID

        Returns:
            Number of cache keys deleted
        """
        keys_to_delete = [
            CacheKeys.rate_limit("core", installation_id),
            CacheKeys.rate_limit("search", installation_id),
            CacheKeys.rate_limit("graphql", installation_id),
        ]

        deleted_count = await self.redis.delete(*keys_to_delete)

        logger.debug(
            f"Cleared rate limit cache",
            extra={
                "installation_id": installation_id,
                "deleted_count": deleted_count
            }
        )

        return deleted_count

    async def get_cache_stats(self, installation_id: int) -> dict:
        """
        Get cache statistics for an installation.

        Returns information about which cache entries exist.

        Args:
            installation_id: GitHub installation ID

        Returns:
            Dictionary with cache existence flags
        """
        keys_to_check = {
            "installation_token": CacheKeys.installation_token(installation_id),
            "rate_limit_core": CacheKeys.rate_limit("core", installation_id),
            "rate_limit_search": CacheKeys.rate_limit("search", installation_id),
            "installation_repos": CacheKeys.installation_repositories(installation_id),
        }

        stats = {}
        for name, key in keys_to_check.items():
            exists = await self.redis.exists(key)
            ttl = await self.redis.ttl(key) if exists else None
            stats[name] = {
                "exists": bool(exists),
                "ttl_seconds": ttl
            }

        return stats


# FastAPI dependency
async def get_cache_invalidator() -> CacheInvalidator:
    """
    FastAPI dependency to get cache invalidator instance.

    Returns:
        CacheInvalidator instance
    """
    from app.cache.redis_client import get_redis_client

    redis_client = await get_redis_client()
    return CacheInvalidator(redis_client)
