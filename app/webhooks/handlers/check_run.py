"""
Check Run Webhook Handler.

Handles GitHub check_run events, specifically the requested_action action
which occurs when users click action buttons on check runs.

Implements the click-to-accept workflow for resolution application.
"""

import logging
from typing import Dict, Optional
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.webhooks.router import webhook_handler
from app.database.session import AsyncSessionLocal
from app.models.resolution_package import ResolutionPackage
from app.models.resolution import ResolutionResult, ResolutionStatus
from app.services.github_notifier import GitHubNotifier
from app.services.resolution_application import ResolutionApplicationService
from app.clients.github_client import GitHubAPIClient

logger = logging.getLogger(__name__)

# Package ID prefix (pkg_) identifies resolution actions
PACKAGE_ID_PREFIX = "pkg_"


@webhook_handler("check_run")
async def handle_check_run_event(payload: Dict, delivery_id: str):
    """
    Handle GitHub check_run webhook events.

    Only processes 'requested_action' action (button clicks).
    Other actions (created, completed, rerequested) are ignored.

    Args:
        payload: GitHub webhook payload
        delivery_id: GitHub delivery ID for tracking

    Flow:
    1. Validate action type is 'requested_action'
    2. Parse action identifier to get package_id
    3. Retrieve resolution package from database
    4. Validate user has write permission
    5. Apply resolution (create commit)
    6. Update check run with success status
    """
    action = payload.get("action")

    # Only handle button clicks (requested_action)
    if action != "requested_action":
        logger.debug(
            f"Ignoring check_run action: {action}",
            extra={
                "delivery_id": delivery_id,
                "action": action
            }
        )
        return

    # Extract required data
    check_run = payload.get("check_run", {})
    requested_action = payload.get("requested_action", {})
    repository = payload.get("repository", {})
    sender = payload.get("sender", {})
    installation = payload.get("installation", {})

    check_run_id = check_run.get("id")
    action_identifier = requested_action.get("identifier", "")
    owner = repository.get("owner", {}).get("login")
    repo = repository.get("name")
    repository_full_name = repository.get("full_name")
    sender_login = sender.get("login")
    installation_id = installation.get("id")

    logger.info(
        f"Received check_run requested_action",
        extra={
            "delivery_id": delivery_id,
            "check_run_id": check_run_id,
            "action_identifier": action_identifier,
            "repository": repository_full_name,
            "sender": sender_login,
            "installation_id": installation_id
        }
    )

    # Validate we have all required data
    if not all([check_run_id, action_identifier, owner, repo, sender_login, installation_id]):
        logger.error(
            f"Missing required data in check_run payload",
            extra={
                "delivery_id": delivery_id,
                "check_run_id": check_run_id,
                "action_identifier": action_identifier
            }
        )
        return

    # Parse action identifier - should be a package_id (pkg_<16 chars>)
    if not action_identifier.startswith(PACKAGE_ID_PREFIX):
        logger.warning(
            f"Unknown action identifier format (expected pkg_...)",
            extra={
                "delivery_id": delivery_id,
                "action_identifier": action_identifier
            }
        )
        return

    # The identifier IS the package_id directly
    package_id = action_identifier

    logger.info(
        f"Processing resolution application request",
        extra={
            "delivery_id": delivery_id,
            "package_id": package_id,
            "sender": sender_login
        }
    )

    # Process the resolution application
    await process_resolution_application(
        installation_id=installation_id,
        owner=owner,
        repo=repo,
        check_run_id=check_run_id,
        package_id=package_id,
        sender_login=sender_login,
        delivery_id=delivery_id
    )


