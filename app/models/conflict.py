"""
Conflict Detection Model

Represents detected merge conflicts.
Each conflict is associated with an installation.
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Numeric, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base


class ConflictDetection(Base):
    """
    Conflict detection model.

    Represents a merge conflict detected in a GitHub repository. Each conflict
    is associated with a GitHub App installation.

    Attributes (matching migration 001 schema):
        id: Primary key
        installation_id: Foreign key to GitHubInstallation
        repository_full_name: Full repository name (owner/repo)
        source_ref: Source branch/ref name
        target_ref: Target branch/ref name
        base_sha: Base commit SHA for merge simulation
        commit_sha: Head commit SHA
        has_conflicts: Boolean indicating if conflicts were detected
        conflicted_files: JSONB array of conflicted file paths
        resolution_generated: Whether CRE resolution was generated
        resolution_confidence: Confidence score of resolution (0.00-1.00)
        resolution_strategy: Strategy used for resolution
        user_action: User action taken (if any)
        user_action_at: Timestamp of user action
        detected_at: Timestamp when conflict was detected
        webhook_delivery_id: GitHub webhook delivery ID for deduplication
        event_type: Webhook event type (e.g., 'push')
        processing_duration_ms: Time taken to detect conflicts in milliseconds

    Relationships:
        installation: Parent GitHubInstallation
        resolution: Child ResolutionResult (one-to-one)
    """

    __tablename__ = "conflict_detections"

    # Primary key
    id = Column(Integer, primary_key=True, index=True)

    # Foreign keys
    installation_id = Column(
        Integer,
        ForeignKey("github_installations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Conflict context
    repository_full_name = Column(
        String(512),
        nullable=False,
        index=True,
        comment="Full repository name (owner/repo)"
    )

    source_ref = Column(
        String(255),
        nullable=False,
        comment="Source branch/ref name"
    )

    target_ref = Column(
        String(255),
        nullable=False,
        comment="Target branch/ref name"
    )

    base_sha = Column(
        String(40),
        nullable=True,
        comment="Base commit SHA for merge simulation"
    )

    commit_sha = Column(
        String(40),
        nullable=False,
        comment="Head commit SHA"
    )

    # Conflict details
    has_conflicts = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether conflicts were detected in the merge"
    )

    conflicted_files = Column(
        JSONB,
        nullable=True,
        comment="JSONB array of conflicted file paths"
    )

    # Resolution tracking
    resolution_generated = Column(
        Boolean,
        nullable=False,
        server_default='false',
        comment="Whether CRE resolution was generated"
    )

    resolution_confidence = Column(
        Numeric(precision=3, scale=2),
        nullable=True,
        comment="Confidence score of resolution (0.00-1.00)"
    )

    resolution_strategy = Column(
        String(100),
        nullable=True,
        comment="Strategy used for resolution"
    )

    # User action tracking
    user_action = Column(
        String(50),
        nullable=True,
        comment="User action taken (e.g., 'accepted', 'rejected')"
    )

    user_action_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of user action"
    )

    # Timestamps
    detected_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
        comment="Timestamp when conflict was detected"
    )

    # Webhook metadata (per Workstream 3 spec)
    webhook_delivery_id = Column(
        String(255),
        nullable=True,
        index=True,
        comment="GitHub webhook delivery ID for deduplication"
    )

    event_type = Column(
        String(50),
        nullable=True,
        comment="Webhook event type that triggered detection (e.g., 'push')"
    )

    # Performance metrics (per Workstream 3 spec)
    processing_duration_ms = Column(
        Integer,
        nullable=True,
        comment="Time taken to detect conflicts in milliseconds"
    )

    # Relationships
    installation = relationship(
        "GitHubInstallation",
        back_populates="conflicts"
    )

    resolution = relationship(
        "ResolutionResult",
        back_populates="conflict",
        uselist=False,
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<ConflictDetection(id={self.id}, repository='{self.repository_full_name}', source='{self.source_ref}', target='{self.target_ref}')>"

    def to_dict(self):
        """Convert model to dictionary representation."""
        return {
            "id": self.id,
            "installation_id": self.installation_id,
            "repository_full_name": self.repository_full_name,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "base_sha": self.base_sha,
            "commit_sha": self.commit_sha,
            "has_conflicts": self.has_conflicts,
            "conflicted_files": self.conflicted_files,
            "resolution_generated": self.resolution_generated,
            "resolution_confidence": float(self.resolution_confidence) if self.resolution_confidence else None,
            "resolution_strategy": self.resolution_strategy,
            "user_action": self.user_action,
            "user_action_at": self.user_action_at.isoformat() if self.user_action_at else None,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "webhook_delivery_id": self.webhook_delivery_id,
            "event_type": self.event_type,
            "processing_duration_ms": self.processing_duration_ms,
        }