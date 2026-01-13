"""
GitHub API Client for MergeWeave GitHub App.

Provides async interface to GitHub REST API with installation token management.
Handles authentication, rate limiting, and common GitHub operations.

Per Workstream 2 specification.
"""

import logging
import httpx
import asyncio
from typing import Dict, Optional, Any
from datetime import datetime, timedelta
from urllib.parse import quote

from app.auth.token_manager import InstallationTokenManager
from app.cache.redis_client import get_redis_client
from app.config import get_config
from app.clients.rate_limit_tracker import RateLimitTracker

logger = logging.getLogger(__name__)


# =============================================================================
# Custom Exception Classes
# =============================================================================

class GitHubClientError(Exception):
    """Base exception for GitHub client errors."""
    pass


class BranchNotFoundError(GitHubClientError):
    """Raised when a branch does not exist in the repository."""
    def __init__(self, branch: str, repo: str = ""):
        self.branch = branch
        self.repo = repo
        message = f"Branch '{branch}' not found"
        if repo:
            message += f" in repository '{repo}'"
        super().__init__(message)


class TreeResolutionError(GitHubClientError):
    """Raised when a branch's tree SHA cannot be resolved."""
    def __init__(self, branch: str, reason: str = ""):
        self.branch = branch
        self.reason = reason
        message = f"Could not resolve tree SHA for branch '{branch}'"
        if reason:
            message += f": {reason}"
        super().__init__(message)


def encode_ref_for_url(ref: str) -> str:
    """
    URL-encode a git ref (branch name, tag, etc.) for use in URL path segments.

    GitHub's REST API requires ref names to be URL-encoded when they contain
    special characters like forward slashes. For example:
    - 'feature/my-branch' should become 'feature%2Fmy-branch'

    This is especially important for non-default branches which often use
    naming conventions like 'feature/xxx', 'bugfix/xxx', etc.

    Args:
        ref: The git ref (branch name, tag name, etc.)

    Returns:
        URL-encoded ref safe for use in URL path segments
    """
    # quote with safe='' encodes ALL special characters including '/'
    return quote(ref, safe='')


