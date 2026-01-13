"""
MergeContext SQLAlchemy Model.

Database model for storing merge context state and history.
Central entity for the Dashboard API's GitHub integration.
"""

from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID, uuid4
import enum

from sqlalchemy import (
    Column, String, DateTime, Integer, BigInteger, Text,
    Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.sql import func

from app.models.base import Base


class MergeContextStatus(str, enum.Enum):
    """Status states for MergeContext lifecycle."""
    PENDING = "pending"
    ANALYZING = "analyzing"
    CONFLICT_DETECTED = "conflict_detected"
    RESOLVING = "resolving"
    RESOLVED = "resolved"
    APPLIED = "applied"
    FAILED = "failed"


class SourceType(str, enum.Enum):
    """Source types for MergeContext origin."""
    GITHUB = "github"
    LOCAL = "local"
    MANUAL = "manual"


class MergeContext(Base):
    """
    SQLAlchemy model for merge contexts.

    Stores the complete state of a merge operation including
    branch configuration, conflict data, resolution, and version manifest.

    This is the central entity for the Dashboard API, representing
    a merge operation from creation through resolution and application.

    Attributes:
        id: Primary key (UUID)
        source_type: Origin of the merge context (github, local, manual)
        installation_id: GitHub App installation ID (for github source)
        repository_full_name: Full repository name (owner/repo)
        user_id: User ID from Public API
        branch_config: Branch selection and merge-base information (JSONB)
        commit_graph: 3-way diff data with ancestry (JSONB)
        conflict_set: Detected conflicts grouped for navigation (JSONB)
        resolution: CRE resolution result (JSONB)
        version_manifest: Complete version information for debugging (JSONB)
        status: Current status in the lifecycle
        status_message: Human-readable status description
        error_details: Error information if status is failed (JSONB)
        processing_started_at: When processing began
        processing_completed_at: When processing completed
        processing_duration_ms: Total processing time in milliseconds
        created_at: Creation timestamp
        updated_at: Last update timestamp
        deleted_at: Soft delete timestamp
    """

    __tablename__ = 'merge_contexts'

    # Primary key
    id = Column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid()
    )

    # Source information
    source_type = Column(
        SAEnum(SourceType, name='source_type', create_type=False),
        nullable=False,
        comment='Origin of the merge context'
    )
    installation_id = Column(
        BigInteger,
        nullable=True,
        index=True,
        comment='GitHub installation ID (null for local/manual)'
    )
    repository_full_name = Column(
        String(255),
        nullable=True,
        index=True,
        comment='Full repository name (owner/repo)'
    )

    # User ownership
    user_id = Column(
        PGUUID(as_uuid=True),
        nullable=True,
        index=True,
        comment='User ID from Public API'
    )

    # Branch configuration (JSONB)
    branch_config = Column(
        JSONB,
        nullable=False,
        comment='Branch selection and merge-base info'
    )

    # Commit graph (JSONB)
    commit_graph = Column(
        JSONB,
        nullable=True,
        comment='CommitGraph with base, source, target ancestry'
    )

    # Conflict information (JSONB)
    conflict_set = Column(
        JSONB,
        nullable=True,
        comment='Detected conflicts grouped for navigation'
    )

    # Resolution result (JSONB)
    resolution = Column(
        JSONB,
        nullable=True,
        comment='CRE resolution result'
    )

    # Version manifest (JSONB, always present)
    version_manifest = Column(
        JSONB,
        nullable=False,
        comment='Complete version information for debugging'
    )

    # Status tracking
    status = Column(
        SAEnum(MergeContextStatus, name='merge_context_status', create_type=False),
        nullable=False,
        default=MergeContextStatus.PENDING,
        server_default='pending',
        index=True
    )
    status_message = Column(
        Text,
        nullable=True,
        comment='Human-readable status message'
    )
    error_details = Column(
        JSONB,
        nullable=True,
        comment='Error information if status is failed'
    )

    # Processing metadata
    processing_started_at = Column(
        DateTime(timezone=True),
        nullable=True
    )
    processing_completed_at = Column(
        DateTime(timezone=True),
        nullable=True
    )
    processing_duration_ms = Column(
        Integer,
        nullable=True
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    # Soft delete
    deleted_at = Column(
        DateTime(timezone=True),
        nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<MergeContext(id={self.id}, "
            f"source={self.source_type.value if self.source_type else 'None'}, "
            f"status={self.status.value if self.status else 'None'})>"
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert model to dictionary for API responses.

        Returns:
            Dictionary representation suitable for JSON serialization.
        """
        return {
            "id": str(self.id) if self.id else None,
            "source_type": self.source_type.value if self.source_type else None,
            "installation_id": self.installation_id,
            "repository_full_name": self.repository_full_name,
            "user_id": str(self.user_id) if self.user_id else None,
            "branch_config": self.branch_config,
            "commit_graph": self.commit_graph,
            "conflict_set": self.conflict_set,
            "resolution": self.resolution,
            "version_manifest": self.version_manifest,
            "status": self.status.value if self.status else None,
            "status_message": self.status_message,
            "error_details": self.error_details,
            "processing_started_at": (
                self.processing_started_at.isoformat()
                if self.processing_started_at else None
            ),
            "processing_completed_at": (
                self.processing_completed_at.isoformat()
                if self.processing_completed_at else None
            ),
            "processing_duration_ms": self.processing_duration_ms,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @property
    def is_active(self) -> bool:
        """Check if context is not soft-deleted."""
        return self.deleted_at is None

    @property
    def is_processing(self) -> bool:
        """Check if context is currently being processed."""
        return self.status in (
            MergeContextStatus.ANALYZING,
            MergeContextStatus.RESOLVING
        )

    @property
    def has_conflicts(self) -> bool:
        """Check if conflicts were detected."""
        return (
            self.conflict_set is not None and
            self.conflict_set.get("total_conflicts", 0) > 0
        )

    @property
    def is_resolved(self) -> bool:
        """Check if resolution is complete."""
        return self.status in (
            MergeContextStatus.RESOLVED,
            MergeContextStatus.APPLIED
        )

    def set_failed(self, error: Exception, message: Optional[str] = None) -> None:
        """
        Set the context to failed status with error details.

        Args:
            error: The exception that caused the failure
            message: Optional custom error message
        """
        self.status = MergeContextStatus.FAILED
        self.status_message = message or f"Failed: {str(error)}"
        self.error_details = {
            "error": str(error),
            "error_type": type(error).__name__,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.processing_completed_at = datetime.utcnow()
        if self.processing_started_at:
            self.processing_duration_ms = int(
                (self.processing_completed_at - self.processing_started_at)
                .total_seconds() * 1000
            )
