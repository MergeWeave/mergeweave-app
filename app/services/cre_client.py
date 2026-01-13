"""
CRE (Conflict Resolution Engine) Client.

Communicates with Public API's /v1/conflicts/resolve/package endpoint
to get automated conflict resolutions.

Updated to match documented API schema (2025-11-24).
"""

import logging
import httpx
import base64
from typing import Dict, List, Optional, Any
from datetime import datetime

from app.config import get_config
from app.clients.github_client import GitHubAPIClient

logger = logging.getLogger(__name__)


class CREResolutionResult:
    """
    Result from CRE resolution request.

    Attributes:
        success: Whether resolution was successful
        resolution_id: Unique ID for the resolution package
        strategy_used: Primary CRE strategy used (or comma-separated list)
        strategies_used: List of all strategies used
        resolved_files: Dict mapping file paths to resolved content (decoded)
        confidence_score: Overall confidence score (0.0-1.0)
        error_message: Error message if resolution failed
        processing_time_ms: Time taken by CRE to process
        proposed_commit_message: Suggested commit message
        file_diffs: Raw file_diffs from response
        git_operations: Raw git_operations from response
        expires_at: When the resolution package expires
    """

    def __init__(
        self,
        success: bool,
        resolution_id: Optional[str] = None,
        strategy_used: Optional[str] = None,
        strategies_used: Optional[List[str]] = None,
        resolved_files: Optional[Dict[str, str]] = None,
        confidence_score: Optional[str] = None,
        error_message: Optional[str] = None,
        processing_time_ms: Optional[int] = None,
        proposed_commit_message: Optional[str] = None,
        file_diffs: Optional[List[Dict]] = None,
        git_operations: Optional[List[Dict]] = None,
        expires_at: Optional[str] = None
    ):
        self.success = success
        self.resolution_id = resolution_id
        self.strategy_used = strategy_used
        self.strategies_used = strategies_used or []
        self.resolved_files = resolved_files or {}
        self.confidence_score = confidence_score
        self.error_message = error_message
        self.processing_time_ms = processing_time_ms
        self.proposed_commit_message = proposed_commit_message
        self.file_diffs = file_diffs or []
        self.git_operations = git_operations or []
        self.expires_at = expires_at


