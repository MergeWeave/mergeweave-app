"""
Version Manifest Service.

Collects and assembles version information from all system components.
"""

import hashlib
import logging
from typing import Optional, List, Dict
from datetime import datetime

import httpx

from app.config import get_config
from app.models.version_manifest import (
    VersionManifest,
    ServiceVersions,
    CREVersions,
    OriginInfo,
    SchemaVersions,
    ConfigSnapshot
)

logger = logging.getLogger(__name__)

# GitHub App version - should match the deployed version
GITHUB_APP_VERSION = "1.0.0"

# Default language support
DEFAULT_ENABLED_LANGUAGES = [
    "python", "javascript", "typescript", "java", "csharp",
    "go", "rust", "ruby", "kotlin", "swift", "cpp", "r", "css"
]


class VersionManifestService:
    """
    Service for collecting version manifests.

    Collects version information from all components and assembles
    a complete VersionManifest for embedding in payloads.
    """

    def __init__(self):
        self._config = get_config()
        self._cached_cre_versions: Optional[CREVersions] = None
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_seconds = 300  # 5 minute cache

    async def collect(
        self,
        origin: OriginInfo,
        include_config: bool = True
    ) -> VersionManifest:
        """
        Collect complete version manifest.

        Args:
            origin: Information about request origin
            include_config: Whether to include config snapshot

        Returns:
            Complete VersionManifest
        """
        logger.debug("Collecting version manifest", extra={"origin": origin.source})

        # Collect all version information
        services = await self._collect_service_versions(origin)
        cre = await self._collect_cre_versions()
        schemas = self._collect_schema_versions()
        config = await self._collect_config_snapshot() if include_config else ConfigSnapshot()

        manifest = VersionManifest(
            services=services,
            cre=cre,
            origin=origin,
            schemas=schemas,
            config=config
        )

        logger.info(
            "Version manifest collected",
            extra={
                "cre_version": cre.engine,
                "io_contract": cre.io_contract,
                "origin": origin.source
            }
        )

        return manifest

    async def _collect_service_versions(
        self,
        origin: OriginInfo
    ) -> ServiceVersions:
        """Collect versions from all services."""

        # GitHub App version (self)
        github_app_version = GITHUB_APP_VERSION

        # Public API version (fetch from health endpoint or use cached)
        public_api_version = await self._fetch_public_api_version()

        # Web dashboard version (from origin if applicable)
        web_dashboard_version = None
        if origin.source == "web_dashboard":
            web_dashboard_version = origin.source_version

        # Config manager version
        config_manager_version = self._get_config_manager_version()

        return ServiceVersions(
            public_api=public_api_version,
            github_app=github_app_version,
            web_dashboard=web_dashboard_version,
            config_manager=config_manager_version
        )

    async def _fetch_public_api_version(self) -> str:
        """Fetch Public API version from health endpoint."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{self._config.public_api_url}/health"
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("version", "unknown")
        except Exception as e:
            logger.warning(f"Failed to fetch Public API version: {e}")

        return "unknown"

    async def _collect_cre_versions(self) -> CREVersions:
        """Collect CRE engine version information."""

        # Check cache
        if self._cached_cre_versions and self._cache_timestamp:
            age = (datetime.utcnow() - self._cache_timestamp).total_seconds()
            if age < self._cache_ttl_seconds:
                return self._cached_cre_versions

        # Import CRE components
        try:
            from mergeweave_cre import __version__ as cre_version
            from mergeweave_cre import __io_contract_version__

            # Try to get strategy registry info
            try:
                from mergeweave_cre.core.strategies import get_strategy_registry
                registry = get_strategy_registry()
                strategy_hash = registry.compute_hash()
                enabled_strategies = registry.list_enabled()
                language_adapters = registry.get_adapter_versions()
            except (ImportError, AttributeError):
                # Strategy registry not yet implemented with new interface
                strategy_hash = self._compute_default_strategy_hash()
                enabled_strategies = self._get_default_enabled_strategies()
                language_adapters = {}

            cre_versions = CREVersions(
                engine=cre_version,
                io_contract=__io_contract_version__,
                strategy_registry_hash=strategy_hash,
                enabled_strategies=enabled_strategies,
                language_adapters=language_adapters
            )

            # Cache result
            self._cached_cre_versions = cre_versions
            self._cache_timestamp = datetime.utcnow()

            return cre_versions

        except ImportError as e:
            logger.error(f"Failed to import CRE: {e}")
            return CREVersions(
                engine="unknown",
                io_contract="1.0.0",
                strategy_registry_hash="unknown",
                enabled_strategies=[],
                language_adapters={}
            )

    def _compute_default_strategy_hash(self) -> str:
        """Compute a default hash for strategy configuration."""
        # Until the registry is updated, compute hash from __all__ exports
        try:
            from mergeweave_cre.core.strategies import __all__ as strategies
            strategy_list = sorted(strategies)
            strategy_str = ",".join(strategy_list)
            return hashlib.sha256(strategy_str.encode()).hexdigest()[:16]
        except ImportError:
            return "unknown"

    def _get_default_enabled_strategies(self) -> List[str]:
        """Get default list of enabled strategies."""
        try:
            from mergeweave_cre.core.strategies import __all__ as strategies
            # Filter to strategy classes (those ending in 'Strategy')
            return [s for s in strategies if s.endswith('Strategy')]
        except ImportError:
            return []

    def _collect_schema_versions(self) -> SchemaVersions:
        """Collect schema versions from registry."""
        # Load from schema registry
        try:
            from config.schema_versions import SCHEMA_VERSIONS
            return SchemaVersions(
                merge_context=SCHEMA_VERSIONS.get("merge_context", "1.0.0"),
                resolution_package=SCHEMA_VERSIONS.get("resolution_package", "1.0.0"),
                io_contract=SCHEMA_VERSIONS.get("io_contract", "1.0.0"),
                version_manifest=SCHEMA_VERSIONS.get("version_manifest", "1.0.0")
            )
        except ImportError:
            logger.warning("Schema version registry not found, using defaults")
            return SchemaVersions()

    async def _collect_config_snapshot(self) -> ConfigSnapshot:
        """Collect current configuration snapshot."""
        return ConfigSnapshot(
            confidence_threshold=0.7,  # Default threshold
            auto_apply_threshold=0.95,  # Default auto-apply
            enabled_languages=DEFAULT_ENABLED_LANGUAGES,
            feature_flags={
                "cross_file_detection": False,
                "enable_write_back": self._config.enable_write_back,
            }
        )

    def _get_config_manager_version(self) -> Optional[str]:
        """Get config manager version."""
        try:
            from config import __version__ as config_version
            return config_version
        except (ImportError, AttributeError):
            # Try reading VERSION file
            try:
                from pathlib import Path
                version_file = Path(__file__).parent.parent.parent.parent / "config" / "VERSION"
                if version_file.exists():
                    return version_file.read_text().strip()
            except Exception:
                pass
            return None


# Singleton instance
_version_manifest_service: Optional[VersionManifestService] = None


def get_version_manifest_service() -> VersionManifestService:
    """Get singleton VersionManifestService instance."""
    global _version_manifest_service
    if _version_manifest_service is None:
        _version_manifest_service = VersionManifestService()
    return _version_manifest_service
