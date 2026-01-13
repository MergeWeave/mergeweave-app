"""
Configuration Parser Service.

Parses and validates .mergeweave.yaml files from repositories.
Provides branch tracking rules, detection settings, and resolution configuration.

Phase 4 - Configuration & Production Readiness.
"""

import logging
from typing import Optional, Any
from fnmatch import fnmatch
import yaml
from pydantic import BaseModel, Field, field_validator
from functools import lru_cache

from app.clients.github_client import GitHubAPIClient
from app.cache.redis_client import get_redis_client
import httpx

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Schema Models
# =============================================================================


class BranchConfig(BaseModel):
    """Branch tracking default configuration."""
    enabled: bool = True
    auto_resolve: bool = False
    min_confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)


class BranchesConfig(BaseModel):
    """Branch pattern configuration."""
    default: BranchConfig = Field(default_factory=BranchConfig)
    protected: list[str] = Field(default_factory=lambda: ["main", "master"])
    tracked: list[str] = Field(default_factory=lambda: ["main", "master"])
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class LanguageSettings(BaseModel):
    """Language-specific analysis settings."""
    analyze_imports: bool = True
    analyze_types: bool = False
    analyze_dependencies: bool = False


class DetectionConfig(BaseModel):
    """Conflict detection configuration."""
    semantic_analysis: bool = True
    max_file_size: int = Field(default=1048576, ge=0)  # 1MB default
    ignore_patterns: list[str] = Field(default_factory=lambda: [
        "*.lock",
        "*.min.js",
        "*.min.css",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    ])
    languages: dict[str, LanguageSettings] = Field(default_factory=dict)


class AutoResolveConfig(BaseModel):
    """Auto-resolution configuration."""
    min_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    never_auto_resolve: list[str] = Field(default_factory=lambda: [
        "*.config.*",
        "*.env*",
        "migrations/*",
    ])
    require_review: list[str] = Field(default_factory=lambda: [
        "structural",
        "semantic",
    ])


class ResolutionConfig(BaseModel):
    """Resolution strategy configuration."""
    strategy_order: list[str] = Field(
        default_factory=lambda: ["ast_merge", "semantic_merge", "textual_merge"]
    )
    auto_resolve: AutoResolveConfig = Field(default_factory=AutoResolveConfig)


class PRCommentsConfig(BaseModel):
    """PR comment configuration."""
    enabled: bool = True
    on_conflict: bool = True
    on_resolution: bool = True
    summary_only: bool = False


class NotificationsConfig(BaseModel):
    """Notification configuration."""
    check_annotations: bool = True
    pr_comments: PRCommentsConfig = Field(default_factory=PRCommentsConfig)
    webhooks: list[str] = Field(default_factory=list)


class DashboardConfig(BaseModel):
    """Dashboard UI configuration."""
    default_view: str = "file"
    theme: str = "dark"
    show_confidence: bool = True
    show_strategies: bool = True

    @field_validator("default_view")
    @classmethod
    def validate_default_view(cls, v: str) -> str:
        valid_views = {"file", "conflict", "commit"}
        if v not in valid_views:
            raise ValueError(f"default_view must be one of {valid_views}")
        return v

    @field_validator("theme")
    @classmethod
    def validate_theme(cls, v: str) -> str:
        valid_themes = {"dark", "light", "system"}
        if v not in valid_themes:
            raise ValueError(f"theme must be one of {valid_themes}")
        return v


