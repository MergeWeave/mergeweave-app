"""
Database Models Package

Exports all SQLAlchemy ORM models for the GitHub App.

Note: ConflictDetection and ResolutionResult models are imported from their
dedicated domain files (conflict.py, resolution.py) per Domain-Driven Design
principles and approved Interface Contracts v1.1.
"""

from app.models.base import Base
from app.models.user import User
from app.models.installation import GitHubInstallation
from app.models.repository import GitHubRepository
from app.models.branch import GitHubBranch
from app.models.conflict import ConflictDetection
from app.models.resolution import ResolutionResult, ResolutionStatus
from app.models.resolution_package import ResolutionPackage
from app.models.merge_context import MergeContext, MergeContextStatus, SourceType

__all__ = [
    "Base",
    "User",
    "GitHubInstallation",
    "GitHubRepository",
    "GitHubBranch",
    "ConflictDetection",
    "ResolutionResult",
    "ResolutionStatus",
    "ResolutionPackage",
    "MergeContext",
    "MergeContextStatus",
    "SourceType",
]
