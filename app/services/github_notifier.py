"""
GitHub Notifier Service.

Handles GitHub notifications via Check Runs and PR comments.
Provides rich feedback with action buttons for click-to-accept workflow.

Per Phase 3 specification - Check Runs support.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from app.clients.github_client import GitHubAPIClient
from app.config import get_config

logger = logging.getLogger(__name__)

# Check run name (consistent across all notifications)
CHECK_RUN_NAME = "MergeWeave Conflict Detection"


class GitHubNotifier:
    """
    Service for creating GitHub notifications via Check Runs and PR comments.

    Check Runs provide:
    - Rich status display in PR/commit UI
    - Inline annotations for file-level feedback
    - Action buttons for click-to-accept workflow
    - Integration with GitHub's checks tab

    Usage:
        notifier = GitHubNotifier(installation_id=12345)

        # Create initial check run (in progress)
        check_run_id = await notifier.notify_detection_started(
            owner="user",
            repo="repo",
            head_sha="abc123",
            source_branch="feature",
            target_branch="main"
        )

        # Update with results
        await notifier.notify_conflicts_detected(
            owner="user",
            repo="repo",
            check_run_id=check_run_id,
            conflicted_files=["src/main.py"],
            package_id="pkg_abc123",
            confidence_score="0.85"
        )
    """

    def __init__(self, installation_id: int):
        """
        Initialize notifier for a specific GitHub installation.

        Args:
            installation_id: GitHub App installation ID
        """
        self.installation_id = installation_id
        self.config = get_config()

    async def notify_detection_started(
        self,
        owner: str,
        repo: str,
        head_sha: str,
        source_branch: str,
        target_branch: str
    ) -> int:
        """
        Create initial check run when conflict detection starts.

        Args:
            owner: Repository owner
            repo: Repository name
            head_sha: Commit SHA to associate check with
            source_branch: Source branch being merged
            target_branch: Target branch (base)

        Returns:
            int: Check run ID for subsequent updates
        """
        async with GitHubAPIClient(self.installation_id) as client:
            check_run = await client.create_check_run(
                owner=owner,
                repo=repo,
                name=CHECK_RUN_NAME,
                head_sha=head_sha,
                status="in_progress",
                title="Checking for merge conflicts...",
                summary=self._format_in_progress_summary(source_branch, target_branch)
            )

            logger.info(
                f"Created check run for conflict detection",
                extra={
                    "check_run_id": check_run["id"],
                    "owner": owner,
                    "repo": repo,
                    "head_sha": head_sha[:7]
                }
            )

            return check_run["id"]

    async def notify_no_conflicts(
        self,
        owner: str,
        repo: str,
        check_run_id: int,
        source_branch: str,
        target_branch: str,
        processing_time_ms: int
    ) -> None:
        """
        Update check run when no conflicts are detected.

        Args:
            owner: Repository owner
            repo: Repository name
            check_run_id: ID of the check run to update
            source_branch: Source branch
            target_branch: Target branch
            processing_time_ms: Time taken for detection
        """
        async with GitHubAPIClient(self.installation_id) as client:
            await client.update_check_run(
                owner=owner,
                repo=repo,
                check_run_id=check_run_id,
                status="completed",
                conclusion="success",
                title="No merge conflicts detected",
                summary=self._format_no_conflicts_summary(
                    source_branch,
                    target_branch,
                    processing_time_ms
                )
            )

            logger.info(
                f"Updated check run - no conflicts",
                extra={
                    "check_run_id": check_run_id,
                    "owner": owner,
                    "repo": repo
                }
            )

    async def notify_conflicts_detected(
        self,
        owner: str,
        repo: str,
        check_run_id: int,
        source_branch: str,
        target_branch: str,
        conflicted_files: List[str],
        package_id: str,
        confidence_score: str,
        processing_time_ms: int
    ) -> None:
        """
        Update check run when conflicts are detected with resolution available.

        Creates action button for applying the resolution.

        Args:
            owner: Repository owner
            repo: Repository name
            check_run_id: ID of the check run to update
            source_branch: Source branch
            target_branch: Target branch
            conflicted_files: List of files with conflicts
            package_id: Resolution package ID
            confidence_score: CRE confidence score
            processing_time_ms: Time taken for detection and resolution
        """
        # Build action button for applying resolution
        # NOTE: GitHub limits identifier to 20 characters max
        # Package IDs are exactly 20 chars: pkg_<16 base64url chars>
        actions = [{
            "label": "Apply Resolution",
            "description": f"Apply MergeWeave fix ({confidence_score})",
            "identifier": package_id
        }]

        async with GitHubAPIClient(self.installation_id) as client:
            await client.update_check_run(
                owner=owner,
                repo=repo,
                check_run_id=check_run_id,
                status="completed",
                conclusion="action_required",
                title=f"Conflicts detected in {len(conflicted_files)} file(s)",
                summary=self._format_conflicts_summary(
                    source_branch,
                    target_branch,
                    conflicted_files,
                    confidence_score,
                    processing_time_ms
                ),
                text=self._format_conflicts_details(conflicted_files, package_id),
                actions=actions
            )

            logger.info(
                f"Updated check run - conflicts detected with resolution",
                extra={
                    "check_run_id": check_run_id,
                    "owner": owner,
                    "repo": repo,
                    "package_id": package_id,
                    "conflicted_files": len(conflicted_files)
                }
            )

    async def notify_conflicts_no_resolution(
        self,
        owner: str,
        repo: str,
        check_run_id: int,
        source_branch: str,
        target_branch: str,
        conflicted_files: List[str],
        error_message: Optional[str] = None
    ) -> None:
        """
        Update check run when conflicts detected but resolution failed.

        No action button since resolution isn't available.

        Args:
            owner: Repository owner
            repo: Repository name
            check_run_id: ID of the check run to update
            source_branch: Source branch
            target_branch: Target branch
            conflicted_files: List of files with conflicts
            error_message: Optional error message from CRE
        """
        async with GitHubAPIClient(self.installation_id) as client:
            await client.update_check_run(
                owner=owner,
                repo=repo,
                check_run_id=check_run_id,
                status="completed",
                conclusion="failure",
                title=f"Conflicts detected in {len(conflicted_files)} file(s)",
                summary=self._format_conflicts_no_resolution_summary(
                    source_branch,
                    target_branch,
                    conflicted_files,
                    error_message
                )
            )

            logger.info(
                f"Updated check run - conflicts without resolution",
                extra={
                    "check_run_id": check_run_id,
                    "owner": owner,
                    "repo": repo,
                    "conflicted_files": len(conflicted_files)
                }
            )

    async def notify_resolution_applied(
        self,
        owner: str,
        repo: str,
        check_run_id: int,
        applied_by: str,
        commit_sha: str,
        resolved_files: List[str]
    ) -> None:
        """
        Update check run when resolution has been applied.

        Args:
            owner: Repository owner
            repo: Repository name
            check_run_id: ID of the check run to update
            applied_by: GitHub username who applied the resolution
            commit_sha: SHA of the commit that applied the resolution
            resolved_files: List of files that were resolved
        """
        async with GitHubAPIClient(self.installation_id) as client:
            await client.update_check_run(
                owner=owner,
                repo=repo,
                check_run_id=check_run_id,
                status="completed",
                conclusion="success",
                title="Resolution applied successfully",
                summary=self._format_resolution_applied_summary(
                    applied_by,
                    commit_sha,
                    resolved_files
                )
            )

            logger.info(
                f"Updated check run - resolution applied",
                extra={
                    "check_run_id": check_run_id,
                    "owner": owner,
                    "repo": repo,
                    "applied_by": applied_by,
                    "commit_sha": commit_sha[:7]
                }
            )

    async def notify_error(
        self,
        owner: str,
        repo: str,
        check_run_id: int,
        error_message: str,
        source_branch: str,
        target_branch: str
    ) -> None:
        """
        Update check run when an error occurs during detection.

        Args:
            owner: Repository owner
            repo: Repository name
            check_run_id: ID of the check run to update
            error_message: Error description
            source_branch: Source branch
            target_branch: Target branch
        """
        async with GitHubAPIClient(self.installation_id) as client:
            await client.update_check_run(
                owner=owner,
                repo=repo,
                check_run_id=check_run_id,
                status="completed",
                conclusion="failure",
                title="Conflict detection failed",
                summary=self._format_error_summary(
                    error_message,
                    source_branch,
                    target_branch
                )
            )

            logger.error(
                f"Updated check run - error",
                extra={
                    "check_run_id": check_run_id,
                    "owner": owner,
                    "repo": repo,
                    "error": error_message
                }
            )

    async def create_check_run_for_error(
        self,
        owner: str,
        repo: str,
        head_sha: str,
        error_message: str,
        source_branch: str,
        target_branch: str
    ) -> int:
        """
        Create a check run directly with error status.

        Used when error occurs before check run was created.

        Args:
            owner: Repository owner
            repo: Repository name
            head_sha: Commit SHA
            error_message: Error description
            source_branch: Source branch
            target_branch: Target branch

        Returns:
            int: Check run ID
        """
        async with GitHubAPIClient(self.installation_id) as client:
            check_run = await client.create_check_run(
                owner=owner,
                repo=repo,
                name=CHECK_RUN_NAME,
                head_sha=head_sha,
                status="completed",
                conclusion="failure",
                title="Conflict detection failed",
                summary=self._format_error_summary(
                    error_message,
                    source_branch,
                    target_branch
                )
            )

            logger.error(
                f"Created check run with error",
                extra={
                    "check_run_id": check_run["id"],
                    "owner": owner,
                    "repo": repo,
                    "error": error_message
                }
            )

            return check_run["id"]

    async def post_pr_comment(
        self,
        owner: str,
        repo: str,
        source_branch: str,
        body: str
    ) -> Optional[str]:
        """
        Post a comment on the PR associated with the source branch.

        Args:
            owner: Repository owner
            repo: Repository name
            source_branch: Source branch to find PR for
            body: Comment body (Markdown)

        Returns:
            Optional[str]: Comment URL if PR found and comment posted
        """
        async with GitHubAPIClient(self.installation_id) as client:
            # Find PR for this branch
            prs = await client.list_pull_requests(
                owner=owner,
                repo=repo,
                state="open",
                head=source_branch
            )

            if not prs:
                logger.info(
                    f"No open PR found for branch",
                    extra={
                        "owner": owner,
                        "repo": repo,
                        "branch": source_branch
                    }
                )
                return None

            # Post comment on first matching PR
            pr_number = prs[0]["number"]
            comment = await client.create_issue_comment(
                owner=owner,
                repo=repo,
                issue_number=pr_number,
                body=body
            )

            return comment.get("html_url")

    # =========================================================================
    # Summary Formatting Methods
    # =========================================================================

    def _format_in_progress_summary(
        self,
        source_branch: str,
        target_branch: str
    ) -> str:
        """Format summary for in-progress detection."""
        return f"""## 🔍 Checking for Merge Conflicts

