"""
Centralized Cache Key Management.

Provides standardized cache key patterns and TTL values for the GitHub App.
All cache keys follow a namespace pattern to prevent collisions and enable
efficient cache invalidation.

Key Namespace Structure:
    github:installation_token:{installation_id}
    github:rate_limit:core:{installation_id}
    github:rate_limit:search:{installation_id}
    github:repo:{owner}/{name}:default_branch
    github:webhook:{delivery_id}
    github:user:{user_id}:installation
"""

from typing import Optional, Union
from uuid import UUID


class CacheKeys:
    """
    Centralized cache key management.

    Provides static methods for generating standardized cache keys
    across the GitHub App service.
    """

    # Installation tokens
    INSTALLATION_TOKEN = "github:installation_token:{installation_id}"

    # Rate limiting
    RATE_LIMIT_CORE = "github:rate_limit:core:{installation_id}"
    RATE_LIMIT_SEARCH = "github:rate_limit:search:{installation_id}"
    RATE_LIMIT_GRAPHQL = "github:rate_limit:graphql:{installation_id}"

    # Repository metadata
    REPO_DEFAULT_BRANCH = "github:repo:{owner}/{name}:default_branch"
    REPO_METADATA = "github:repo:{owner}/{name}:metadata"

    # Webhook deduplication
    WEBHOOK_PROCESSED = "github:webhook:{delivery_id}"

    # User mappings
    USER_INSTALLATION = "github:user:{user_id}:installation"

    # Installation data
    INSTALLATION_REPOS = "github:installation:{installation_id}:repositories"

    @staticmethod
    def installation_token(installation_id: int) -> str:
        """
        Generate cache key for installation token.

        Args:
            installation_id: GitHub installation ID

        Returns:
            Cache key string
        """
        return CacheKeys.INSTALLATION_TOKEN.format(installation_id=installation_id)

    @staticmethod
    def rate_limit(resource: str, installation_id: int) -> str:
        """
        Generate cache key for rate limit tracking.

        Args:
            resource: GitHub API resource (core, search, graphql)
            installation_id: GitHub installation ID

        Returns:
            Cache key string
        """
        if resource == "core":
            return CacheKeys.RATE_LIMIT_CORE.format(installation_id=installation_id)
        elif resource == "search":
            return CacheKeys.RATE_LIMIT_SEARCH.format(installation_id=installation_id)
        elif resource == "graphql":
            return CacheKeys.RATE_LIMIT_GRAPHQL.format(installation_id=installation_id)
        else:
            return f"github:rate_limit:{resource}:{installation_id}"

    @staticmethod
    def repo_default_branch(owner: str, name: str) -> str:
        """
        Generate cache key for repository default branch.

        Args:
            owner: Repository owner
            name: Repository name

        Returns:
            Cache key string
        """
        return CacheKeys.REPO_DEFAULT_BRANCH.format(owner=owner, name=name)

    @staticmethod
    def repo_metadata(owner: str, name: str) -> str:
        """
        Generate cache key for repository metadata.

        Args:
            owner: Repository owner
            name: Repository name

        Returns:
            Cache key string
        """
        return CacheKeys.REPO_METADATA.format(owner=owner, name=name)

    @staticmethod
    def webhook_processed(delivery_id: str) -> str:
        """
        Generate cache key for webhook deduplication.

        Args:
            delivery_id: GitHub webhook delivery ID

        Returns:
            Cache key string
        """
        return CacheKeys.WEBHOOK_PROCESSED.format(delivery_id=delivery_id)

    @staticmethod
    def user_installation(user_id: Union[UUID, str]) -> str:
        """
        Generate cache key for user-installation mapping.

        Args:
            user_id: MergeWeave user ID (UUID)

        Returns:
            Cache key string
        """
        # Convert UUID to string for cache key
        user_id_str = str(user_id) if isinstance(user_id, UUID) else user_id
        return CacheKeys.USER_INSTALLATION.format(user_id=user_id_str)

    @staticmethod
    def installation_repositories(installation_id: int) -> str:
        """
        Generate cache key for installation repositories list.

        Args:
            installation_id: GitHub installation ID

        Returns:
            Cache key string
        """
        return CacheKeys.INSTALLATION_REPOS.format(installation_id=installation_id)


class CacheTTL:
    """
    Cache time-to-live values (in seconds).

    Defines standard TTL values for different types of cached data.
    """

    # Installation tokens (GitHub issues for 60 minutes, cache for 50)
    INSTALLATION_TOKEN = 3000  # 50 minutes

    # Rate limit status (refresh frequently to stay accurate)
    RATE_LIMIT = 60  # 1 minute

    # Repository metadata (relatively stable)
    REPO_DEFAULT_BRANCH = 3600  # 1 hour
    REPO_METADATA = 3600  # 1 hour

    # Webhook deduplication (prevent duplicate processing)
    WEBHOOK_DEDUP = 300  # 5 minutes

    # User-installation mapping (relatively stable)
    USER_INSTALLATION = 86400  # 24 hours

    # Installation repositories list
    INSTALLATION_REPOS = 1800  # 30 minutes


class CacheLocks:
    """
    Distributed lock key patterns.

    Used for preventing race conditions in distributed systems.
    """

    # Lock for token generation (prevent concurrent token requests)
    TOKEN_GENERATION = "github:lock:token:{installation_id}"

    @staticmethod
    def token_generation_lock(installation_id: int) -> str:
        """
        Generate lock key for token generation.

        Args:
            installation_id: GitHub installation ID

        Returns:
            Lock key string
        """
        return CacheLocks.TOKEN_GENERATION.format(installation_id=installation_id)
