"""
Dashboard API Pydantic Schemas.

Request and response models for Dashboard API endpoints.
All responses include version_manifest for debugging per Phase 0 requirements.
"""

from datetime import datetime
from typing import List, Optional, Dict, Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


# =============================================================================
# Common Models
# =============================================================================

class PaginationInfo(BaseModel):
    """Pagination information for list endpoints."""
    page: int = Field(..., ge=1, description="Current page number")
    per_page: int = Field(..., ge=1, le=100, description="Items per page")
    total: int = Field(..., ge=0, description="Total number of items")
    total_pages: int = Field(..., ge=0, description="Total number of pages")


class ErrorResponse(BaseModel):
    """Standard error response format."""
    error: bool = True
    error_code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional error details"
    )
    version_manifest: Optional[Dict[str, Any]] = Field(
        None,
        description="Version manifest for debugging"
    )


# =============================================================================
# Repository Endpoints
# =============================================================================

class RepositoryInfo(BaseModel):
    """Repository information from GitHub."""
    id: int = Field(..., description="GitHub repository ID")
    name: str = Field(..., description="Repository name")
    full_name: str = Field(..., description="Full repository name (owner/repo)")
    private: bool = Field(..., description="Whether repository is private")
    default_branch: str = Field(..., description="Default branch name")
    language: Optional[str] = Field(None, description="Primary language")
    updated_at: Optional[datetime] = Field(None, description="Last update time")


class AccountInfo(BaseModel):
    """GitHub account (user or organization) information."""
    login: str = Field(..., description="Account login name")
    type: Literal["User", "Organization"] = Field(
        ...,
        description="Type of GitHub account"
    )
    avatar_url: Optional[str] = Field(None, description="Avatar URL")


class InstallationRepositories(BaseModel):
    """Repositories accessible under a GitHub App installation."""
    installation_id: int = Field(..., description="GitHub installation ID")
    account: AccountInfo = Field(..., description="Account that owns the installation")
    repositories: List[RepositoryInfo] = Field(
        default_factory=list,
        description="List of accessible repositories"
    )
    repository_selection: Literal["all", "selected"] = Field(
        ...,
        description="Repository access scope"
    )
    permissions: Dict[str, str] = Field(
        default_factory=dict,
        description="Installation permissions"
    )


class RepositoriesResponse(BaseModel):
    """Response for GET /api/dashboard/repositories."""
    repositories: List[InstallationRepositories] = Field(
        ...,
        description="List of installations with their repositories"
    )
    pagination: PaginationInfo = Field(..., description="Pagination information")
    version_manifest: Dict[str, Any] = Field(
        ...,
        description="Version manifest for debugging"
    )


# =============================================================================
# Branch Endpoints
# =============================================================================

class CommitSummary(BaseModel):
    """Summary information about a commit."""
    sha: str = Field(..., description="Full commit SHA")
    message: str = Field(..., description="Commit message (first line)")
    author: Optional[str] = Field(None, description="Author username")
    date: Optional[datetime] = Field(None, description="Commit date")


class BranchInfo(BaseModel):
    """Branch information from GitHub."""
    name: str = Field(..., description="Branch name")
    sha: str = Field(..., description="Head commit SHA")
    protected: bool = Field(..., description="Whether branch is protected")
    is_default: bool = Field(..., description="Whether this is the default branch")
    commit: Optional[CommitSummary] = Field(None, description="Latest commit info")


class BranchesResponse(BaseModel):
    """Response for GET /api/dashboard/repositories/{id}/branches.

    P1-F2: Updated to return all branches with auto-pagination.
    The `limited` flag indicates if results were capped at MAX_BRANCHES_FETCH.
    """
    installation_id: int = Field(..., description="GitHub installation ID")
    repository: str = Field(..., description="Full repository name")
    default_branch: str = Field(..., description="Default branch name")
    branches: List[BranchInfo] = Field(
        default_factory=list,
        description="List of branches"
    )
    pagination: PaginationInfo = Field(..., description="Pagination information")
    limited: bool = Field(
        False,
        description="P1-F2: True if results were limited by MAX_BRANCHES_FETCH"
    )
    max_limit: int = Field(
        500,
        description="P1-F2: The MAX_BRANCHES_FETCH value used (default 500)"
    )
    version_manifest: Dict[str, Any] = Field(
        ...,
        description="Version manifest for debugging"
    )


