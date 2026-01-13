"""
Resolution Package Model

Represents temporary resolution packages from the Public API.
Used for the click-to-accept workflow with 24-hour expiry.
"""

from sqlalchemy import Column, Integer, BigInteger, String, DateTime, JSON, Index
from sqlalchemy.sql import func
from app.models.base import Base


class ResolutionPackage(Base):
    """
    Resolution package model for click-to-accept workflow.

    Stores resolution packages returned by the Public API for 24 hours.
    When a push event triggers conflict detection, the resolution package
    is stored here and the user is presented with a button to accept it.

    This is separate from ResolutionResult which tracks historical/applied resolutions.
    ResolutionPackage is temporary storage (24h TTL) for pending user decisions.

    Attributes:
        id: Primary key (auto-increment)
        package_id: Unique package identifier (format: pkg_<random>)
        installation_id: GitHub installation ID (not FK to avoid cascade issues)
        repository_owner: Repository owner (GitHub username or org)
        repository_name: Repository name
        source_branch: Source branch name (where conflicts exist)
        source_commit_sha: Source commit SHA (HEAD of source branch)
        target_branch: Target branch name (base branch to merge into)
        package_data: Full resolution package from Public API (JSON)
        created_at: Timestamp when package was created
        expires_at: Timestamp when package expires (created_at + 24h)
        applied_at: Timestamp when resolution was applied (NULL if pending)
        applied_by: GitHub username who applied the resolution (NULL if pending)
        check_run_id: GitHub check run ID associated with this package

    Indexes:
        - package_id (unique, for retrieval)
        - installation_id (for querying by installation)
        - source_commit_sha (for querying by commit)
        - expires_at (for cleanup queries)
        - (installation_id, repository_owner, repository_name) composite (for repo queries)
    """

    __tablename__ = "resolution_packages"

    # Primary key
    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="Primary key"
    )

    # Package identifier
    package_id = Column(
        String(32),
        unique=True,
        nullable=False,
        index=True,
        comment="Unique package identifier (format: pkg_<random>)"
    )

    # Installation context (not FK to avoid cascade complexity)
    installation_id = Column(
        BigInteger,  # Must be BigInteger - GitHub installation IDs can exceed int32
        nullable=False,
        index=True,
        comment="GitHub installation ID"
    )

    # Repository context
    repository_owner = Column(
        String(255),
        nullable=False,
        comment="Repository owner (GitHub username or org)"
    )

    repository_name = Column(
        String(255),
        nullable=False,
        comment="Repository name"
    )

    # Branch context
    source_branch = Column(
        String(255),
        nullable=False,
        comment="Source branch name (where conflicts exist)"
    )

    source_commit_sha = Column(
        String(40),
        nullable=False,
        index=True,
        comment="Source commit SHA (HEAD of source branch)"
    )

    target_branch = Column(
        String(255),
        nullable=False,
        comment="Target branch name (base branch to merge into)"
    )

    # Resolution package (full JSON from Public API)
    package_data = Column(
        JSON,
        nullable=False,
        comment="Full resolution package from Public API (JSON)"
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Timestamp when package was created"
    )

    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment="Timestamp when package expires (created_at + 24h)"
    )

    # Application tracking
    applied_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when resolution was applied (NULL if pending)"
    )

    applied_by = Column(
        String(255),
        nullable=True,
        comment="GitHub username who applied the resolution (NULL if pending)"
    )

    # GitHub integration metadata
    check_run_id = Column(
        BigInteger,  # Must be BigInteger - GitHub check run IDs exceed int32
        nullable=True,
        comment="GitHub check run ID associated with this package"
    )

    # Composite indexes
    __table_args__ = (
        Index(
            "idx_resolution_package_repo",
            "installation_id",
            "repository_owner",
            "repository_name"
        ),
        Index(
            "idx_resolution_package_expires",
            "expires_at"
        ),
    )

    def __repr__(self):
        status = "applied" if self.applied_at else "pending"
        return (
            f"<ResolutionPackage(id={self.id}, "
            f"package_id='{self.package_id}', "
            f"repository='{self.repository_owner}/{self.repository_name}', "
            f"status='{status}')>"
        )

    def to_dict(self):
        """Convert model to dictionary representation."""
        return {
            "id": self.id,
            "package_id": self.package_id,
            "installation_id": self.installation_id,
            "repository_owner": self.repository_owner,
            "repository_name": self.repository_name,
            "source_branch": self.source_branch,
            "source_commit_sha": self.source_commit_sha,
            "target_branch": self.target_branch,
            "package_data": self.package_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "applied_by": self.applied_by,
            "check_run_id": self.check_run_id,
        }

    @property
    def is_expired(self) -> bool:
        """Check if package has expired."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_applied(self) -> bool:
        """Check if package has been applied."""
        return self.applied_at is not None

    @property
    def repository_full_name(self) -> str:
        """Get full repository name (owner/repo)."""
        return f"{self.repository_owner}/{self.repository_name}"