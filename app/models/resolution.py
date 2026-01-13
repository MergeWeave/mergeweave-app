"""
Resolution Result Model

Represents CRE conflict resolution results.
Each resolution is linked to a conflict detection record.
"""

import enum
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base


class ResolutionStatus(str, enum.Enum):
    """Resolution status enumeration."""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    PENDING = "pending"
    APPLIED = "applied"  # Resolution has been applied to the repository
    EXPIRED = "expired"  # Resolution package has expired or been deleted


class ResolutionResult(Base):
    """
    CRE resolution result model.

    Represents the result of attempting to resolve a merge conflict using the
    Conflict Resolution Engine (CRE). Each resolution is linked to a conflict
    detection record.

    Attributes:
        id: Primary key
        conflict_id: Foreign key to ConflictDetection (one-to-one)
        status: Resolution status (success, partial, failed, pending, applied, expired)
        strategy_used: CRE strategy used for resolution
        resolved_files: JSON array of successfully resolved file paths
        unresolved_files: JSON array of files that couldn't be resolved
        resolution_dag: JSON representation of the resolved DAG
        confidence_score: Confidence score for the resolution (0.0-1.0)
        error_message: Error message if resolution failed
        processing_time_ms: Time taken to process resolution in milliseconds
        resolution_id: Unique ID for the resolution package (UUID)
        resolution_package_url: S3/MinIO URL for the resolution package
        resolution_package_size: Size of the resolution package in bytes
        comment_url: GitHub comment URL where resolution was posted
        applied_at: Timestamp when the resolution was applied to the repository
        created_at: Timestamp when resolution was attempted
        updated_at: Timestamp when record was last updated

    Relationships:
        conflict: Parent ConflictDetection (one-to-one)
    """

    __tablename__ = "resolution_results"

    # Primary key
    id = Column(Integer, primary_key=True, index=True)

    # Foreign keys
    conflict_id = Column(
        Integer,
        ForeignKey("conflict_detections.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
        comment="Foreign key to ConflictDetection (one-to-one)"
    )

    # Resolution status (stored as varchar to match existing schema)
    status = Column(
        String(50),
        nullable=False,
        default="pending",
        index=True,
        comment="Resolution status (success, partial, failed, pending, applied, expired)"
    )

    # Resolution details
    strategy_used = Column(
        String(100),
        nullable=True,
        comment="CRE strategy used for resolution"
    )

    resolved_files = Column(
        JSON,
        nullable=True,
        comment="JSON array of successfully resolved file paths"
    )

    unresolved_files = Column(
        JSON,
        nullable=True,
        comment="JSON array of files that couldn't be resolved"
    )

    resolution_dag = Column(
        JSON,
        nullable=True,
        comment="JSON representation of the resolved DAG"
    )

    confidence_score = Column(
        String(20),
        nullable=True,
        comment="Confidence score for the resolution (e.g., '0.95', 'high', 'medium')"
    )

    # Error handling
    error_message = Column(
        Text,
        nullable=True,
        comment="Error message if resolution failed"
    )

    # Performance metrics
    processing_time_ms = Column(
        Integer,
        nullable=True,
        comment="Time taken to process resolution in milliseconds"
    )

    # Storage metadata (per Workstream 3 spec)
    resolution_id = Column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
        comment="Unique ID for the resolution package (UUID)"
    )

    resolution_package_url = Column(
        String(1024),
        nullable=True,
        comment="S3/MinIO URL for the resolution package"
    )

    resolution_package_size = Column(
        Integer,
        nullable=True,
        comment="Size of the resolution package in bytes"
    )

    # GitHub integration metadata (per Workstream 3 spec)
    comment_url = Column(
        String(1024),
        nullable=True,
        comment="GitHub comment URL where resolution was posted"
    )

    applied_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the resolution was applied to the repository"
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Timestamp when resolution was attempted"
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="Timestamp when record was last updated"
    )

    # Relationships
    conflict = relationship(
        "ConflictDetection",
        back_populates="resolution"
    )

    def __repr__(self):
        return f"<ResolutionResult(id={self.id}, conflict_id={self.conflict_id}, status='{self.status}')>"

    def to_dict(self):
        """Convert model to dictionary representation."""
        return {
            "id": self.id,
            "conflict_id": self.conflict_id,
            "status": self.status if self.status else None,
            "strategy_used": self.strategy_used,
            "resolved_files": self.resolved_files,
            "unresolved_files": self.unresolved_files,
            "resolution_dag": self.resolution_dag,
            "confidence_score": self.confidence_score,
            "error_message": self.error_message,
            "processing_time_ms": self.processing_time_ms,
            "resolution_id": self.resolution_id,
            "resolution_package_url": self.resolution_package_url,
            "resolution_package_size": self.resolution_package_size,
            "comment_url": self.comment_url,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
