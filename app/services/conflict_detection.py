"""
Conflict Detection Service.

Detects merge conflicts by cloning repositories and performing test merges.
Extracts conflict content with markers for CRE processing.

Per Workstream 3 specification.
"""

import logging
import time
from typing import Dict, List, Optional
from datetime import datetime, timezone

from app.services.repository_manager import RepositoryManager
from app.clients.github_client import GitHubAPIClient

logger = logging.getLogger(__name__)


class ConflictDetectionResult:
    """
    Result of conflict detection operation.

    Attributes:
        has_conflicts: Whether conflicts were detected
        base_sha: Base commit SHA
        head_sha: Head commit SHA
        conflicted_files: List of file paths with conflicts
        conflict_content: Dict mapping file paths to content with markers
        processing_duration_ms: Time taken to detect conflicts
    """

    def __init__(
        self,
        has_conflicts: bool,
        base_sha: str,
        head_sha: str,
        conflicted_files: List[str],
        conflict_content: Dict[str, str],
        processing_duration_ms: int
    ):
        self.has_conflicts = has_conflicts
        self.base_sha = base_sha
        self.head_sha = head_sha
        self.conflicted_files = conflicted_files
        self.conflict_content = conflict_content
        self.processing_duration_ms = processing_duration_ms


class ConflictDetectionService:
    """
    Service for detecting merge conflicts in GitHub repositories.

    Uses git operations to simulate merges and detect conflicts.
    Extracts conflict content with markers for downstream processing.

    Usage:
        service = ConflictDetectionService()
        result = await service.detect_conflicts(
            installation_id=12345,
            repository_full_name="owner/repo",
            source_branch="feature",
            target_branch="main"
        )
    """

    def __init__(self):
        """Initialize conflict detection service."""
        self.repo_manager = RepositoryManager()

    async def detect_conflicts(
        self,
        installation_id: int,
        repository_full_name: str,
        source_branch: str,
        target_branch: str
    ) -> ConflictDetectionResult:
        """
        Detect merge conflicts between two branches.

        Process:
        1. Get installation token
        2. Clone repository
        3. Fetch both branches
        4. Get commit SHAs
        5. Attempt merge
        6. Extract conflict information if present
        7. Cleanup

        Args:
            installation_id: GitHub App installation ID
            repository_full_name: Full repository name (owner/repo)
            source_branch: Source branch to merge (head)
            target_branch: Target branch (base)

        Returns:
            ConflictDetectionResult: Detection results

        Raises:
            RuntimeError: If detection process fails
        """
        start_time = time.time()

        logger.info(
            f"Starting conflict detection",
            extra={
                "installation_id": installation_id,
                "repository": repository_full_name,
                "source_branch": source_branch,
                "target_branch": target_branch
            }
        )

        repo_path = None

        try:
            # Step 1: Get installation token and build clone URL
            clone_url = f"https://github.com/{repository_full_name}.git"

            async with GitHubAPIClient(installation_id) as github_client:
                token = await github_client.get_installation_token()

                # Step 2: Clone repository (shallow clone of target branch)
                repo_path = await self.repo_manager.clone(
                    clone_url=clone_url,
                    token=token,
                    branch=target_branch,
                    shallow=False  # Need full history for merge
                )

                # Step 3: Fetch source branch
                await self.repo_manager.fetch_branch(
                    repo_path=repo_path,
                    branch=source_branch
                )

                # Step 4: Get commit SHAs
                base_sha = await self.repo_manager.get_branch_sha(
                    repo_path=repo_path,
                    ref=target_branch
                )

                head_sha = await self.repo_manager.get_branch_sha(
                    repo_path=repo_path,
                    ref=f"origin/{source_branch}"
                )

                logger.info(
                    f"Branch SHAs retrieved",
                    extra={
                        "base_sha": base_sha[:7],
                        "head_sha": head_sha[:7]
                    }
                )

                # Step 5: Attempt merge
                merge_success = await self.repo_manager.attempt_merge(
                    repo_path=repo_path,
                    target_branch=target_branch,
                    source_branch=f"origin/{source_branch}"
                )

                # Step 6: Extract conflict information
                if merge_success:
                    # No conflicts
                    logger.info("No conflicts detected - merge would succeed")

                    processing_ms = int((time.time() - start_time) * 1000)

                    return ConflictDetectionResult(
                        has_conflicts=False,
                        base_sha=base_sha,
                        head_sha=head_sha,
                        conflicted_files=[],
                        conflict_content={},
                        processing_duration_ms=processing_ms
                    )

                else:
                    # Conflicts detected - extract content
                    logger.info("Conflicts detected - extracting conflict content")

                    conflicted_files = await self.repo_manager.get_conflicted_files(
                        repo_path=repo_path
                    )

                    # Extract conflict content with markers
                    conflict_content = await self._extract_conflict_content(
                        repo_path=repo_path,
                        conflicted_files=conflicted_files
                    )

                    processing_ms = int((time.time() - start_time) * 1000)

                    logger.info(
                        f"Conflict detection complete",
                        extra={
                            "has_conflicts": True,
                            "conflicted_files": len(conflicted_files),
                            "processing_ms": processing_ms
                        }
                    )

                    return ConflictDetectionResult(
                        has_conflicts=True,
                        base_sha=base_sha,
                        head_sha=head_sha,
                        conflicted_files=conflicted_files,
                        conflict_content=conflict_content,
                        processing_duration_ms=processing_ms
                    )

        except Exception as e:
            logger.error(
                f"Conflict detection failed",
                extra={
                    "repository": repository_full_name,
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                    "error": str(e)
                },
                exc_info=True
            )
            raise RuntimeError(f"Conflict detection failed: {str(e)}")

        finally:
            # Step 7: Cleanup (always runs)
            if repo_path:
                await self.repo_manager.cleanup(repo_path)

    async def _extract_conflict_content(
        self,
        repo_path,
        conflicted_files: List[str]
    ) -> Dict[str, str]:
        """
        Extract conflict content with markers from conflicted files.

        Args:
            repo_path: Path to cloned repository
            conflicted_files: List of files with conflicts

        Returns:
            Dict mapping file paths to content with conflict markers
        """
        conflict_content = {}

        for file_path in conflicted_files:
            try:
                # Read file with conflict markers preserved
                content = await self.repo_manager.read_file_with_markers(
                    repo_path=repo_path,
                    file_path=file_path
                )

                # Validate that conflict markers are present
                if self._has_conflict_markers(content):
                    conflict_content[file_path] = content

                    logger.debug(
                        f"Extracted conflict content",
                        extra={
                            "file": file_path,
                            "size": len(content)
                        }
                    )
                else:
                    logger.warning(
                        f"File listed as conflicted but no markers found",
                        extra={"file": file_path}
                    )

            except Exception as e:
                logger.error(
                    f"Failed to extract conflict content",
                    extra={
                        "file": file_path,
                        "error": str(e)
                    }
                )
                # Skip this file but continue with others
                continue

        return conflict_content

    def _has_conflict_markers(self, content: str) -> bool:
        """
        Check if content contains git conflict markers.

        Args:
            content: File content

        Returns:
            bool: True if conflict markers are present
        """
        return (
            "<<<<<<<" in content and
            "=======" in content and
            ">>>>>>>" in content
        )