class GitHubAPIClient:
    """
    Async GitHub API client with installation token management.

    Features:
    - Automatic installation token management
    - Rate limit handling
    - Retry logic with exponential backoff
    - Structured error handling

    Usage:
        async with GitHubAPIClient(installation_id=12345) as client:
            comment = await client.post_commit_comment(
                owner="user",
                repo="repo",
                sha="abc123",
                body="Comment text"
            )
    """

    GITHUB_API_BASE = "https://api.github.com"
    DEFAULT_TIMEOUT = 30.0  # seconds
    MAX_RETRIES = 3  # Maximum number of retry attempts
    RETRY_BACKOFF_BASE = 2  # Base for exponential backoff (seconds)
    RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}  # HTTP status codes to retry

    def __init__(self, installation_id: int):
        """
        Initialize GitHub API client for a specific installation.

        Args:
            installation_id: GitHub App installation ID
        """
        self.installation_id = installation_id
        self.config = get_config()
        self._token_manager: Optional[InstallationTokenManager] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._rate_limiter: Optional[RateLimitTracker] = None

    async def __aenter__(self):
        """Async context manager entry."""
        # Initialize HTTP client
        self._http_client = httpx.AsyncClient(
            base_url=self.GITHUB_API_BASE,
            timeout=self.DEFAULT_TIMEOUT,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"
            }
        )

        # Initialize token manager and rate limiter
        redis_client = await get_redis_client()
        self._token_manager = InstallationTokenManager(
            app_id=self.config.github_app_id,
            private_key=self.config.github_app_private_key,
            redis_client=redis_client
        )
        self._rate_limiter = RateLimitTracker(redis_client)

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._http_client:
            await self._http_client.aclose()
        if self._token_manager:
            await self._token_manager.close()

    async def get_installation_token(self) -> str:
        """
        Get valid installation access token.

        Uses InstallationTokenManager to handle token caching and renewal.

        Returns:
            str: GitHub installation access token

        Raises:
            Exception: If token retrieval fails
        """
        if not self._token_manager:
            raise RuntimeError("Client not initialized. Use async context manager.")

        try:
            token = await self._token_manager.get_installation_token(
                self.installation_id
            )
            return token
        except Exception as e:
            logger.error(
                f"Failed to get installation token",
                extra={
                    "installation_id": self.installation_id,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make authenticated API request to GitHub with retry logic.

        Implements exponential backoff for retryable errors (rate limits, server errors).

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "/repos/owner/repo/commits")
            **kwargs: Additional arguments for httpx request

        Returns:
            Dict: Response JSON

        Raises:
            httpx.HTTPStatusError: If request fails after all retries
        """
        if not self._http_client:
            raise RuntimeError("Client not initialized. Use async context manager.")

        # Get installation token
        token = await self.get_installation_token()

        # Add authorization header
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"

        # Retry loop with exponential backoff
        last_exception = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                # Make request
                response = await self._http_client.request(
                    method=method,
                    url=endpoint,
                    headers=headers,
                    **kwargs
                )

                # Check for errors
                response.raise_for_status()

                # Track rate limits if rate limiter is available
                if self._rate_limiter:
                    await self._rate_limiter.update_from_response(
                        installation_id=self.installation_id,
                        response_headers=dict(response.headers),
                        resource="core"
                    )

                # Success - return response
                return response.json() if response.content else {}

            except httpx.HTTPStatusError as e:
                last_exception = e
                status_code = e.response.status_code

                # Check if this error is retryable
                if status_code not in self.RETRYABLE_STATUS_CODES:
                    # Non-retryable error - raise immediately
                    logger.error(
                        f"Non-retryable HTTP error {status_code}",
                        extra={
                            "method": method,
                            "endpoint": endpoint,
                            "status_code": status_code,
                            "attempt": attempt + 1
                        }
                    )
                    raise

                # Calculate backoff time
                if status_code == 429:
                    # Rate limit exceeded - use Retry-After header if available
                    retry_after = e.response.headers.get("Retry-After")
                    if retry_after:
                        backoff_time = int(retry_after)
                    else:
                        backoff_time = self.RETRY_BACKOFF_BASE ** attempt
                else:
                    # Exponential backoff for server errors
                    backoff_time = self.RETRY_BACKOFF_BASE ** attempt

                # Don't retry if this was the last attempt
                if attempt >= self.MAX_RETRIES:
                    logger.error(
                        f"Max retries exceeded for {method} {endpoint}",
                        extra={
                            "method": method,
                            "endpoint": endpoint,
                            "status_code": status_code,
                            "attempts": attempt + 1
                        }
                    )
                    raise

                # Log retry attempt
                logger.warning(
                    f"Retrying {method} {endpoint} after {backoff_time}s",
                    extra={
                        "method": method,
                        "endpoint": endpoint,
                        "status_code": status_code,
                        "attempt": attempt + 1,
                        "max_retries": self.MAX_RETRIES,
                        "backoff_seconds": backoff_time
                    }
                )

                # Wait before retrying
                await asyncio.sleep(backoff_time)

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exception = e

                # Calculate exponential backoff
                backoff_time = self.RETRY_BACKOFF_BASE ** attempt

                # Don't retry if this was the last attempt
                if attempt >= self.MAX_RETRIES:
                    logger.error(
                        f"Max retries exceeded for {method} {endpoint}",
                        extra={
                            "method": method,
                            "endpoint": endpoint,
                            "error_type": type(e).__name__,
                            "attempts": attempt + 1
                        }
                    )
                    raise

                # Log retry attempt
                logger.warning(
                    f"Retrying {method} {endpoint} after network error",
                    extra={
                        "method": method,
                        "endpoint": endpoint,
                        "error_type": type(e).__name__,
                        "attempt": attempt + 1,
                        "backoff_seconds": backoff_time
                    }
                )

                # Wait before retrying
                await asyncio.sleep(backoff_time)

        # Should not reach here, but raise last exception if we do
        if last_exception:
            raise last_exception
        raise RuntimeError("Unexpected exit from retry loop")

    async def post_commit_comment(
        self,
        owner: str,
        repo: str,
        sha: str,
        body: str
    ) -> Dict[str, Any]:
        """
        Post a comment on a specific commit.

        Args:
            owner: Repository owner (user or organization)
            repo: Repository name
            sha: Commit SHA to comment on
            body: Comment body (supports Markdown)

        Returns:
            Dict: GitHub API response with comment data

        Example:
            >>> comment = await client.post_commit_comment(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     sha="abc123",
            ...     body="## Conflicts Detected\\n\\nSee details..."
            ... )
            >>> print(comment["html_url"])
        """
        endpoint = f"/repos/{owner}/{repo}/commits/{sha}/comments"

        logger.info(
            f"Posting commit comment",
            extra={
                "owner": owner,
                "repo": repo,
                "sha": sha[:7],
                "body_length": len(body)
            }
        )

        try:
            response = await self._make_request(
                method="POST",
                endpoint=endpoint,
                json={"body": body}
            )

            logger.info(
                f"Commit comment posted successfully",
                extra={
                    "comment_id": response.get("id"),
                    "comment_url": response.get("html_url")
                }
            )

            return response

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to post commit comment",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "sha": sha[:7],
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def get_commit(
        self,
        owner: str,
        repo: str,
        sha: str
    ) -> Dict[str, Any]:
        """
        Get commit details.

        Args:
            owner: Repository owner
            repo: Repository name
            sha: Commit SHA

        Returns:
            Dict: Commit data
        """
        endpoint = f"/repos/{owner}/{repo}/commits/{sha}"
        return await self._make_request("GET", endpoint)

    async def get_repository(
        self,
        owner: str,
        repo: str
    ) -> Dict[str, Any]:
        """
        Get repository details.

        Args:
            owner: Repository owner
            repo: Repository name

        Returns:
            Dict: Repository data
        """
        endpoint = f"/repos/{owner}/{repo}"
        return await self._make_request("GET", endpoint)

    async def get_branch(
        self,
        owner: str,
        repo: str,
        branch: str
    ) -> Dict[str, Any]:
        """
        Get branch details.

        Args:
            owner: Repository owner
            repo: Repository name
            branch: Branch name

        Returns:
            Dict: Branch data including latest commit
        """
        # URL-encode branch name to handle slashes in branch names
        # e.g., 'feature/my-branch' -> 'feature%2Fmy-branch'
        encoded_branch = encode_ref_for_url(branch)
        endpoint = f"/repos/{owner}/{repo}/branches/{encoded_branch}"
        return await self._make_request("GET", endpoint)

    async def get_branch_tree_sha(
        self,
        owner: str,
        repo: str,
        branch: str
    ) -> str:
        """
        Resolve a branch name to its tree SHA.

        This method properly resolves branch names to tree SHAs by:
        1. Fetching the branch to get its commit SHA
        2. Fetching the commit to get its tree SHA

        This is the correct approach because GitHub's Git Trees API
        does NOT reliably resolve branch names for non-default branches.

        Args:
            owner: Repository owner
            repo: Repository name
            branch: Branch name to resolve

        Returns:
            str: The tree SHA for the branch's HEAD commit

        Raises:
            BranchNotFoundError: If the branch does not exist
            TreeResolutionError: If the tree SHA cannot be extracted

        Example:
            >>> tree_sha = await client.get_branch_tree_sha(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     branch="feature-branch"
            ... )
            >>> # Now use tree_sha with get_tree()
            >>> tree = await client.get_tree(owner, repo, tree_sha)
        """
        logger.info(
            f"Resolving branch to tree SHA",
            extra={
                "owner": owner,
                "repo": repo,
                "branch": branch
            }
        )

        try:
            # Step 1: Get the branch reference to obtain commit SHA
            branch_data = await self.get_branch(owner, repo, branch)

            commit_sha = branch_data.get("commit", {}).get("sha")
            if not commit_sha:
                raise TreeResolutionError(
                    branch,
                    "Branch exists but commit SHA not found in response"
                )

            # Step 2: Get the commit to extract tree SHA
            # The branch response includes commit.commit.tree.sha
            tree_sha = branch_data.get("commit", {}).get("commit", {}).get("tree", {}).get("sha")

            if not tree_sha:
                # Fallback: fetch commit directly if tree not in branch response
                commit_data = await self.get_commit(owner, repo, commit_sha)
                tree_sha = commit_data.get("commit", {}).get("tree", {}).get("sha")

            if not tree_sha:
                raise TreeResolutionError(
                    branch,
                    f"Could not extract tree SHA from commit {commit_sha[:7]}"
                )

            logger.info(
                f"Branch resolved to tree SHA",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "branch": branch,
                    "commit_sha": commit_sha[:7],
                    "tree_sha": tree_sha[:7]
                }
            )

            return tree_sha

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise BranchNotFoundError(branch, f"{owner}/{repo}")
            logger.error(
                f"Failed to resolve branch to tree SHA",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "branch": branch,
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise TreeResolutionError(branch, f"API error: {e.response.status_code}")

    async def update_file_contents(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        sha: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create or update file contents in a repository.

        Args:
            owner: Repository owner
            repo: Repository name
            path: File path in repository
            content: Base64-encoded file content
            message: Commit message
            branch: Branch name to commit to
            sha: Current file SHA (required for updates)

        Returns:
            Dict: GitHub API response with commit data
        """
        endpoint = f"/repos/{owner}/{repo}/contents/{path}"

        payload = {
            "message": message,
            "content": content,
            "branch": branch
        }

        if sha:
            payload["sha"] = sha

        return await self._make_request("PUT", endpoint, json=payload)

    async def get_file_contents(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str
    ) -> str:
        """
        Get file contents at specific ref.

        Args:
            owner: Repository owner
            repo: Repository name
            path: File path
            ref: Git ref (branch, tag, or commit SHA)

        Returns:
            str: File contents (decoded from base64)

        Raises:
            httpx.HTTPStatusError: If file not found or request fails
        """
        import base64

        endpoint = f"/repos/{owner}/{repo}/contents/{path}"

        logger.info(
            f"Fetching file contents",
            extra={
                "owner": owner,
                "repo": repo,
                "path": path,
                "ref": ref
            }
        )

        try:
            response = await self._make_request(
                method="GET",
                endpoint=endpoint,
                params={"ref": ref}
            )

            # Decode base64 content
            content = base64.b64decode(response["content"]).decode("utf-8")
            return content

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to fetch file contents",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "path": path,
                    "ref": ref,
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def compare_commits(
        self,
        owner: str,
        repo: str,
        base: str,
        head: str
    ) -> Dict[str, Any]:
        """
        Compare two commits/branches.

        Args:
            owner: Repository owner
            repo: Repository name
            base: Base ref (e.g., "main")
            head: Head ref (e.g., "feature-branch")

        Returns:
            Dict: Comparison data including files changed, commits, etc.
        """
        endpoint = f"/repos/{owner}/{repo}/compare/{base}...{head}"

        logger.info(
            f"Comparing commits",
            extra={
                "owner": owner,
                "repo": repo,
                "base": base,
                "head": head
            }
        )

        return await self._make_request("GET", endpoint)

    async def get_merge_base(
        self,
        owner: str,
        repo: str,
        source_branch: str,
        target_branch: str
    ) -> Dict[str, Any]:
        """
        Get the merge base (common ancestor) commit between two branches.

        Uses GitHub's Compare API to find the merge base commit,
        which is the most recent common ancestor of the two branches.

        Args:
            owner: Repository owner
            repo: Repository name
            source_branch: Source branch name (the branch being merged)
            target_branch: Target branch name (the branch being merged into)

        Returns:
            Dict containing:
                - merge_base_sha: SHA of the merge base commit
                - merge_base_commit: Full commit object with message, author, date, etc.
                - source_sha: Current SHA of source branch
                - target_sha: Current SHA of target branch
                - ahead_by: Commits source is ahead of merge base
                - behind_by: Commits target is ahead of merge base
                - status: Comparison status ("ahead", "behind", "diverged", "identical")

        Raises:
            httpx.HTTPStatusError: If comparison fails (e.g., no common ancestor)

        Example:
            >>> result = await client.get_merge_base(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     source_branch="feature",
            ...     target_branch="main"
            ... )
            >>> print(result["merge_base_sha"][:7])
            'abc1234'
        """
        logger.info(
            f"Getting merge base",
            extra={
                "owner": owner,
                "repo": repo,
                "source_branch": source_branch,
                "target_branch": target_branch
            }
        )

        try:
            # Use compare API: target...source gives us what's in source but not in target
            # The merge_base_commit is included in the response
            compare_result = await self.compare_commits(
                owner=owner,
                repo=repo,
                base=target_branch,
                head=source_branch
            )

            merge_base_commit = compare_result.get("merge_base_commit", {})

            result = {
                "merge_base_sha": merge_base_commit.get("sha"),
                "merge_base_commit": {
                    "sha": merge_base_commit.get("sha"),
                    "message": merge_base_commit.get("commit", {}).get("message", ""),
                    "author": merge_base_commit.get("commit", {}).get("author", {}),
                    "committer": merge_base_commit.get("commit", {}).get("committer", {}),
                    "html_url": merge_base_commit.get("html_url"),
                },
                "source_sha": compare_result.get("head", {}).get("sha") if compare_result.get("head") else None,
                "target_sha": compare_result.get("base", {}).get("sha") if compare_result.get("base") else None,
                "ahead_by": compare_result.get("ahead_by", 0),
                "behind_by": compare_result.get("behind_by", 0),
                "status": compare_result.get("status", "unknown"),
                "total_commits": compare_result.get("total_commits", 0),
            }

            logger.info(
                f"Merge base found",
                extra={
                    "merge_base_sha": result["merge_base_sha"][:7] if result["merge_base_sha"] else None,
                    "status": result["status"],
                    "ahead_by": result["ahead_by"],
                    "behind_by": result["behind_by"]
                }
            )

            return result

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to get merge base",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def list_installation_repositories(self) -> list[Dict[str, Any]]:
        """
        List repositories accessible to installation.

        Returns:
            list: List of repository data

        Example:
            >>> repos = await client.list_installation_repositories()
            >>> for repo in repos:
            ...     print(repo["full_name"])
        """
        endpoint = "/installation/repositories"

        logger.info(
            f"Listing installation repositories",
            extra={"installation_id": self.installation_id}
        )

        response = await self._make_request("GET", endpoint)
        return response.get("repositories", [])

    # =========================================================================
    # GitHub Checks API Methods
    # =========================================================================

    async def create_check_run(
        self,
        owner: str,
        repo: str,
        name: str,
        head_sha: str,
        status: str = "queued",
        conclusion: Optional[str] = None,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        text: Optional[str] = None,
        annotations: Optional[list[Dict[str, Any]]] = None,
        actions: Optional[list[Dict[str, str]]] = None,
        details_url: Optional[str] = None,
        external_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a new check run for a specific commit.

        GitHub Checks API allows displaying rich feedback directly in the PR/commit UI
        with status indicators, annotations, and action buttons.

        Args:
            owner: Repository owner
            repo: Repository name
            name: Name of the check (e.g., "MergeWeave Conflict Detection")
            head_sha: SHA of the commit to associate check with
            status: Current status - "queued", "in_progress", or "completed"
            conclusion: Required if status is "completed" - "action_required",
                       "cancelled", "failure", "neutral", "success", "skipped",
                       "stale", or "timed_out"
            title: Title for the check run output
            summary: Summary text (supports Markdown, max 65535 chars)
            text: Detailed text (supports Markdown, max 65535 chars)
            annotations: List of annotation objects for inline feedback
            actions: List of action button objects (max 3)
                     Each action: {"label": str, "description": str, "identifier": str}
            details_url: URL for "Details" link
            external_id: External identifier for tracking

        Returns:
            Dict: GitHub API response with check run data including 'id'

        Example:
            >>> check_run = await client.create_check_run(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     name="MergeWeave Conflict Detection",
            ...     head_sha="abc123",
            ...     status="completed",
            ...     conclusion="action_required",
            ...     title="Conflicts Detected",
            ...     summary="Found 3 merge conflicts with main branch",
            ...     actions=[{
            ...         "label": "Apply Resolution",
            ...         "description": "Apply MergeWeave's suggested fix",
            ...         "identifier": "apply_resolution:pkg_abc123"
            ...     }]
            ... )
        """
        endpoint = f"/repos/{owner}/{repo}/check-runs"

        # Build payload
        payload: Dict[str, Any] = {
            "name": name,
            "head_sha": head_sha,
            "status": status
        }

        if conclusion:
            payload["conclusion"] = conclusion

        if details_url:
            payload["details_url"] = details_url

        if external_id:
            payload["external_id"] = external_id

        # Build output object if any output fields provided
        if title or summary or text or annotations:
            output: Dict[str, Any] = {}
            if title:
                output["title"] = title
            if summary:
                output["summary"] = summary
            if text:
                output["text"] = text
            if annotations:
                output["annotations"] = annotations
            payload["output"] = output

        if actions:
            payload["actions"] = actions

        logger.info(
            f"Creating check run",
            extra={
                "owner": owner,
                "repo": repo,
                "check_name": name,
                "head_sha": head_sha[:7],
                "status": status,
                "conclusion": conclusion
            }
        )

        try:
            response = await self._make_request(
                method="POST",
                endpoint=endpoint,
                json=payload
            )

            logger.info(
                f"Check run created successfully",
                extra={
                    "check_run_id": response.get("id"),
                    "html_url": response.get("html_url")
                }
            )

            return response

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to create check run",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "head_sha": head_sha[:7],
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def update_check_run(
        self,
        owner: str,
        repo: str,
        check_run_id: int,
        status: Optional[str] = None,
        conclusion: Optional[str] = None,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        text: Optional[str] = None,
        annotations: Optional[list[Dict[str, Any]]] = None,
        actions: Optional[list[Dict[str, str]]] = None,
        details_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update an existing check run.

        Used to update status, add results, or modify action buttons.

        Args:
            owner: Repository owner
            repo: Repository name
            check_run_id: ID of the check run to update
            status: New status - "queued", "in_progress", or "completed"
            conclusion: Required if status is "completed"
            title: Title for the check run output
            summary: Summary text (supports Markdown)
            text: Detailed text (supports Markdown)
            annotations: List of annotation objects
            actions: List of action button objects (max 3)
            details_url: URL for "Details" link

        Returns:
            Dict: GitHub API response with updated check run data

        Example:
            >>> await client.update_check_run(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     check_run_id=12345,
            ...     status="completed",
            ...     conclusion="success",
            ...     title="Resolution Applied",
            ...     summary="Successfully applied MergeWeave resolution"
            ... )
        """
        endpoint = f"/repos/{owner}/{repo}/check-runs/{check_run_id}"

        # Build payload with only provided fields
        payload: Dict[str, Any] = {}

        if status:
            payload["status"] = status

        if conclusion:
            payload["conclusion"] = conclusion

        if details_url:
            payload["details_url"] = details_url

        # Build output object if any output fields provided
        if title or summary or text or annotations:
            output: Dict[str, Any] = {}
            if title:
                output["title"] = title
            if summary:
                output["summary"] = summary
            if text:
                output["text"] = text
            if annotations:
                output["annotations"] = annotations
            payload["output"] = output

        if actions:
            payload["actions"] = actions

        logger.info(
            f"Updating check run",
            extra={
                "owner": owner,
                "repo": repo,
                "check_run_id": check_run_id,
                "status": status,
                "conclusion": conclusion
            }
        )

        try:
            response = await self._make_request(
                method="PATCH",
                endpoint=endpoint,
                json=payload
            )

            logger.info(
                f"Check run updated successfully",
                extra={
                    "check_run_id": check_run_id,
                    "status": response.get("status"),
                    "conclusion": response.get("conclusion")
                }
            )

            return response

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to update check run",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "check_run_id": check_run_id,
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def list_check_runs_for_ref(
        self,
        owner: str,
        repo: str,
        ref: str,
        check_name: Optional[str] = None,
        status: Optional[str] = None,
        filter_param: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        List check runs for a specific ref (branch, tag, or SHA).

        Args:
            owner: Repository owner
            repo: Repository name
            ref: Git ref (branch name, tag, or SHA)
            check_name: Filter by check name
            status: Filter by status ("queued", "in_progress", "completed")
            filter_param: Filter by type ("latest" or "all")

        Returns:
            list: List of check run objects

        Example:
            >>> check_runs = await client.list_check_runs_for_ref(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     ref="feature-branch",
            ...     check_name="MergeWeave Conflict Detection"
            ... )
        """
        # URL-encode ref to handle slashes in branch names
        encoded_ref = encode_ref_for_url(ref)
        endpoint = f"/repos/{owner}/{repo}/commits/{encoded_ref}/check-runs"

        params: Dict[str, str] = {}
        if check_name:
            params["check_name"] = check_name
        if status:
            params["status"] = status
        if filter_param:
            params["filter"] = filter_param

        logger.info(
            f"Listing check runs for ref",
            extra={
                "owner": owner,
                "repo": repo,
                "ref": ref[:7] if len(ref) == 40 else ref,
                "check_name": check_name
            }
        )

        response = await self._make_request(
            method="GET",
            endpoint=endpoint,
            params=params if params else None
        )

        return response.get("check_runs", [])

    async def create_issue_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str
    ) -> Dict[str, Any]:
        """
        Create a comment on an issue or pull request.

        Note: PRs are issues in GitHub's API, so this works for PR comments too.

        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue or PR number
            body: Comment body (supports Markdown)

        Returns:
            Dict: GitHub API response with comment data

        Example:
            >>> comment = await client.create_issue_comment(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     issue_number=42,
            ...     body="## Conflicts Detected\\n\\nSee Check Run for details."
            ... )
        """
        endpoint = f"/repos/{owner}/{repo}/issues/{issue_number}/comments"

        logger.info(
            f"Creating issue/PR comment",
            extra={
                "owner": owner,
                "repo": repo,
                "issue_number": issue_number,
                "body_length": len(body)
            }
        )

        try:
            response = await self._make_request(
                method="POST",
                endpoint=endpoint,
                json={"body": body}
            )

            logger.info(
                f"Issue comment created successfully",
                extra={
                    "comment_id": response.get("id"),
                    "html_url": response.get("html_url")
                }
            )

            return response

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to create issue comment",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "issue_number": issue_number,
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        head: Optional[str] = None,
        base: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        List pull requests for a repository.

        Args:
            owner: Repository owner
            repo: Repository name
            state: Filter by state ("open", "closed", "all")
            head: Filter by head branch (format: "user:branch" or "branch")
            base: Filter by base branch

        Returns:
            list: List of pull request objects

        Example:
            >>> prs = await client.list_pull_requests(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     head="feature-branch"
            ... )
        """
        endpoint = f"/repos/{owner}/{repo}/pulls"

        params: Dict[str, str] = {"state": state}
        if head:
            # GitHub expects "owner:branch" format for cross-fork PRs
            # For same-repo PRs, just the branch name works
            params["head"] = head if ":" in head else f"{owner}:{head}"
        if base:
            params["base"] = base

        logger.info(
            f"Listing pull requests",
            extra={
                "owner": owner,
                "repo": repo,
                "state": state,
                "head": head,
                "base": base
            }
        )

        response = await self._make_request(
            method="GET",
            endpoint=endpoint,
            params=params
        )

        return response if isinstance(response, list) else []

    async def get_user_repository_permission(
        self,
        owner: str,
        repo: str,
        username: str
    ) -> Dict[str, Any]:
        """
        Get a user's permission level for a repository.

        Args:
            owner: Repository owner
            repo: Repository name
            username: GitHub username to check

        Returns:
            Dict: Permission data including 'permission' field
                  ("admin", "write", "read", "none")

        Example:
            >>> perm = await client.get_user_repository_permission(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     username="contributor"
            ... )
            >>> if perm["permission"] in ("admin", "write"):
            ...     print("User can push to repository")
        """
        endpoint = f"/repos/{owner}/{repo}/collaborators/{username}/permission"

        logger.info(
            f"Getting user repository permission",
            extra={
                "owner": owner,
                "repo": repo,
                "username": username
            }
        )

        try:
            response = await self._make_request("GET", endpoint)
            return response

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # User not found or no access
                return {"permission": "none", "user": {"login": username}}
            raise

    async def get_tree(
        self,
        owner: str,
        repo: str,
        tree_sha: str,
        recursive: bool = True
    ) -> Dict[str, Any]:
        """
        Get a Git tree from a repository.

        The tree represents the directory structure at a specific commit.
        Use recursive=True to get all files including those in subdirectories.

        Args:
            owner: Repository owner
            repo: Repository name
            tree_sha: SHA of the tree or branch name
            recursive: Whether to recursively fetch all subtrees (default True)

        Returns:
            Dict: Tree data including 'tree' array with file/directory entries
                  Each entry has: path, mode, type ('blob'/'tree'), sha, size (for blobs)

        Example:
            >>> tree = await client.get_tree(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     tree_sha="main",
            ...     recursive=True
            ... )
            >>> for item in tree["tree"]:
            ...     print(f"{item['path']} ({item['type']})")
        """
        endpoint = f"/repos/{owner}/{repo}/git/trees/{tree_sha}"

        params = {}
        if recursive:
            params["recursive"] = "1"

        logger.info(
            f"Fetching tree",
            extra={
                "owner": owner,
                "repo": repo,
                "tree_sha": tree_sha[:7] if len(tree_sha) == 40 else tree_sha,
                "recursive": recursive
            }
        )

        try:
            response = await self._make_request(
                method="GET",
                endpoint=endpoint,
                params=params if params else None
            )

            logger.info(
                f"Tree fetched successfully",
                extra={
                    "sha": response.get("sha", "")[:7],
                    "item_count": len(response.get("tree", [])),
                    "truncated": response.get("truncated", False)
                }
            )

            return response

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to fetch tree",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "tree_sha": tree_sha,
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def get_file_contents_raw(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str
    ) -> Dict[str, Any]:
        """
        Get file contents with full metadata (not decoded).

        Unlike get_file_contents() which returns decoded content,
        this returns the raw GitHub API response including base64 content,
        SHA, size, and other metadata.

        Args:
            owner: Repository owner
            repo: Repository name
            path: File path in repository
            ref: Git ref (branch, tag, or commit SHA)

        Returns:
            Dict: File data including content (base64), sha, size, encoding, etc.

        Raises:
            httpx.HTTPStatusError: If file not found or request fails
        """
        endpoint = f"/repos/{owner}/{repo}/contents/{path}"

        logger.info(
            f"Fetching file contents (raw)",
            extra={
                "owner": owner,
                "repo": repo,
                "path": path,
                "ref": ref
            }
        )

        try:
            response = await self._make_request(
                method="GET",
                endpoint=endpoint,
                params={"ref": ref}
            )
            return response

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to fetch file contents",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "path": path,
                    "ref": ref,
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    # =========================================================================
    # Branch Protection and Git Operations
    # =========================================================================

    async def is_branch_protected(
        self,
        owner: str,
        repo: str,
        branch: str
    ) -> bool:
        """
        Check if a branch has protection rules enabled.

        This is used to determine whether we can commit directly to a branch
        or need to create a separate branch with a PR for the resolution.

        Args:
            owner: Repository owner
            repo: Repository name
            branch: Branch name to check

        Returns:
            bool: True if branch is protected, False otherwise

        Example:
            >>> is_protected = await client.is_branch_protected(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     branch="main"
            ... )
            >>> if is_protected:
            ...     print("Need to create PR for changes")
        """
        # URL-encode branch name to handle slashes in branch names
        encoded_branch = encode_ref_for_url(branch)
        endpoint = f"/repos/{owner}/{repo}/branches/{encoded_branch}/protection"

        logger.info(
            f"Checking branch protection status",
            extra={
                "owner": owner,
                "repo": repo,
                "branch": branch
            }
        )

        try:
            await self._make_request("GET", endpoint)
            # If request succeeds, branch is protected
            logger.info(
                f"Branch is protected",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "branch": branch
                }
            )
            return True

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # 404 means no protection rules exist
                logger.info(
                    f"Branch is not protected",
                    extra={
                        "owner": owner,
                        "repo": repo,
                        "branch": branch
                    }
                )
                return False
            elif e.response.status_code == 403:
                # 403 means app lacks permission to check branch protection
                # This typically means the app doesn't have administration:read permission
                # Safe to assume not protected and proceed with direct commit
                logger.warning(
                    f"Cannot check branch protection (403 Forbidden) - assuming not protected",
                    extra={
                        "owner": owner,
                        "repo": repo,
                        "branch": branch,
                        "hint": "Add 'administration:read' permission to check branch protection"
                    }
                )
                return False
            # Re-raise other errors
            logger.error(
                f"Error checking branch protection",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "branch": branch,
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def create_branch(
        self,
        owner: str,
        repo: str,
        new_branch: str,
        from_ref: str
    ) -> Dict[str, Any]:
        """
        Create a new branch from an existing ref.

        Used when applying resolutions to protected branches - we create
        a new branch with the resolution and open a PR.

        Args:
            owner: Repository owner
            repo: Repository name
            new_branch: Name for the new branch (e.g., "mergeweave/resolution-20250114")
            from_ref: Source branch or SHA to branch from

        Returns:
            Dict: GitHub API response with ref data including 'ref' and 'object.sha'

        Example:
            >>> result = await client.create_branch(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     new_branch="mergeweave/resolution-20250114-123456",
            ...     from_ref="feature-branch"
            ... )
            >>> print(f"Created branch at {result['object']['sha'][:7]}")
        """
        logger.info(
            f"Creating new branch",
            extra={
                "owner": owner,
                "repo": repo,
                "new_branch": new_branch,
                "from_ref": from_ref
            }
        )

        try:
            # First, get the SHA of the source ref
            # URL-encode ref name to handle slashes in branch names
            encoded_from_ref = encode_ref_for_url(from_ref)
            ref_endpoint = f"/repos/{owner}/{repo}/git/ref/heads/{encoded_from_ref}"
            try:
                ref_response = await self._make_request("GET", ref_endpoint)
                source_sha = ref_response["object"]["sha"]
            except httpx.HTTPStatusError:
                # If not a branch, try as a commit SHA directly
                source_sha = from_ref

            # Create the new branch
            create_endpoint = f"/repos/{owner}/{repo}/git/refs"
            response = await self._make_request(
                "POST",
                create_endpoint,
                json={
                    "ref": f"refs/heads/{new_branch}",
                    "sha": source_sha
                }
            )

            logger.info(
                f"Branch created successfully",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "new_branch": new_branch,
                    "sha": response.get("object", {}).get("sha", "")[:7]
                }
            )

            return response

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to create branch",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "new_branch": new_branch,
                    "from_ref": from_ref,
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False
    ) -> Dict[str, Any]:
        """
        Create a pull request.

        Used to create PRs for resolutions when the target branch is protected.

        Args:
            owner: Repository owner
            repo: Repository name
            title: PR title
            head: Head branch (the branch with changes)
            base: Base branch (the branch to merge into)
            body: PR description (supports Markdown)
            draft: Whether to create as draft PR

        Returns:
            Dict: GitHub API response with PR data including 'number', 'html_url'

        Example:
            >>> pr = await client.create_pull_request(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     title="Mergeweave: Resolved conflicts",
            ...     head="mergeweave/resolution-123",
            ...     base="feature-branch",
            ...     body="## Automated Resolution\\n\\nResolved 3 files."
            ... )
            >>> print(f"Created PR #{pr['number']}")
        """
        endpoint = f"/repos/{owner}/{repo}/pulls"

        payload = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft
        }

        logger.info(
            f"Creating pull request",
            extra={
                "owner": owner,
                "repo": repo,
                "head": head,
                "base": base,
                "draft": draft
            }
        )

        try:
            response = await self._make_request(
                "POST",
                endpoint,
                json=payload
            )

            logger.info(
                f"Pull request created successfully",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "pr_number": response.get("number"),
                    "html_url": response.get("html_url")
                }
            )

            return response

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to create pull request",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "head": head,
                    "base": base,
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def get_commit_files(
        self,
        owner: str,
        repo: str,
        sha: str
    ) -> Dict[str, Any]:
        """
        Get the list of files changed in a specific commit.

        Returns detailed information about each file including status
        (added, modified, removed), additions, deletions, and patch data.

        Args:
            owner: Repository owner
            repo: Repository name
            sha: Commit SHA to get files for

        Returns:
            Dict containing:
                - sha: Commit SHA
                - files: List of file objects with path, status, additions, deletions
                - stats: Total additions, deletions, total changes
                - message: Commit message
                - author: Author info

        Example:
            >>> commit_data = await client.get_commit_files(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     sha="abc123def456"
            ... )
            >>> for file in commit_data["files"]:
            ...     print(f"{file['filename']} ({file['status']})")
        """
        endpoint = f"/repos/{owner}/{repo}/commits/{sha}"

        logger.info(
            f"Fetching files for commit",
            extra={
                "owner": owner,
                "repo": repo,
                "sha": sha[:7]
            }
        )

        try:
            response = await self._make_request(
                method="GET",
                endpoint=endpoint
            )

            # Extract relevant data
            files = response.get("files", [])
            stats = response.get("stats", {})
            commit_info = response.get("commit", {})

            result = {
                "sha": response.get("sha"),
                "files": [
                    {
                        "filename": f.get("filename"),
                        "status": f.get("status"),  # added, modified, removed, renamed
                        "additions": f.get("additions", 0),
                        "deletions": f.get("deletions", 0),
                        "changes": f.get("changes", 0),
                        "patch": f.get("patch"),  # May be None for binary files
                        "previous_filename": f.get("previous_filename"),  # For renames
                    }
                    for f in files
                ],
                "stats": {
                    "additions": stats.get("additions", 0),
                    "deletions": stats.get("deletions", 0),
                    "total": stats.get("total", 0),
                },
                "message": commit_info.get("message", ""),
                "author": {
                    "name": commit_info.get("author", {}).get("name"),
                    "email": commit_info.get("author", {}).get("email"),
                    "date": commit_info.get("author", {}).get("date"),
                },
                "html_url": response.get("html_url"),
            }

            logger.info(
                f"Fetched commit files",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "sha": sha[:7],
                    "file_count": len(files),
                    "additions": result["stats"]["additions"],
                    "deletions": result["stats"]["deletions"],
                }
            )

            return result

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to fetch commit files",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "sha": sha[:7],
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def get_commits_for_file(
        self,
        owner: str,
        repo: str,
        path: str,
        branch: str,
        limit: int = 20
    ) -> list[Dict[str, Any]]:
        """
        Get commit history for a specific file on a branch.

        Returns commits that modified the specified file, ordered by date
        (most recent first).

        Args:
            owner: Repository owner
            repo: Repository name
            path: File path in repository (e.g., "src/components/Header.tsx")
            branch: Branch name to get history from
            limit: Maximum number of commits to return (default: 20, max: 100)

        Returns:
            list: List of commit objects, each containing:
                - sha: Full commit SHA
                - commit: Object with message, author, committer info
                - author: GitHub user object (may be null)
                - html_url: GitHub URL for the commit

        Example:
            >>> commits = await client.get_commits_for_file(
            ...     owner="octocat",
            ...     repo="Hello-World",
            ...     path="src/main.py",
            ...     branch="main",
            ...     limit=10
            ... )
            >>> for c in commits:
            ...     print(f"{c['sha'][:7]} - {c['commit']['message']}")
        """
        endpoint = f"/repos/{owner}/{repo}/commits"

        params = {
            "path": path,
            "sha": branch,
            "per_page": min(limit, 100)
        }

        logger.info(
            f"Fetching commits for file",
            extra={
                "owner": owner,
                "repo": repo,
                "path": path,
                "branch": branch,
                "limit": limit
            }
        )

        try:
            response = await self._make_request(
                method="GET",
                endpoint=endpoint,
                params=params
            )

            # Response is a list of commits
            commits = response if isinstance(response, list) else []

            logger.info(
                f"Fetched commits for file",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "path": path,
                    "commit_count": len(commits)
                }
            )

            return commits

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to fetch commits for file",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "path": path,
                    "branch": branch,
                    "status_code": e.response.status_code,
                    "error": str(e)
                },
                exc_info=True
            )
            raise
