"""
Version Manifest Data Models.

Defines the structure for comprehensive version tracking across all components.
Embedded in every MergeContext and ResolutionPackage for debugging.

These are Pydantic models (not SQLAlchemy) for API payload serialization.
"""

from datetime import datetime
from typing import Dict, List, Optional, Literal
from pydantic import BaseModel, Field
from uuid import uuid4


class ServiceVersions(BaseModel):
    """Versions of deployed services."""

    public_api: str = Field(
        ...,
        description="Public API service version (e.g., '3.1.4')",
        examples=["3.1.4"]
    )
    github_app: str = Field(
        ...,
        description="GitHub App service version (e.g., '1.2.0')",
        examples=["1.2.0"]
    )
    web_dashboard: Optional[str] = Field(
        None,
        description="Web Dashboard version (if request originated from dashboard)",
        examples=["1.0.0"]
    )
    config_manager: Optional[str] = Field(
        None,
        description="Config Manager version",
        examples=["1.0.0"]
    )


class CREVersions(BaseModel):
    """CRE engine and component versions."""

    engine: str = Field(
        ...,
        description="CRE engine version",
        examples=["3.1.4"]
    )
    io_contract: str = Field(
        ...,
        description="IO Contract version used",
        examples=["1.0.0"]
    )
    strategy_registry_hash: str = Field(
        ...,
        description="SHA256 hash of enabled strategy configuration",
        examples=["a1b2c3d4e5f6"]
    )
    enabled_strategies: List[str] = Field(
        default_factory=list,
        description="List of enabled strategy identifiers"
    )
    language_adapters: Dict[str, str] = Field(
        default_factory=dict,
        description="Language adapter versions (language -> version)"
    )


class OriginInfo(BaseModel):
    """Information about request origin."""

    source: Literal["cli", "web_dashboard", "github_webhook", "api_direct"] = Field(
        ...,
        description="Origin source type"
    )
    source_version: Optional[str] = Field(
        None,
        description="Version of the originating component"
    )
    user_agent: Optional[str] = Field(
        None,
        description="User-Agent header (for web requests)"
    )
    correlation_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique correlation ID for request tracing"
    )


class SchemaVersions(BaseModel):
    """Data format schema versions."""

    merge_context: str = Field(
        "1.0.0",
        description="MergeContext schema version"
    )
    resolution_package: str = Field(
        "1.0.0",
        description="ResolutionPackage schema version"
    )
    io_contract: str = Field(
        "1.0.0",
        description="CRE IO Contract version"
    )
    version_manifest: str = Field(
        "1.0.0",
        description="This VersionManifest schema version"
    )


class ConfigSnapshot(BaseModel):
    """Configuration state at processing time."""

    confidence_threshold: float = Field(
        0.7,
        description="Minimum confidence threshold in effect"
    )
    auto_apply_threshold: float = Field(
        0.95,
        description="Auto-apply threshold in effect"
    )
    enabled_languages: List[str] = Field(
        default_factory=list,
        description="Languages with enabled resolution support"
    )
    feature_flags: Dict[str, bool] = Field(
        default_factory=dict,
        description="Active feature flags"
    )


class VersionManifest(BaseModel):
    """
    Complete version information for debugging and reproducibility.

    This model is embedded in every MergeContext and ResolutionPackage,
    ensuring full traceability without external lookups.
    """

    # Manifest metadata
    manifest_version: str = Field(
        "1.0.0",
        description="Version of this manifest schema"
    )
    generated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when manifest was generated"
    )

    # Component versions
    services: ServiceVersions = Field(
        ...,
        description="Service version information"
    )
    cre: CREVersions = Field(
        ...,
        description="CRE engine version information"
    )

    # Request context
    origin: OriginInfo = Field(
        ...,
        description="Request origin information"
    )

    # Schema versions
    schemas: SchemaVersions = Field(
        default_factory=SchemaVersions,
        description="Data schema versions"
    )

    # Configuration
    config: ConfigSnapshot = Field(
        default_factory=ConfigSnapshot,
        description="Configuration snapshot"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "manifest_version": "1.0.0",
                "generated_at": "2025-12-01T10:30:00Z",
                "services": {
                    "public_api": "3.1.4",
                    "github_app": "1.2.0",
                    "web_dashboard": "1.0.0"
                },
                "cre": {
                    "engine": "3.1.4",
                    "io_contract": "1.0.0",
                    "strategy_registry_hash": "a1b2c3d4...",
                    "enabled_strategies": ["import_merge", "semantic_python"],
                    "language_adapters": {"python": "2.1.0", "javascript": "1.3.0"}
                },
                "origin": {
                    "source": "web_dashboard",
                    "source_version": "1.0.0",
                    "correlation_id": "corr_abc123"
                },
                "schemas": {
                    "merge_context": "1.0.0",
                    "resolution_package": "1.0.0",
                    "io_contract": "1.0.0"
                },
                "config": {
                    "confidence_threshold": 0.7,
                    "auto_apply_threshold": 0.95,
                    "enabled_languages": ["python", "javascript", "typescript"]
                }
            }
        }
    }