# =============================================================================
# MergeContext Endpoints
# =============================================================================

class CreateMergeContextOptions(BaseModel):
    """Options for creating a MergeContext."""
    auto_detect_merge_base: bool = Field(
        True,
        description="Automatically detect merge-base commit"
    )
    include_ancestry: bool = Field(
        True,
        description="Include commit ancestry in context"
    )
    conflict_detection_mode: Literal["full", "quick"] = Field(
        "full",
        description="Conflict detection thoroughness"
    )


class CreateMergeContextRequest(BaseModel):
    """Request for POST /api/dashboard/merge-context."""
    installation_id: int = Field(..., description="GitHub installation ID")
    repository: str = Field(..., description="Full repository name (owner/repo)")
    base_branch: str = Field(..., description="Base/target branch name")
    source_branch: str = Field(..., description="Source branch for merge")
    target_branch: Optional[str] = Field(
        None,
        description="Optional second branch for 3-way merge"
    )
    options: Optional[CreateMergeContextOptions] = Field(
        None,
        description="Creation options"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "installation_id": 12345678,
                "repository": "myorg/my-repo",
                "base_branch": "main",
                "source_branch": "feature/new-feature",
                "options": {
                    "auto_detect_merge_base": True,
                    "conflict_detection_mode": "full"
                }
            }
        }
    )


class BranchConfig(BaseModel):
    """Branch configuration stored in MergeContext."""
    base_branch: str = Field(..., description="Base/target branch name")
    source_branch: str = Field(..., description="Source branch name")
    target_branch: Optional[str] = Field(None, description="Optional target branch")
    base_sha: str = Field(..., description="Base branch HEAD SHA")
    source_sha: str = Field(..., description="Source branch HEAD SHA")
    target_sha: Optional[str] = Field(None, description="Target branch HEAD SHA")
    merge_base_sha: str = Field(..., description="Merge-base commit SHA")
    commits_ahead_source: Optional[int] = Field(
        None,
        description="Commits ahead in source"
    )
    commits_ahead_target: Optional[int] = Field(
        None,
        description="Commits ahead in target"
    )


class ConflictRegion(BaseModel):
    """A specific region of conflict within a file."""
    start_line: int = Field(..., ge=1, description="Starting line number")
    end_line: int = Field(..., ge=1, description="Ending line number")
    base_content: Optional[str] = Field(None, description="Content in base")
    source_content: Optional[str] = Field(None, description="Content in source")
    target_content: Optional[str] = Field(None, description="Content in target")


class Conflict(BaseModel):
    """Individual conflict detected in the merge."""
    id: str = Field(..., description="Unique conflict identifier")
    type: Literal["textual", "semantic", "structural"] = Field(
        ...,
        description="Type of conflict"
    )
    primary_file: str = Field(..., description="Primary file with conflict")
    affected_files: List[str] = Field(
        default_factory=list,
        description="All files affected by this conflict"
    )
    regions: Optional[List[ConflictRegion]] = Field(
        None,
        description="Specific conflict regions"
    )


class ConflictSet(BaseModel):
    """Complete set of conflicts detected."""
    total_conflicts: int = Field(..., ge=0, description="Total number of conflicts")
    total_files_affected: int = Field(
        ...,
        ge=0,
        description="Number of files with conflicts"
    )
    conflicts: List[Conflict] = Field(
        default_factory=list,
        description="List of conflicts"
    )
    by_file: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Conflict IDs grouped by file path"
    )


class ResolvedFile(BaseModel):
    """A file that has been resolved by CRE."""
    path: str = Field(..., description="File path")
    content: str = Field(..., description="Resolved content")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score")
    primary_strategy: str = Field(..., description="Main strategy used")


