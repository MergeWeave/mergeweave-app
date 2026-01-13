"""
Configuration API Router.

Provides REST endpoints for managing repository configuration (.mergeweave.yaml):
- Get repository configuration
- Update/create configuration
- Delete configuration
- Validate configuration
- Check branch tracking rules
- Check auto-resolve eligibility

Phase 4 - Configuration & Production Readiness.
"""

import logging
from typing import Optional
import base64

import yaml
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.database.session import get_db
from app.clients.github_client import GitHubAPIClient
from app.services.config_parser import (
    ConfigParser,
    get_config_parser,
    MergeWeaveConfig,
)
from app.services.version_manifest import get_version_manifest_service
from app.api.schemas.config import (
    ConfigResponse,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
    ConfigDeleteResponse,
    ConfigValidateRequest,
    ConfigValidationResponse,
    ValidationError,
    BranchTrackingCheckRequest,
    BranchTrackingCheckResponse,
    AutoResolveCheckRequest,
    AutoResolveCheckResponse,
    FileIgnoreCheckRequest,
    FileIgnoreCheckResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard/config", tags=["configuration"])


# =============================================================================
# Get Configuration
# =============================================================================


@router.get(
    "/{installation_id}/{owner}/{repo}",
    response_model=ConfigResponse,
    responses={
        401: {"description": "Unauthorized"},
        404: {"description": "Repository not found"},
        500: {"description": "Internal server error"},
    }
)
async def get_repository_config(
    installation_id: int,
    owner: str,
    repo: str,
    ref: str = Query("HEAD", description="Git ref to read config from"),
    force_refresh: bool = Query(False, description="Bypass cache"),
    request: Request = None,
):
    """
    Get configuration for a repository.

    Returns the parsed .mergeweave.yaml file or default configuration
    if no config file exists in the repository.

    Args:
        installation_id: GitHub App installation ID
        owner: Repository owner
        repo: Repository name
        ref: Git ref to read config from (branch, tag, or SHA)
        force_refresh: If true, bypass cache and fetch fresh config

    Returns:
        ConfigResponse with parsed configuration and source info
    """
    repository = f"{owner}/{repo}"

    logger.info(
        "Getting repository config",
        extra={
            "installation_id": installation_id,
            "repository": repository,
            "ref": ref,
            "force_refresh": force_refresh,
        }
    )

    parser = get_config_parser()

    try:
        config = await parser.get_config(
            installation_id=installation_id,
            repository=repository,
            ref=ref,
            force_refresh=force_refresh,
        )

        # Determine if config came from file or defaults
        # Try to fetch the file to see if it exists
        config_content = await parser._fetch_config_file(
            installation_id=installation_id,
            repository=repository,
            ref=ref,
        )

        source = "file" if config_content else "default"
        file_path = ".mergeweave.yaml" if config_content else None

        return ConfigResponse(
            repository=repository,
            config=config,
            source=source,
            file_path=file_path,
            ref=ref,
        )

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository {repository} not found or not accessible",
            )
        logger.error(f"GitHub API error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch repository configuration",
        )
    except Exception as e:
        logger.error(f"Error fetching config: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch repository configuration",
        )


# =============================================================================
# Update Configuration
# =============================================================================


