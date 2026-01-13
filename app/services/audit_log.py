"""
Audit Logging Service.

Provides structured audit logging for compliance, security monitoring,
and debugging. Captures important events like configuration changes,
resolution applications, and authentication events.

Phase 4 - Configuration & Production Readiness.
"""

import logging
import json
from datetime import datetime, timezone
from typing import Optional, Any
from enum import Enum
from dataclasses import dataclass, asdict
from uuid import uuid4

from app.cache.redis_client import get_redis_client
from app.middleware.correlation_id import get_correlation_id

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    """Types of audit events."""
    # Authentication events
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_TOKEN_REFRESH = "auth.token_refresh"
    AUTH_FAILED = "auth.failed"

    # Configuration events
    CONFIG_READ = "config.read"
    CONFIG_UPDATE = "config.update"
    CONFIG_DELETE = "config.delete"

    # Resolution events
    RESOLUTION_REQUESTED = "resolution.requested"
    RESOLUTION_COMPLETED = "resolution.completed"
    RESOLUTION_APPLIED = "resolution.applied"
    RESOLUTION_FAILED = "resolution.failed"

    # Repository events
    REPO_INSTALL = "repo.install"
    REPO_UNINSTALL = "repo.uninstall"
    REPO_ACCESS = "repo.access"

    # Webhook events
    WEBHOOK_RECEIVED = "webhook.received"
    WEBHOOK_PROCESSED = "webhook.processed"
    WEBHOOK_FAILED = "webhook.failed"

    # Admin events
    ADMIN_ACTION = "admin.action"
    RATE_LIMIT_EXCEEDED = "rate_limit.exceeded"


