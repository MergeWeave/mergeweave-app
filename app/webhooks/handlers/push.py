"""
Push Webhook Handler.

Handles GitHub push events to detect conflicts when branches are updated.
Triggers async conflict detection for non-default branch pushes.

Per Workstream 3 specification.
"""

import logging
import asyncio
from typing import Dict

from app.webhooks.router import webhook_handler
from app.workers.background_tasks import queue_conflict_detection

logger = logging.getLogger(__name__)


@webhook_handler("push")
async def handle_push_event(payload: Dict, delivery_id: str):
    """
    Handle GitHub push webhook events.

    Triggers conflict detection for feature branch pushes.
    Skips:
    - Default branch pushes (nothing to merge into)
    - Branch deletions (ref deleted)
    - Tag pushes (refs/tags/*)

    Args:
        payload: GitHub webhook payload
        delivery_id: GitHub delivery ID for tracking

    Process:
    1. Extract repository and branch info
    2. Validate event (skip default branch, deletions, tags)
    3. Queue async conflict detection task
    4. Return 200 OK immediately (non-blocking)
    """
    # Extract repository info
    repository_data = payload.get("repository", {})
    repository_full_name = repository_data.get("full_name")
    default_branch = repository_data.get("default_branch")

    # Extract push details
    ref = payload.get("ref", "")  # e.g., "refs/heads/feature-branch"
    head_sha = payload.get("after")  # Commit SHA after push
    deleted = payload.get("deleted", False)

    # Extract installation ID
    installation_data = payload.get("installation", {})
    installation_id = installation_data.get("id")

    logger.info(
        f"Received push event",
        extra={
            "repository": repository_full_name,
            "ref": ref,
            "head_sha": head_sha[:7] if head_sha else None,
            "delivery_id": delivery_id,
            "installation_id": installation_id
        }
    )

    # Validation 1: Skip tag pushes
    if ref.startswith("refs/tags/"):
        logger.info(
            f"Skipping tag push",
            extra={"ref": ref, "delivery_id": delivery_id}
        )
        return

    # Extract branch name from ref
    if not ref.startswith("refs/heads/"):
        logger.warning(
            f"Unexpected ref format",
            extra={"ref": ref, "delivery_id": delivery_id}
        )
        return

    branch_name = ref.replace("refs/heads/", "")

    # Validation 2: Skip default branch pushes
    if branch_name == default_branch:
        logger.info(
            f"Skipping default branch push",
            extra={
                "branch": branch_name,
                "delivery_id": delivery_id
            }
        )
        return

    # Validation 3: Skip branch deletions
    if deleted or head_sha == "0000000000000000000000000000000000000000":
        logger.info(
            f"Skipping branch deletion",
            extra={
                "branch": branch_name,
                "delivery_id": delivery_id
            }
        )
        return

    # Validation 4: Ensure we have required data
    if not all([repository_full_name, branch_name, head_sha, installation_id]):
        logger.error(
            f"Missing required data in push payload",
            extra={
                "repository": repository_full_name,
                "branch": branch_name,
                "head_sha": head_sha,
                "installation_id": installation_id,
                "delivery_id": delivery_id
            }
        )
        return

    # Queue async conflict detection task
    logger.info(
        f"Queueing conflict detection",
        extra={
            "repository": repository_full_name,
            "source_branch": branch_name,
            "target_branch": default_branch,
            "head_sha": head_sha[:7],
            "delivery_id": delivery_id
        }
    )

    # Fire and forget - don't wait for completion
    asyncio.create_task(
        queue_conflict_detection(
            installation_id=installation_id,
            repository_full_name=repository_full_name,
            source_branch=branch_name,
            target_branch=default_branch,
            head_sha=head_sha,
            webhook_delivery_id=delivery_id
        )
    )

    logger.info(
        f"Push event handled - conflict detection queued",
        extra={"delivery_id": delivery_id}
    )

    # Return immediately (non-blocking)
    # Background task will run independently
