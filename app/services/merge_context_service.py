"""
MergeContext Service.

Orchestrates MergeContext lifecycle including:
- Creation from GitHub branches
- Conflict detection via branch comparison
- CRE resolution triggering
- Resolution application
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from uuid import UUID

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.merge_context import MergeContext, MergeContextStatus, SourceType
from app.models.version_manifest import OriginInfo
from app.services.branch_service import get_branch_service
from app.services.version_manifest import get_version_manifest_service
from app.services.cre_client import CREClient
from app.clients.github_client import GitHubAPIClient
from app.database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


class MergeContextService:
    """
    Service for MergeContext lifecycle management.

    Handles the complete lifecycle of a merge context from creation
    through resolution and application.
    """

    def __init__(self):
        self._branch_service = get_branch_service()
        self._version_service = get_version_manifest_service()
        self._cre_client = CREClient()

    async def create(
        self,
        installation_id: int,
        repository_full_name: str,
        base_branch: str,
        source_branch: str,
        user_id: Optional[UUID] = None,
        options: Optional[Dict[str, Any]] = None,
        origin: Optional[OriginInfo] = None
    ) -> MergeContext:
        """
        Create a new MergeContext from GitHub branches.

        This method:
        1. Validates branches exist
        2. Finds the merge-base commit
        3. Detects any conflicts
        4. Creates and persists the MergeContext

        Args:
            installation_id: GitHub installation ID
            repository_full_name: Full repository name (owner/repo)
            base_branch: Base/target branch (e.g., "main")
            source_branch: Source branch to merge
            user_id: Optional user ID for ownership
            options: Optional creation options
            origin: Optional request origin info

        Returns:
            Created MergeContext instance

        Raises:
            ValueError: If branch validation fails
            Exception: If creation fails
        """
        logger.info(
            "Creating MergeContext",
            extra={
                "installation_id": installation_id,
                "repository": repository_full_name,
                "base_branch": base_branch,
                "source_branch": source_branch
            }
        )

        processing_start = datetime.utcnow()

        # Build origin info if not provided
        if not origin:
            origin = OriginInfo(
                source="web_dashboard",
                source_version="1.0.0"
            )

        # Collect version manifest
        version_manifest = await self._version_service.collect(
            origin=origin,
            include_config=True
        )

        try:
            # Find merge-base and get branch info
            merge_base_result = await self._branch_service.find_merge_base(
                installation_id=installation_id,
                repository_full_name=repository_full_name,
                source_branch=source_branch,
                target_branch=base_branch
            )

            # Build branch config
            branch_config = {
                "base_branch": base_branch,
                "source_branch": source_branch,
                "target_branch": None,  # 2-way merge
                "base_sha": merge_base_result.target_sha,
                "source_sha": merge_base_result.source_sha,
                "target_sha": None,
                "merge_base_sha": merge_base_result.merge_base_sha,
                "commits_ahead_source": merge_base_result.commits_ahead_source,
                "commits_ahead_target": merge_base_result.commits_ahead_target,
            }

            # Detect conflicts
            conflict_set = await self._detect_conflicts(
                installation_id=installation_id,
                repository_full_name=repository_full_name,
                base_branch=base_branch,
                source_branch=source_branch
            )

            # Determine initial status
            status = MergeContextStatus.PENDING
            status_message = "MergeContext created, awaiting analysis"

            if conflict_set and conflict_set.get("total_conflicts", 0) > 0:
                status = MergeContextStatus.CONFLICT_DETECTED
                status_message = (
                    f"Detected {conflict_set['total_conflicts']} conflicts "
                    f"in {conflict_set['total_files_affected']} files"
                )

            # Calculate processing duration
            processing_end = datetime.utcnow()
            processing_duration_ms = int(
                (processing_end - processing_start).total_seconds() * 1000
            )

            # Create MergeContext
            merge_context = MergeContext(
                source_type=SourceType.GITHUB,
                installation_id=installation_id,
                repository_full_name=repository_full_name,
                user_id=user_id,
                branch_config=branch_config,
                commit_graph=None,  # Populate if options.include_ancestry
                conflict_set=conflict_set,
                resolution=None,
                version_manifest=version_manifest.model_dump(),
                status=status,
                status_message=status_message,
                processing_started_at=processing_start,
                processing_completed_at=processing_end,
                processing_duration_ms=processing_duration_ms
            )

            # Persist to database
            async with AsyncSessionLocal() as session:
                session.add(merge_context)
                await session.commit()
                await session.refresh(merge_context)

            logger.info(
                "MergeContext created",
                extra={
                    "merge_context_id": str(merge_context.id),
                    "status": status.value,
                    "has_conflicts": conflict_set is not None,
                    "processing_time_ms": processing_duration_ms
                }
            )

            return merge_context

        except Exception as e:
            logger.error(
                "Failed to create MergeContext",
                extra={
                    "installation_id": installation_id,
                    "repository": repository_full_name,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    async def get_by_id(
        self,
        merge_context_id: UUID,
        include_deleted: bool = False
    ) -> Optional[MergeContext]:
        """
        Get a MergeContext by ID.

        Args:
            merge_context_id: MergeContext UUID
            include_deleted: Include soft-deleted contexts

        Returns:
            MergeContext instance or None if not found
        """
        async with AsyncSessionLocal() as session:
            query = select(MergeContext).where(MergeContext.id == merge_context_id)

            if not include_deleted:
                query = query.where(MergeContext.deleted_at.is_(None))

            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: UUID,
        status: Optional[MergeContextStatus] = None,
        page: int = 1,
        per_page: int = 20
    ) -> tuple[List[MergeContext], int]:
        """
        List MergeContexts for a user.

        Args:
            user_id: User UUID
            status: Optional status filter
            page: Page number (1-indexed)
            per_page: Items per page

        Returns:
            Tuple of (list of MergeContexts, total count)
        """
        async with AsyncSessionLocal() as session:
            # Build query
            conditions = [
                MergeContext.user_id == user_id,
                MergeContext.deleted_at.is_(None)
            ]

            if status:
                conditions.append(MergeContext.status == status)

            query = (
                select(MergeContext)
                .where(and_(*conditions))
                .order_by(MergeContext.created_at.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
            )

            result = await session.execute(query)
            contexts = result.scalars().all()

            # Get count
            from sqlalchemy import func
            count_query = (
                select(func.count())
                .select_from(MergeContext)
                .where(and_(*conditions))
            )
            count_result = await session.execute(count_query)
            total = count_result.scalar() or 0

            return list(contexts), total

    async def resolve(
        self,
        merge_context_id: UUID,
        options: Optional[Dict[str, Any]] = None
    ) -> MergeContext:
        """
        Trigger CRE resolution for a MergeContext.

        Args:
            merge_context_id: MergeContext UUID
            options: Optional resolution options

        Returns:
            Updated MergeContext with resolution

        Raises:
            ValueError: If context not found or invalid state
        """
        async with AsyncSessionLocal() as session:
            merge_context = await session.get(MergeContext, merge_context_id)

            if not merge_context:
                raise ValueError(f"MergeContext not found: {merge_context_id}")

            if merge_context.deleted_at:
                raise ValueError(f"MergeContext has been deleted: {merge_context_id}")

            if merge_context.status not in (
                MergeContextStatus.PENDING,
                MergeContextStatus.CONFLICT_DETECTED
            ):
                raise ValueError(
                    f"Cannot resolve in status {merge_context.status.value}"
                )

            # Update status
            merge_context.status = MergeContextStatus.RESOLVING
            merge_context.status_message = "Requesting CRE resolution..."
            merge_context.processing_started_at = datetime.utcnow()
            await session.commit()

            try:
                owner, repo = merge_context.repository_full_name.split("/")
                branch_config = merge_context.branch_config

                # Call CRE
                result = await self._cre_client.resolve_conflicts(
                    installation_id=merge_context.installation_id,
                    owner=owner,
                    repo=repo,
                    source_branch=branch_config["source_branch"],
                    target_branch=branch_config["base_branch"]
                )

                # Update with result
                if result.success:
                    merge_context.status = MergeContextStatus.RESOLVED
                    merge_context.status_message = (
                        f"Resolution complete with {result.confidence_score} confidence"
                    )
                    merge_context.resolution = {
                        "status": "success",
                        "resolution_id": result.resolution_id,
                        "overall_confidence": float(result.confidence_score) if result.confidence_score else 0.0,
                        "resolved_files": {
                            path: {
                                "path": path,
                                "content": content,
                                "confidence": float(result.confidence_score) if result.confidence_score else 0.0,
                                "primary_strategy": result.strategy_used or "unknown"
                            }
                            for path, content in result.resolved_files.items()
                        },
                        "strategies_used": result.strategies_used,
                        "processing_time_ms": result.processing_time_ms,
                        "proposed_commit_message": result.proposed_commit_message,
                        "metadata": {
                            "expires_at": result.expires_at
                        }
                    }
                else:
                    merge_context.status = MergeContextStatus.FAILED
                    merge_context.status_message = result.error_message
                    merge_context.error_details = {
                        "error": result.error_message,
                        "timestamp": datetime.utcnow().isoformat()
                    }

                merge_context.processing_completed_at = datetime.utcnow()
                if merge_context.processing_started_at:
                    merge_context.processing_duration_ms = int(
                        (merge_context.processing_completed_at - merge_context.processing_started_at)
                        .total_seconds() * 1000
                    )

                await session.commit()
                await session.refresh(merge_context)

                logger.info(
                    "MergeContext resolution complete",
                    extra={
                        "merge_context_id": str(merge_context_id),
                        "status": merge_context.status.value,
                        "success": result.success
                    }
                )

                return merge_context

            except Exception as e:
                merge_context.set_failed(e, f"Resolution failed: {str(e)}")
                await session.commit()
                raise

    async def apply(
        self,
        merge_context_id: UUID,
        target_branch: str,
        commit_message: Optional[str] = None,
        create_pull_request: bool = False
    ) -> Dict[str, Any]:
        """
        Apply resolution to the repository.

        Args:
            merge_context_id: MergeContext UUID
            target_branch: Branch to apply resolution to
            commit_message: Optional custom commit message
            create_pull_request: Whether to create a PR

        Returns:
            Application result dictionary

        Raises:
            ValueError: If context not found or invalid state
        """
        async with AsyncSessionLocal() as session:
            merge_context = await session.get(MergeContext, merge_context_id)

            if not merge_context:
                raise ValueError(f"MergeContext not found: {merge_context_id}")

            if merge_context.status != MergeContextStatus.RESOLVED:
                raise ValueError(
                    f"Cannot apply in status {merge_context.status.value}. "
                    "Must be RESOLVED."
                )

            if not merge_context.resolution:
                raise ValueError("No resolution available to apply")

            owner, repo = merge_context.repository_full_name.split("/")
            resolved_files = merge_context.resolution.get("resolved_files", {})

            if not resolved_files:
                raise ValueError("No resolved files to apply")

            # Build commit message
            if not commit_message:
                commit_message = (
                    merge_context.resolution.get("proposed_commit_message") or
                    f"Resolve merge conflicts via MergeWeave\n\n"
                    f"Resolved {len(resolved_files)} files with "
                    f"{merge_context.resolution.get('overall_confidence', 0):.0%} confidence"
                )

            logger.info(
                "Applying resolution",
                extra={
                    "merge_context_id": str(merge_context_id),
                    "target_branch": target_branch,
                    "files_count": len(resolved_files)
                }
            )

            import base64

            try:
                async with GitHubAPIClient(merge_context.installation_id) as client:
                    # Get current branch HEAD for SHA
                    branch_data = await client.get_branch(owner, repo, target_branch)
                    current_sha = branch_data["commit"]["sha"]

                    applied_files = []

                    for path, file_info in resolved_files.items():
                        content = file_info.get("content", "")

                        # Get current file SHA (for updates)
                        try:
                            endpoint = f"/repos/{owner}/{repo}/contents/{path}"
                            params = {"ref": target_branch}
                            current_file = await client._make_request(
                                "GET", endpoint, params=params
                            )
                            file_sha = current_file.get("sha")
                        except Exception:
                            file_sha = None  # New file

                        # Update/create file
                        content_b64 = base64.b64encode(
                            content.encode("utf-8")
                        ).decode("ascii")

                        await client.update_file_contents(
                            owner=owner,
                            repo=repo,
                            path=path,
                            content=content_b64,
                            message=commit_message,
                            branch=target_branch,
                            sha=file_sha
                        )

                        applied_files.append(path)

                    # Get new commit SHA
                    branch_data = await client.get_branch(owner, repo, target_branch)
                    new_sha = branch_data["commit"]["sha"]

                    # Update status
                    merge_context.status = MergeContextStatus.APPLIED
                    merge_context.status_message = (
                        f"Applied to {target_branch}: {len(applied_files)} files modified"
                    )
                    await session.commit()

                    result = {
                        "commit_sha": new_sha,
                        "branch": target_branch,
                        "files_modified": len(applied_files),
                        "pull_request_url": None
                    }

                    logger.info(
                        "Resolution applied",
                        extra={
                            "merge_context_id": str(merge_context_id),
                            "commit_sha": new_sha,
                            "files_modified": len(applied_files)
                        }
                    )

                    return result

            except Exception as e:
                merge_context.set_failed(e, f"Application failed: {str(e)}")
                await session.commit()
                raise

    async def delete(self, merge_context_id: UUID) -> bool:
        """
        Soft-delete a MergeContext.

        Args:
            merge_context_id: MergeContext UUID

        Returns:
            True if deleted, False if not found
        """
        async with AsyncSessionLocal() as session:
            merge_context = await session.get(MergeContext, merge_context_id)

            if not merge_context or merge_context.deleted_at:
                return False

            merge_context.deleted_at = datetime.utcnow()
            await session.commit()

            logger.info(
                "MergeContext deleted",
                extra={"merge_context_id": str(merge_context_id)}
            )

            return True

    async def _detect_conflicts(
        self,
        installation_id: int,
        repository_full_name: str,
        base_branch: str,
        source_branch: str
    ) -> Optional[Dict[str, Any]]:
        """
        Detect conflicts between branches.

        Uses GitHub's compare API to find files with conflicts.

        Returns:
            ConflictSet dictionary or None if no conflicts
        """
        owner, repo = repository_full_name.split("/")

        try:
            async with GitHubAPIClient(installation_id) as client:
                comparison = await client.compare_commits(
                    owner, repo, base_branch, source_branch
                )

                # Get files with changes
                files = comparison.get("files", [])

                if not files:
                    return None

                # Simple conflict detection: files modified in both branches
                # For true 3-way merge conflict detection, we'd need to:
                # 1. Get merge-base
                # 2. Compare base to source
                # 3. Compare base to target
                # 4. Find overlapping modifications

                # For now, return files as potential conflicts
                conflicts = []
                files_affected = set()

                for f in files:
                    if f.get("status") in ("modified", "added"):
                        conflict_id = f"conflict_{len(conflicts)}"
                        conflicts.append({
                            "id": conflict_id,
                            "type": "textual",
                            "primary_file": f["filename"],
                            "affected_files": [f["filename"]],
                            "regions": None  # Would need file content comparison
                        })
                        files_affected.add(f["filename"])

                if not conflicts:
                    return None

                # Build by_file mapping
                by_file = {}
                for c in conflicts:
                    for f in c["affected_files"]:
                        if f not in by_file:
                            by_file[f] = []
                        by_file[f].append(c["id"])

                return {
                    "total_conflicts": len(conflicts),
                    "total_files_affected": len(files_affected),
                    "conflicts": conflicts,
                    "by_file": by_file
                }

        except Exception as e:
            logger.warning(
                "Conflict detection failed",
                extra={
                    "installation_id": installation_id,
                    "repository": repository_full_name,
                    "error": str(e)
                }
            )
            return None


# Singleton instance
_merge_context_service: Optional[MergeContextService] = None


def get_merge_context_service() -> MergeContextService:
    """Get singleton MergeContextService instance."""
    global _merge_context_service
    if _merge_context_service is None:
        _merge_context_service = MergeContextService()
    return _merge_context_service
