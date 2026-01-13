"""
Branch Service.

Handles branch operations including listing, merge-base detection,
and content fetching from GitHub repositories.

P1-F2: Implements automatic pagination for branch listing to ensure
all branches are loaded up to MAX_BRANCHES_FETCH limit.
"""

import asyncio
import logging
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime

from app.clients.github_client import GitHubAPIClient
from app.cache.redis_client import get_redis_client
from app.config import get_config

logger = logging.getLogger(__name__)


@dataclass
class BranchInfo:
    """Branch information from GitHub."""
    name: str
    sha: str
    protected: bool
    is_default: bool
    commit_message: Optional[str] = None
    commit_author: Optional[str] = None
    commit_date: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "sha": self.sha,
            "protected": self.protected,
            "is_default": self.is_default,
            "commit_message": self.commit_message,
            "commit_author": self.commit_author,
            "commit_date": (
                self.commit_date.isoformat() if self.commit_date else None
            ),
        }


@dataclass
class CommitInfo:
    """Commit information from GitHub."""
    sha: str
    message: str
    author: str
    date: datetime
    parents: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "sha": self.sha,
            "message": self.message,
            "author": self.author,
            "date": self.date.isoformat() if self.date else None,
            "parents": self.parents,
        }


@dataclass
class MergeBaseResult:
    """Result of merge-base detection."""
    merge_base_sha: str
    source_sha: str
    target_sha: str
    commits_ahead_source: int
    commits_ahead_target: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class BranchListResult:
    """
    Result of branch listing with pagination metadata.

    P1-F2: Provides complete branch list with pagination info to prevent
    partial branch loading issues.

    Attributes:
        branches: List of BranchInfo objects
        total: Total number of branches fetched
        limited: True if results were limited by MAX_BRANCHES_FETCH
        max_limit: The MAX_BRANCHES_FETCH value used
    """
    branches: List[BranchInfo]
    total: int
    limited: bool = False
    max_limit: int = 500

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "branches": [b.to_dict() for b in self.branches],
            "total": self.total,
            "limited": self.limited,
            "max_limit": self.max_limit,
        }