class MergeWeaveConfig(BaseModel):
    """
    Root configuration schema for .mergeweave.yaml files.

    Provides repository-level customization for conflict detection,
    resolution behavior, and dashboard preferences.
    """
    version: str = "1.0"
    branches: BranchesConfig = Field(default_factory=BranchesConfig)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    resolution: ResolutionConfig = Field(default_factory=ResolutionConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        supported_versions = {"1.0"}
        if v not in supported_versions:
            raise ValueError(f"Unsupported config version: {v}. Supported: {supported_versions}")
        return v


# =============================================================================
# Configuration Parser Service
# =============================================================================


class ConfigParser:
    """
    Parses and caches repository configuration.

    Features:
    - Fetches .mergeweave.yaml from repository
    - Validates against Pydantic schema
    - Caches parsed configurations in Redis
    - Falls back to defaults if no config found

    Usage:
        parser = ConfigParser()
        config = await parser.get_config(installation_id, "owner/repo")

        if parser.should_track_branch(config, "feature/new-feature"):
            # Process branch
            pass
    """

    CONFIG_FILENAME = ".mergeweave.yaml"
    ALT_CONFIG_FILENAME = ".mergeweave.yml"
    CACHE_TTL_SECONDS = 300  # 5 minute cache

    def __init__(self):
        self._memory_cache: dict[str, tuple[float, MergeWeaveConfig]] = {}

    async def get_config(
        self,
        installation_id: int,
        repository: str,
        ref: str = "HEAD",
        force_refresh: bool = False,
    ) -> MergeWeaveConfig:
        """
        Get configuration for a repository.

        Attempts to load .mergeweave.yaml from the repository.
        Falls back to default configuration if not found.

        Args:
            installation_id: GitHub installation ID
            repository: Repository full name (owner/repo)
            ref: Git ref to read config from (branch, tag, or SHA)
            force_refresh: Bypass cache and fetch fresh config

        Returns:
            MergeWeaveConfig instance (either parsed or default)
        """
        import time

        cache_key = f"config:{repository}:{ref}"

        # Check memory cache first (for hot path)
        if not force_refresh and cache_key in self._memory_cache:
            cached_time, config = self._memory_cache[cache_key]
            if time.time() - cached_time < self.CACHE_TTL_SECONDS:
                logger.debug(f"Config cache hit (memory): {repository}")
                return config

        # Check Redis cache
        if not force_refresh:
            redis = await get_redis_client()
            if redis:
                cached_data = await redis.get(cache_key)
                if cached_data:
                    try:
                        import json
                        config = MergeWeaveConfig(**json.loads(cached_data))
                        self._memory_cache[cache_key] = (time.time(), config)
                        logger.debug(f"Config cache hit (redis): {repository}")
                        return config
                    except Exception as e:
                        logger.warning(f"Failed to parse cached config: {e}")

        # Fetch from repository
        config_content = await self._fetch_config_file(
            installation_id,
            repository,
            ref,
        )

        if config_content:
            try:
                config = self._parse_config(config_content)
                logger.info(
                    f"Loaded config for {repository}",
                    extra={"version": config.version, "ref": ref}
                )
            except Exception as e:
                logger.warning(
                    f"Failed to parse config for {repository}: {e}",
                    exc_info=True
                )
                config = MergeWeaveConfig()
        else:
            logger.debug(f"No config file found for {repository}, using defaults")
            config = MergeWeaveConfig()

        # Cache result
        await self._cache_config(cache_key, config)
        self._memory_cache[cache_key] = (time.time(), config)

        return config

    async def _fetch_config_file(
        self,
        installation_id: int,
        repository: str,
        ref: str,
    ) -> Optional[str]:
        """Fetch config file content from repository."""
        owner, repo = repository.split("/", 1)

        async with GitHubAPIClient(installation_id) as client:
            # Try primary filename
            try:
                content = await client.get_file_contents(
                    owner=owner,
                    repo=repo,
                    path=self.CONFIG_FILENAME,
                    ref=ref,
                )
                return content
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 404:
                    logger.warning(f"Error fetching {self.CONFIG_FILENAME}: {e}")
                    raise

            # Try alternate filename
            try:
                content = await client.get_file_contents(
                    owner=owner,
                    repo=repo,
                    path=self.ALT_CONFIG_FILENAME,
                    ref=ref,
                )
                return content
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 404:
                    logger.warning(f"Error fetching {self.ALT_CONFIG_FILENAME}: {e}")
                    raise

        return None

    def _parse_config(self, content: str) -> MergeWeaveConfig:
        """Parse YAML content into config object."""
        data = yaml.safe_load(content)

        if data is None:
            # Empty file - use defaults
            return MergeWeaveConfig()

        if not isinstance(data, dict):
            raise ValueError("Config must be a YAML mapping")

        return MergeWeaveConfig(**data)

    async def _cache_config(self, cache_key: str, config: MergeWeaveConfig) -> None:
        """Cache config in Redis."""
        try:
            redis = await get_redis_client()
            if redis:
                import json
                await redis.setex(
                    cache_key,
                    self.CACHE_TTL_SECONDS,
                    json.dumps(config.model_dump()),
                )
        except Exception as e:
            logger.warning(f"Failed to cache config: {e}")

    async def clear_cache(self, repository: Optional[str] = None) -> None:
        """
        Clear configuration cache.

        Args:
            repository: If provided, only clear cache for this repository.
                       If None, clears entire memory cache.
        """
        if repository:
            # Clear memory cache entries for this repo
            keys_to_remove = [
                k for k in self._memory_cache
                if k.startswith(f"config:{repository}:")
            ]
            for key in keys_to_remove:
                del self._memory_cache[key]

            # Clear Redis cache
            redis = await get_redis_client()
            if redis:
                pattern = f"config:{repository}:*"
                cursor = 0
                while True:
                    cursor, keys = await redis.scan(cursor, match=pattern, count=100)
                    if keys:
                        await redis.delete(*keys)
                    if cursor == 0:
                        break
        else:
            self._memory_cache.clear()

    # =========================================================================
    # Branch Tracking Logic
    # =========================================================================

    def should_track_branch(
        self,
        config: MergeWeaveConfig,
        branch_name: str,
    ) -> bool:
        """
        Determine if a branch should be tracked for conflicts.

        Priority order:
        1. Exclude patterns (if match, don't track)
        2. Protected branches (if match, always track)
        3. Include patterns (if set and match, track per default.enabled)
        4. Default enabled setting

        Args:
            config: Repository configuration
            branch_name: Branch name to check

        Returns:
            True if branch should be tracked
        """
        # Check if explicitly excluded
        for pattern in config.branches.exclude:
            if fnmatch(branch_name, pattern):
                logger.debug(f"Branch {branch_name} excluded by pattern {pattern}")
                return False

        # Check if in protected list (always track)
        for pattern in config.branches.protected:
            if fnmatch(branch_name, pattern):
                logger.debug(f"Branch {branch_name} is protected, tracking")
                return True

        # Check if in include list
        if config.branches.include:
            for pattern in config.branches.include:
                if fnmatch(branch_name, pattern):
                    return config.branches.default.enabled
            # Has include patterns but doesn't match any
            return False

        # Use default
        return config.branches.default.enabled

    def is_tracked_branch(
        self,
        config: MergeWeaveConfig,
        branch_name: str,
    ) -> bool:
        """
        Determine if a branch is a tracked branch for state comparison.

        Tracked branches serve as comparison baselines for computing branch
        states (ahead/behind/diverged). They are typically important branches
        like main, master, or release branches.

        Uses glob pattern matching, so patterns like 'release/*' are supported.

        Args:
            config: Repository configuration
            branch_name: Branch name to check

        Returns:
            True if branch matches any tracked pattern
        """
        for pattern in config.branches.tracked:
            if fnmatch(branch_name, pattern):
                logger.debug(f"Branch {branch_name} matches tracked pattern {pattern}")
                return True
        return False

    def should_auto_resolve(
        self,
        config: MergeWeaveConfig,
        file_path: str,
        conflict_type: str,
        confidence: float,
    ) -> bool:
        """
        Determine if a conflict should be auto-resolved.

        Args:
            config: Repository configuration
            file_path: Path of file with conflict
            conflict_type: Type of conflict (e.g., "textual", "structural", "semantic")
            confidence: Resolution confidence score (0.0-1.0)

        Returns:
            True if conflict can be auto-resolved
        """
        # Check if auto-resolve is globally enabled
        if not config.branches.default.auto_resolve:
            return False

        auto_config = config.resolution.auto_resolve

        # Check confidence threshold
        if confidence < auto_config.min_confidence:
            logger.debug(
                f"Auto-resolve blocked: confidence {confidence} < {auto_config.min_confidence}"
            )
            return False

        # Check if file requires manual resolution
        for pattern in auto_config.never_auto_resolve:
            if fnmatch(file_path, pattern):
                logger.debug(f"Auto-resolve blocked: file {file_path} matches {pattern}")
                return False

        # Check if conflict type requires review
        if conflict_type in auto_config.require_review:
            logger.debug(f"Auto-resolve blocked: conflict type {conflict_type} requires review")
            return False

        return True

    def should_ignore_file(
        self,
        config: MergeWeaveConfig,
        file_path: str,
        file_size: int = 0,
    ) -> bool:
        """
        Determine if a file should be ignored for conflict detection.

        Args:
            config: Repository configuration
            file_path: File path to check
            file_size: File size in bytes

        Returns:
            True if file should be ignored
        """
        # Check file size limit
        if file_size > config.detection.max_file_size:
            logger.debug(f"Ignoring {file_path}: size {file_size} > {config.detection.max_file_size}")
            return True

        # Check ignore patterns
        for pattern in config.detection.ignore_patterns:
            if fnmatch(file_path, pattern):
                logger.debug(f"Ignoring {file_path}: matches pattern {pattern}")
                return True

        return False

    def get_language_settings(
        self,
        config: MergeWeaveConfig,
        language: str,
    ) -> LanguageSettings:
        """
        Get language-specific settings.

        Args:
            config: Repository configuration
            language: Language identifier (e.g., "python", "typescript")

        Returns:
            LanguageSettings for the language (defaults if not configured)
        """
        return config.detection.languages.get(language, LanguageSettings())


# =============================================================================
# Service Factory
# =============================================================================


_config_parser: Optional[ConfigParser] = None


def get_config_parser() -> ConfigParser:
    """Get global ConfigParser instance."""
    global _config_parser
    if _config_parser is None:
        _config_parser = ConfigParser()
    return _config_parser


async def get_repository_config(
    installation_id: int,
    repository: str,
    ref: str = "HEAD",
) -> MergeWeaveConfig:
    """
    Convenience function to get repository configuration.

    Args:
        installation_id: GitHub installation ID
        repository: Repository full name (owner/repo)
        ref: Git ref to read config from

    Returns:
        MergeWeaveConfig instance
    """
    parser = get_config_parser()
    return await parser.get_config(installation_id, repository, ref)