@router.put(
    "/{installation_id}/{owner}/{repo}",
    response_model=ConfigUpdateResponse,
    responses={
        400: {"description": "Invalid configuration"},
        401: {"description": "Unauthorized"},
        404: {"description": "Repository not found"},
        500: {"description": "Internal server error"},
    }
)
async def update_repository_config(
    installation_id: int,
    owner: str,
    repo: str,
    update_request: ConfigUpdateRequest,
    request: Request = None,
):
    """
    Update repository configuration.

    Creates or updates .mergeweave.yaml in the repository.
    The configuration is validated before saving.

    Args:
        installation_id: GitHub App installation ID
        owner: Repository owner
        repo: Repository name
        update_request: Configuration update request body

    Returns:
        ConfigUpdateResponse with commit details
    """
    repository = f"{owner}/{repo}"

    logger.info(
        "Updating repository config",
        extra={
            "installation_id": installation_id,
            "repository": repository,
            "branch": update_request.branch,
        }
    )

    # Validate configuration
    try:
        config = MergeWeaveConfig(**update_request.config)
    except Exception as e:
        logger.warning(f"Invalid configuration: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid configuration: {str(e)}",
        )

    # Serialize to YAML
    config_dict = config.model_dump(exclude_none=True)
    config_yaml = yaml.dump(
        config_dict,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )

    # Add header comment
    header = f"# MergeWeave Configuration\n# Version: {config.version}\n# Generated by MergeWeave Dashboard\n\n"
    config_content = header + config_yaml

    # Encode content as base64
    content_base64 = base64.b64encode(config_content.encode("utf-8")).decode("utf-8")

    # Commit message
    commit_message = update_request.commit_message or "Update MergeWeave configuration"

    try:
        async with GitHubAPIClient(installation_id) as client:
            # Check if file already exists to get its SHA
            existing_sha = None
            try:
                # GitHub API requires base64 content, but get_file_contents decodes it
                # We need to make a raw request to get the SHA
                endpoint = f"/repos/{owner}/{repo}/contents/.mergeweave.yaml"
                response = await client._make_request(
                    "GET",
                    endpoint,
                    params={"ref": update_request.branch}
                )
                existing_sha = response.get("sha")
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 404:
                    raise

            # Create or update file
            result = await client.update_file_contents(
                owner=owner,
                repo=repo,
                path=".mergeweave.yaml",
                content=content_base64,
                message=commit_message,
                branch=update_request.branch,
                sha=existing_sha,
            )

            # Clear cache for this repository
            parser = get_config_parser()
            await parser.clear_cache(repository)

            return ConfigUpdateResponse(
                success=True,
                commit_sha=result["commit"]["sha"],
                file_url=result["content"]["html_url"],
                message="Configuration updated successfully",
            )

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository {repository} not found or not accessible",
            )
        elif e.response.status_code == 422:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to update configuration. Check branch exists and you have write access.",
            )
        logger.error(f"GitHub API error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update configuration",
        )
    except Exception as e:
        logger.error(f"Error updating config: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update configuration",
        )


# =============================================================================
# Delete Configuration
# =============================================================================


@router.delete(
    "/{installation_id}/{owner}/{repo}",
    response_model=ConfigDeleteResponse,
    responses={
        401: {"description": "Unauthorized"},
        404: {"description": "Configuration file not found"},
        500: {"description": "Internal server error"},
    }
)
async def delete_repository_config(
    installation_id: int,
    owner: str,
    repo: str,
    branch: str = Query("main", description="Branch to delete config from"),
    request: Request = None,
):
    """
    Delete repository configuration.

    Removes .mergeweave.yaml from the repository.

    Args:
        installation_id: GitHub App installation ID
        owner: Repository owner
        repo: Repository name
        branch: Branch to delete config from

    Returns:
        ConfigDeleteResponse with commit details
    """
    repository = f"{owner}/{repo}"

    logger.info(
        "Deleting repository config",
        extra={
            "installation_id": installation_id,
            "repository": repository,
            "branch": branch,
        }
    )

    try:
        async with GitHubAPIClient(installation_id) as client:
            # Get file SHA (required for deletion)
            try:
                endpoint = f"/repos/{owner}/{repo}/contents/.mergeweave.yaml"
                response = await client._make_request(
                    "GET",
                    endpoint,
                    params={"ref": branch}
                )
                file_sha = response["sha"]
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Configuration file not found in repository",
                    )
                raise

            # Delete file
            result = await client._make_request(
                "DELETE",
                endpoint,
                json={
                    "message": "Remove MergeWeave configuration",
                    "sha": file_sha,
                    "branch": branch,
                }
            )

            # Clear cache
            parser = get_config_parser()
            await parser.clear_cache(repository)

            return ConfigDeleteResponse(
                success=True,
                commit_sha=result["commit"]["sha"],
                message="Configuration deleted successfully",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting config: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete configuration",
        )