**Source Branch:** `{source_branch}`
**Target Branch:** `{target_branch}`

MergeWeave is analyzing your branch for potential merge conflicts with `{target_branch}`.
"""

    def _format_no_conflicts_summary(
        self,
        source_branch: str,
        target_branch: str,
        processing_time_ms: int
    ) -> str:
        """Format summary when no conflicts detected."""
        processing_time = processing_time_ms / 1000
        return f"""## ✅ No Merge Conflicts Detected

**Source Branch:** `{source_branch}`
**Target Branch:** `{target_branch}`

Your branch can be cleanly merged into `{target_branch}` without conflicts.

---
*Analysis completed in {processing_time:.1f}s by [MergeWeave](https://mergeweave.cloud)*
"""

    def _format_conflicts_summary(
        self,
        source_branch: str,
        target_branch: str,
        conflicted_files: List[str],
        confidence_score: str,
        processing_time_ms: int
    ) -> str:
        """Format summary when conflicts detected with resolution available."""
        processing_time = processing_time_ms / 1000
        file_count = len(conflicted_files)

        # Format confidence for display
        try:
            confidence_pct = float(confidence_score) * 100
            confidence_display = f"{confidence_pct:.0f}%"
        except (ValueError, TypeError):
            confidence_display = confidence_score

        return f"""## ⚠️ Merge Conflicts Detected

**Source Branch:** `{source_branch}`
**Target Branch:** `{target_branch}`
**Conflicted Files:** {file_count}
**Resolution Confidence:** {confidence_display}

MergeWeave has analyzed the conflicts and prepared an automated resolution.
Click **"Apply Resolution"** to apply the fix to your branch.

---
*Analysis completed in {processing_time:.1f}s by [MergeWeave](https://mergeweave.cloud)*
"""

    def _format_conflicts_details(
        self,
        conflicted_files: List[str],
        package_id: str
    ) -> str:
        """Format detailed text section with file list."""
        files_list = "\n".join(f"- `{f}`" for f in conflicted_files)
        return f"""### Conflicted Files

{files_list}

### Resolution Package

Package ID: `{package_id}`

Click the **"Apply Resolution"** button above to apply MergeWeave's suggested fix.
The resolution will create a new commit on your branch with the resolved files.
"""

    def _format_conflicts_no_resolution_summary(
        self,
        source_branch: str,
        target_branch: str,
        conflicted_files: List[str],
        error_message: Optional[str]
    ) -> str:
        """Format summary when conflicts detected but no resolution available."""
        file_count = len(conflicted_files)
        files_list = "\n".join(f"- `{f}`" for f in conflicted_files[:10])

        if len(conflicted_files) > 10:
            files_list += f"\n- ... and {len(conflicted_files) - 10} more files"

        error_section = ""
        if error_message:
            error_section = f"""
### Resolution Status

MergeWeave was unable to automatically resolve these conflicts:
> {error_message}

Please resolve these conflicts manually.
"""

        return f"""## ❌ Merge Conflicts Detected

**Source Branch:** `{source_branch}`
**Target Branch:** `{target_branch}`
**Conflicted Files:** {file_count}

### Files with Conflicts

{files_list}
{error_section}
---
*Analyzed by [MergeWeave](https://mergeweave.cloud)*
"""

    def _format_resolution_applied_summary(
        self,
        applied_by: str,
        commit_sha: str,
        resolved_files: List[str]
    ) -> str:
        """Format summary when resolution has been applied."""
        file_count = len(resolved_files)
        files_list = "\n".join(f"- `{f}`" for f in resolved_files)

        return f"""## ✅ Resolution Applied Successfully

**Applied by:** @{applied_by}
**Commit:** `{commit_sha[:7]}`
**Files Resolved:** {file_count}

### Resolved Files

{files_list}

The merge conflicts have been resolved and committed to your branch.

---
*Resolution applied by [MergeWeave](https://mergeweave.cloud)*
"""

    def _format_error_summary(
        self,
        error_message: str,
        source_branch: str,
        target_branch: str
    ) -> str:
        """Format summary when an error occurs."""
        return f"""## ❌ Conflict Detection Failed

**Source Branch:** `{source_branch}`
**Target Branch:** `{target_branch}`

An error occurred while checking for merge conflicts:

> {error_message}

Please try again or check the [MergeWeave status page](https://status.mergeweave.cloud) for any service issues.

---
*Error reported by [MergeWeave](https://mergeweave.cloud)*
"""
