"""
Configuration API Schemas.

Request/response models for the repository configuration endpoints.

Phase 4 - Configuration & Production Readiness.
"""

from typing import Optional, Any
from pydantic import BaseModel, Field

from app.services.config_parser import (
    MergeWeaveConfig,
    BranchesConfig,
    DetectionConfig,
    ResolutionConfig,
    NotificationsConfig,
    DashboardConfig,
)


# =============================================================================
# Response Schemas
# =============================================================================


class ConfigResponse(BaseModel):
    """Response containing repository configuration."""
    repository: str = Field(..., description="Repository full name (owner/repo)")
    config: MergeWeaveConfig = Field(..., description="Parsed configuration")
    source: str = Field(..., description="Config source: 'file' or 'default'")
    file_path: Optional[str] = Field(
        None,
        description="Path to config file if source is 'file'"
    )
    ref: str = Field(..., description="Git ref the config was loaded from")


class ConfigUpdateResponse(BaseModel):
    """Response after updating repository configuration."""
    success: bool = Field(..., description="Whether the update was successful")
    commit_sha: str = Field(..., description="SHA of the commit that updated config")
    file_url: str = Field(..., description="URL to the config file on GitHub")
    message: str = Field(..., description="Success or error message")


class ConfigDeleteResponse(BaseModel):
    """Response after deleting repository configuration."""
    success: bool = Field(..., description="Whether the deletion was successful")
    commit_sha: str = Field(..., description="SHA of the commit that deleted config")
    message: str = Field(..., description="Success or error message")


class ValidationError(BaseModel):
    """A single validation error."""
    path: str = Field(..., description="JSON path to the error location")
    message: str = Field(..., description="Human-readable error message")
    error_type: str = Field(..., description="Type of validation error")


class ConfigValidationResponse(BaseModel):
    """Response from configuration validation."""
    valid: bool = Field(..., description="Whether the configuration is valid")
    errors: list[ValidationError] = Field(
        default_factory=list,
        description="List of validation errors if invalid"
    )
    config: Optional[MergeWeaveConfig] = Field(
        None,
        description="Parsed config if valid"
    )


# =============================================================================
# Request Schemas
# =============================================================================


class ConfigUpdateRequest(BaseModel):
    """Request to update repository configuration."""
    config: dict[str, Any] = Field(
        ...,
        description="Configuration object to save"
    )
    commit_message: Optional[str] = Field(
        None,
        description="Custom commit message for the config update"
    )
    branch: str = Field(
        default="main",
        description="Branch to commit the config to"
    )


class ConfigValidateRequest(BaseModel):
    """Request to validate a configuration without saving."""
    config: dict[str, Any] = Field(
        ...,
        description="Configuration object to validate"
    )


# =============================================================================
# Branch Tracking Check Schemas
# =============================================================================


class BranchTrackingCheckRequest(BaseModel):
    """Request to check if a branch should be tracked."""
    branch_name: str = Field(..., description="Branch name to check")
    config: Optional[dict[str, Any]] = Field(
        None,
        description="Optional config to use (uses repo config if not provided)"
    )


class BranchTrackingCheckResponse(BaseModel):
    """Response indicating if a branch should be tracked."""
    branch_name: str = Field(..., description="Branch name that was checked")
    should_track: bool = Field(..., description="Whether the branch should be tracked")
    reason: str = Field(..., description="Explanation for the decision")
    matched_pattern: Optional[str] = Field(
        None,
        description="Pattern that matched (if any)"
    )


# =============================================================================
# Auto-Resolve Check Schemas
# =============================================================================


class AutoResolveCheckRequest(BaseModel):
    """Request to check if a conflict can be auto-resolved."""
    file_path: str = Field(..., description="Path of the file with conflict")
    conflict_type: str = Field(..., description="Type of conflict")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Resolution confidence")
    config: Optional[dict[str, Any]] = Field(
        None,
        description="Optional config to use"
    )


class AutoResolveCheckResponse(BaseModel):
    """Response indicating if a conflict can be auto-resolved."""
    file_path: str = Field(..., description="File path that was checked")
    can_auto_resolve: bool = Field(..., description="Whether auto-resolution is allowed")
    reason: str = Field(..., description="Explanation for the decision")
    confidence_threshold: float = Field(
        ...,
        description="Minimum confidence required for auto-resolve"
    )


# =============================================================================
# File Ignore Check Schemas
# =============================================================================


class FileIgnoreCheckRequest(BaseModel):
    """Request to check if a file should be ignored."""
    file_path: str = Field(..., description="File path to check")
    file_size: int = Field(default=0, ge=0, description="File size in bytes")
    config: Optional[dict[str, Any]] = Field(
        None,
        description="Optional config to use"
    )


class FileIgnoreCheckResponse(BaseModel):
    """Response indicating if a file should be ignored."""
    file_path: str = Field(..., description="File path that was checked")
    should_ignore: bool = Field(..., description="Whether the file should be ignored")
    reason: str = Field(..., description="Explanation for the decision")
    matched_pattern: Optional[str] = Field(
        None,
        description="Pattern that matched (if any)"
    )
