"""
Dashboard API Router.

Provides REST endpoints for the web dashboard to:
- List repositories and branches from GitHub installations
- Create and manage MergeContexts
- Trigger CRE resolution and apply results

All endpoints include version_manifest for debugging per Phase 0 requirements.
"""

import logging
import math
from typing import Optional
from uuid import UUID
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_config
from app.database.session import get_db
from app.middleware.dashboard_auth import require_auth
from app.models.installation import GitHubInstallation
from app.clients.github_client import (
    GitHubAPIClient,
    BranchNotFoundError,
    TreeResolutionError,
)
from app.services.branch_service import get_branch_service
from app.services.merge_context_service import get_merge_context_service
from app.services.version_manifest import get_version_manifest_service
from app.models.version_manifest import OriginInfo
from app.api.schemas.dashboard import (
    RepositoriesResponse,
    InstallationRepositories,
    RepositoryInfo,
    AccountInfo,
    BranchesResponse,
    BranchInfo,
    CommitSummary,
    CreateMergeContextRequest,
    MergeContextResponse,
    BranchConfig,
    ConflictSet,
    Resolution,
    ResolveRequest,
    ApplyRequest,
    ApplyResponse,
    ApplyResult,
    PaginationInfo,
    ErrorResponse,
    FileTreeItem,
    FileTreeResponse,
    FileContentResponse,
    MergeBaseResponse,
    MergeBaseCommitInfo,
    FileHistoryResponse,
    BranchHistory,
    FileCommitInfo,
    CommitAuthorInfo,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# =============================================================================
# Helper Functions
# =============================================================================

async def get_version_manifest(request: Request) -> dict:
    """Collect version manifest for response embedding."""
    version_service = get_version_manifest_service()

    # Build origin from request
    origin = OriginInfo(
        source="web_dashboard",
        source_version=request.headers.get("X-Dashboard-Version", "1.0.0"),
        user_agent=request.headers.get("User-Agent"),
        correlation_id=request.headers.get("X-Correlation-ID", str(UUID(int=0)))
    )

    manifest = await version_service.collect(origin=origin, include_config=True)
    return manifest.model_dump()


# =============================================================================
# Repository Endpoints
# =============================================================================

@router.get(
    "/repositories",
    response_model=RepositoriesResponse,
    responses={
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def list_repositories(
    request: Request,
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    installation_id: Optional[int] = Query(
        None, description="Filter by installation ID"
    ),
    user_id: UUID = Depends(require_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    List repositories accessible to the user's GitHub installations.

    Returns repositories grouped by installation (organization/user account).
    Requires authentication via dashboard token.

    If installation_id is not provided, returns repositories for all
    of the user's active installations.
    """
    logger.info(
        "Listing repositories",
        extra={
            "user_id": str(user_id),
            "page": page,
            "per_page": per_page,
            "installation_id": installation_id
        }
    )

    try:
        # Query user's installations from database
        query = select(GitHubInstallation).where(
            GitHubInstallation.user_id == user_id,
            GitHubInstallation.is_active == True
        )

        # Filter by specific installation if requested
        if installation_id:
            query = query.where(GitHubInstallation.installation_id == installation_id)

        result = await db.execute(query)
        user_installations = result.scalars().all()

        # If no installations found, return empty list
        if not user_installations:
            version_manifest = await get_version_manifest(request)
            return RepositoriesResponse(
                repositories=[],
                pagination=PaginationInfo(
                    page=page,
                    per_page=per_page,
                    total=0,
                    total_pages=0
                ),
                version_manifest=version_manifest
            )

        # Fetch repositories for each installation
        all_installation_repos = []
        total_repos = 0

        for inst in user_installations:
            try:
                async with GitHubAPIClient(inst.installation_id) as client:
                    # Get repositories for this installation
                    repos_data = await client.list_installation_repositories()

                    # Build repository list
                    repos = []
                    for repo in repos_data:
                        repos.append(RepositoryInfo(
                            id=repo["id"],
                            name=repo["name"],
                            full_name=repo["full_name"],
                            private=repo.get("private", False),
                            default_branch=repo.get("default_branch", "main"),
                            language=repo.get("language"),
                            updated_at=datetime.fromisoformat(
                                repo["updated_at"].replace("Z", "+00:00")
                            ) if repo.get("updated_at") else None
                        ))

                    # Build account info from database record
                    account = AccountInfo(
                        login=inst.account_login,
                        type=inst.account_type,
                        avatar_url=None  # Not stored in our DB
                    )

                    installation_repos = InstallationRepositories(
                        installation_id=inst.installation_id,
                        account=account,
                        repositories=repos,
                        repository_selection="selected",  # Default
                        permissions=inst.permissions or {}
                    )

                    all_installation_repos.append(installation_repos)
                    total_repos += len(repos)

            except Exception as e:
                logger.warning(
                    f"Failed to fetch repos for installation {inst.installation_id}: {e}"
                )
                # Continue with other installations even if one fails
                continue

        version_manifest = await get_version_manifest(request)

        return RepositoriesResponse(
            repositories=all_installation_repos,
            pagination=PaginationInfo(
                page=page,
                per_page=per_page,
                total=total_repos,
                total_pages=max(1, math.ceil(total_repos / per_page))
            ),
            version_manifest=version_manifest
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list repositories: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list repositories: {str(e)}"
        )


@router.get(
    "/repositories/{repository_id}/branches",
    response_model=BranchesResponse,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def list_branches(
    request: Request,
    repository_id: int,
    installation_id: int = Query(..., description="GitHub installation ID"),
    repository: str = Query(..., description="Full repository name (owner/repo)"),
    page: int = Query(1, ge=1, description="Deprecated: pagination now auto-handled"),
    per_page: int = Query(30, ge=1, le=100, description="Deprecated: pagination now auto-handled"),
    protected_only: bool = Query(False, description="Only return protected branches")
):
    """
    List branches for a repository.

    P1-F2: Returns ALL branches with automatic pagination up to MAX_BRANCHES_FETCH.
    The `page` and `per_page` parameters are deprecated but kept for backward compatibility.
    Use the `limited` and `max_limit` fields in the response to check if results were capped.
    """
    logger.info(
        "Listing branches",
        extra={
            "repository_id": repository_id,
            "installation_id": installation_id,
            "repository": repository
        }
    )

    try:
        branch_service = get_branch_service()

        # P1-F2: Use list_branches_all for automatic pagination
        result = await branch_service.list_branches_all(
            installation_id=installation_id,
            repository_full_name=repository,
            protected_only=protected_only
        )

        # Get default branch
        owner, repo = repository.split("/")
        async with GitHubAPIClient(installation_id) as client:
            repo_info = await client.get_repository(owner, repo)
            default_branch = repo_info.get("default_branch")
            if not default_branch:
                logger.warning(
                    f"GitHub API did not return default_branch for {repository}, "
                    f"falling back to 'main'"
                )
                default_branch = "main"

        # Convert to response schema
        branch_infos = []
        for branch in result.branches:
            commit = None
            if branch.commit_message:
                commit = CommitSummary(
                    sha=branch.sha,
                    message=branch.commit_message,
                    author=branch.commit_author,
                    date=branch.commit_date
                )

            branch_infos.append(BranchInfo(
                name=branch.name,
                sha=branch.sha,
                protected=branch.protected,
                is_default=branch.is_default,
                commit=commit
            ))

        version_manifest = await get_version_manifest(request)

        return BranchesResponse(
            installation_id=installation_id,
            repository=repository,
            default_branch=default_branch,
            branches=branch_infos,
            pagination=PaginationInfo(
                page=1,  # All results in single page now
                per_page=result.total,  # All items returned
                total=result.total,
                total_pages=1  # Single page with all results
            ),
            limited=result.limited,
            max_limit=result.max_limit,
            version_manifest=version_manifest
        )

    except Exception as e:
        logger.error(f"Failed to list branches: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list branches: {str(e)}"
        )


@router.get(
    "/repositories/{repository_id}/tree",
    response_model=FileTreeResponse,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def get_file_tree(
    request: Request,
    repository_id: int,
    installation_id: int = Query(..., description="GitHub installation ID"),
    branch: str = Query(..., description="Branch name to get tree from"),
    path: str = Query("", description="Optional path prefix to filter tree")
):
    """
    Get file tree for a repository branch.

    Returns the directory structure of the repository at the specified branch.
    If path is provided, only returns items under that path.
    """
    logger.info(
        "Getting file tree",
        extra={
            "repository_id": repository_id,
            "installation_id": installation_id,
            "branch": branch,
            "path": path
        }
    )

    try:
        async with GitHubAPIClient(installation_id) as client:
            # First, get repository info to get owner/repo
            repos = await client.list_installation_repositories()
            repo_info = next(
                (r for r in repos if r["id"] == repository_id),
                None
            )

            if not repo_info:
                logger.warning(
                    "[FILE_TREE] Repository not found",
                    extra={"repository_id": repository_id, "repo_count": len(repos)}
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Repository not found: {repository_id}"
                )

            owner, repo = repo_info["full_name"].split("/")
            logger.info(
                "[FILE_TREE] Resolved repository",
                extra={"owner": owner, "repo": repo, "branch": branch}
            )

            # Properly resolve branch name to tree SHA
            # GitHub's Git Trees API does NOT reliably resolve branch names
            # for non-default branches, so we must resolve explicitly
            try:
                logger.info(
                    "[FILE_TREE] Calling get_branch_tree_sha",
                    extra={"owner": owner, "repo": repo, "branch": branch}
                )
                tree_sha = await client.get_branch_tree_sha(
                    owner=owner,
                    repo=repo,
                    branch=branch
                )
                logger.info(
                    "[FILE_TREE] Got tree SHA",
                    extra={"branch": branch, "tree_sha": tree_sha[:7] if tree_sha else "N/A"}
                )
            except BranchNotFoundError:
                logger.warning(
                    "[FILE_TREE] Branch not found",
                    extra={"owner": owner, "repo": repo, "branch": branch}
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Branch '{branch}' not found in repository"
                )
            except TreeResolutionError as e:
                logger.warning(
                    "[FILE_TREE] Tree resolution error",
                    extra={"owner": owner, "repo": repo, "branch": branch, "error": str(e)}
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Could not resolve branch '{branch}': {str(e)}"
                )

            # Now fetch the tree using the resolved SHA
            logger.info(
                "[FILE_TREE] Calling get_tree",
                extra={"owner": owner, "repo": repo, "tree_sha": tree_sha[:7]}
            )
            tree_data = await client.get_tree(
                owner=owner,
                repo=repo,
                tree_sha=tree_sha,
                recursive=True
            )
            logger.info(
                "[FILE_TREE] Got tree data",
                extra={
                    "branch": branch,
                    "item_count": len(tree_data.get("tree", [])),
                    "truncated": tree_data.get("truncated", False)
                }
            )

        # Transform GitHub tree format to our format
        # GitHub returns: {path, mode, type ('blob'/'tree'), sha, size}
        # We need: {name, path, type ('file'/'dir'), size, sha}
        tree_items = []
        for item in tree_data.get("tree", []):
            # Skip if path filter is specified and item doesn't match
            if path and not item["path"].startswith(path):
                continue

            # Extract name from path
            name = item["path"].split("/")[-1]

            # Map GitHub type to our type
            item_type = "file" if item["type"] == "blob" else "dir"

            tree_items.append(FileTreeItem(
                name=name,
                path=item["path"],
                type=item_type,
                size=item.get("size"),
                sha=item["sha"]
            ))

        version_manifest = await get_version_manifest(request)

        logger.info(
            "[FILE_TREE] Returning response",
            extra={
                "branch": branch,
                "tree_item_count": len(tree_items),
                "truncated": tree_data.get("truncated", False),
                "tree_sha": tree_data.get("sha", "")[:7] if tree_data.get("sha") else "N/A"
            }
        )

        return FileTreeResponse(
            tree=tree_items,
            truncated=tree_data.get("truncated", False),
            sha=tree_data.get("sha", ""),
            version_manifest=version_manifest
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FILE_TREE] Failed to get file tree: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get file tree: {str(e)}"
        )


@router.get(
    "/repositories/{repository_id}/content",
    response_model=FileContentResponse,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def get_file_content(
    request: Request,
    repository_id: int,
    installation_id: int = Query(..., description="GitHub installation ID"),
    branch: str = Query(..., description="Branch name"),
    path: str = Query(..., description="File path in repository")
):
    """
    Get file content from a repository branch.

    Returns the content of a file at the specified path and branch.
    Content is returned as base64-encoded string.
    """
    logger.info(
        "Getting file content",
        extra={
            "repository_id": repository_id,
            "installation_id": installation_id,
            "branch": branch,
            "path": path
        }
    )

    try:
        async with GitHubAPIClient(installation_id) as client:
            # First, get repository info to get owner/repo
            repos = await client.list_installation_repositories()
            repo_info = next(
                (r for r in repos if r["id"] == repository_id),
                None
            )

            if not repo_info:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Repository not found: {repository_id}"
                )

            owner, repo = repo_info["full_name"].split("/")

            # Get file content (raw, with metadata)
            file_data = await client.get_file_contents_raw(
                owner=owner,
                repo=repo,
                path=path,
                ref=branch
            )

        # GitHub returns content with newlines for formatting
        # Remove them to get clean base64
        content = file_data.get("content", "").replace("\n", "")

        version_manifest = await get_version_manifest(request)

        return FileContentResponse(
            content=content,
            encoding=file_data.get("encoding", "base64"),
            sha=file_data.get("sha", ""),
            size=file_data.get("size", 0),
            path=path,
            version_manifest=version_manifest
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get file content: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get file content: {str(e)}"
        )


@router.get(
    "/repositories/{repository_id}/merge-base",
    response_model=MergeBaseResponse,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def get_merge_base(
    request: Request,
    repository_id: int,
    installation_id: int = Query(..., description="GitHub installation ID"),
    source_branch: str = Query(..., description="Source branch name"),
    target_branch: str = Query(..., description="Target branch name")
):
    """
    Get the merge base (common ancestor) between two branches.

    Returns the common ancestor commit between the source and target branches,
    along with metadata about how the branches have diverged.

    This is useful for:
    - Determining the base content for 3-way merge resolution
    - Understanding how far branches have diverged
    - Detecting if branches have already been merged
    """
    logger.info(
        "Getting merge base",
        extra={
            "repository_id": repository_id,
            "installation_id": installation_id,
            "source_branch": source_branch,
            "target_branch": target_branch
        }
    )

    try:
        async with GitHubAPIClient(installation_id) as client:
            # First, get repository info to get owner/repo
            repos = await client.list_installation_repositories()
            repo_info = next(
                (r for r in repos if r["id"] == repository_id),
                None
            )

            if not repo_info:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Repository not found: {repository_id}"
                )

            owner, repo = repo_info["full_name"].split("/")

            # Get merge base using the compare API
            merge_base_data = await client.get_merge_base(
                owner=owner,
                repo=repo,
                source_branch=source_branch,
                target_branch=target_branch
            )

        # Build commit info from the response
        mb_commit = merge_base_data.get("merge_base_commit", {})
        commit_info = MergeBaseCommitInfo(
            sha=mb_commit.get("sha", ""),
            message=mb_commit.get("message", ""),
            author=mb_commit.get("author"),
            committer=mb_commit.get("committer"),
            html_url=mb_commit.get("html_url")
        )

        version_manifest = await get_version_manifest(request)

        return MergeBaseResponse(
            merge_base_sha=merge_base_data.get("merge_base_sha", ""),
            merge_base_commit=commit_info,
            source_branch=source_branch,
            target_branch=target_branch,
            source_sha=merge_base_data.get("source_sha"),
            target_sha=merge_base_data.get("target_sha"),
            ahead_by=merge_base_data.get("ahead_by", 0),
            behind_by=merge_base_data.get("behind_by", 0),
            status=merge_base_data.get("status", "unknown"),
            total_commits=merge_base_data.get("total_commits", 0),
            version_manifest=version_manifest
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get merge base: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get merge base: {str(e)}"
        )


@router.get(
    "/repositories/{repository_id}/file-history",
    response_model=FileHistoryResponse,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def get_file_history(
    request: Request,
    repository_id: int,
    installation_id: int = Query(..., description="GitHub installation ID"),
    path: str = Query(..., description="File path in repository"),
    branches: list[str] = Query(..., description="Branch names to get history for"),
    limit: int = Query(20, ge=1, le=100, description="Max commits per branch")
):
    """
    Get commit history for a specific file across multiple branches.

    Returns the commits that modified the specified file on each branch,
    allowing users to understand when and why the file changed.

    This is useful for:
    - Understanding file evolution across branches
    - Identifying who made conflicting changes
    - Providing context for merge conflict resolution
    """
    logger.info(
        "Getting file history",
        extra={
            "repository_id": repository_id,
            "installation_id": installation_id,
            "path": path,
            "branches": branches,
            "limit": limit
        }
    )

    try:
        async with GitHubAPIClient(installation_id) as client:
            # First, get repository info to get owner/repo
            repos = await client.list_installation_repositories()
            repo_info = next(
                (r for r in repos if r["id"] == repository_id),
                None
            )

            if not repo_info:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Repository not found: {repository_id}"
                )

            owner, repo = repo_info["full_name"].split("/")

            # Fetch commit history for each branch
            branches_history = {}

            for branch in branches:
                try:
                    commits_data = await client.get_commits_for_file(
                        owner=owner,
                        repo=repo,
                        path=path,
                        branch=branch,
                        limit=limit
                    )

                    # Transform commits to our schema
                    commits = []
                    for c in commits_data:
                        commit_info = c.get("commit", {})
                        author_info = commit_info.get("author", {})
                        github_author = c.get("author")  # GitHub user object

                        commits.append(FileCommitInfo(
                            sha=c["sha"],
                            short_sha=c["sha"][:7],
                            message=commit_info.get("message", "").split("\n")[0],
                            author=CommitAuthorInfo(
                                name=author_info.get("name", "Unknown"),
                                email=author_info.get("email", ""),
                                avatar_url=github_author.get("avatar_url") if github_author else None
                            ),
                            date=author_info.get("date", ""),
                            url=c.get("html_url", "")
                        ))

                    branches_history[branch] = BranchHistory(
                        commits=commits,
                        total_count=len(commits),
                        has_more=len(commits) >= limit
                    )

                except Exception as e:
                    logger.warning(
                        f"Failed to get file history for branch {branch}: {e}"
                    )
                    # Return empty history for this branch instead of failing
                    branches_history[branch] = BranchHistory(
                        commits=[],
                        total_count=0,
                        has_more=False
                    )

        version_manifest = await get_version_manifest(request)

        return FileHistoryResponse(
            path=path,
            branches=branches_history,
            version_manifest=version_manifest
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get file history: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get file history: {str(e)}"
        )


# =============================================================================
# Device Discovery Endpoints (Phase 4 - Remote-based device discovery)
# =============================================================================

@router.get(
    "/repos/by-remote/{provider}/{owner}/{name}/devices",
    responses={
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def get_devices_for_remote(
    request: Request,
    provider: str,
    owner: str,
    name: str,
    user_id: UUID = Depends(require_auth),
):
    """
    Get all devices watching a specific remote repository.

    This endpoint proxies to the Public API's device discovery endpoint,
    passing the authenticated user's token. It returns all devices across
    all users that are tracking the specified remote repository.

    This enables team-wide visibility in the dashboard - showing all clones
    of a repository regardless of which user registered them.

    Args:
        provider: Git provider (e.g., 'github', 'gitlab')
        owner: Repository owner/organization
        name: Repository name

    Returns:
        DevicesByRemoteResponse with list of devices and aggregated counts
    """
    logger.info(
        "Getting devices for remote",
        extra={
            "user_id": str(user_id),
            "provider": provider,
            "owner": owner,
            "repo_name": name
        }
    )

    try:
        config = get_config()
        public_api_url = f"{config.public_api_url}/v1/repos/by-remote/{provider}/{owner}/{name}/devices"

        # Get the authenticated token from request state
        token = getattr(request.state, "token", None)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authentication token"
            )

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                public_api_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Correlation-ID": request.headers.get("X-Correlation-ID", ""),
                }
            )

            if response.status_code == 200:
                data = response.json()

                # Add version manifest
                version_manifest = await get_version_manifest(request)
                data["version_manifest"] = version_manifest

                return data

            elif response.status_code == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token rejected by Public API"
                )
            else:
                logger.error(
                    f"Public API returned error: {response.status_code}",
                    extra={"response_text": response.text[:500]}
                )
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Public API error: {response.text[:200]}"
                )

    except HTTPException:
        raise
    except httpx.TimeoutException:
        logger.error("Public API request timed out")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Public API request timed out"
        )
    except Exception as e:
        logger.error(f"Failed to get devices for remote: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get devices: {str(e)}"
        )


# =============================================================================
# MergeContext Endpoints
# =============================================================================

@router.post(
    "/merge-context",
    response_model=MergeContextResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def create_merge_context(
    request: Request,
    body: CreateMergeContextRequest
):
    """
    Create a new MergeContext from GitHub branches.

    This endpoint:
    1. Validates the specified branches exist
    2. Finds the merge-base commit
    3. Detects any conflicts between branches
    4. Creates a MergeContext for resolution

    The MergeContext can then be used to trigger CRE resolution
    and apply the results to the repository.
    """
    logger.info(
        "Creating MergeContext",
        extra={
            "installation_id": body.installation_id,
            "repository": body.repository,
            "base_branch": body.base_branch,
            "source_branch": body.source_branch
        }
    )

    try:
        # Build origin info
        origin = OriginInfo(
            source="web_dashboard",
            source_version=request.headers.get("X-Dashboard-Version", "1.0.0"),
            user_agent=request.headers.get("User-Agent"),
            correlation_id=request.headers.get("X-Correlation-ID")
        )

        # Create MergeContext
        service = get_merge_context_service()
        merge_context = await service.create(
            installation_id=body.installation_id,
            repository_full_name=body.repository,
            base_branch=body.base_branch,
            source_branch=body.source_branch,
            user_id=None,  # TODO: Get from authenticated user
            options=body.options.model_dump() if body.options else None,
            origin=origin
        )

        return _build_merge_context_response(merge_context)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to create MergeContext: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create MergeContext: {str(e)}"
        )


@router.get(
    "/merge-context/{merge_context_id}",
    response_model=MergeContextResponse,
    responses={
        404: {"model": ErrorResponse}
    }
)
async def get_merge_context(
    request: Request,
    merge_context_id: UUID
):
    """
    Get a MergeContext by ID.

    Returns the full MergeContext including conflict information,
    resolution status, and version manifest.
    """
    logger.info(
        "Getting MergeContext",
        extra={"merge_context_id": str(merge_context_id)}
    )

    service = get_merge_context_service()
    merge_context = await service.get_by_id(merge_context_id)

    if not merge_context:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MergeContext not found: {merge_context_id}"
        )

    return _build_merge_context_response(merge_context)


@router.post(
    "/merge-context/{merge_context_id}/resolve",
    response_model=MergeContextResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def resolve_merge_context(
    request: Request,
    merge_context_id: UUID,
    body: Optional[ResolveRequest] = None
):
    """
    Trigger CRE resolution for a MergeContext.

    Sends the MergeContext to the Conflict Resolution Engine (CRE)
    for automated resolution. The response includes the resolution
    result with confidence scores and resolved file content.

    Prerequisites:
    - MergeContext must be in PENDING or CONFLICT_DETECTED status
    """
    logger.info(
        "Resolving MergeContext",
        extra={"merge_context_id": str(merge_context_id)}
    )

    try:
        service = get_merge_context_service()
        merge_context = await service.resolve(
            merge_context_id=merge_context_id,
            options=body.options if body else None
        )

        return _build_merge_context_response(merge_context)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to resolve MergeContext: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Resolution failed: {str(e)}"
        )


@router.post(
    "/merge-context/{merge_context_id}/apply",
    response_model=ApplyResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def apply_merge_context(
    request: Request,
    merge_context_id: UUID,
    body: ApplyRequest
):
    """
    Apply resolution to the repository.

    Applies the CRE resolution by committing the resolved files
    to the specified target branch. Optionally creates a pull request.

    Prerequisites:
    - MergeContext must be in RESOLVED status
    - User must have write access to the repository
    """
    logger.info(
        "Applying MergeContext",
        extra={
            "merge_context_id": str(merge_context_id),
            "target_branch": body.target_branch
        }
    )

    try:
        service = get_merge_context_service()
        result = await service.apply(
            merge_context_id=merge_context_id,
            target_branch=body.target_branch,
            commit_message=body.commit_message,
            create_pull_request=body.create_pull_request
        )

        version_manifest = await get_version_manifest(request)

        return ApplyResponse(
            id=str(merge_context_id),
            status="applied",
            status_message=f"Resolution applied to {body.target_branch}",
            apply_result=ApplyResult(
                commit_sha=result["commit_sha"],
                branch=result["branch"],
                files_modified=result["files_modified"],
                pull_request_url=result.get("pull_request_url")
            ),
            version_manifest=version_manifest
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to apply MergeContext: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Application failed: {str(e)}"
        )


@router.delete(
    "/merge-context/{merge_context_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": ErrorResponse}
    }
)
async def delete_merge_context(
    merge_context_id: UUID
):
    """
    Delete a MergeContext (soft delete).

    Marks the MergeContext as deleted. It will no longer appear
    in list queries but remains in the database for auditing.
    """
    logger.info(
        "Deleting MergeContext",
        extra={"merge_context_id": str(merge_context_id)}
    )

    service = get_merge_context_service()
    deleted = await service.delete(merge_context_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MergeContext not found: {merge_context_id}"
        )

    return None


# =============================================================================
# Helper Functions
# =============================================================================

def _build_merge_context_response(merge_context) -> MergeContextResponse:
    """Build MergeContextResponse from MergeContext model."""
    # Build BranchConfig
    bc = merge_context.branch_config
    branch_config = BranchConfig(
        base_branch=bc["base_branch"],
        source_branch=bc["source_branch"],
        target_branch=bc.get("target_branch"),
        base_sha=bc["base_sha"],
        source_sha=bc["source_sha"],
        target_sha=bc.get("target_sha"),
        merge_base_sha=bc["merge_base_sha"],
        commits_ahead_source=bc.get("commits_ahead_source"),
        commits_ahead_target=bc.get("commits_ahead_target")
    )

    # Build ConflictSet if present
    conflict_set = None
    if merge_context.conflict_set:
        cs = merge_context.conflict_set
        conflict_set = ConflictSet(
            total_conflicts=cs.get("total_conflicts", 0),
            total_files_affected=cs.get("total_files_affected", 0),
            conflicts=cs.get("conflicts", []),
            by_file=cs.get("by_file", {})
        )

    # Build Resolution if present
    resolution = None
    if merge_context.resolution:
        r = merge_context.resolution
        resolution = Resolution(
            status=r.get("status", "unknown"),
            overall_confidence=r.get("overall_confidence", 0.0),
            resolved_files=r.get("resolved_files", {}),
            confidence_regions=r.get("confidence_regions"),
            strategies_used=r.get("strategies_used"),
            metadata=r.get("metadata", {})
        )

    return MergeContextResponse(
        id=str(merge_context.id),
        status=merge_context.status.value,
        status_message=merge_context.status_message,
        source_type=merge_context.source_type.value,
        installation_id=merge_context.installation_id,
        repository_full_name=merge_context.repository_full_name,
        branch_config=branch_config,
        commit_graph=merge_context.commit_graph,
        conflict_set=conflict_set,
        resolution=resolution,
        processing_duration_ms=merge_context.processing_duration_ms,
        created_at=merge_context.created_at,
        updated_at=merge_context.updated_at,
        version_manifest=merge_context.version_manifest
    )
