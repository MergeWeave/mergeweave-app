"""
GitHub API Rate Limit Tracker.

Tracks GitHub API rate limits using response headers and Redis caching.
Provides proactive throttling when limits approach exhaustion.

Per Workstream 2 specification.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import logging

from app.cache.redis_client import RedisClient
from app.cache.keys import CacheKeys, CacheTTL

logger = logging.getLogger(__name__)


@dataclass
class RateLimitStatus:
    """Rate limit status for a resource."""

    limit: int
    remaining: int
    reset_at: datetime
    used: int

    @property
    def percentage_used(self) -> float:
        """Calculate percentage of quota used."""
        return (self.used / self.limit) * 100 if self.limit > 0 else 0

    @property
    def is_exhausted(self) -> bool:
        """Check if rate limit is exhausted."""
        return self.remaining == 0

    def is_low(self, threshold: float = 20.0) -> bool:
        """
        Check if remaining quota is below threshold percentage.

        Args:
            threshold: Percentage threshold (default 20%)

        Returns:
            bool: True if remaining percentage is below threshold
        """
        percentage_remaining = (self.remaining / self.limit) * 100 if self.limit > 0 else 0
        return percentage_remaining < threshold


class RateLimitTracker:
    """Track GitHub API rate limits."""

    def __init__(self, redis_client: RedisClient):
        """
        Initialize rate limit tracker.

        Args:
            redis_client: Redis client for caching rate limit data
        """
        self.redis = redis_client

    async def update_from_response(
        self,
        installation_id: int,
        response_headers: dict,
        resource: str = "core"
    ) -> None:
        """
        Update rate limit status from API response headers.

        GitHub includes these headers in every API response:
        - X-RateLimit-Limit: Maximum requests
        - X-RateLimit-Remaining: Remaining requests
        - X-RateLimit-Reset: Unix timestamp when quota resets
        - X-RateLimit-Used: Requests used

        Args:
            installation_id: GitHub installation ID
            response_headers: HTTP response headers from GitHub API
            resource: Resource type (core, search, graphql, etc.)
        """
        limit = int(response_headers.get("X-RateLimit-Limit", 5000))
        remaining = int(response_headers.get("X-RateLimit-Remaining", limit))
        reset_timestamp = int(response_headers.get("X-RateLimit-Reset", 0))
        used = int(response_headers.get("X-RateLimit-Used", 0))

        reset_at = datetime.fromtimestamp(reset_timestamp, tz=timezone.utc)

        status = RateLimitStatus(
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
            used=used
        )

        # Store in Redis
        cache_key = CacheKeys.rate_limit(resource, installation_id)
        await self.redis.set(
            cache_key,
            {
                "limit": limit,
                "remaining": remaining,
                "reset_at": reset_at.isoformat(),
                "used": used
            },
            ttl=CacheTTL.RATE_LIMIT
        )

        # Log warning if quota is low
        if status.is_low():
            logger.warning(
                f"Rate limit low for installation {installation_id}: "
                f"{status.remaining}/{status.limit} remaining "
                f"(resets at {status.reset_at})",
                extra={
                    "installation_id": installation_id,
                    "resource": resource,
                    "remaining": status.remaining,
                    "limit": status.limit,
                    "percentage_remaining": (status.remaining / status.limit) * 100,
                    "reset_at": status.reset_at.isoformat()
                }
            )

        # Log critical if exhausted
        if status.is_exhausted:
            logger.critical(
                f"Rate limit EXHAUSTED for installation {installation_id}: "
                f"resets at {status.reset_at}",
                extra={
                    "installation_id": installation_id,
                    "resource": resource,
                    "reset_at": status.reset_at.isoformat()
                }
            )

    async def get_status(
        self,
        installation_id: int,
        resource: str = "core"
    ) -> Optional[RateLimitStatus]:
        """
        Get current rate limit status from cache.

        Args:
            installation_id: GitHub installation ID
            resource: Resource type (core, search, graphql, etc.)

        Returns:
            RateLimitStatus if cached, None otherwise
        """
        cache_key = CacheKeys.rate_limit(resource, installation_id)
        data = await self.redis.get(cache_key)

        if not data:
            return None

        return RateLimitStatus(
            limit=data["limit"],
            remaining=data["remaining"],
            reset_at=datetime.fromisoformat(data["reset_at"]),
            used=data["used"]
        )

    async def should_throttle(
        self,
        installation_id: int,
        resource: str = "core",
        threshold: int = 100
    ) -> bool:
        """
        Check if we should throttle requests.

        Args:
            installation_id: GitHub installation ID
            resource: Resource type (core, search, graphql, etc.)
            threshold: Minimum remaining requests before throttling

        Returns:
            bool: True if should throttle, False otherwise
        """
        status = await self.get_status(installation_id, resource)

        if not status:
            return False  # No cached status, allow request

        return status.remaining < threshold

    async def get_wait_time_seconds(
        self,
        installation_id: int,
        resource: str = "core"
    ) -> Optional[float]:
        """
        Get seconds until rate limit resets.

        Args:
            installation_id: GitHub installation ID
            resource: Resource type

        Returns:
            float: Seconds until reset, or None if not exhausted
        """
        status = await self.get_status(installation_id, resource)

        if not status or not status.is_exhausted:
            return None

        now = datetime.now(timezone.utc)
        wait_time = (status.reset_at - now).total_seconds()

        return max(0, wait_time)  # Never return negative
