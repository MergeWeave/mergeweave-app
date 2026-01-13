"""
User Model

Represents MergeWeave user accounts (shared with Public API).
Users can have multiple GitHub App installations.

IMPORTANT: This model MUST match the Public API's users table schema exactly.
Do not add fields without coordinating with Public API team.
"""

import uuid
from sqlalchemy import Column, String, Boolean, DateTime, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base
from app.models.types import UUID


class User(Base):
    """
    MergeWeave user model (shared schema with Public API).

    Represents a user account in the MergeWeave system. Each user can have
    multiple GitHub App installations across different organizations/accounts.

    This model references the existing 'users' table created by the Public API.
    The schema is defined in public-api/app/models/database.py and must match exactly.

    Attributes:
        id: Primary key (UUID)
        email: User's email address (unique, nullable - can use phone instead)
        phone: User's phone number (unique, nullable - can use email instead)
        tier: User's subscription tier ('free', 'pro', 'enterprise')
        created_at: Timestamp when user was created
        last_login_at: Timestamp of last login
        active: Whether the user account is active
        extra_data: JSON field for extensibility

    Relationships:
        installations: Child GitHubInstallation records

    Note:
        At least one of email or phone must be non-null (enforced by DB constraint).
    """

    __tablename__ = "users"

    # Primary key (UUID matches Public API schema)
    id = Column(UUID(), primary_key=True, default=uuid.uuid4, index=True)

    # Authentication identifiers (at least one required)
    email = Column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
        comment="User's email address (unique)"
    )

    phone = Column(
        String(20),
        unique=True,
        nullable=True,
        index=True,
        comment="User's phone number (unique)"
    )

    # User metadata
    tier = Column(
        String(20),
        nullable=False,
        default="free",
        comment="User's subscription tier"
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Timestamp when user was created"
    )

    last_login_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of last login"
    )

    # Account status
    active = Column(
        Boolean,
        default=True,
        nullable=False,
        comment="Whether the user account is active"
    )

    # Extensibility
    extra_data = Column(
        JSON,
        default={},
        comment="Additional user data (JSON)"
    )

    # Relationships
    installations = relationship(
        "GitHubInstallation",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        identifier = self.email or self.phone
        return f"<User(id={self.id}, identifier='{identifier}', tier='{self.tier}')>"

    def to_dict(self):
        """Convert model to dictionary representation."""
        return {
            "id": str(self.id),  # Convert UUID to string for JSON serialization
            "email": self.email,
            "phone": self.phone,
            "tier": self.tier,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "active": self.active,
        }