class Resolution(BaseModel):
    """CRE resolution result."""
    status: Literal["success", "partial", "failed"] = Field(
        ...,
        description="Resolution status"
    )
    overall_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall confidence score"
    )
    resolved_files: Dict[str, ResolvedFile] = Field(
        default_factory=dict,
        description="Resolved files by path"
    )
    confidence_regions: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Confidence scores per region"
    )
    strategies_used: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Strategies used in resolution"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Resolution metadata"
    )


class MergeContextResponse(BaseModel):
    """Response for MergeContext endpoints."""
    id: str = Field(..., description="MergeContext UUID")
    status: str = Field(..., description="Current status")
    status_message: Optional[str] = Field(None, description="Status description")
    source_type: str = Field(..., description="Source type (github/local/manual)")
    installation_id: Optional[int] = Field(None, description="GitHub installation ID")
    repository_full_name: Optional[str] = Field(None, description="Repository name")
    branch_config: BranchConfig = Field(..., description="Branch configuration")
    commit_graph: Optional[Dict[str, Any]] = Field(None, description="Commit graph")
    conflict_set: Optional[ConflictSet] = Field(None, description="Detected conflicts")
    resolution: Optional[Resolution] = Field(None, description="CRE resolution")
    processing_duration_ms: Optional[int] = Field(
        None,
        description="Processing duration in milliseconds"
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    version_manifest: Dict[str, Any] = Field(
        ...,
        description="Version manifest for debugging"
    )


class ResolveRequest(BaseModel):
    """Request for POST /api/dashboard/merge-context/{id}/resolve."""
    options: Optional[Dict[str, Any]] = Field(
        None,
        description="Resolution options passed to CRE"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "options": {
                    "min_confidence_threshold": 0.7,
                    "preferred_strategies": ["semantic", "syntactic"]
                }
            }
        }
    )


class ApplyRequest(BaseModel):
    """Request for POST /api/dashboard/merge-context/{id}/apply."""
    target_branch: str = Field(..., description="Branch to apply resolution to")
    commit_message: Optional[str] = Field(
        "Resolve merge conflicts via MergeWeave",
        description="Commit message"
    )
    create_pull_request: bool = Field(
        False,
        description="Whether to create a PR instead of direct push"
    )


class ApplyResult(BaseModel):
    """Result of applying a resolution."""
    commit_sha: str = Field(..., description="New commit SHA")
    branch: str = Field(..., description="Branch the commit was applied to")
    files_modified: int = Field(..., ge=0, description="Number of files modified")
    pull_request_url: Optional[str] = Field(None, description="PR URL if created")


class ApplyResponse(BaseModel):
    """Response for POST /api/dashboard/merge-context/{id}/apply."""
    id: str = Field(..., description="MergeContext UUID")
    status: str = Field(..., description="Updated status (should be 'applied')")
    status_message: str = Field(..., description="Status description")
    apply_result: ApplyResult = Field(..., description="Application result details")
    version_manifest: Dict[str, Any] = Field(
        ...,
        description="Version manifest for debugging"
    )


# =============================================================================
# File Tree and Content Endpoints
# =============================================================================

class FileTreeItem(BaseModel):
    """A file or directory entry in the repository tree."""
    name: str = Field(..., description="File or directory name")
    path: str = Field(..., description="Full path in repository")
    type: Literal["file", "dir"] = Field(..., description="Entry type")
    size: Optional[int] = Field(None, description="File size in bytes (files only)")
    sha: str = Field(..., description="Git SHA of the object")


class FileTreeResponse(BaseModel):
    """Response for GET /api/dashboard/repositories/{id}/tree."""
    tree: List[FileTreeItem] = Field(
        default_factory=list,
        description="List of files and directories"
    )
    truncated: bool = Field(
        False,
        description="Whether the tree was truncated due to size"
    )
    sha: str = Field(..., description="SHA of the tree object")
    version_manifest: Dict[str, Any] = Field(
        ...,
        description="Version manifest for debugging"
    )


