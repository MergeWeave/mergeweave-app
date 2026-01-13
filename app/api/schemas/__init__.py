"""
API Schemas Package.

Exports all Pydantic request/response models for the Dashboard API.
"""

from app.api.schemas.dashboard import (
    # Repository schemas
    RepositoryInfo,
    AccountInfo,
    InstallationRepositories,
    RepositoriesResponse,
    # Branch schemas
    CommitSummary,
    BranchInfo,
    BranchesResponse,
    # MergeContext schemas
    CreateMergeContextOptions,
    CreateMergeContextRequest,
    BranchConfig,
    ConflictRegion,
    Conflict,
    ConflictSet,
    ResolvedFile,
    Resolution,
    MergeContextResponse,
    ResolveRequest,
    ApplyRequest,
    ApplyResult,
    ApplyResponse,
    # Common schemas
    PaginationInfo,
    ErrorResponse,
)

from app.api.schemas.config import (
    # Config response schemas
    ConfigResponse,
    ConfigUpdateResponse,
    ConfigDeleteResponse,
    ConfigValidationResponse,
    ValidationError,
    # Config request schemas
    ConfigUpdateRequest,
    ConfigValidateRequest,
    # Branch tracking schemas
    BranchTrackingCheckRequest,
    BranchTrackingCheckResponse,
    # Auto-resolve schemas
    AutoResolveCheckRequest,
    AutoResolveCheckResponse,
    # File ignore schemas
    FileIgnoreCheckRequest,
    FileIgnoreCheckResponse,
)

__all__ = [
    # Repository schemas
    "RepositoryInfo",
    "AccountInfo",
    "InstallationRepositories",
    "RepositoriesResponse",
    # Branch schemas
    "CommitSummary",
    "BranchInfo",
    "BranchesResponse",
    # MergeContext schemas
    "CreateMergeContextOptions",
    "CreateMergeContextRequest",
    "BranchConfig",
    "ConflictRegion",
    "Conflict",
    "ConflictSet",
    "ResolvedFile",
    "Resolution",
    "MergeContextResponse",
    "ResolveRequest",
    "ApplyRequest",
    "ApplyResult",
    "ApplyResponse",
    # Common schemas
    "PaginationInfo",
    "ErrorResponse",
    # Config response schemas
    "ConfigResponse",
    "ConfigUpdateResponse",
    "ConfigDeleteResponse",
    "ConfigValidationResponse",
    "ValidationError",
    # Config request schemas
    "ConfigUpdateRequest",
    "ConfigValidateRequest",
    # Branch tracking schemas
    "BranchTrackingCheckRequest",
    "BranchTrackingCheckResponse",
    # Auto-resolve schemas
    "AutoResolveCheckRequest",
    "AutoResolveCheckResponse",
    # File ignore schemas
    "FileIgnoreCheckRequest",
    "FileIgnoreCheckResponse",
]