async def process_resolution_application(
    installation_id: int,
    owner: str,
    repo: str,
    check_run_id: int,
    package_id: str,
    sender_login: str,
    delivery_id: str
):
    """
    Process a resolution application request.

    Steps:
    1. Retrieve resolution package from database
    2. Validate package is not expired or already applied
    3. Validate user has write permission
    4. Check if branch is protected
    5. Apply resolution:
       - If protected: create branch + PR
       - If not protected: commit directly
    6. Update check run and database

    Args:
        installation_id: GitHub installation ID
        owner: Repository owner
        repo: Repository name
        check_run_id: GitHub check run ID
        package_id: Resolution package ID
        sender_login: GitHub username who clicked the button
        delivery_id: Webhook delivery ID for logging
    """
    notifier = GitHubNotifier(installation_id)
    application_service = ResolutionApplicationService(installation_id)

    try:
        # Step 1: Retrieve resolution package
        async with AsyncSessionLocal() as db:
            stmt = select(ResolutionPackage).where(
                ResolutionPackage.package_id == package_id
            )
            package = (await db.execute(stmt)).scalar_one_or_none()

            if not package:
                logger.error(
                    f"Resolution package not found",
                    extra={
                        "delivery_id": delivery_id,
                        "package_id": package_id
                    }
                )
                # Update check run with error
                await _update_check_run_with_error(
                    notifier=notifier,
                    owner=owner,
                    repo=repo,
                    check_run_id=check_run_id,
                    error_message="Resolution package not found. It may have been deleted."
                )
                return

            # Step 2: Validate package state
            if package.is_expired:
                logger.warning(
                    f"Resolution package expired",
                    extra={
                        "delivery_id": delivery_id,
                        "package_id": package_id,
                        "expires_at": package.expires_at.isoformat()
                    }
                )
                await _update_check_run_with_error(
                    notifier=notifier,
                    owner=owner,
                    repo=repo,
                    check_run_id=check_run_id,
                    error_message="Resolution package has expired. Please push again to generate a new resolution."
                )
                return

            if package.is_applied:
                logger.warning(
                    f"Resolution package already applied",
                    extra={
                        "delivery_id": delivery_id,
                        "package_id": package_id,
                        "applied_by": package.applied_by,
                        "applied_at": package.applied_at.isoformat()
                    }
                )
                await _update_check_run_with_error(
                    notifier=notifier,
                    owner=owner,
                    repo=repo,
                    check_run_id=check_run_id,
                    error_message=f"Resolution was already applied by @{package.applied_by}"
                )
                return

            # Step 3: Validate user has write permission
            has_permission = await application_service.validate_user_permission(
                owner=owner,
                repo=repo,
                username=sender_login
            )

            if not has_permission:
                logger.warning(
                    f"User lacks write permission",
                    extra={
                        "delivery_id": delivery_id,
                        "package_id": package_id,
                        "sender": sender_login
                    }
                )
                await _update_check_run_with_error(
                    notifier=notifier,
                    owner=owner,
                    repo=repo,
                    check_run_id=check_run_id,
                    error_message=f"@{sender_login} does not have write permission to this repository."
                )
                return

            # Step 4: Apply resolution (try direct commit, fallback to PR if protected)
            # This approach avoids needing administration:read permission to check
            # branch protection. We try direct commit first, and if it fails due to
            # branch protection, we automatically create a PR instead.

            logger.info(
                f"Attempting to apply resolution directly",
                extra={
                    "delivery_id": delivery_id,
                    "package_id": package_id,
                    "sender": sender_login,
                    "source_branch": package.source_branch
                }
            )

            direct_commit_succeeded = False
            commit_sha = None

            try:
                commit_sha = await application_service.apply_resolution(
                    owner=owner,
                    repo=repo,
                    package=package
                )
                direct_commit_succeeded = True

            except Exception as apply_error:
                # Check if this looks like a branch protection error
                error_str = str(apply_error).lower()
                is_protection_error = any(phrase in error_str for phrase in [
                    "protected branch",
                    "403",
                    "422",
                    "reference update failed",
                    "required status check",
                    "required review",
                    "force push",
                    "restrictions"
                ])

                if is_protection_error:
                    logger.info(
                        f"Direct commit blocked (likely branch protection), falling back to PR",
                        extra={
                            "delivery_id": delivery_id,
                            "package_id": package_id,
                            "error_hint": error_str[:200]
                        }
                    )
                else:
                    # Not a protection error - log it but still try PR as fallback
                    logger.warning(
                        f"Direct commit failed with unexpected error, attempting PR fallback",
                        extra={
                            "delivery_id": delivery_id,
                            "package_id": package_id,
                            "error": str(apply_error)
                        },
                        exc_info=True
                    )

            if direct_commit_succeeded:
                # Direct commit worked - update database and check run
                package.applied_at = datetime.now(timezone.utc)
                package.applied_by = sender_login
                await db.commit()

                await _update_resolution_result_status(
                    db=db,
                    package_id=package_id,
                    status="applied"
                )

                logger.info(
                    f"Resolution applied successfully via direct commit",
                    extra={
                        "delivery_id": delivery_id,
                        "package_id": package_id,
                        "commit_sha": commit_sha,
                        "applied_by": sender_login
                    }
                )

                # Update check run with success
                resolved_files = package.package_data.get("conflicted_files", [])
                await notifier.notify_resolution_applied(
                    owner=owner,
                    repo=repo,
                    check_run_id=check_run_id,
                    applied_by=sender_login,
                    commit_sha=commit_sha,
                    resolved_files=resolved_files
                )

            else:
                # Direct commit failed - try PR approach
                logger.info(
                    f"Creating PR for resolution (branch may be protected)",
                    extra={
                        "delivery_id": delivery_id,
                        "package_id": package_id,
                        "source_branch": package.source_branch
                    }
                )

                result = await _apply_resolution_via_pr(
                    installation_id=installation_id,
                    owner=owner,
                    repo=repo,
                    package=package,
                    sender_login=sender_login,
                    delivery_id=delivery_id
                )

                if not result:
                    await _update_check_run_with_error(
                        notifier=notifier,
                        owner=owner,
                        repo=repo,
                        check_run_id=check_run_id,
                        error_message="Failed to apply resolution. Please try again or resolve manually."
                    )
                    return

                pr_url, commit_sha, pr_number = result

                # Update database
                package.applied_at = datetime.now(timezone.utc)
                package.applied_by = sender_login
                await db.commit()

                await _update_resolution_result_status(
                    db=db,
                    package_id=package_id,
                    status="applied_via_pr"
                )

                # Update check run with PR info
                resolved_files = package.package_data.get("conflicted_files", [])
                await _update_check_run_with_pr(
                    notifier=notifier,
                    owner=owner,
                    repo=repo,
                    check_run_id=check_run_id,
                    applied_by=sender_login,
                    pr_url=pr_url,
                    pr_number=pr_number,
                    resolved_files=resolved_files
                )

                logger.info(
                    f"Resolution applied via PR",
                    extra={
                        "delivery_id": delivery_id,
                        "package_id": package_id,
                        "pr_url": pr_url,
                        "applied_by": sender_login
                    }
                )

    except Exception as e:
        logger.error(
            f"Error processing resolution application",
            extra={
                "delivery_id": delivery_id,
                "package_id": package_id,
                "error": str(e)
            },
            exc_info=True
        )

        # Try to update check run with error
        try:
            await _update_check_run_with_error(
                notifier=notifier,
                owner=owner,
                repo=repo,
                check_run_id=check_run_id,
                error_message=f"An unexpected error occurred: {str(e)}"
            )
        except Exception as notify_err:
            logger.error(
                f"Failed to update check run with error",
                extra={"error": str(notify_err)}
            )


