"""
Webhook deduplication using Redis.

Prevents duplicate webhook processing by tracking delivery IDs in Redis cache.
"""

from app.cache.redis_client import RedisClient
from app.cache.keys import CacheKeys, CacheTTL
import logging

logger = logging.getLogger(__name__)


class WebhookDeduplicator:
    """Handle webhook deduplication using Redis."""

    def __init__(self, redis: RedisClient):
        """
        Initialize webhook deduplicator.

        Args:
            redis: Redis client instance
        """
        self.redis = redis

    async def is_duplicate(self, delivery_id: str) -> bool:
        """
        Check if webhook has already been processed.

        Args:
            delivery_id: X-GitHub-Delivery header value

        Returns:
            True if webhook is duplicate, False otherwise
        """
        cache_key = CacheKeys.webhook_processed(delivery_id)
        exists = await self.redis.exists(cache_key)

        if exists:
            logger.warning(
                f"Duplicate webhook detected",
                extra={"delivery_id": delivery_id}
            )
            return True

        return False

    async def mark_processed(self, delivery_id: str) -> bool:
        """
        Mark webhook as processed.

        Args:
            delivery_id: X-GitHub-Delivery header value

        Returns:
            True if marked successfully, False otherwise
        """
        cache_key = CacheKeys.webhook_processed(delivery_id)
        success = await self.redis.set(
            cache_key,
            {"processed_at": "now"},  # Simple marker
            ttl=CacheTTL.WEBHOOK_DEDUP
        )

        if success:
            logger.debug(
                f"Marked webhook as processed",
                extra={"delivery_id": delivery_id}
            )

        return success
