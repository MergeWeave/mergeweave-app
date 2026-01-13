"""
GitHub Installation Model

Represents GitHub App installations.
Each installation belongs to a user and contains multiple repositories.
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base
from app.models.types import UUID


class GitHubInstallation(Base):
    """
    GitHub App installation model.

    Represents a GitHub App installation for a user. Each installation
    corresponds to a GitHub organization or user account that has installed
    the MergeWeave GitHub App.

    Attributes:
        id: Primary key
        user_id: Foreign key to User
        installation_id: GitHub's unique installation ID
        account_id: GitHub account ID (org or user)
        account_login: GitHub account login name
        account_type: Type of account (Organization or User)
        target_type: Installation target type
        permissions: JSON object containing installation permissions
        events: JSON array of webhook events this installation subscribes to
        is_active: Whether this installation is active
        suspended_at: Timestamp when installation was suspended (if applicable)
        created_at: Timestamp when installation was created
        updated_at: Timestamp when installation was last updated

    Relationships:
        user: Parent User
        repositories: Child GitHubRepository records
        conflicts: Child ConflictDetection records
    """

    __tablename__ = "github_installations"

    # Primary key
    id = Column(Integer, primary_key=True, index=True)

    # Foreign keys (UUID to match Public API users table)
    user_id = Column(
        UUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # GitHub installation data
    installation_id = Column(
        Integer,
        unique=True,
        nullable=False,
        index=True,
        comment="GitHub's unique installation ID"
    )

    account_id = Column(
        Integer,
        nullable=False,
        index=True,
        comment="GitHub account ID (org or user)"
    )

    account_login = Column(
        String(255),
        nullable=False,
        index=True,
        comment="GitHub account login name"
    )

    account_type = Column(
        String(50),
        nullable=False,
        comment="Type of account (Organization or User)"
    )

    target_type = Column(
        String(50),
        nullable=False,
        comment="Installation target type"
    )

    # Installation metadata
    permissions = Column(
        JSON,
        nullable=True,
        comment="JSON object containing installation permissions"
    )

    events = Column(
        JSON,
        nullable=True,
        comment="JSON array of webhook events this installation subscribes to"
    )

    # Status
    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
        comment="Whether this installation is active"
    )

    suspended_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when installation was suspended (if applicable)"
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Timestamp when installation was created"
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="Timestamp when installation was last updated"
    )

    # Relationships
    user = relationship(
        "User",
        back_populates="installations"
    )

    repositories = relationship(
        "GitHubRepository",
        back_populates="installation",
        cascade="all, delete-orphan"
    )

    conflicts = relationship(
        "ConflictDetection",
        back_populates="installation"
    )

    def __repr__(self):
        return f"<GitHubInstallation(id={self.id}, installation_id={self.installation_id}, account_login='{self.account_login}')>"

    def to_dict(self):
        """Convert model to dictionary representation."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "installation_id": self.installation_id,
            "account_id": self.account_id,
            "account_login": self.account_login,
            "account_type": self.account_type,
            "target_type": self.target_type,
            "permissions": self.permissions,
            "events": self.events,
            "is_active": self.is_active,
            "suspended_at": self.suspended_at.isoformat() if self.suspended_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