async def _check_branch_protection(
    installation_id: int,
    owner: str,
    repo: str,
    branch: str
) -> bool:
    """
    Check if a branch has protection rules.

    Args:
        installation_id: GitHub installation ID
        owner: Repository owner
        repo: Repository name
        branch: Branch to check

    Returns:
        bool: True if branch is protected
    """
    async with GitHubAPIClient(installation_id) as client:
        return await client.is_branch_protected(
            owner=owner,
            repo=repo,
            branch=branch
        )


async def _apply_resolution_via_pr(
    installation_id: int,
    owner: str,
    repo: str,
    package: ResolutionPackage,
    sender_login: str,
    delivery_id: str
) -> Optional[tuple]:
    """
    Apply resolution by creating a new branch and PR for protected branches.

    Args:
        installation_id: GitHub installation ID
        owner: Repository owner
        repo: Repository name
        package: Resolution package
        sender_login: User who triggered the action
        delivery_id: Webhook delivery ID

    Returns:
        Optional[tuple]: (pr_url, commit_sha, pr_number) if successful, None otherwise
    """
    application_service = ResolutionApplicationService(installation_id)
    source_branch = package.source_branch

    # Generate unique branch name
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    package_suffix = package.package_id[-8:]  # Last 8 chars of package ID
    resolution_branch = f"mergeweave/resolution-{timestamp}-{package_suffix}"

    try:
        async with GitHubAPIClient(installation_id) as client:
            # Step 1: Create new branch from source
            logger.info(
                f"Creating resolution branch for protected branch",
                extra={
                    "delivery_id": delivery_id,
                    "source_branch": source_branch,
                    "resolution_branch": resolution_branch
                }
            )

            await client.create_branch(
                owner=owner,
                repo=repo,
                new_branch=resolution_branch,
                from_ref=source_branch
            )

            # Step 2: Apply resolution to the new branch
            # We need to modify the package temporarily to target the new branch
            original_branch = package.source_branch
            package.source_branch = resolution_branch

            try:
                commit_sha = await application_service.apply_resolution(
                    owner=owner,
                    repo=repo,
                    package=package
                )
            finally:
                # Restore original branch
                package.source_branch = original_branch

            # Step 3: Create PR from resolution branch to source branch
            resolved_files = package.package_data.get("conflicted_files", [])
            pr_body = _generate_pr_body(
                resolved_files=resolved_files,
                package=package,
                sender_login=sender_login
            )

            pr = await client.create_pull_request(
                owner=owner,
                repo=repo,
                title=f"MergeWeave: Resolve {len(resolved_files)} conflict(s)",
                head=resolution_branch,
                base=source_branch,
                body=pr_body
            )

            pr_url = pr.get("html_url")
            pr_number = pr.get("number")

            logger.info(
                f"Created PR for protected branch resolution",
                extra={
                    "delivery_id": delivery_id,
                    "pr_number": pr_number,
                    "pr_url": pr_url,
                    "commit_sha": commit_sha[:7]
                }
            )

            return (pr_url, commit_sha, pr_number)

    except Exception as e:
        logger.error(
            f"Failed to apply resolution via PR",
            extra={
                "delivery_id": delivery_id,
                "package_id": package.package_id,
                "source_branch": source_branch,
                "error": str(e)
            },
            exc_info=True
        )
        return None


