"""
GitHub Branch Model

Represents branches within GitHub repositories.
Tracks branch state for conflict monitoring and resolution.
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base


class GitHubBranch(Base):
    """
    GitHub branch model for tracking branch state.

    Represents a branch within a GitHub repository. Used for tracking branch
    state to detect conflicts and monitor merge status.

    Attributes:
        id: Primary key
        repository_id: Foreign key to GitHubRepository
        branch_name: Name of the branch
        head_sha: Current HEAD commit SHA
        protected: Whether the branch is protected
        is_default: Whether this is the default branch
        last_commit_at: Timestamp of the last commit
        created_at: Timestamp when record was created
        updated_at: Timestamp when record was last updated

    Relationships:
        repository: Parent GitHubRepository

    Constraints:
        - Unique constraint on (repository_id, branch_name) to ensure one record per branch per repository
    """

    __tablename__ = "github_branches"

    # Primary key
    id = Column(Integer, primary_key=True, index=True)

    # Foreign keys
    repository_id = Column(
        Integer,
        ForeignKey("github_repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Branch information
    # Column name is 'name' in database (from migration 001)
    name = Column(
        String(255),
        nullable=False,
        index=True,
        comment="Name of the branch"
    )

    # Column name is 'commit_sha' in database (from migration 001)
    commit_sha = Column(
        String(40),
        nullable=False,
        index=True,
        comment="Current HEAD commit SHA"
    )

    # Branch metadata
    # Column name is 'is_protected' in database (from migration 001)
    is_protected = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default='false',
        comment="Whether the branch is protected"
    )

    # Timestamps
    # Column name is 'cached_at' in database (from migration 001)
    cached_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Timestamp when record was cached/updated"
    )

    # Relationships
    repository = relationship(
        "GitHubRepository",
        back_populates="branches"
    )

    # Unique constraint: one record per branch per repository
    __table_args__ = (
        UniqueConstraint('repository_id', 'name', name='uix_repo_branch'),
    )

    def __repr__(self):
        return f"<GitHubBranch(id={self.id}, repository_id={self.repository_id}, name='{self.name}', commit_sha='{self.commit_sha[:8]}')>"

    def to_dict(self):
        """Convert model to dictionary representation."""
        return {
            "id": self.id,
            "repository_id": self.repository_id,
            "name": self.name,
            "commit_sha": self.commit_sha,
            "is_protected": self.is_protected,
            "cached_at": self.cached_at.isoformat() if self.cached_at else None,
        }
