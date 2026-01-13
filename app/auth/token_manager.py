"""
GitHub App Installation Token Manager.

Manages the lifecycle of installation access tokens:
- Generate JWT
- Exchange JWT for installation token
- Cache tokens in Redis (50-minute TTL)
- Handle race conditions with distributed locking
"""

import json
import asyncio
import httpx
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from app.auth.github_jwt import GitHubJWTGenerator

logger = logging.getLogger(__name__)


class InstallationTokenManager:
    """Manages GitHub App installation access tokens."""

    def __init__(
        self,
        app_id: int,
        private_key: str,
        redis_client: Any,
        base_url: str = "https://api.github.com"
    ):
        """
        Initialize token manager.

        Args:
            app_id: GitHub App ID
            private_key: Private key for signing JWTs
            redis_client: Redis client for caching
            base_url: GitHub API base URL
        """
        self.jwt_generator = GitHubJWTGenerator(app_id, private_key)
        self.redis = redis_client
        self.base_url = base_url
        self.http_client = httpx.AsyncClient(timeout=30.0)

    async def get_installation_token(
        self,
        installation_id: int,
        force_refresh: bool = False
    ) -> str:
        """
        Get installation access token (cached or generate new).

        Uses distributed locking to prevent race conditions where
        multiple concurrent requests try to generate tokens simultaneously.

        Args:
            installation_id: GitHub App installation ID
            force_refresh: Force regeneration even if cached

        Returns:
            Installation access token (ghs_*)

        Raises:
            RuntimeError: If token generation fails
        """
        cache_key = f"github_app:installation:{installation_id}:token"
        lock_key = f"github_app:installation:{installation_id}:lock"

        # Try cache first (unless forcing refresh)
        if not force_refresh:
            cached = await self._get_cached_token(cache_key)
            if cached:
                logger.debug(
                    f"Using cached installation token",
                    extra={"installation_id": installation_id}
                )
                return cached

        # Acquire distributed lock
        # Use raw Redis client for lock with nx (only if not exists) parameter
        lock_acquired = await self.redis.client.set(
            lock_key,
            "1",
            ex=10,  # 10-second lock timeout
            nx=True  # Only set if doesn't exist
        )

        if not lock_acquired:
            logger.debug(
                f"Lock held by another request, waiting...",
                extra={"installation_id": installation_id}
            )

            # Wait for other request to finish
            await asyncio.sleep(0.5)

            # Check cache again
            cached = await self._get_cached_token(cache_key)
            if cached:
                return cached

            # If still not available, proceed to generate
            # (original request may have failed)

        try:
            logger.info(
                f"Generating new installation token",
                extra={
                    "installation_id": installation_id,
                    "force_refresh": force_refresh
                }
            )

            # Generate new token
            token_data = await self._generate_installation_token(installation_id)

            # Cache with 50-minute TTL (tokens valid for 1 hour, 10min safety margin)
            await self.redis.set(
                cache_key,
                token_data,  # RedisClient handles JSON serialization
                ttl=3000  # 50 minutes in seconds
            )

            logger.info(
                f"Installation token cached",
                extra={
                    "installation_id": installation_id,
                    "expires_at": token_data["expires_at"]
                }
            )

            return token_data["token"]

        finally:
            # Always release lock
            await self.redis.delete(lock_key)

    async def _get_cached_token(self, cache_key: str) -> Optional[str]:
        """Get token from cache if valid."""
        try:
            cached_data = await self.redis.get(cache_key)
            if not cached_data:
                return None

            token_data = json.loads(cached_data)

            # Verify token hasn't expired
            expires_at = datetime.fromisoformat(token_data["expires_at"])
            now = datetime.now(timezone.utc)

            if expires_at <= now:
                logger.warning(f"Cached token expired, regenerating")
                return None

            return token_data["token"]

        except Exception as e:
            logger.warning(f"Error reading cached token: {e}")
            return None

    async def _generate_installation_token(
        self,
        installation_id: int
    ) -> Dict[str, Any]:
        """
        Generate new installation token from GitHub API.

        Process:
        1. Generate JWT with app private key
        2. POST to /app/installations/{id}/access_tokens with JWT
        3. Receive installation token (ghs_*)

        Returns:
            Dict with keys: token, expires_at
        """
        # Step 1: Generate JWT
        jwt_token = self.jwt_generator.generate_jwt(expiration_seconds=600)

        # Step 2: Exchange JWT for installation token
        url = f"{self.base_url}/app/installations/{installation_id}/access_tokens"

        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

        logger.debug(
            f"Requesting installation token from GitHub",
            extra={
                "installation_id": installation_id,
                "url": url
            }
        )

        try:
            response = await self.http_client.post(url, headers=headers)
            response.raise_for_status()

            data = response.json()

            logger.info(
                f"Installation token generated successfully",
                extra={
                    "installation_id": installation_id,
                    "expires_at": data.get("expires_at")
                }
            )

            return {
                "token": data["token"],
                "expires_at": data["expires_at"]
            }

        except httpx.HTTPStatusError as e:
            logger.error(
                f"GitHub API error generating installation token",
                extra={
                    "installation_id": installation_id,
                    "status_code": e.response.status_code,
                    "response_body": e.response.text
                },
                exc_info=True
            )
            raise RuntimeError(
                f"Failed to generate installation token: HTTP {e.response.status_code}"
            )

        except Exception as e:
            logger.error(
                f"Unexpected error generating installation token",
                extra={
                    "installation_id": installation_id,
                    "error": str(e)
                },
                exc_info=True
            )
            raise RuntimeError(f"Failed to generate installation token: {str(e)}")

    async def close(self):
        """Close HTTP client."""
        await self.http_client.aclose()


# FastAPI dependency
async def get_token_manager():
    """FastAPI dependency to get token manager instance."""
    from app.config import get_config
    from app.cache.redis_client import get_redis_client

    config = get_config()
    redis_client = await get_redis_client()

    manager = InstallationTokenManager(
        app_id=config.github_app_id,
        private_key=config.github_app_private_key,
        redis_client=redis_client
    )

    try:
        yield manager
    finally:
        await manager.close()