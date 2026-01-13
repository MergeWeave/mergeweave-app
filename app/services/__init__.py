"""
Services Package.

Exports all service classes and factory functions.
"""

from app.services.branch_service import BranchService, get_branch_service
from app.services.merge_context_service import MergeContextService, get_merge_context_service
from app.services.version_manifest import VersionManifestService, get_version_manifest_service
from app.services.cre_client import CREClient
from app.services.config_parser import (
    ConfigParser,
    get_config_parser,
    get_repository_config,
    MergeWeaveConfig,
    BranchesConfig,
    DetectionConfig,
    ResolutionConfig,
    NotificationsConfig,
    DashboardConfig,
)
from app.services.audit_log import (
    AuditLogger,
    AuditEvent,
    AuditEventType,
    AuditSeverity,
    get_audit_logger,
    log_config_change,
    log_resolution_event,
    log_auth_event,
)

__all__ = [
    "BranchService",
    "get_branch_service",
    "MergeContextService",
    "get_merge_context_service",
    "VersionManifestService",
    "get_version_manifest_service",
    "CREClient",
    # Config parser
    "ConfigParser",
    "get_config_parser",
    "get_repository_config",
    "MergeWeaveConfig",
    "BranchesConfig",
    "DetectionConfig",
    "ResolutionConfig",
    "NotificationsConfig",
    "DashboardConfig",
    # Audit logging
    "AuditLogger",
    "AuditEvent",
    "AuditEventType",
    "AuditSeverity",
    "get_audit_logger",
    "log_config_change",
    "log_resolution_event",
    "log_auth_event",
]
