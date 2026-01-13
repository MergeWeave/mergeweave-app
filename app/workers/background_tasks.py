"""
Background Task Workers for Async Processing.

Handles long-running operations like conflict detection and CRE integration
without blocking webhook responses.

Updated for Phase 3: Uses GitHub Check Runs instead of commit comments.
"""

import logging
import uuid
import asyncio
import secrets
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import AsyncSessionLocal
from app.models.installation import GitHubInstallation
from app.models.conflict import ConflictDetection
from app.models.resolution import ResolutionResult, ResolutionStatus
from app.models.resolution_package import ResolutionPackage
from app.services.conflict_detection import ConflictDetectionService
from app.services.cre_client import CREClient
from app.services.resolution_storage import ResolutionStorageService
from app.services.github_notifier import GitHubNotifier
from app.clients.github_client import GitHubAPIClient
from app.utils.comment_formatting import format_conflict_comment, format_error_comment
from app.config import get_config

logger = logging.getLogger(__name__)

# Package ID prefix for resolution packages
PACKAGE_ID_PREFIX = "pkg_"
MAX_PACKAGE_ID_RETRIES = 5


def generate_package_id() -> str:
    """Generate a package ID for resolution packages.

    Format: pkg_<16 base64url chars> = 20 chars total
    This fits GitHub Check Run action identifier limit (20 chars max).
    12 bytes = 16 base64url chars = 96 bits of entropy.
    """
    return f"{PACKAGE_ID_PREFIX}{secrets.token_urlsafe(12)}"