def _generate_pr_body(
    resolved_files: list,
    package: ResolutionPackage,
    sender_login: str
) -> str:
    """
    Generate PR body with resolution details and diff preview.

    Args:
        resolved_files: List of resolved file paths
        package: Resolution package
        sender_login: User who triggered the resolution

    Returns:
        str: PR body in Markdown format
    """
    file_count = len(resolved_files)
    confidence = package.package_data.get("confidence_score", "N/A")
    strategy = package.package_data.get("strategy_used", "auto")

    # Format file list
    files_list = "\n".join(f"- `{f}`" for f in resolved_files[:10])
    if len(resolved_files) > 10:
        files_list += f"\n- ... and {len(resolved_files) - 10} more files"

    return f"""## 🤖 MergeWeave Automated Resolution

This PR contains MergeWeave's automated resolution for **{file_count}** conflicted file(s).

### Resolution Summary

| Metric | Value |
|--------|-------|
| **Files Resolved** | {file_count} |
| **Confidence Score** | {confidence} |
| **Strategy Used** | {strategy} |
| **Requested By** | @{sender_login} |

### Files Changed

{files_list}

### ⚠️ Protected Branch

This resolution is delivered as a PR because `{package.source_branch}` has branch protection rules enabled.

**To apply the resolution:** Review the changes and merge this PR.

### How to Review

1. Check the **Files changed** tab to review MergeWeave's resolution
2. If changes look correct, approve and merge
3. If changes need adjustment, modify before merging or close this PR

---
🤖 *Generated by [MergeWeave](https://mergeweave.cloud) | Package ID: `{package.package_id}`*
"""


async def _update_check_run_with_pr(
    notifier: GitHubNotifier,
    owner: str,
    repo: str,
    check_run_id: int,
    applied_by: str,
    pr_url: str,
    pr_number: int,
    resolved_files: list
):
    """Update check run when resolution is applied via PR."""
    async with GitHubAPIClient(notifier.installation_id) as client:
        file_count = len(resolved_files)
        files_list = "\n".join(f"- `{f}`" for f in resolved_files[:10])
        if len(resolved_files) > 10:
            files_list += f"\n- ... and {len(resolved_files) - 10} more files"

        await client.update_check_run(
            owner=owner,
            repo=repo,
            check_run_id=check_run_id,
            status="completed",
            conclusion="success",
            title="Resolution PR Created",
            summary=f"""## ✅ Resolution PR Created

**Requested by:** @{applied_by}
**Pull Request:** [#{pr_number}]({pr_url})
**Files Resolved:** {file_count}

Because this branch has protection rules, the resolution has been created as a pull request.

### Resolved Files

{files_list}

### Next Steps

1. Review the changes in [PR #{pr_number}]({pr_url})
2. Approve and merge to apply the resolution

---
*Resolution created by [MergeWeave](https://mergeweave.cloud)*
"""
        )


async def _update_check_run_with_error(
    notifier: GitHubNotifier,
    owner: str,
    repo: str,
    check_run_id: int,
    error_message: str
):
    """Update check run with error message."""
    async with GitHubAPIClient(notifier.installation_id) as client:
        await client.update_check_run(
            owner=owner,
            repo=repo,
            check_run_id=check_run_id,
            status="completed",
            conclusion="failure",
            title="Resolution Application Failed",
            summary=f"""## ❌ Failed to Apply Resolution

{error_message}

---
*Error reported by [MergeWeave](https://mergeweave.cloud)*
"""
        )


async def _update_resolution_result_status(
    db: AsyncSession,
    package_id: str,
    status: str
):
    """Update ResolutionResult status for the given package."""
    stmt = select(ResolutionResult).where(
        ResolutionResult.resolution_id == package_id
    )
    result = (await db.execute(stmt)).scalar_one_or_none()

    if result:
        result.status = status
        result.applied_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info(
            f"Updated ResolutionResult status",
            extra={
                "package_id": package_id,
                "status": status
            }
        )