# =============================================================================
# Validate Configuration
# =============================================================================


@router.post(
    "/validate",
    response_model=ConfigValidationResponse,
    responses={
        400: {"description": "Invalid request"},
    }
)
async def validate_config(
    validate_request: ConfigValidateRequest,
    request: Request = None,
):
    """
    Validate a configuration without saving.

    Parses the configuration and returns any validation errors.

    Args:
        validate_request: Configuration to validate

    Returns:
        ConfigValidationResponse with validation results
    """
    errors: list[ValidationError] = []

    try:
        config = MergeWeaveConfig(**validate_request.config)
        return ConfigValidationResponse(
            valid=True,
            errors=[],
            config=config,
        )
    except Exception as e:
        # Parse Pydantic validation errors
        if hasattr(e, "errors"):
            for error in e.errors():
                errors.append(ValidationError(
                    path=".".join(str(p) for p in error.get("loc", [])),
                    message=error.get("msg", str(error)),
                    error_type=error.get("type", "validation_error"),
                ))
        else:
            errors.append(ValidationError(
                path="",
                message=str(e),
                error_type="validation_error",
            ))

        return ConfigValidationResponse(
            valid=False,
            errors=errors,
            config=None,
        )


# =============================================================================
# Branch Tracking Check
# =============================================================================


@router.post(
    "/{installation_id}/{owner}/{repo}/check-branch",
    response_model=BranchTrackingCheckResponse,
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "Repository not found"},
    }
)
async def check_branch_tracking(
    installation_id: int,
    owner: str,
    repo: str,
    check_request: BranchTrackingCheckRequest,
    request: Request = None,
):
    """
    Check if a branch should be tracked for conflicts.

    Uses either the provided config or fetches the repository's config.

    Args:
        installation_id: GitHub App installation ID
        owner: Repository owner
        repo: Repository name
        check_request: Branch to check and optional config

    Returns:
        BranchTrackingCheckResponse indicating if branch should be tracked
    """
    repository = f"{owner}/{repo}"
    parser = get_config_parser()

    # Get config - either from request or repository
    if check_request.config:
        try:
            config = MergeWeaveConfig(**check_request.config)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid configuration: {str(e)}",
            )
    else:
        config = await parser.get_config(installation_id, repository)

    # Check branch tracking
    should_track = parser.should_track_branch(config, check_request.branch_name)

    # Determine reason
    reason = ""
    matched_pattern = None

    if not should_track:
        # Check exclude patterns
        from fnmatch import fnmatch
        for pattern in config.branches.exclude:
            if fnmatch(check_request.branch_name, pattern):
                reason = f"Excluded by pattern: {pattern}"
                matched_pattern = pattern
                break
        else:
            if config.branches.include:
                reason = "Does not match any include pattern"
            else:
                reason = "Branch tracking disabled by default"
    else:
        # Check why it's tracked
        from fnmatch import fnmatch
        for pattern in config.branches.protected:
            if fnmatch(check_request.branch_name, pattern):
                reason = f"Protected branch matching: {pattern}"
                matched_pattern = pattern
                break
        else:
            for pattern in config.branches.include:
                if fnmatch(check_request.branch_name, pattern):
                    reason = f"Included by pattern: {pattern}"
                    matched_pattern = pattern
                    break
            else:
                reason = "Tracked by default configuration"

    return BranchTrackingCheckResponse(
        branch_name=check_request.branch_name,
        should_track=should_track,
        reason=reason,
        matched_pattern=matched_pattern,
    )


# =============================================================================
# Auto-Resolve Check
# =============================================================================