async def generate_unique_package_id(db: AsyncSession) -> str:
    """Generate a unique package ID, retrying on collision.

    Although collisions are astronomically unlikely (96 bits of entropy),
    this provides defensive handling in case one occurs.

    Args:
        db: Database session for checking existing IDs

    Returns:
        str: A unique package ID

    Raises:
        RuntimeError: If unable to generate unique ID after MAX_PACKAGE_ID_RETRIES attempts
    """
    for attempt in range(MAX_PACKAGE_ID_RETRIES):
        package_id = generate_package_id()

        # Check if this ID already exists
        stmt = select(ResolutionPackage).where(
            ResolutionPackage.package_id == package_id
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()

        if existing is None:
            return package_id

        # Collision detected (extremely rare)
        logger.warning(
            f"Package ID collision detected, regenerating",
            extra={
                "package_id": package_id,
                "attempt": attempt + 1,
                "max_retries": MAX_PACKAGE_ID_RETRIES
            }
        )

    # All retries exhausted (should essentially never happen)
    logger.error(
        f"Failed to generate unique package ID after {MAX_PACKAGE_ID_RETRIES} attempts"
    )
    raise RuntimeError(
        f"Unable to generate unique package ID after {MAX_PACKAGE_ID_RETRIES} attempts"
    )


async def queue_conflict_detection(
    installation_id: int,
    repository_full_name: str,
    source_branch: str,
    target_branch: str,
    head_sha: str,
    webhook_delivery_id: str,
    retry_count: int = 0
):
    """
    Background task: Detect conflicts and process with CRE.

    Process (Updated for Check Runs):
    1. Create GitHub Check Run (in_progress)
    2. Detect conflicts using ConflictDetectionService
    3. Save ConflictDetection record to database
    4. Update Check Run with results:
       - No conflicts: success
       - Conflicts + resolution: action_required with Apply button
       - Conflicts without resolution: failure
       - Error: failure with error message

    Args:
        installation_id: GitHub installation ID
        repository_full_name: Repository (owner/repo)
        source_branch: Source branch being merged
        target_branch: Target branch (base)
        head_sha: Head commit SHA
        webhook_delivery_id: GitHub delivery ID
        retry_count: Retry attempt number (for error handling)
    """
    owner, repo = repository_full_name.split("/")
    notifier = GitHubNotifier(installation_id)
    check_run_id: Optional[int] = None

    logger.info(
        f"Starting conflict detection task",
        extra={
            "installation_id": installation_id,
            "repository": repository_full_name,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "delivery_id": webhook_delivery_id,
            "retry": retry_count
        }
    )

    try:
        # Step 1: Create Check Run (in_progress)
        check_run_id = await notifier.notify_detection_started(
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            source_branch=source_branch,
            target_branch=target_branch
        )

        logger.info(
            f"Check run created",
            extra={
                "check_run_id": check_run_id,
                "repository": repository_full_name
            }
        )

        # Step 2: Detect conflicts
        detection_service = ConflictDetectionService()

        result = await detection_service.detect_conflicts(
            installation_id=installation_id,
            repository_full_name=repository_full_name,
            source_branch=source_branch,
            target_branch=target_branch
        )

        # Step 3: Save to database
        async with AsyncSessionLocal() as db:
            # Get installation DB record
            stmt = select(GitHubInstallation).where(
                GitHubInstallation.installation_id == installation_id
            )
            db_installation = (await db.execute(stmt)).scalar_one_or_none()

            if not db_installation:
                logger.error(
                    f"Installation not found in database",
                    extra={"installation_id": installation_id}
                )
                # Still update check run with error
                await notifier.notify_error(
                    owner=owner,
                    repo=repo,
                    check_run_id=check_run_id,
                    error_message="Installation not found in database",
                    source_branch=source_branch,
                    target_branch=target_branch
                )
                return

            # Create conflict detection record
            conflict_record = ConflictDetection(
                installation_id=db_installation.id,  # DB FK
                repository_full_name=repository_full_name,
                target_ref=target_branch,
                source_ref=source_branch,
                base_sha=result.base_sha,
                commit_sha=result.head_sha,
                conflicted_files=result.conflicted_files,
                has_conflicts=result.has_conflicts,
                webhook_delivery_id=webhook_delivery_id,
                event_type="push",
                processing_duration_ms=result.processing_duration_ms,
                detected_at=datetime.now(timezone.utc)
            )

            db.add(conflict_record)
            await db.commit()
            await db.refresh(conflict_record)

            logger.info(
                f"Conflict detection saved to database",
                extra={
                    "conflict_id": conflict_record.id,
                    "has_conflicts": result.has_conflicts
                }
            )

        # Step 4: Update Check Run based on results
        if not result.has_conflicts:
            # No conflicts - update check run with success
            await notifier.notify_no_conflicts(
                owner=owner,
                repo=repo,
                check_run_id=check_run_id,
                source_branch=source_branch,
                target_branch=target_branch,
                processing_time_ms=result.processing_duration_ms
            )
        else:
            # Conflicts detected - process with CRE
            await process_with_cre(
                conflict_id=conflict_record.id,
                installation_id=installation_id,
                repository=repository_full_name,
                source_branch=source_branch,
                target_branch=target_branch,
                head_sha=head_sha,
                conflict_content=result.conflict_content,
                check_run_id=check_run_id,
                processing_duration_ms=result.processing_duration_ms
            )

    except Exception as e:
        logger.error(
            f"Conflict detection task failed",
            extra={
                "repository": repository_full_name,
                "retry_count": retry_count,
                "error": str(e)
            },
            exc_info=True
        )

        # Update check run with error (if we have one)
        if check_run_id:
            try:
                await notifier.notify_error(
                    owner=owner,
                    repo=repo,
                    check_run_id=check_run_id,
                    error_message=str(e),
                    source_branch=source_branch,
                    target_branch=target_branch
                )
            except Exception as notify_err:
                logger.error(
                    f"Failed to update check run with error",
                    extra={"error": str(notify_err)}
                )

        # Retry logic (up to 3 attempts)
        if retry_count < 3:
            logger.info(f"Retrying conflict detection (attempt {retry_count + 1})")
            await asyncio.sleep(2 ** retry_count)  # Exponential backoff
            await queue_conflict_detection(
                installation_id=installation_id,
                repository_full_name=repository_full_name,
                source_branch=source_branch,
                target_branch=target_branch,
                head_sha=head_sha,
                webhook_delivery_id=webhook_delivery_id,
                retry_count=retry_count + 1
            )
        else:
            logger.error(f"Conflict detection failed after 3 retries")

            # Create check run with error if we don't have one
            if not check_run_id:
                try:
                    await notifier.create_check_run_for_error(
                        owner=owner,
                        repo=repo,
                        head_sha=head_sha,
                        error_message=str(e),
                        source_branch=source_branch,
                        target_branch=target_branch
                    )
                except Exception as notify_err:
                    logger.error(
                        f"Failed to create error check run",
                        extra={"error": str(notify_err)}
                    )


async def process_with_cre(
    conflict_id: int,
    installation_id: int,
    repository: str,
    source_branch: str,
    target_branch: str,
    head_sha: str,
    conflict_content: Dict[str, str],
    check_run_id: int,
    processing_duration_ms: int
):
    """
    Background task: Process conflicts with CRE and store results.

    Process (Updated for Check Runs):
    1. Call CRE API with conflict data
    2. Store resolution package in database (ResolutionPackage model)
    3. Create ResolutionResult database record
    4. Update Check Run with action button for applying resolution

    Args:
        conflict_id: ConflictDetection record ID
        installation_id: GitHub installation ID
        repository: Repository full name
        source_branch: Source branch
        target_branch: Target branch
        head_sha: Head commit SHA
        conflict_content: Dict of file paths to content with markers
        check_run_id: GitHub Check Run ID to update
        processing_duration_ms: Time spent on conflict detection
    """
    owner, repo = repository.split("/")
    notifier = GitHubNotifier(installation_id)

    logger.info(
        f"Starting CRE processing",
        extra={
            "conflict_id": conflict_id,
            "repository": repository,
            "conflict_count": len(conflict_content),
            "check_run_id": check_run_id
        }
    )

    config = get_config()
    cre_client = CREClient()
    storage_service = ResolutionStorageService()

    try:
        # Step 1: Call CRE API (new endpoint: /v1/conflicts/resolve/package)
        # The new API auto-detects conflicts from branch comparison
        cre_result = await cre_client.resolve_conflicts(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            source_branch=source_branch,
            target_branch=target_branch
        )

        # Total processing time
        total_processing_ms = processing_duration_ms + (cre_result.processing_time_ms or 0)

        # Step 2 & 3: Handle CRE result
        async with AsyncSessionLocal() as db:
            if cre_result.success:
                # Generate unique package ID for click-to-accept workflow
                package_id = await generate_unique_package_id(db)

                # Store resolution in S3 (existing storage)
                url, key, size = await storage_service.store_resolution(
                    resolution_id=package_id,
                    resolved_files=cre_result.resolved_files,
                    metadata={
                        "strategy": cre_result.strategy_used,
                        "confidence_score": cre_result.confidence_score,
                        "repository": repository,
                        "source_branch": source_branch,
                        "target_branch": target_branch
                    }
                )

                # Store resolution package in database for retrieval
                resolution_package = ResolutionPackage(
                    package_id=package_id,
                    installation_id=installation_id,
                    repository_owner=owner,
                    repository_name=repo,
                    source_branch=source_branch,
                    source_commit_sha=head_sha,
                    target_branch=target_branch,
                    package_data={
                        "resolved_files": cre_result.resolved_files,
                        "strategy_used": cre_result.strategy_used,
                        "confidence_score": cre_result.confidence_score,
                        "conflicted_files": list(conflict_content.keys()),
                        "storage_url": url,
                        "storage_key": key
                    },
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                    check_run_id=check_run_id
                )

                db.add(resolution_package)

                # Create ResolutionResult record (historical tracking)
                resolution_record = ResolutionResult(
                    conflict_id=conflict_id,
                    status="pending",  # Pending user action
                    strategy_used=cre_result.strategy_used,
                    resolved_files=list(cre_result.resolved_files.keys()),
                    unresolved_files=[],
                    resolution_dag=None,
                    confidence_score=cre_result.confidence_score,
                    processing_time_ms=cre_result.processing_time_ms,
                    resolution_id=package_id,
                    resolution_package_url=url,
                    resolution_package_size=size
                )

                db.add(resolution_record)
                await db.commit()

                logger.info(
                    f"Resolution package stored",
                    extra={
                        "package_id": package_id,
                        "url": url,
                        "check_run_id": check_run_id
                    }
                )

                # Step 4: Update Check Run with action button
                await notifier.notify_conflicts_detected(
                    owner=owner,
                    repo=repo,
                    check_run_id=check_run_id,
                    source_branch=source_branch,
                    target_branch=target_branch,
                    conflicted_files=list(conflict_content.keys()),
                    package_id=package_id,
                    confidence_score=cre_result.confidence_score,
                    processing_time_ms=total_processing_ms
                )

            else:
                # CRE failed - create failed resolution record
                resolution_record = ResolutionResult(
                    conflict_id=conflict_id,
                    status="failed",
                    error_message=cre_result.error_message,
                    processing_time_ms=cre_result.processing_time_ms
                )

                db.add(resolution_record)
                await db.commit()

                logger.warning(
                    f"CRE resolution failed",
                    extra={"error": cre_result.error_message}
                )

                # Update Check Run - conflicts without resolution
                await notifier.notify_conflicts_no_resolution(
                    owner=owner,
                    repo=repo,
                    check_run_id=check_run_id,
                    source_branch=source_branch,
                    target_branch=target_branch,
                    conflicted_files=list(conflict_content.keys()),
                    error_message=cre_result.error_message
                )

    except Exception as e:
        logger.error(
            f"CRE processing failed",
            extra={
                "conflict_id": conflict_id,
                "error": str(e)
            },
            exc_info=True
        )

        # Create failed resolution record
        async with AsyncSessionLocal() as db:
            resolution_record = ResolutionResult(
                conflict_id=conflict_id,
                status="failed",
                error_message=f"CRE processing error: {str(e)}"
            )
            db.add(resolution_record)
            await db.commit()

        # Update check run with error
        try:
            await notifier.notify_conflicts_no_resolution(
                owner=owner,
                repo=repo,
                check_run_id=check_run_id,
                source_branch=source_branch,
                target_branch=target_branch,
                conflicted_files=list(conflict_content.keys()),
                error_message=str(e)
            )
        except Exception as notify_err:
            logger.error(
                f"Failed to update check run after CRE error",
                extra={"error": str(notify_err)}
            )


# =========================================================================
# Legacy Helper Functions (kept for backward compatibility)
# =========================================================================

async def _post_no_conflicts_comment(
    installation_id: int,
    repository: str,
    head_sha: str,
    source_branch: str,
    target_branch: str
):
    """Post 'no conflicts' comment to GitHub (legacy)."""
    comment_body = format_conflict_comment(
        repository=repository,
        source_branch=source_branch,
        target_branch=target_branch,
        has_conflicts=False
    )

    async with GitHubAPIClient(installation_id) as client:
        owner, repo = repository.split("/")
        await client.post_commit_comment(
            owner=owner,
            repo=repo,
            sha=head_sha,
            body=comment_body
        )


async def _post_resolution_comment(
    installation_id: int,
    repository: str,
    head_sha: str,
    source_branch: str,
    target_branch: str,
    conflicted_files: List[str],
    resolution_id: str,
    confidence_score: str
) -> str:
    """Post resolution comment with Apply Fix link (legacy)."""
    config = get_config()

    comment_body = format_conflict_comment(
        repository=repository,
        source_branch=source_branch,
        target_branch=target_branch,
        has_conflicts=True,
        conflicted_files=conflicted_files,
        resolution_id=resolution_id,
        confidence_score=confidence_score,
        app_base_url=config._get_app_base_url()
    )

    async with GitHubAPIClient(installation_id) as client:
        owner, repo = repository.split("/")
        response = await client.post_commit_comment(
            owner=owner,
            repo=repo,
            sha=head_sha,
            body=comment_body
        )
        return response.get("html_url", "")


async def _post_conflicts_no_resolution_comment(
    installation_id: int,
    repository: str,
    head_sha: str,
    source_branch: str,
    target_branch: str,
    conflicted_files: List[str]
):
    """Post conflicts detected comment (no resolution available) (legacy)."""
    comment_body = format_conflict_comment(
        repository=repository,
        source_branch=source_branch,
        target_branch=target_branch,
        has_conflicts=True,
        conflicted_files=conflicted_files
    )

    async with GitHubAPIClient(installation_id) as client:
        owner, repo = repository.split("/")
        await client.post_commit_comment(
            owner=owner,
            repo=repo,
            sha=head_sha,
            body=comment_body
        )


async def _post_error_comment(
    installation_id: int,
    repository: str,
    head_sha: str,
    source_branch: str,
    target_branch: str,
    error: str
):
    """Post error comment to GitHub (legacy)."""
    comment_body = format_error_comment(
        repository=repository,
        source_branch=source_branch,
        target_branch=target_branch,
        error_message=error
    )

    async with GitHubAPIClient(installation_id) as client:
        owner, repo = repository.split("/")
        await client.post_commit_comment(
            owner=owner,
            repo=repo,
            sha=head_sha,
            body=comment_body
        )