class AuditSeverity(str, Enum):
    """Severity levels for audit events."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AuditEvent:
    """Structured audit event."""
    event_id: str
    event_type: AuditEventType
    severity: AuditSeverity
    timestamp: str
    correlation_id: Optional[str]

    # Actor information
    actor_type: str  # "user", "installation", "system", "anonymous"
    actor_id: Optional[str]
    actor_name: Optional[str]

    # Target information
    target_type: Optional[str]  # "repository", "config", "resolution", etc.
    target_id: Optional[str]
    target_name: Optional[str]

    # Request context
    ip_address: Optional[str]
    user_agent: Optional[str]
    request_path: Optional[str]
    request_method: Optional[str]

    # Event details
    action: str
    outcome: str  # "success", "failure", "pending"
    details: dict

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


class AuditLogger:
    """
    Audit logging service.

    Features:
    - Structured audit event logging
    - Redis-backed event storage for real-time queries
    - Log file output for long-term archival
    - Correlation ID tracking for request tracing

    Usage:
        audit = AuditLogger()
        await audit.log_event(
            event_type=AuditEventType.CONFIG_UPDATE,
            actor_type="user",
            actor_id="123",
            target_type="repository",
            target_id="owner/repo",
            action="update_config",
            outcome="success",
            details={"changes": ["branches.protected"]}
        )
    """

    REDIS_KEY_PREFIX = "audit:"
    REDIS_LIST_KEY = "audit:events"
    MAX_REDIS_EVENTS = 10000  # Keep last 10k events in Redis
    LOG_RETENTION_DAYS = 90

    def __init__(self):
        self._audit_logger = logging.getLogger("audit")
        # Ensure audit logger has appropriate handlers
        if not self._audit_logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                '%(asctime)s - AUDIT - %(message)s'
            ))
            self._audit_logger.addHandler(handler)
            self._audit_logger.setLevel(logging.INFO)

    async def log_event(
        self,
        event_type: AuditEventType,
        action: str,
        outcome: str = "success",
        severity: AuditSeverity = AuditSeverity.INFO,
        actor_type: str = "system",
        actor_id: Optional[str] = None,
        actor_name: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        target_name: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_path: Optional[str] = None,
        request_method: Optional[str] = None,
        details: Optional[dict] = None,
        correlation_id: Optional[str] = None,
    ) -> AuditEvent:
        """
        Log an audit event.

        Args:
            event_type: Type of audit event
            action: Human-readable action description
            outcome: "success", "failure", or "pending"
            severity: Event severity level
            actor_type: Type of actor ("user", "installation", "system")
            actor_id: Unique identifier for the actor
            actor_name: Human-readable actor name
            target_type: Type of target resource
            target_id: Unique identifier for the target
            target_name: Human-readable target name
            ip_address: Client IP address
            user_agent: Client user agent
            request_path: HTTP request path
            request_method: HTTP request method
            details: Additional event details
            correlation_id: Request correlation ID

        Returns:
            The created AuditEvent
        """
        # Get correlation ID from context if not provided
        if correlation_id is None:
            correlation_id = get_correlation_id()

        event = AuditEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            severity=severity,
            timestamp=datetime.now(timezone.utc).isoformat(),
            correlation_id=correlation_id,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            ip_address=ip_address,
            user_agent=user_agent,
            request_path=request_path,
            request_method=request_method,
            action=action,
            outcome=outcome,
            details=details or {},
        )

        # Log to structured logger
        self._log_to_logger(event)

        # Store in Redis for real-time queries
        await self._store_in_redis(event)

        return event

    def _log_to_logger(self, event: AuditEvent) -> None:
        """Log event to Python logger."""
        log_message = (
            f"[{event.event_type.value}] "
            f"{event.action} - {event.outcome} | "
            f"actor={event.actor_type}:{event.actor_id} | "
            f"target={event.target_type}:{event.target_id}"
        )

        level = {
            AuditSeverity.INFO: logging.INFO,
            AuditSeverity.WARNING: logging.WARNING,
            AuditSeverity.ERROR: logging.ERROR,
            AuditSeverity.CRITICAL: logging.CRITICAL,
        }.get(event.severity, logging.INFO)

        self._audit_logger.log(
            level,
            log_message,
            extra={
                "audit_event": event.to_dict(),
                "correlation_id": event.correlation_id,
            }
        )

    async def _store_in_redis(self, event: AuditEvent) -> None:
        """Store event in Redis for real-time queries."""
        try:
            redis = await get_redis_client()
            if redis is None:
                return

            event_json = event.to_json()

            # Store in list (for recent events)
            await redis.lpush(self.REDIS_LIST_KEY, event_json)

            # Trim list to max size
            await redis.ltrim(self.REDIS_LIST_KEY, 0, self.MAX_REDIS_EVENTS - 1)

            # Store individual event by ID (for lookups)
            event_key = f"{self.REDIS_KEY_PREFIX}{event.event_id}"
            await redis.setex(
                event_key,
                self.LOG_RETENTION_DAYS * 24 * 60 * 60,  # TTL in seconds
                event_json,
            )

            # Index by actor for faster queries
            if event.actor_id:
                actor_key = f"{self.REDIS_KEY_PREFIX}actor:{event.actor_type}:{event.actor_id}"
                await redis.lpush(actor_key, event.event_id)
                await redis.ltrim(actor_key, 0, 999)  # Keep last 1000 events per actor

            # Index by target for faster queries
            if event.target_id:
                target_key = f"{self.REDIS_KEY_PREFIX}target:{event.target_type}:{event.target_id}"
                await redis.lpush(target_key, event.event_id)
                await redis.ltrim(target_key, 0, 999)  # Keep last 1000 events per target

        except Exception as e:
            logger.warning(f"Failed to store audit event in Redis: {e}")

    async def get_recent_events(
        self,
        limit: int = 100,
        event_type: Optional[AuditEventType] = None,
    ) -> list[AuditEvent]:
        """
        Get recent audit events.

        Args:
            limit: Maximum number of events to return
            event_type: Filter by event type

        Returns:
            List of AuditEvent objects
        """
        try:
            redis = await get_redis_client()
            if redis is None:
                return []

            # Get events from list
            events_json = await redis.lrange(self.REDIS_LIST_KEY, 0, limit * 2)

            events = []
            for event_json in events_json:
                try:
                    event_dict = json.loads(event_json)
                    event = AuditEvent(**event_dict)

                    # Filter by type if specified
                    if event_type and event.event_type != event_type:
                        continue

                    events.append(event)

                    if len(events) >= limit:
                        break
                except Exception as e:
                    logger.warning(f"Failed to parse audit event: {e}")

            return events

        except Exception as e:
            logger.error(f"Failed to get recent audit events: {e}")
            return []

    async def get_events_by_actor(
        self,
        actor_type: str,
        actor_id: str,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """
        Get audit events for a specific actor.

        Args:
            actor_type: Type of actor
            actor_id: Actor identifier
            limit: Maximum number of events

        Returns:
            List of AuditEvent objects
        """
        try:
            redis = await get_redis_client()
            if redis is None:
                return []

            # Get event IDs for this actor
            actor_key = f"{self.REDIS_KEY_PREFIX}actor:{actor_type}:{actor_id}"
            event_ids = await redis.lrange(actor_key, 0, limit - 1)

            events = []
            for event_id in event_ids:
                event_key = f"{self.REDIS_KEY_PREFIX}{event_id}"
                event_json = await redis.get(event_key)
                if event_json:
                    try:
                        event = AuditEvent(**json.loads(event_json))
                        events.append(event)
                    except Exception as e:
                        logger.warning(f"Failed to parse audit event {event_id}: {e}")

            return events

        except Exception as e:
            logger.error(f"Failed to get audit events by actor: {e}")
            return []

    async def get_events_by_target(
        self,
        target_type: str,
        target_id: str,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """
        Get audit events for a specific target.

        Args:
            target_type: Type of target
            target_id: Target identifier
            limit: Maximum number of events

        Returns:
            List of AuditEvent objects
        """
        try:
            redis = await get_redis_client()
            if redis is None:
                return []

            # Get event IDs for this target
            target_key = f"{self.REDIS_KEY_PREFIX}target:{target_type}:{target_id}"
            event_ids = await redis.lrange(target_key, 0, limit - 1)

            events = []
            for event_id in event_ids:
                event_key = f"{self.REDIS_KEY_PREFIX}{event_id}"
                event_json = await redis.get(event_key)
                if event_json:
                    try:
                        event = AuditEvent(**json.loads(event_json))
                        events.append(event)
                    except Exception as e:
                        logger.warning(f"Failed to parse audit event {event_id}: {e}")

            return events

        except Exception as e:
            logger.error(f"Failed to get audit events by target: {e}")
            return []


# =============================================================================
# Singleton and Convenience Functions
# =============================================================================


_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get global AuditLogger instance."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


async def log_config_change(
    repository: str,
    action: str,
    actor_id: Optional[str] = None,
    actor_name: Optional[str] = None,
    changes: Optional[dict] = None,
    success: bool = True,
) -> AuditEvent:
    """
    Convenience function to log configuration changes.

    Args:
        repository: Repository full name
        action: Description of the change
        actor_id: User or installation ID making the change
        actor_name: Human-readable name of actor
        changes: Dictionary describing the changes
        success: Whether the change succeeded

    Returns:
        The created AuditEvent
    """
    audit = get_audit_logger()
    return await audit.log_event(
        event_type=AuditEventType.CONFIG_UPDATE,
        action=action,
        outcome="success" if success else "failure",
        severity=AuditSeverity.INFO if success else AuditSeverity.WARNING,
        actor_type="user" if actor_id else "system",
        actor_id=actor_id,
        actor_name=actor_name,
        target_type="repository",
        target_id=repository,
        target_name=repository,
        details={"changes": changes} if changes else {},
    )


async def log_resolution_event(
    repository: str,
    event_type: AuditEventType,
    action: str,
    resolution_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    details: Optional[dict] = None,
    success: bool = True,
) -> AuditEvent:
    """
    Convenience function to log resolution events.

    Args:
        repository: Repository full name
        event_type: Type of resolution event
        action: Description of the action
        resolution_id: Resolution package ID
        actor_id: User or installation ID
        details: Additional details
        success: Whether the operation succeeded

    Returns:
        The created AuditEvent
    """
    audit = get_audit_logger()
    return await audit.log_event(
        event_type=event_type,
        action=action,
        outcome="success" if success else "failure",
        severity=AuditSeverity.INFO if success else AuditSeverity.ERROR,
        actor_type="user" if actor_id else "system",
        actor_id=actor_id,
        target_type="resolution",
        target_id=resolution_id,
        target_name=f"{repository}:{resolution_id}" if resolution_id else repository,
        details=details or {},
    )


async def log_auth_event(
    event_type: AuditEventType,
    action: str,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    success: bool = True,
    details: Optional[dict] = None,
) -> AuditEvent:
    """
    Convenience function to log authentication events.

    Args:
        event_type: Type of auth event
        action: Description of the action
        user_id: User ID
        username: Username
        ip_address: Client IP
        user_agent: Client user agent
        success: Whether auth succeeded
        details: Additional details

    Returns:
        The created AuditEvent
    """
    audit = get_audit_logger()
    return await audit.log_event(
        event_type=event_type,
        action=action,
        outcome="success" if success else "failure",
        severity=AuditSeverity.INFO if success else AuditSeverity.WARNING,
        actor_type="user" if user_id else "anonymous",
        actor_id=user_id,
        actor_name=username,
        ip_address=ip_address,
        user_agent=user_agent,
        details=details or {},
    )