class BranchService:
    """
    Service for branch operations.

    Provides methods for:
    - Listing branches for a repository
    - Finding merge-base between branches
    - Getting commit ancestry
    - Fetching file content from branches

    Uses Redis caching for performance (5-minute TTL for branch lists).
    """

    CACHE_TTL = 300  # 5 minutes

    def __init__(self):
        self._cache_key_prefix = "dashboard:branches"

    async def list_branches(
        self,
        installation_id: int,
        repository_full_name: str,
        page: int = 1,
        per_page: int = 50,
        protected_only: bool = False
    ) -> Tuple[List[BranchInfo], int]:
        """
        List branches for a repository (legacy method for backward compatibility).

        Args:
            installation_id: GitHub installation ID
            repository_full_name: Full repository name (owner/repo)
            page: Page number (1-indexed)
            per_page: Items per page (max 100)
            protected_only: Only return protected branches

        Returns:
            Tuple of (list of BranchInfo, total count)

        Note:
            This method is kept for backward compatibility. For new code,
            use list_branches_all() which handles pagination automatically
            and returns a BranchListResult with metadata.
        """
        result = await self.list_branches_all(
            installation_id=installation_id,
            repository_full_name=repository_full_name,
            protected_only=protected_only
        )
        return result.branches, result.total

    async def list_branches_all(
        self,
        installation_id: int,
        repository_full_name: str,
        protected_only: bool = False
    ) -> BranchListResult:
        """
        List all branches for a repository with automatic pagination.

        P1-F2: Fetches all branches up to MAX_BRANCHES_FETCH limit,
        automatically paginating through GitHub's API.

        Args:
            installation_id: GitHub installation ID
            repository_full_name: Full repository name (owner/repo)
            protected_only: Only return protected branches

        Returns:
            BranchListResult with branches, total count, and limit metadata
        """
        config = get_config()
        max_branches = config.max_branches_fetch

        logger.info(
            "Listing all branches with auto-pagination",
            extra={
                "installation_id": installation_id,
                "repository": repository_full_name,
                "max_branches": max_branches,
                "protected_only": protected_only
            }
        )

        # Check cache (only for non-protected queries)
        if not protected_only:
            cache_key = f"{self._cache_key_prefix}:{installation_id}:{repository_full_name}:all"
            redis = await get_redis_client()
            cached = await redis.get(cache_key)

            if cached:
                logger.debug("Returning cached branch list")
                cached_data = cached
                branches = self._deserialize_branches(cached_data.get("branches", []))
                return BranchListResult(
                    branches=branches,
                    total=cached_data.get("total", len(branches)),
                    limited=cached_data.get("limited", False),
                    max_limit=max_branches
                )

        owner, repo = repository_full_name.split("/")

        async with GitHubAPIClient(installation_id) as client:
            # Get repository info for default branch
            repo_info = await client.get_repository(owner, repo)
            default_branch = repo_info.get("default_branch")
            if not default_branch:
                logger.warning(
                    f"GitHub API did not return default_branch for {repository_full_name}, "
                    f"falling back to 'main'"
                )
                default_branch = "main"

            # Paginate through all branches
            all_branches: List[BranchInfo] = []
            page = 1
            per_page = 100  # GitHub's max per page
            limited = False

            while True:
                # GitHub API: /repos/{owner}/{repo}/branches
                endpoint = f"/repos/{owner}/{repo}/branches"
                params: Dict[str, Any] = {
                    "per_page": per_page,
                    "page": page
                }
                if protected_only:
                    params["protected"] = "true"

                # Make request directly using _make_request
                branches_data = await client._make_request(
                    method="GET",
                    endpoint=endpoint,
                    params=params
                )

                # Process branches from this page
                for branch in branches_data:
                    # Check if we've hit the limit
                    if len(all_branches) >= max_branches:
                        limited = True
                        break

                    commit_data = branch.get("commit", {})
                    commit_info = commit_data.get("commit", {})

                    # Parse commit date
                    commit_date = None
                    author_info = commit_info.get("author", {})
                    if author_info.get("date"):
                        try:
                            date_str = author_info["date"]
                            commit_date = datetime.fromisoformat(
                                date_str.replace("Z", "+00:00")
                            )
                        except (ValueError, TypeError):
                            pass

                    all_branches.append(BranchInfo(
                        name=branch["name"],
                        sha=commit_data.get("sha", ""),
                        protected=branch.get("protected", False),
                        is_default=branch["name"] == default_branch,
                        commit_message=commit_info.get("message", "")[:100] if commit_info.get("message") else None,
                        commit_author=author_info.get("name"),
                        commit_date=commit_date
                    ))

                # Check termination conditions
                if limited:
                    logger.warning(
                        "Branch listing limited by MAX_BRANCHES_FETCH",
                        extra={
                            "installation_id": installation_id,
                            "repository": repository_full_name,
                            "fetched": len(all_branches),
                            "limit": max_branches
                        }
                    )
                    break

                # No more pages if we got fewer than per_page
                if len(branches_data) < per_page:
                    break

                page += 1

            # Cache results (only for non-protected queries)
            if not protected_only and all_branches:
                redis = await get_redis_client()
                cache_data = {
                    "branches": self._serialize_branches(all_branches),
                    "total": len(all_branches),
                    "limited": limited
                }
                await redis.set(
                    f"{self._cache_key_prefix}:{installation_id}:{repository_full_name}:all",
                    cache_data,
                    ttl=self.CACHE_TTL
                )

            logger.info(
                "Branch listing complete",
                extra={
                    "installation_id": installation_id,
                    "repository": repository_full_name,
                    "total_branches": len(all_branches),
                    "pages_fetched": page,
                    "limited": limited
                }
            )

            return BranchListResult(
                branches=all_branches,
                total=len(all_branches),
                limited=limited,
                max_limit=max_branches
            )

    async def find_merge_base(
        self,
        installation_id: int,
        repository_full_name: str,
        source_branch: str,
        target_branch: str
    ) -> MergeBaseResult:
        """
        Find the merge-base (common ancestor) between two branches.

        Uses GitHub's compare API to find the merge-base commit and
        calculate how many commits each branch is ahead.

        Args:
            installation_id: GitHub installation ID
            repository_full_name: Full repository name (owner/repo)
            source_branch: Source branch name
            target_branch: Target branch name

        Returns:
            MergeBaseResult with merge-base SHA and commit counts

        Raises:
            ValueError: If merge-base cannot be determined
        """
        logger.info(
            "Finding merge-base",
            extra={
                "installation_id": installation_id,
                "repository": repository_full_name,
                "source": source_branch,
                "target": target_branch
            }
        )

        owner, repo = repository_full_name.split("/")

        async with GitHubAPIClient(installation_id) as client:
            # Get branch SHAs
            source_data = await client.get_branch(owner, repo, source_branch)
            target_data = await client.get_branch(owner, repo, target_branch)

            source_sha = source_data["commit"]["sha"]
            target_sha = target_data["commit"]["sha"]

            # Use GitHub's compare API to find merge-base
            comparison = await client.compare_commits(
                owner, repo, target_sha, source_sha
            )

            merge_base_commit = comparison.get("merge_base_commit", {})
            merge_base_sha = merge_base_commit.get("sha")

            if not merge_base_sha:
                raise ValueError(
                    f"Could not find merge-base between {source_branch} "
                    f"and {target_branch}"
                )

            # Get commit counts
            ahead_by = comparison.get("ahead_by", 0)
            behind_by = comparison.get("behind_by", 0)

            logger.info(
                "Merge-base found",
                extra={
                    "merge_base": merge_base_sha[:7],
                    "source_sha": source_sha[:7],
                    "target_sha": target_sha[:7],
                    "ahead_by": ahead_by,
                    "behind_by": behind_by
                }
            )

            return MergeBaseResult(
                merge_base_sha=merge_base_sha,
                source_sha=source_sha,
                target_sha=target_sha,
                commits_ahead_source=ahead_by,
                commits_ahead_target=behind_by
            )

    async def get_ancestry_path(
        self,
        installation_id: int,
        repository_full_name: str,
        from_sha: str,
        to_sha: str,
        max_commits: int = 50
    ) -> List[CommitInfo]:
        """
        Get commits between two SHAs (ancestry path).

        Returns commits from from_sha (exclusive) to to_sha (inclusive),
        in chronological order (oldest first).

        Args:
            installation_id: GitHub installation ID
            repository_full_name: Full repository name (owner/repo)
            from_sha: Starting SHA (older, exclusive)
            to_sha: Ending SHA (newer, inclusive)
            max_commits: Maximum commits to return

        Returns:
            List of CommitInfo in chronological order
        """
        logger.info(
            "Getting ancestry path",
            extra={
                "installation_id": installation_id,
                "repository": repository_full_name,
                "from": from_sha[:7],
                "to": to_sha[:7]
            }
        )

        owner, repo = repository_full_name.split("/")

        async with GitHubAPIClient(installation_id) as client:
            # List commits from to_sha back
            endpoint = f"/repos/{owner}/{repo}/commits"
            params = {
                "sha": to_sha,
                "per_page": max_commits
            }

            commits_data = await client._make_request(
                method="GET",
                endpoint=endpoint,
                params=params
            )

            commits: List[CommitInfo] = []
            for commit in commits_data:
                # Stop if we reach the starting SHA
                if commit["sha"] == from_sha:
                    break

                # Parse commit info
                commit_info = commit.get("commit", {})
                author_info = commit_info.get("author", {})

                # Parse date
                commit_date = datetime.utcnow()
                if author_info.get("date"):
                    try:
                        date_str = author_info["date"]
                        commit_date = datetime.fromisoformat(
                            date_str.replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass

                commits.append(CommitInfo(
                    sha=commit["sha"],
                    message=commit_info.get("message", "")[:200],
                    author=author_info.get("name", "Unknown"),
                    date=commit_date,
                    parents=[p["sha"] for p in commit.get("parents", [])]
                ))

            # Return in chronological order (oldest first)
            return list(reversed(commits))

    async def get_file_content(
        self,
        installation_id: int,
        repository_full_name: str,
        branch_or_sha: str,
        file_path: str
    ) -> Optional[str]:
        """
        Get file content from a branch or commit.

        Args:
            installation_id: GitHub installation ID
            repository_full_name: Full repository name (owner/repo)
            branch_or_sha: Branch name or commit SHA
            file_path: Path to file in repository

        Returns:
            File content as string, or None if not found
        """
        owner, repo = repository_full_name.split("/")

        async with GitHubAPIClient(installation_id) as client:
            try:
                content = await client.get_file_contents(
                    owner, repo, file_path, branch_or_sha
                )
                return content
            except Exception as e:
                logger.warning(
                    f"File not found: {file_path}",
                    extra={
                        "repository": repository_full_name,
                        "ref": branch_or_sha,
                        "error": str(e)
                    }
                )
                return None

    async def get_multiple_file_contents(
        self,
        installation_id: int,
        repository_full_name: str,
        branch_or_sha: str,
        file_paths: List[str]
    ) -> Dict[str, Optional[str]]:
        """
        Get multiple file contents in parallel.

        Args:
            installation_id: GitHub installation ID
            repository_full_name: Full repository name (owner/repo)
            branch_or_sha: Branch name or commit SHA
            file_paths: List of file paths

        Returns:
            Dictionary mapping file paths to content (None if not found)
        """
        tasks = [
            self.get_file_content(
                installation_id, repository_full_name, branch_or_sha, path
            )
            for path in file_paths
        ]

        contents = await asyncio.gather(*tasks)

        return dict(zip(file_paths, contents))

    async def invalidate_cache(
        self,
        installation_id: int,
        repository_full_name: str
    ) -> None:
        """
        Invalidate cached branch list for a repository.

        Call this after push events or branch updates.
        P1-F2: Also invalidates the new :all cache key.

        Args:
            installation_id: GitHub installation ID
            repository_full_name: Full repository name (owner/repo)
        """
        redis = await get_redis_client()

        # Delete both legacy and new cache keys
        legacy_key = f"{self._cache_key_prefix}:{installation_id}:{repository_full_name}"
        all_key = f"{self._cache_key_prefix}:{installation_id}:{repository_full_name}:all"

        await redis.delete(legacy_key)
        await redis.delete(all_key)

        logger.debug(
            "Branch cache invalidated",
            extra={
                "installation_id": installation_id,
                "repository": repository_full_name
            }
        )

    def _serialize_branches(self, branches: List[BranchInfo]) -> List[Dict[str, Any]]:
        """Serialize branches for caching."""
        return [b.to_dict() for b in branches]

    def _deserialize_branches(self, data: List[Dict[str, Any]]) -> List[BranchInfo]:
        """Deserialize branches from cache."""
        branches = []
        for d in data:
            commit_date = None
            if d.get("commit_date"):
                try:
                    commit_date = datetime.fromisoformat(d["commit_date"])
                except (ValueError, TypeError):
                    pass

            branches.append(BranchInfo(
                name=d["name"],
                sha=d["sha"],
                protected=d["protected"],
                is_default=d["is_default"],
                commit_message=d.get("commit_message"),
                commit_author=d.get("commit_author"),
                commit_date=commit_date
            ))
        return branches


# Singleton instance
_branch_service: Optional[BranchService] = None


def get_branch_service() -> BranchService:
    """Get singleton BranchService instance."""
    global _branch_service
    if _branch_service is None:
        _branch_service = BranchService()
    return _branch_service