@router.post(
    "/{installation_id}/{owner}/{repo}/check-auto-resolve",
    response_model=AutoResolveCheckResponse,
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "Repository not found"},
    }
)
async def check_auto_resolve(
    installation_id: int,
    owner: str,
    repo: str,
    check_request: AutoResolveCheckRequest,
    request: Request = None,
):
    """
    Check if a conflict can be auto-resolved.

    Evaluates the file path, conflict type, and confidence against
    the repository's auto-resolve rules.

    Args:
        installation_id: GitHub App installation ID
        owner: Repository owner
        repo: Repository name
        check_request: Conflict details to check

    Returns:
        AutoResolveCheckResponse indicating if auto-resolve is allowed
    """
    repository = f"{owner}/{repo}"
    parser = get_config_parser()

    # Get config
    if check_request.config:
        try:
            config = MergeWeaveConfig(**check_request.config)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid configuration: {str(e)}",
            )
    else:
        config = await parser.get_config(installation_id, repository)

    # Check auto-resolve eligibility
    can_auto_resolve = parser.should_auto_resolve(
        config,
        check_request.file_path,
        check_request.conflict_type,
        check_request.confidence,
    )

    # Determine reason
    auto_config = config.resolution.auto_resolve
    reason = ""

    if not config.branches.default.auto_resolve:
        reason = "Auto-resolve is disabled globally"
    elif check_request.confidence < auto_config.min_confidence:
        reason = f"Confidence {check_request.confidence:.2f} below threshold {auto_config.min_confidence}"
    elif check_request.conflict_type in auto_config.require_review:
        reason = f"Conflict type '{check_request.conflict_type}' requires manual review"
    else:
        from fnmatch import fnmatch
        for pattern in auto_config.never_auto_resolve:
            if fnmatch(check_request.file_path, pattern):
                reason = f"File matches never_auto_resolve pattern: {pattern}"
                break
        else:
            if can_auto_resolve:
                reason = "Meets all auto-resolve criteria"
            else:
                reason = "Does not meet auto-resolve criteria"

    return AutoResolveCheckResponse(
        file_path=check_request.file_path,
        can_auto_resolve=can_auto_resolve,
        reason=reason,
        confidence_threshold=auto_config.min_confidence,
    )


# =============================================================================
# File Ignore Check
# =============================================================================


@router.post(
    "/{installation_id}/{owner}/{repo}/check-ignore",
    response_model=FileIgnoreCheckResponse,
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "Repository not found"},
    }
)
async def check_file_ignore(
    installation_id: int,
    owner: str,
    repo: str,
    check_request: FileIgnoreCheckRequest,
    request: Request = None,
):
    """
    Check if a file should be ignored for conflict detection.

    Evaluates the file path and size against the repository's
    ignore rules.

    Args:
        installation_id: GitHub App installation ID
        owner: Repository owner
        repo: Repository name
        check_request: File details to check

    Returns:
        FileIgnoreCheckResponse indicating if file should be ignored
    """
    repository = f"{owner}/{repo}"
    parser = get_config_parser()

    # Get config
    if check_request.config:
        try:
            config = MergeWeaveConfig(**check_request.config)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid configuration: {str(e)}",
            )
    else:
        config = await parser.get_config(installation_id, repository)

    # Check if file should be ignored
    should_ignore = parser.should_ignore_file(
        config,
        check_request.file_path,
        check_request.file_size,
    )

    # Determine reason
    reason = ""
    matched_pattern = None

    if check_request.file_size > config.detection.max_file_size:
        reason = f"File size {check_request.file_size} exceeds limit {config.detection.max_file_size}"
    else:
        from fnmatch import fnmatch
        for pattern in config.detection.ignore_patterns:
            if fnmatch(check_request.file_path, pattern):
                reason = f"Matches ignore pattern: {pattern}"
                matched_pattern = pattern
                break
        else:
            if should_ignore:
                reason = "Matches ignore criteria"
            else:
                reason = "File will be analyzed"

    return FileIgnoreCheckResponse(
        file_path=check_request.file_path,
        should_ignore=should_ignore,
        reason=reason,
        matched_pattern=matched_pattern,
    )