class FileContentResponse(BaseModel):
    """Response for GET /api/dashboard/repositories/{id}/content."""
    content: str = Field(..., description="File content (base64 encoded)")
    encoding: Literal["base64", "utf-8"] = Field(
        "base64",
        description="Content encoding"
    )
    sha: str = Field(..., description="Git SHA of the file")
    size: int = Field(..., ge=0, description="File size in bytes")
    path: str = Field(..., description="File path in repository")
    version_manifest: Dict[str, Any] = Field(
        ...,
        description="Version manifest for debugging"
    )


# =============================================================================
# Merge Base Endpoints
# =============================================================================

class MergeBaseCommitInfo(BaseModel):
    """Information about the merge base commit."""
    sha: str = Field(..., description="Full commit SHA")
    message: str = Field(..., description="Commit message")
    author: Optional[Dict[str, Any]] = Field(None, description="Author info (name, email, date)")
    committer: Optional[Dict[str, Any]] = Field(None, description="Committer info")
    html_url: Optional[str] = Field(None, description="GitHub URL for the commit")


class MergeBaseResponse(BaseModel):
    """Response for GET /api/dashboard/repositories/{id}/merge-base.

    Returns the common ancestor commit between source and target branches,
    along with metadata about how the branches have diverged.
    """
    merge_base_sha: str = Field(..., description="SHA of the merge base commit")
    merge_base_commit: MergeBaseCommitInfo = Field(
        ...,
        description="Full merge base commit information"
    )
    source_branch: str = Field(..., description="Source branch name")
    target_branch: str = Field(..., description="Target branch name")
    source_sha: Optional[str] = Field(None, description="Current SHA of source branch HEAD")
    target_sha: Optional[str] = Field(None, description="Current SHA of target branch HEAD")
    ahead_by: int = Field(
        ...,
        ge=0,
        description="Number of commits source is ahead of merge base"
    )
    behind_by: int = Field(
        ...,
        ge=0,
        description="Number of commits target is ahead of merge base"
    )
    status: str = Field(
        ...,
        description="Comparison status: 'ahead', 'behind', 'diverged', or 'identical'"
    )
    total_commits: int = Field(
        ...,
        ge=0,
        description="Total commits between the branches"
    )
    version_manifest: Dict[str, Any] = Field(
        ...,
        description="Version manifest for debugging"
    )


# =============================================================================
# File History Endpoints (Phase 2)
# =============================================================================

class CommitAuthorInfo(BaseModel):
    """Author information for a commit."""
    name: str = Field(..., description="Author name")
    email: str = Field(..., description="Author email")
    avatar_url: Optional[str] = Field(None, description="GitHub avatar URL")


class FileCommitInfo(BaseModel):
    """Information about a commit that modified a file."""
    sha: str = Field(..., description="Full commit SHA")
    short_sha: str = Field(..., description="Short commit SHA (7 chars)")
    message: str = Field(..., description="Commit message (first line)")
    author: CommitAuthorInfo = Field(..., description="Commit author info")
    date: str = Field(..., description="Commit date (ISO 8601 format)")
    url: str = Field(..., description="GitHub URL for the commit")


class BranchHistory(BaseModel):
    """Commit history for a file on a specific branch."""
    commits: List[FileCommitInfo] = Field(
        default_factory=list,
        description="List of commits affecting the file"
    )
    total_count: int = Field(..., ge=0, description="Total number of commits")
    has_more: bool = Field(
        False,
        description="Whether there are more commits beyond the limit"
    )


class FileHistoryResponse(BaseModel):
    """Response for GET /api/dashboard/repositories/{id}/file-history.

    Returns commit history for a specific file across multiple branches.
    """
    path: str = Field(..., description="File path in repository")
    branches: Dict[str, BranchHistory] = Field(
        ...,
        description="Commit history grouped by branch name"
    )
    version_manifest: Dict[str, Any] = Field(
        ...,
        description="Version manifest for debugging"
    )
