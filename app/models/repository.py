"""
GitHub Repository Model

Represents GitHub repositories tracked under installations.
Each repository belongs to a GitHub App installation and contains branches.
"""

from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base


class GitHubRepository(Base):
    """
    GitHub repository model.

    Represents a GitHub repository that is monitored by the MergeWeave GitHub App.
    Each repository belongs to a specific installation and contains multiple branches.

    Attributes (matching migration 001 schema):
        id: Primary key (internal DB ID)
        installation_id: Foreign key to GitHubInstallation
        github_repo_id: GitHub's unique repository ID (BigInteger)
        owner: Repository owner's GitHub login
        name: Repository name only
        full_name: Full repository name (owner/repo)
        default_branch: Default branch name (usually 'main' or 'master')
        is_private: Whether the repository is private
        cached_at: Timestamp when record was cached/updated

    Relationships:
        installation: Parent GitHubInstallation
        branches: Child GitHubBranch records
    """

    __tablename__ = "github_repositories"

    # Primary key
    id = Column(Integer, primary_key=True, index=True)

    # Foreign keys
    installation_id = Column(
        Integer,
        ForeignKey("github_installations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # GitHub repository data
    github_repo_id = Column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
        comment="GitHub's unique repository ID"
    )

    owner = Column(
        String(255),
        nullable=False,
        index=True,
        comment="Repository owner's GitHub login"
    )

    name = Column(
        String(255),
        nullable=False,
        comment="Repository name only"
    )

    full_name = Column(
        String(512),
        nullable=False,
        index=True,
        comment="Full repository name (owner/repo)"
    )

    # Repository metadata
    default_branch = Column(
        String(255),
        nullable=True,
        comment="Default branch name (usually 'main' or 'master')"
    )

    is_private = Column(
        Boolean,
        nullable=False,
        server_default='false',
        comment="Whether the repository is private"
    )

    # Timestamps
    cached_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Timestamp when record was cached/updated"
    )

    # Relationships
    installation = relationship(
        "GitHubInstallation",
        back_populates="repositories"
    )

    branches = relationship(
        "GitHubBranch",
        back_populates="repository",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<GitHubRepository(id={self.id}, full_name='{self.full_name}', github_repo_id={self.github_repo_id})>"

    def to_dict(self):
        """Convert model to dictionary representation."""
        return {
            "id": self.id,
            "installation_id": self.installation_id,
            "github_repo_id": self.github_repo_id,
            "owner": self.owner,
            "name": self.name,
            "full_name": self.full_name,
            "default_branch": self.default_branch,
            "is_private": self.is_private,
            "cached_at": self.cached_at.isoformat() if self.cached_at else None,
        }