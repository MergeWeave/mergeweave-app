"""
Resolution Application Service.

Handles applying conflict resolutions by creating commits on GitHub.
Implements the write-back functionality for the click-to-accept workflow.
"""

import logging
import base64
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone

from app.clients.github_client import GitHubAPIClient
from app.models.resolution_package import ResolutionPackage
from app.config import get_config

logger = logging.getLogger(__name__)


class ResolutionApplicationService:
    """
    Service for applying conflict resolutions to GitHub repositories.

    Creates commits with resolved files using the GitHub API.
    Supports both single-file and multi-file resolutions.

    Usage:
        service = ResolutionApplicationService(installation_id=12345)

        # Validate user permission
        has_permission = await service.validate_user_permission(
            owner="user",
            repo="repo",
            username="contributor"
        )

        # Apply resolution
        commit_sha = await service.apply_resolution(
            owner="user",
            repo="repo",
            package=resolution_package
        )
    """

    def __init__(self, installation_id: int):
        """
        Initialize the resolution application service.

        Args:
            installation_id: GitHub App installation ID
        """
        self.installation_id = installation_id
        self.config = get_config()

    async def validate_user_permission(
        self,
        owner: str,
        repo: str,
        username: str
    ) -> bool:
        """
        Validate that user has write permission to the repository.

        Args:
            owner: Repository owner
            repo: Repository name
            username: GitHub username to check

        Returns:
            bool: True if user has write or admin permission
        """
        async with GitHubAPIClient(self.installation_id) as client:
            try:
                permission = await client.get_user_repository_permission(
                    owner=owner,
                    repo=repo,
                    username=username
                )

                permission_level = permission.get("permission", "none")
                has_write = permission_level in ("admin", "write", "maintain")

                logger.info(
                    f"Checked user permission",
                    extra={
                        "owner": owner,
                        "repo": repo,
                        "username": username,
                        "permission": permission_level,
                        "has_write": has_write
                    }
                )

                return has_write

            except Exception as e:
                logger.error(
                    f"Failed to check user permission",
                    extra={
                        "owner": owner,
                        "repo": repo,
                        "username": username,
                        "error": str(e)
                    },
                    exc_info=True
                )
                return False

    async def apply_resolution(
        self,
        owner: str,
        repo: str,
        package: ResolutionPackage
    ) -> str:
        """
        Apply a resolution package by creating a merge commit on the source branch.

        Creates a merge commit with TWO parents:
        1. The source branch commit (feature branch)
        2. The target branch commit (e.g., main)

        This informs git that the target branch's changes have been incorporated,
        allowing the source branch to be merged cleanly into the target branch afterward.

        Process:
        1. Get current source branch reference
        2. Get current target branch reference
        3. Get source commit and its tree
        4. Create blobs for resolved files
        5. Create new tree with resolved files
        6. Create merge commit with both parents
        7. Update source branch reference

        Args:
            owner: Repository owner
            repo: Repository name
            package: ResolutionPackage with resolved files

        Returns:
            str: SHA of the created merge commit

        Raises:
            RuntimeError: If commit creation fails
        """
        source_branch = package.source_branch
        target_branch = package.target_branch
        resolved_files = package.package_data.get("resolved_files", {})

        if not resolved_files:
            raise RuntimeError("No resolved files in package")

        logger.info(
            f"Applying resolution to branch",
            extra={
                "owner": owner,
                "repo": repo,
                "source_branch": source_branch,
                "target_branch": target_branch,
                "file_count": len(resolved_files)
            }
        )

        async with GitHubAPIClient(self.installation_id) as client:
            # Step 1: Get current source branch reference
            source_ref = await self._get_branch_ref(
                client=client,
                owner=owner,
                repo=repo,
                branch=source_branch
            )

            source_commit_sha = source_ref["object"]["sha"]

            logger.debug(
                f"Got source branch ref",
                extra={
                    "branch": source_branch,
                    "sha": source_commit_sha[:7]
                }
            )

            # Step 2: Get current target branch reference
            target_ref = await self._get_branch_ref(
                client=client,
                owner=owner,
                repo=repo,
                branch=target_branch
            )

            target_commit_sha = target_ref["object"]["sha"]

            logger.debug(
                f"Got target branch ref",
                extra={
                    "branch": target_branch,
                    "sha": target_commit_sha[:7]
                }
            )

            # Step 3: Get source commit to get tree SHA
            source_commit = await self._get_commit(
                client=client,
                owner=owner,
                repo=repo,
                sha=source_commit_sha
            )

            base_tree_sha = source_commit["tree"]["sha"]

            # Step 4 & 5: Create new tree with resolved files
            new_tree_sha = await self._create_tree_with_files(
                client=client,
                owner=owner,
                repo=repo,
                base_tree_sha=base_tree_sha,
                files=resolved_files
            )

            # Step 6: Create merge commit with BOTH parents
            # This tells git that the target branch's changes have been incorporated
            commit_message = self._generate_commit_message(
                resolved_files=list(resolved_files.keys()),
                package=package
            )

            new_commit_sha = await self._create_commit(
                client=client,
                owner=owner,
                repo=repo,
                message=commit_message,
                tree_sha=new_tree_sha,
                parent_shas=[source_commit_sha, target_commit_sha]  # Two parents = merge commit
            )

            # Step 7: Update source branch reference
            await self._update_branch_ref(
                client=client,
                owner=owner,
                repo=repo,
                branch=source_branch,
                sha=new_commit_sha
            )

            logger.info(
                f"Resolution merge commit applied successfully",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                    "commit_sha": new_commit_sha[:7],
                    "file_count": len(resolved_files),
                    "parents": [source_commit_sha[:7], target_commit_sha[:7]]
                }
            )

            return new_commit_sha

    async def _get_branch_ref(
        self,
        client: GitHubAPIClient,
        owner: str,
        repo: str,
        branch: str
    ) -> Dict:
        """Get branch reference from GitHub."""
        return await client._make_request(
            method="GET",
            endpoint=f"/repos/{owner}/{repo}/git/ref/heads/{branch}"
        )

    async def _get_commit(
        self,
        client: GitHubAPIClient,
        owner: str,
        repo: str,
        sha: str
    ) -> Dict:
        """Get commit object from GitHub."""
        return await client._make_request(
            method="GET",
            endpoint=f"/repos/{owner}/{repo}/git/commits/{sha}"
        )

    async def _create_blob(
        self,
        client: GitHubAPIClient,
        owner: str,
        repo: str,
        content: str
    ) -> str:
        """
        Create a blob with the given content.

        Args:
            client: GitHub API client
            owner: Repository owner
            repo: Repository name
            content: File content

        Returns:
            str: Blob SHA
        """
        # Base64 encode content to handle binary/special characters
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("ascii")

        response = await client._make_request(
            method="POST",
            endpoint=f"/repos/{owner}/{repo}/git/blobs",
            json={
                "content": encoded_content,
                "encoding": "base64"
            }
        )

        return response["sha"]

    async def _create_tree_with_files(
        self,
        client: GitHubAPIClient,
        owner: str,
        repo: str,
        base_tree_sha: str,
        files: Dict[str, str]
    ) -> str:
        """
        Create a new tree with the resolved files.

        Args:
            client: GitHub API client
            owner: Repository owner
            repo: Repository name
            base_tree_sha: Base tree SHA to build on
            files: Dict of file paths to content

        Returns:
            str: New tree SHA
        """
        tree_items = []

        for file_path, content in files.items():
            # Create blob for each file
            blob_sha = await self._create_blob(
                client=client,
                owner=owner,
                repo=repo,
                content=content
            )

            tree_items.append({
                "path": file_path,
                "mode": "100644",  # Regular file
                "type": "blob",
                "sha": blob_sha
            })

            logger.debug(
                f"Created blob for file",
                extra={
                    "file_path": file_path,
                    "blob_sha": blob_sha[:7]
                }
            )

        # Create tree
        response = await client._make_request(
            method="POST",
            endpoint=f"/repos/{owner}/{repo}/git/trees",
            json={
                "base_tree": base_tree_sha,
                "tree": tree_items
            }
        )

        return response["sha"]

    async def _create_commit(
        self,
        client: GitHubAPIClient,
        owner: str,
        repo: str,
        message: str,
        tree_sha: str,
        parent_shas: List[str]
    ) -> str:
        """
        Create a commit with the given tree.

        Args:
            client: GitHub API client
            owner: Repository owner
            repo: Repository name
            message: Commit message
            tree_sha: Tree SHA for the commit
            parent_shas: Parent commit SHAs

        Returns:
            str: New commit SHA
        """
        response = await client._make_request(
            method="POST",
            endpoint=f"/repos/{owner}/{repo}/git/commits",
            json={
                "message": message,
                "tree": tree_sha,
                "parents": parent_shas
            }
        )

        return response["sha"]

    async def _update_branch_ref(
        self,
        client: GitHubAPIClient,
        owner: str,
        repo: str,
        branch: str,
        sha: str
    ):
        """
        Update branch reference to point to new commit.

        Args:
            client: GitHub API client
            owner: Repository owner
            repo: Repository name
            branch: Branch name
            sha: Commit SHA to point to
        """
        await client._make_request(
            method="PATCH",
            endpoint=f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
            json={
                "sha": sha,
                "force": False  # Don't force push
            }
        )

        logger.info(
            f"Updated branch reference",
            extra={
                "branch": branch,
                "sha": sha[:7]
            }
        )

    def _generate_commit_message(
        self,
        resolved_files: List[str],
        package: ResolutionPackage
    ) -> str:
        """
        Generate commit message for the resolution merge commit.

        Args:
            resolved_files: List of resolved file paths
            package: Resolution package

        Returns:
            str: Commit message
        """
        file_count = len(resolved_files)
        strategy = package.package_data.get("strategy_used", "auto")
        confidence = package.package_data.get("confidence_score", "N/A")

        # Format file list (truncate if too many)
        if file_count <= 5:
            file_list = "\n".join(f"  - {f}" for f in resolved_files)
        else:
            file_list = "\n".join(f"  - {f}" for f in resolved_files[:5])
            file_list += f"\n  - ... and {file_count - 5} more files"

        return f"""Merge branch '{package.target_branch}' into {package.source_branch} (via MergeWeave)

Automatically resolved {file_count} conflicted file(s):
{file_list}

Strategy: {strategy}
Confidence: {confidence}

This merge commit was created by MergeWeave's automated conflict resolution.
The {package.source_branch} branch now incorporates changes from {package.target_branch}
and can be merged cleanly.

---
🤖 Generated by [MergeWeave](https://mergeweave.cloud)
"""