class CREClient:
    """
    Client for communicating with Conflict Resolution Engine (CRE) via Public API.

    Uses the /v1/conflicts/resolve/package endpoint to get resolution packages
    without applying changes to the repository.

    Usage:
        client = CREClient()
        result = await client.resolve_conflicts(
            installation_id=12345,
            owner="octocat",
            repo="Hello-World",
            source_branch="feature-branch",
            target_branch="main"
        )

        if result.success:
            print(f"Resolved {len(result.resolved_files)} files")
            print(f"Confidence: {result.confidence_score}")
            for path, content in result.resolved_files.items():
                print(f"  - {path}")
    """

    # API timeout: 120 seconds (CRE may take time for complex conflicts)
    API_TIMEOUT = 120.0

    # Endpoint path (no /api prefix per documentation)
    ENDPOINT_PATH = "/v1/conflicts/resolve/package"

    def __init__(self):
        """Initialize CRE client with configuration."""
        self.config = get_config()
        self.base_url = self.config.public_api_url.rstrip("/")
        self.api_key = self.config.operations_api_key

    async def resolve_conflicts(
        self,
        installation_id: int,
        owner: str,
        repo: str,
        source_branch: str,
        target_branch: str,
        create_backup: bool = False,
        create_commit: bool = False
    ) -> CREResolutionResult:
        """
        Request conflict resolution from CRE via Public API.

        This method:
        1. Gets a fresh GitHub installation token
        2. Calls the Public API with proper authentication
        3. Parses the response and extracts resolved file contents

        Args:
            installation_id: GitHub App installation ID
            owner: Repository owner
            repo: Repository name
            source_branch: Source branch being merged (feature branch)
            target_branch: Target branch (base, typically main)
            create_backup: Whether to create backup tag (default: False)
            create_commit: Whether to apply as commit (default: False for packages)

        Returns:
            CREResolutionResult: Resolution result with decoded file contents

        Note:
            For click-to-accept workflow, always use create_backup=False and
            create_commit=False to get a resolution package without applying changes.
        """
        endpoint = f"{self.base_url}{self.ENDPOINT_PATH}"

        logger.info(
            f"Requesting CRE resolution",
            extra={
                "owner": owner,
                "repo": repo,
                "source_branch": source_branch,
                "target_branch": target_branch,
                "endpoint": endpoint
            }
        )

        try:
            # Step 1: Get fresh GitHub installation token
            async with GitHubAPIClient(installation_id) as github_client:
                installation_token = await github_client.get_installation_token()

                if not installation_token:
                    logger.error(
                        f"Failed to get installation token",
                        extra={"installation_id": installation_id}
                    )
                    return CREResolutionResult(
                        success=False,
                        error_message="Failed to get GitHub installation token"
                    )

            # Step 2: Build request payload per documented schema
            payload = {
                "repository": {
                    "provider": "github",
                    "owner": owner,
                    "name": repo,  # Note: "name" not "repo"
                    "access_token": installation_token
                },
                "source_branch": source_branch,
                "target_branch": target_branch,
                "options": {
                    "create_backup": create_backup,
                    "create_commit": create_commit,
                    "dry_run": False
                }
            }

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            logger.debug(
                f"Sending CRE request",
                extra={
                    "endpoint": endpoint,
                    "owner": owner,
                    "repo": repo,
                    "source_branch": source_branch,
                    "target_branch": target_branch
                }
            )

            # Step 3: Make API request
            async with httpx.AsyncClient(timeout=self.API_TIMEOUT) as client:
                response = await client.post(
                    endpoint,
                    json=payload,
                    headers=headers
                )

                return await self._handle_response(response, owner, repo)

        except httpx.TimeoutException as e:
            logger.error(
                f"CRE request timed out",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "timeout": self.API_TIMEOUT
                },
                exc_info=True
            )

            return CREResolutionResult(
                success=False,
                error_message=f"Request timed out after {self.API_TIMEOUT}s"
            )

        except httpx.HTTPError as e:
            logger.error(
                f"CRE HTTP error",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "error": str(e)
                },
                exc_info=True
            )

            return CREResolutionResult(
                success=False,
                error_message=f"HTTP error: {str(e)}"
            )

        except Exception as e:
            logger.error(
                f"Unexpected CRE client error",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "error": str(e)
                },
                exc_info=True
            )

            return CREResolutionResult(
                success=False,
                error_message=f"Unexpected error: {str(e)}"
            )

    async def _handle_response(
        self,
        response: httpx.Response,
        owner: str,
        repo: str
    ) -> CREResolutionResult:
        """
        Handle API response and parse resolution package.

        Args:
            response: HTTP response from API
            owner: Repository owner (for logging)
            repo: Repository name (for logging)

        Returns:
            CREResolutionResult: Parsed resolution result
        """
        if response.status_code == 200:
            return await self._parse_success_response(response, owner, repo)

        elif response.status_code == 400:
            return self._parse_validation_error(response, owner, repo)

        elif response.status_code == 401:
            logger.error(
                f"CRE authentication failed",
                extra={"owner": owner, "repo": repo}
            )
            return CREResolutionResult(
                success=False,
                error_message="API authentication failed (invalid operations key)"
            )

        elif response.status_code == 404:
            logger.error(
                f"Repository not found or not accessible",
                extra={"owner": owner, "repo": repo}
            )
            return CREResolutionResult(
                success=False,
                error_message="Repository not found or installation token lacks access"
            )

        elif response.status_code == 408 or response.status_code == 504:
            logger.warning(
                f"CRE resolution timed out",
                extra={"owner": owner, "repo": repo, "status": response.status_code}
            )
            return CREResolutionResult(
                success=False,
                error_message="CRE processing timed out"
            )

        elif response.status_code == 503:
            logger.warning(
                f"Public API unavailable",
                extra={"owner": owner, "repo": repo}
            )
            return CREResolutionResult(
                success=False,
                error_message="Public API temporarily unavailable"
            )

        else:
            error_detail = response.text
            logger.error(
                f"CRE resolution failed",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "status_code": response.status_code,
                    "error": error_detail[:500]  # Truncate for logging
                }
            )

            return CREResolutionResult(
                success=False,
                error_message=f"CRE API error: HTTP {response.status_code}"
            )

    async def _parse_success_response(
        self,
        response: httpx.Response,
        owner: str,
        repo: str
    ) -> CREResolutionResult:
        """
        Parse successful resolution response.

        Extracts resolved file contents from git_operations, decoding base64.

        Args:
            response: HTTP response (status 200)
            owner: Repository owner
            repo: Repository name

        Returns:
            CREResolutionResult: Parsed result with decoded files
        """
        try:
            data = response.json()

            # Check if resolution was successful
            if not data.get("success", False):
                return CREResolutionResult(
                    success=False,
                    error_message=data.get("error", {}).get("message", "Resolution failed")
                )

            # Extract resolved files from git_operations
            resolved_files = {}
            git_operations = data.get("git_operations", [])

            for op in git_operations:
                if op.get("operation_type") in ("modify_file", "create_file"):
                    path = op.get("target_path")
                    content_b64 = op.get("content", "")

                    if path and content_b64:
                        try:
                            # Decode base64 content
                            content = base64.b64decode(content_b64).decode("utf-8")
                            resolved_files[path] = content
                        except Exception as decode_err:
                            logger.warning(
                                f"Failed to decode file content",
                                extra={
                                    "path": path,
                                    "error": str(decode_err)
                                }
                            )

            # Extract strategies
            strategies_used = data.get("strategies_used", [])
            strategy_used = ", ".join(strategies_used) if strategies_used else None

            # Extract confidence score
            confidence_score = data.get("confidence_score")
            if confidence_score is not None:
                confidence_score = str(confidence_score)

            # Extract processing time
            processing_time_ms = data.get("processing_time_ms")
            if processing_time_ms is not None:
                processing_time_ms = int(processing_time_ms)

            logger.info(
                f"CRE resolution successful",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "resolution_id": data.get("resolution_id"),
                    "strategies": strategies_used,
                    "confidence": confidence_score,
                    "files_resolved": len(resolved_files),
                    "processing_time_ms": processing_time_ms
                }
            )

            return CREResolutionResult(
                success=True,
                resolution_id=data.get("resolution_id"),
                strategy_used=strategy_used,
                strategies_used=strategies_used,
                resolved_files=resolved_files,
                confidence_score=confidence_score,
                processing_time_ms=processing_time_ms,
                proposed_commit_message=data.get("proposed_commit_message"),
                file_diffs=data.get("file_diffs", []),
                git_operations=git_operations,
                expires_at=data.get("expires_at")
            )

        except Exception as e:
            logger.error(
                f"Failed to parse CRE response",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "error": str(e)
                },
                exc_info=True
            )

            return CREResolutionResult(
                success=False,
                error_message=f"Failed to parse response: {str(e)}"
            )

    def _parse_validation_error(
        self,
        response: httpx.Response,
        owner: str,
        repo: str
    ) -> CREResolutionResult:
        """Parse validation error response (HTTP 400)."""
        try:
            data = response.json()
            error = data.get("error", {})
            message = error.get("message", "Validation error")
            details = error.get("details", {})

            logger.error(
                f"CRE validation error",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "message": message,
                    "details": details
                }
            )

            return CREResolutionResult(
                success=False,
                error_message=f"Validation error: {message}"
            )

        except Exception:
            return CREResolutionResult(
                success=False,
                error_message=f"Validation error: {response.text[:200]}"
            )

    async def health_check(self) -> bool:
        """
        Check if Public API is healthy.

        Returns:
            bool: True if healthy, False otherwise
        """
        health_url = f"{self.base_url}/health"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(health_url)
                return response.status_code == 200

        except Exception as e:
            logger.warning(
                f"CRE health check failed",
                extra={"error": str(e)}
            )
            return False


# Legacy method for backward compatibility
# This wraps the new API with the old interface signature
async def resolve_conflict_legacy(
    cre_client: CREClient,
    repository: str,
    source_ref: str,
    target_ref: str,
    commit_sha: str,
    conflicts: List[Dict[str, str]],
    installation_id: int,
    callback_url: str,
    delivery_id: str
) -> CREResolutionResult:
    """
    Legacy wrapper for backward compatibility.

    The old interface expected conflicts to be passed directly, but the new
    API detects conflicts automatically from the branch comparison.

    Args:
        cre_client: CREClient instance
        repository: Repository full name (owner/repo)
        source_ref: Source branch
        target_ref: Target branch
        commit_sha: Head commit SHA (not used by new API)
        conflicts: Conflicts list (not used by new API)
        installation_id: GitHub installation ID
        callback_url: Callback URL (not used by new API)
        delivery_id: Delivery ID (not used by new API)

    Returns:
        CREResolutionResult: Resolution result
    """
    owner, repo = repository.split("/")

    return await cre_client.resolve_conflicts(
        installation_id=installation_id,
        owner=owner,
        repo=repo,
        source_branch=source_ref,
        target_branch=target_ref
    )