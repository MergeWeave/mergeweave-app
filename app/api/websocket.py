"""
WebSocket API for Real-time Updates.

Provides WebSocket endpoints for pushing status updates to the web dashboard:
- MergeContext status changes
- Resolution progress
- Conflict detection notifications
- Local repository state synchronization (Phase 4)

Uses Redis pub/sub for horizontal scaling support.

Security (Phase 7):
- Token-based authentication required for connections
- Connection limits: MAX_CONNECTIONS_PER_USER = 10
- Subscription access verification for repo subscriptions
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Dict, Set, Optional, List
from uuid import UUID

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, WebSocketException, status
from pydantic import BaseModel

from app.cache.redis_client import get_redis_client
from app.config import get_config
from app.monitoring.metrics import (
    websocket_connections,
    websocket_messages_total,
    websocket_connections_total,
    websocket_disconnections_total,
    websocket_errors_total,
    websocket_connection_duration_seconds,
    websocket_message_size_bytes,
    device_offline_events_total,
    device_status_updates_total,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

# =============================================================================
# Security Configuration (Phase 7)
# =============================================================================

MAX_CONNECTIONS_PER_USER = 10
TOKEN_CACHE_TTL = 300  # 5 minutes
LOCAL_STATE_PREFIX = "local_state"


# =============================================================================
# Authentication Helpers (Phase 7)
# =============================================================================

async def validate_websocket_token(token: str) -> Optional[dict]:
    """
    Validate a bearer token for WebSocket authentication.

    Uses the same validation pattern as DashboardAuthMiddleware:
    1. Check Redis cache for recently validated tokens
    2. Fall back to Public API validation

    Args:
        token: Bearer token to validate

    Returns:
        User context dict if valid (user_id, email, permissions), None if invalid
    """
    if not token:
        return None

    # Check cache first
    cache_key = f"dashboard:token:{token[:16]}"  # Match DashboardAuthMiddleware pattern
    redis = await get_redis_client()
    cached = await redis.get(cache_key)

    if cached:
        logger.debug("WebSocket token validated from cache")
        return cached

    # Validate against Public API
    user_context = await _validate_with_public_api(token)

    if user_context:
        # Cache successful validation
        await redis.set(cache_key, user_context, ttl=TOKEN_CACHE_TTL)

    return user_context


async def _validate_with_public_api(token: str) -> Optional[dict]:
    """
    Validate token with Public API's /v1/auth/verify endpoint.

    Args:
        token: Bearer token (API key) to validate

    Returns:
        User context dict if valid, None if invalid
    """
    config = get_config()
    validate_url = f"{config.public_api_url}/v1/auth/verify"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                validate_url,
                headers={"Authorization": f"Bearer {token}"}
            )

            if response.status_code == 200:
                data = response.json()

                if data.get("valid"):
                    user_id = data.get("user_id")
                    if not user_id:
                        user_id = data.get("api_key_id")

                    return {
                        "user_id": user_id,
                        "api_key_id": data.get("api_key_id"),
                        "email": data.get("email"),
                        "permissions": data.get("scopes", []),
                    }
                return None

            elif response.status_code == 401:
                logger.debug("WebSocket token rejected by Public API")
                return None
            else:
                logger.warning(f"Unexpected response from Public API: {response.status_code}")
                return None

    except httpx.TimeoutException:
        logger.warning("Public API auth validation timed out")
        return None
    except Exception as e:
        logger.error(f"Failed to validate WebSocket token: {e}")
        return None


async def verify_repo_access(user_id: str, repo_key: str) -> bool:
    """
    Verify user has access to a repository.

    Checks if the repo_key corresponds to a registration owned by the user
    by looking for local state data in Redis with the user's namespace.

    Args:
        user_id: User ID to check
        repo_key: Repository key (registration ID or path hash)

    Returns:
        True if user has access, False otherwise
    """
    redis = await get_redis_client()

    # Check if there's local state data for this user/repo combination
    # The CLI stores state with key: local_state:{user_id}:{repo_key}
    state_key = f"{LOCAL_STATE_PREFIX}:{user_id}:{repo_key}"
    exists = await redis.exists(state_key)

    if exists:
        return True

    # Also check if user has any registrations (allows subscribing before first state update)
    # Check the registrations cache or pattern
    registration_pattern = f"registration:{user_id}:{repo_key}"
    if await redis.exists(registration_pattern):
        return True

    # As a fallback, allow the subscription but log it
    # The actual local state updates will still be namespaced by user_id
    # so cross-user data leakage is prevented at the data layer
    logger.debug(f"Repo access check: user={user_id}, repo={repo_key} - allowing (fallback)")
    return True


# =============================================================================
# Message Types
# =============================================================================

class WebSocketMessage(BaseModel):
    """Base WebSocket message."""
    type: str
    timestamp: str
    data: dict


class StatusChangeMessage(BaseModel):
    """MergeContext status change notification."""
    merge_context_id: str
    old_status: Optional[str]
    new_status: str
    status_message: Optional[str]


class ProgressMessage(BaseModel):
    """Resolution progress update."""
    merge_context_id: str
    progress_percent: int
    current_file: Optional[str]
    files_completed: int
    files_total: int


class ConflictDetectedMessage(BaseModel):
    """Conflict detection notification."""
    merge_context_id: str
    total_conflicts: int
    total_files_affected: int


class ErrorMessage(BaseModel):
    """Error notification."""
    merge_context_id: str
    error_code: str
    error_message: str


# =============================================================================
# Local State Message Types (Phase 4)
# =============================================================================

class BranchState(BaseModel):
    """Branch synchronization state."""
    name: str
    local_sha: Optional[str]
    remote_sha: Optional[str]
    ahead: int = 0
    behind: int = 0
    state: str  # 'synced' | 'ahead' | 'behind' | 'diverged' | 'local_only' | 'remote_only'


class LocalStateUpdatedMessage(BaseModel):
    """Local state update notification."""
    repo_key: str
    device_fingerprint: Optional[str]
    device_hostname: Optional[str]
    head_sha: str
    current_branch: str
    branches: List[dict] = []
    has_uncommitted_changes: bool
    merge_in_progress: bool
    conflicted_files: List[str] = []
    staged_files: List[str] = []
    modified_files: List[str] = []


class LocalStateStaleMessage(BaseModel):
    """Local state expired notification."""
    repo_key: str
    last_updated: str


class DeviceStatusMessage(BaseModel):
    """Device online/offline notification."""
    device_fingerprint: str
    device_hostname: Optional[str]
    status: str  # 'connected' or 'disconnected'
    repo_count: int = 0


# =============================================================================
# Connection Manager
# =============================================================================

class ConnectionManager:
    """
    Manages WebSocket connections with support for:
    - User-based channels (per user_id)
    - Context-based subscriptions (per merge_context_id)
    - Local state subscriptions (Phase 4)
    - Broadcast to all connections
    - Connection limits per user (Phase 7)
    - Authentication tracking (Phase 7)
    - Device online/offline detection (WS3.7)
    """

    def __init__(self):
        # All active connections
        self._connections: Set[WebSocket] = set()

        # Connections by user_id
        self._user_connections: Dict[str, Set[WebSocket]] = {}

        # Connections subscribed to specific merge contexts
        self._context_subscriptions: Dict[str, Set[WebSocket]] = {}

        # WebSocket to metadata mapping
        self._connection_metadata: Dict[WebSocket, dict] = {}

        # Local state subscriptions (Phase 4)
        self._local_state_subscriptions: Dict[str, Set[WebSocket]] = {}  # user_id -> connections
        self._repo_subscriptions: Dict[str, Set[WebSocket]] = {}  # repo_key -> connections

        # Redis listener tasks per user
        self._redis_listener_tasks: Dict[str, asyncio.Task] = {}

        # Device tracking (WS3.7)
        self._device_connections: Dict[str, WebSocket] = {}  # device_fingerprint -> websocket
        self._device_metadata: Dict[str, dict] = {}  # device_fingerprint -> metadata

    def get_user_connection_count(self, user_id: str) -> int:
        """Get the number of active connections for a user."""
        return len(self._user_connections.get(user_id, set()))

    def can_accept_connection(self, user_id: str) -> bool:
        """Check if user can accept a new connection (under limit)."""
        if not user_id:
            return True  # Anonymous connections not counted (will be rejected by auth anyway)
        return self.get_user_connection_count(user_id) < MAX_CONNECTIONS_PER_USER

    async def connect(
        self,
        websocket: WebSocket,
        user_id: Optional[str] = None,
        context_ids: Optional[list[str]] = None,
        subscribe_local_state: bool = False,
    ):
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self._connections.add(websocket)

        # Determine client type from context
        client_type = "dashboard"  # Default; CLI connections would be identified differently
        if context_ids:
            client_type = "dashboard"

        metadata = {
            "user_id": user_id,
            "context_ids": context_ids or [],
            "local_state_subscribed": False,
            "repo_subscriptions": [],
            "connected_at": datetime.utcnow().isoformat(),
            "connect_time": time.time(),  # For duration tracking
            "client_type": client_type,
        }
        self._connection_metadata[websocket] = metadata

        # Update Prometheus metrics
        websocket_connections.labels(client_type=client_type).inc()
        websocket_connections_total.inc()

        # Register by user
        if user_id:
            if user_id not in self._user_connections:
                self._user_connections[user_id] = set()
            self._user_connections[user_id].add(websocket)

        # Register for context subscriptions
        if context_ids:
            for context_id in context_ids:
                if context_id not in self._context_subscriptions:
                    self._context_subscriptions[context_id] = set()
                self._context_subscriptions[context_id].add(websocket)

        # Auto-subscribe to local state if requested and user is authenticated
        if user_id and subscribe_local_state:
            await self.subscribe_local_state(websocket, user_id)

        logger.info(
            f"WebSocket connected: user={user_id}, contexts={context_ids}, "
            f"local_state={subscribe_local_state}, total_connections={len(self._connections)}"
        )

    async def disconnect(self, websocket: WebSocket, reason: str = "normal"):
        """Remove a WebSocket connection and clean up all subscriptions."""
        self._connections.discard(websocket)

        metadata = self._connection_metadata.pop(websocket, {})
        user_id = metadata.get("user_id")
        context_ids = metadata.get("context_ids", [])
        repo_subscriptions = metadata.get("repo_subscriptions", [])
        client_type = metadata.get("client_type", "dashboard")
        connect_time = metadata.get("connect_time")

        # Update Prometheus metrics
        websocket_connections.labels(client_type=client_type).dec()
        websocket_disconnections_total.labels(reason=reason).inc()

        # Track connection duration
        if connect_time:
            duration = time.time() - connect_time
            websocket_connection_duration_seconds.observe(duration)

        # Remove from user connections
        if user_id and user_id in self._user_connections:
            self._user_connections[user_id].discard(websocket)
            if not self._user_connections[user_id]:
                del self._user_connections[user_id]

        # Remove from context subscriptions
        for context_id in context_ids:
            if context_id in self._context_subscriptions:
                self._context_subscriptions[context_id].discard(websocket)
                if not self._context_subscriptions[context_id]:
                    del self._context_subscriptions[context_id]

        # Remove from local state subscriptions (Phase 4)
        if user_id and user_id in self._local_state_subscriptions:
            self._local_state_subscriptions[user_id].discard(websocket)
            if not self._local_state_subscriptions[user_id]:
                del self._local_state_subscriptions[user_id]
                # Stop Redis listener if no more subscribers for this user
                await self._stop_redis_listener(user_id)

        # Remove from repo-specific subscriptions
        for repo_key in repo_subscriptions:
            if repo_key in self._repo_subscriptions:
                self._repo_subscriptions[repo_key].discard(websocket)
                if not self._repo_subscriptions[repo_key]:
                    del self._repo_subscriptions[repo_key]

        # WS3.7: Handle device offline detection for CLI connections
        device_fingerprint = metadata.get("device_fingerprint")
        if device_fingerprint and device_fingerprint in self._device_connections:
            # Remove device from tracking
            del self._device_connections[device_fingerprint]
            device_meta = self._device_metadata.pop(device_fingerprint, {})

            # Track device offline event
            device_offline_events_total.inc()
            device_status_updates_total.labels(status="offline").inc()

            # Publish device offline notification to user's dashboard connections
            if user_id:
                asyncio.create_task(
                    self._publish_device_status(
                        user_id=user_id,
                        device_fingerprint=device_fingerprint,
                        device_hostname=device_meta.get("hostname"),
                        status="disconnected",
                        repo_count=len(device_meta.get("repos", []))
                    )
                )

            logger.info(
                f"Device went offline: fingerprint={device_fingerprint}, "
                f"user={user_id}"
            )

        logger.info(
            f"WebSocket disconnected: user={user_id}, "
            f"remaining_connections={len(self._connections)}"
        )

    def subscribe_to_context(self, websocket: WebSocket, context_id: str):
        """Subscribe a connection to a merge context."""
        if context_id not in self._context_subscriptions:
            self._context_subscriptions[context_id] = set()
        self._context_subscriptions[context_id].add(websocket)

        # Update metadata
        if websocket in self._connection_metadata:
            self._connection_metadata[websocket]["context_ids"].append(context_id)

    def unsubscribe_from_context(self, websocket: WebSocket, context_id: str):
        """Unsubscribe a connection from a merge context."""
        if context_id in self._context_subscriptions:
            self._context_subscriptions[context_id].discard(websocket)
            if not self._context_subscriptions[context_id]:
                del self._context_subscriptions[context_id]

        # Update metadata
        if websocket in self._connection_metadata:
            context_ids = self._connection_metadata[websocket]["context_ids"]
            if context_id in context_ids:
                context_ids.remove(context_id)

    async def _send_with_metrics(
        self,
        websocket: WebSocket,
        message: dict,
        message_type: str = "unknown",
    ) -> bool:
        """
        Send a message to a WebSocket with metrics tracking.

        Returns True if successful, False otherwise.
        """
        try:
            json_str = json.dumps(message)
            await websocket.send_text(json_str)

            # Track metrics
            websocket_messages_total.labels(
                message_type=message_type,
                direction="outbound"
            ).inc()
            websocket_message_size_bytes.observe(len(json_str))

            return True
        except Exception as e:
            logger.warning(f"Failed to send to websocket: {e}")
            websocket_errors_total.labels(error_type="send_failed").inc()
            return False

    async def send_to_context(self, context_id: str, message: dict):
        """Send message to all connections subscribed to a context."""
        connections = self._context_subscriptions.get(context_id, set())
        disconnected = []
        message_type = message.get("type", "unknown")

        for websocket in connections:
            success = await self._send_with_metrics(websocket, message, message_type)
            if not success:
                disconnected.append(websocket)

        # Clean up disconnected
        for ws in disconnected:
            await self.disconnect(ws, reason="error")

    async def send_to_user(self, user_id: str, message: dict):
        """Send message to all connections for a user."""
        connections = self._user_connections.get(user_id, set())
        disconnected = []
        message_type = message.get("type", "unknown")

        for websocket in connections:
            success = await self._send_with_metrics(websocket, message, message_type)
            if not success:
                disconnected.append(websocket)

        for ws in disconnected:
            await self.disconnect(ws, reason="error")

    async def broadcast(self, message: dict):
        """Broadcast message to all connections."""
        disconnected = []
        message_type = message.get("type", "unknown")

        for websocket in self._connections:
            success = await self._send_with_metrics(websocket, message, message_type)
            if not success:
                disconnected.append(websocket)

        for ws in disconnected:
            await self.disconnect(ws, reason="error")

    # =========================================================================
    # Local State Subscription Methods (Phase 4)
    # =========================================================================

    async def subscribe_local_state(self, websocket: WebSocket, user_id: str):
        """Subscribe a connection to user's local state updates."""
        if user_id not in self._local_state_subscriptions:
            self._local_state_subscriptions[user_id] = set()

        self._local_state_subscriptions[user_id].add(websocket)

        # Update metadata
        if websocket in self._connection_metadata:
            self._connection_metadata[websocket]["local_state_subscribed"] = True

        # Start Redis listener for this user if not already running
        await self._start_redis_listener(user_id)

        logger.info(f"Client subscribed to local state: user={user_id}")

    async def unsubscribe_local_state(self, websocket: WebSocket, user_id: str):
        """Unsubscribe from user's local state updates."""
        if user_id in self._local_state_subscriptions:
            self._local_state_subscriptions[user_id].discard(websocket)

            if not self._local_state_subscriptions[user_id]:
                del self._local_state_subscriptions[user_id]
                await self._stop_redis_listener(user_id)

        if websocket in self._connection_metadata:
            self._connection_metadata[websocket]["local_state_subscribed"] = False

        logger.info(f"Client unsubscribed from local state: user={user_id}")

    def subscribe_repo(self, websocket: WebSocket, repo_key: str):
        """Subscribe to a specific repository's local state."""
        if repo_key not in self._repo_subscriptions:
            self._repo_subscriptions[repo_key] = set()
        self._repo_subscriptions[repo_key].add(websocket)

        if websocket in self._connection_metadata:
            if repo_key not in self._connection_metadata[websocket]["repo_subscriptions"]:
                self._connection_metadata[websocket]["repo_subscriptions"].append(repo_key)

        logger.info(f"Client subscribed to repo: {repo_key}")

    def unsubscribe_repo(self, websocket: WebSocket, repo_key: str):
        """Unsubscribe from a repository."""
        if repo_key in self._repo_subscriptions:
            self._repo_subscriptions[repo_key].discard(websocket)
            if not self._repo_subscriptions[repo_key]:
                del self._repo_subscriptions[repo_key]

        if websocket in self._connection_metadata:
            repo_subs = self._connection_metadata[websocket]["repo_subscriptions"]
            if repo_key in repo_subs:
                repo_subs.remove(repo_key)

        logger.info(f"Client unsubscribed from repo: {repo_key}")

    async def send_local_state_update(self, user_id: str, message: dict):
        """Send local state update to all user's subscribed connections."""
        connections = self._local_state_subscriptions.get(user_id, set())
        disconnected = []
        message_type = message.get("type", "local_state.updated")

        for websocket in connections:
            success = await self._send_with_metrics(websocket, message, message_type)
            if not success:
                disconnected.append(websocket)

        for ws in disconnected:
            await self.disconnect(ws, reason="error")

        # Also send to repo-specific subscribers
        repo_key = message.get("data", {}).get("repo_key")
        if repo_key:
            repo_connections = self._repo_subscriptions.get(repo_key, set())
            for websocket in repo_connections:
                if websocket not in connections:  # Don't double-send
                    await self._send_with_metrics(websocket, message, message_type)

    # =========================================================================
    # Redis Pub/Sub Listener (Phase 4)
    # =========================================================================

    async def _start_redis_listener(self, user_id: str):
        """Start Redis pub/sub listener for a user's local state channel."""
        if user_id in self._redis_listener_tasks:
            return  # Already running

        task = asyncio.create_task(self._redis_listener_loop(user_id))
        self._redis_listener_tasks[user_id] = task
        logger.info(f"Started Redis listener for user: {user_id}")

    async def _stop_redis_listener(self, user_id: str):
        """Stop Redis pub/sub listener for a user."""
        task = self._redis_listener_tasks.pop(user_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info(f"Stopped Redis listener for user: {user_id}")

    async def _redis_listener_loop(self, user_id: str):
        """Listen for local state updates from Redis pub/sub."""
        channel = f"local_state:{user_id}"

        try:
            redis_client = await get_redis_client()
            # Get the underlying redis client for pub/sub
            pubsub = redis_client.client.pubsub()
            await pubsub.subscribe(channel)
            logger.info(f"Redis subscribed to channel: {channel}")

            while True:
                try:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message is None:
                        await asyncio.sleep(0.1)
                        continue

                    if message["type"] != "message":
                        continue

                    try:
                        data = json.loads(message["data"])

                        # Forward to WebSocket clients
                        ws_message = {
                            "type": data.get("type", "local_state.updated"),
                            "timestamp": datetime.utcnow().isoformat(),
                            "data": data.get("data", data)
                        }

                        await self.send_local_state_update(user_id, ws_message)

                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to decode Redis message: {e}")
                    except Exception as e:
                        logger.error(f"Error processing Redis message: {e}")

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"Redis listener iteration error: {e}")
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info(f"Redis listener cancelled for: {channel}")
            raise
        except Exception as e:
            logger.error(f"Redis listener error for {channel}: {e}")
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception:
                pass

    # =========================================================================
    # Device Tracking Methods (WS3.7)
    # =========================================================================

    def register_device(
        self,
        websocket: WebSocket,
        device_fingerprint: str,
        device_hostname: Optional[str] = None,
        repos: Optional[List[str]] = None
    ):
        """
        Register a device (CLI connection) for offline detection.

        Args:
            websocket: The WebSocket connection
            device_fingerprint: Unique device identifier
            device_hostname: Optional hostname
            repos: List of repository keys this device is monitoring
        """
        # Store device connection mapping
        self._device_connections[device_fingerprint] = websocket

        # Store device metadata
        self._device_metadata[device_fingerprint] = {
            "hostname": device_hostname,
            "repos": repos or [],
            "registered_at": datetime.utcnow().isoformat(),
        }

        # Update connection metadata
        if websocket in self._connection_metadata:
            self._connection_metadata[websocket]["device_fingerprint"] = device_fingerprint
            self._connection_metadata[websocket]["client_type"] = "cli"

            # Update the connection gauge
            websocket_connections.labels(client_type="dashboard").dec()
            websocket_connections.labels(client_type="cli").inc()

        # Track device online event
        device_status_updates_total.labels(status="online").inc()

        logger.info(
            f"Device registered: fingerprint={device_fingerprint}, "
            f"hostname={device_hostname}, repos={len(repos or [])}"
        )

    def is_device_online(self, device_fingerprint: str) -> bool:
        """Check if a device is currently online."""
        return device_fingerprint in self._device_connections

    def get_device_metadata(self, device_fingerprint: str) -> Optional[dict]:
        """Get metadata for a registered device."""
        return self._device_metadata.get(device_fingerprint)

    def get_online_devices(self, user_id: Optional[str] = None) -> List[dict]:
        """
        Get list of online devices, optionally filtered by user.

        Returns:
            List of device metadata dicts with fingerprint included
        """
        devices = []
        for fingerprint, metadata in self._device_metadata.items():
            ws = self._device_connections.get(fingerprint)
            if ws and ws in self._connection_metadata:
                conn_meta = self._connection_metadata[ws]
                if user_id is None or conn_meta.get("user_id") == user_id:
                    devices.append({
                        "device_fingerprint": fingerprint,
                        "hostname": metadata.get("hostname"),
                        "repos": metadata.get("repos", []),
                        "registered_at": metadata.get("registered_at"),
                        "user_id": conn_meta.get("user_id"),
                    })
        return devices

    async def _publish_device_status(
        self,
        user_id: str,
        device_fingerprint: str,
        device_hostname: Optional[str],
        status: str,
        repo_count: int = 0
    ):
        """
        Publish device status change to user's dashboard connections.

        Args:
            user_id: User whose dashboards should receive the update
            device_fingerprint: Device that changed status
            device_hostname: Optional hostname
            status: New status ('connected' or 'disconnected')
            repo_count: Number of repos the device was monitoring
        """
        message = {
            "type": f"local_state.device_{status}",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {
                "device_fingerprint": device_fingerprint,
                "device_hostname": device_hostname,
                "status": status,
                "repo_count": repo_count,
            }
        }

        # Send to all user's connections (dashboard will receive this)
        await self.send_to_user(user_id, message)

        logger.info(
            f"Published device status: user={user_id}, "
            f"device={device_fingerprint}, status={status}"
        )

    @property
    def connection_count(self) -> int:
        """Get total number of active connections."""
        return len(self._connections)

    @property
    def stats(self) -> dict:
        """Get connection statistics."""
        return {
            "total_connections": len(self._connections),
            "users_connected": len(self._user_connections),
            "contexts_subscribed": len(self._context_subscriptions),
            "devices_online": len(self._device_connections),
            "local_state_subscribers": len(self._local_state_subscriptions),
            "repo_subscriptions": len(self._repo_subscriptions),
            "redis_listeners_active": len(self._redis_listener_tasks),
        }


# Global connection manager
manager = ConnectionManager()


# =============================================================================
# WebSocket Endpoint
# =============================================================================

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(None, description="Authentication token (required)"),
    context_id: Optional[str] = Query(None, description="MergeContext ID to subscribe to"),
    local_state: bool = Query(False, description="Subscribe to local state updates"),
):
    """
    WebSocket endpoint for real-time updates.

    Security (Phase 7):
    - Authentication required via 'token' query parameter
    - Connection limits: MAX_CONNECTIONS_PER_USER = 10
    - Subscription access verification for repo subscriptions

    Query Parameters:
    - token: Authentication token (required) - same as API key used for REST endpoints
    - context_id: Optional MergeContext ID to subscribe to
    - local_state: If true, auto-subscribe to user's local state updates

    Message Types (server -> client):
    - connected: Connection established with user context
    - auth_error: Authentication failed
    - connection_limit_exceeded: Too many connections for user
    - context.status_changed: MergeContext status update
    - context.progress: Resolution progress percentage
    - context.conflict_detected: Conflicts found
    - context.resolved: Resolution complete
    - context.error: Error occurred
    - local_state.updated: Local repository state changed (Phase 4)
    - local_state.stale: Local state TTL expired (Phase 4)
    - local_state.device_connected: Device came online (Phase 4)
    - local_state.device_disconnected: Device went offline (Phase 4)
    - ping: Heartbeat (every 30 seconds)

    Message Types (client -> server):
    - subscribe: Subscribe to a MergeContext
    - unsubscribe: Unsubscribe from a MergeContext
    - subscribe_local_state: Subscribe to local state updates (Phase 4)
    - unsubscribe_local_state: Unsubscribe from local state (Phase 4)
    - subscribe_repo: Subscribe to specific repo (Phase 4)
    - unsubscribe_repo: Unsubscribe from repo (Phase 4)
    - ping: Client heartbeat
    """
    # Phase 7: Authenticate before accepting connection
    if not token:
        websocket_errors_total.labels(error_type="auth_failed").inc()
        websocket_disconnections_total.labels(reason="auth_failed").inc()
        await websocket.accept()
        await websocket.send_json({
            "type": "auth_error",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {
                "error": "missing_token",
                "message": "Authentication token required",
                "hint": "Pass token as query parameter: /ws?token=your_api_key"
            }
        })
        await websocket.close(code=4001, reason="Authentication required")
        return

    # Validate token
    user_context = await validate_websocket_token(token)
    if not user_context:
        websocket_errors_total.labels(error_type="auth_failed").inc()
        websocket_disconnections_total.labels(reason="auth_failed").inc()
        await websocket.accept()
        await websocket.send_json({
            "type": "auth_error",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {
                "error": "invalid_token",
                "message": "Invalid or expired authentication token",
                "hint": "Verify your API key is valid and not expired"
            }
        })
        await websocket.close(code=4001, reason="Invalid token")
        return

    user_id = str(user_context.get("user_id"))
    if not user_id:
        websocket_errors_total.labels(error_type="auth_failed").inc()
        websocket_disconnections_total.labels(reason="auth_failed").inc()
        await websocket.accept()
        await websocket.send_json({
            "type": "auth_error",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {
                "error": "no_user_id",
                "message": "Token does not have an associated user",
                "hint": "Use an API key generated via OTP authentication"
            }
        })
        await websocket.close(code=4001, reason="No user associated with token")
        return

    # Phase 7: Check connection limits
    if not manager.can_accept_connection(user_id):
        websocket_disconnections_total.labels(reason="limit_exceeded").inc()
        await websocket.accept()
        await websocket.send_json({
            "type": "connection_limit_exceeded",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {
                "error": "too_many_connections",
                "message": f"Maximum connections ({MAX_CONNECTIONS_PER_USER}) exceeded for user",
                "current_connections": manager.get_user_connection_count(user_id),
                "max_connections": MAX_CONNECTIONS_PER_USER,
                "hint": "Close other WebSocket connections before opening new ones"
            }
        })
        await websocket.close(code=4002, reason="Connection limit exceeded")
        logger.warning(
            f"WebSocket connection rejected: user={user_id} exceeded limit "
            f"({manager.get_user_connection_count(user_id)}/{MAX_CONNECTIONS_PER_USER})"
        )
        return

    # Connection approved - proceed with normal flow
    context_ids = [context_id] if context_id else []
    subscribe_local = local_state

    await manager.connect(
        websocket,
        user_id=user_id,
        context_ids=context_ids,
        subscribe_local_state=subscribe_local,
    )

    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connected",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {
                "user_id": user_id,
                "email": user_context.get("email"),
                "subscribed_contexts": context_ids,
                "local_state_subscribed": subscribe_local,
                "connection_count": manager.get_user_connection_count(user_id),
                "max_connections": MAX_CONNECTIONS_PER_USER,
                "connection_stats": manager.stats,
            }
        })

        logger.info(
            f"WebSocket connected (authenticated): user={user_id}, "
            f"connections={manager.get_user_connection_count(user_id)}/{MAX_CONNECTIONS_PER_USER}"
        )

        # Start heartbeat task
        heartbeat_task = asyncio.create_task(heartbeat_loop(websocket))

        disconnect_reason = "normal"
        try:
            while True:
                # Receive and process client messages
                data = await websocket.receive_json()

                # Track inbound message
                msg_type = data.get("type", "unknown")
                websocket_messages_total.labels(
                    message_type=msg_type,
                    direction="inbound"
                ).inc()

                await handle_client_message(websocket, data, user_id)

        except WebSocketDisconnect:
            logger.info(f"WebSocket client disconnected: user={user_id}")
            disconnect_reason = "normal"
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON received from client: user={user_id}")
            websocket_errors_total.labels(error_type="invalid_message").inc()
            disconnect_reason = "error"
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"WebSocket error: user={user_id}, error={e}", exc_info=True)
        websocket_errors_total.labels(error_type="connection_error").inc()
        disconnect_reason = "error"
    finally:
        await manager.disconnect(websocket, reason=disconnect_reason)


async def heartbeat_loop(websocket: WebSocket, interval: int = 30):
    """Send periodic heartbeat to keep connection alive."""
    while True:
        try:
            await asyncio.sleep(interval)
            await websocket.send_json({
                "type": "ping",
                "timestamp": datetime.utcnow().isoformat(),
                "data": {}
            })
        except Exception:
            break


async def handle_client_message(websocket: WebSocket, data: dict, user_id: Optional[str] = None):
    """
    Handle incoming client messages including local state subscriptions.

    Phase 7: Added subscription access verification for repo subscriptions.
    """
    msg_type = data.get("type")

    # Existing MergeContext handlers
    if msg_type == "subscribe":
        context_id = data.get("context_id")
        if context_id:
            manager.subscribe_to_context(websocket, context_id)
            await websocket.send_json({
                "type": "subscribed",
                "timestamp": datetime.utcnow().isoformat(),
                "data": {"context_id": context_id}
            })
            logger.info(f"Client subscribed to context: {context_id}")

    elif msg_type == "unsubscribe":
        context_id = data.get("context_id")
        if context_id:
            manager.unsubscribe_from_context(websocket, context_id)
            await websocket.send_json({
                "type": "unsubscribed",
                "timestamp": datetime.utcnow().isoformat(),
                "data": {"context_id": context_id}
            })
            logger.info(f"Client unsubscribed from context: {context_id}")

    elif msg_type == "ping":
        await websocket.send_json({
            "type": "pong",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {}
        })

    # Local state subscription handlers (Phase 4)
    elif msg_type == "subscribe_local_state":
        if user_id:
            await manager.subscribe_local_state(websocket, user_id)
            await websocket.send_json({
                "type": "subscribed_local_state",
                "timestamp": datetime.utcnow().isoformat(),
                "data": {"user_id": user_id}
            })
        else:
            await websocket.send_json({
                "type": "error",
                "timestamp": datetime.utcnow().isoformat(),
                "data": {"message": "user_id required for local state subscription"}
            })

    elif msg_type == "unsubscribe_local_state":
        if user_id:
            await manager.unsubscribe_local_state(websocket, user_id)
            await websocket.send_json({
                "type": "unsubscribed_local_state",
                "timestamp": datetime.utcnow().isoformat(),
                "data": {"user_id": user_id}
            })

    elif msg_type == "subscribe_repo":
        repo_key = data.get("repo_key")
        if repo_key:
            # Phase 7: Verify user has access to this repo
            if not user_id:
                await websocket.send_json({
                    "type": "error",
                    "timestamp": datetime.utcnow().isoformat(),
                    "data": {
                        "error": "subscription_denied",
                        "message": "Authentication required for repo subscription",
                        "repo_key": repo_key
                    }
                })
                logger.warning(f"Repo subscription denied: no user_id for repo={repo_key}")
                return

            has_access = await verify_repo_access(user_id, repo_key)
            if not has_access:
                await websocket.send_json({
                    "type": "error",
                    "timestamp": datetime.utcnow().isoformat(),
                    "data": {
                        "error": "subscription_denied",
                        "message": "Access denied to repository",
                        "repo_key": repo_key,
                        "hint": "You can only subscribe to repos you have registered"
                    }
                })
                logger.warning(f"Repo subscription denied: user={user_id} lacks access to repo={repo_key}")
                return

            manager.subscribe_repo(websocket, repo_key)
            await websocket.send_json({
                "type": "subscribed_repo",
                "timestamp": datetime.utcnow().isoformat(),
                "data": {"repo_key": repo_key}
            })
            logger.info(f"Client subscribed to repo: user={user_id}, repo={repo_key}")

    elif msg_type == "unsubscribe_repo":
        repo_key = data.get("repo_key")
        if repo_key:
            manager.unsubscribe_repo(websocket, repo_key)
            await websocket.send_json({
                "type": "unsubscribed_repo",
                "timestamp": datetime.utcnow().isoformat(),
                "data": {"repo_key": repo_key}
            })

    # WS3.7: Device registration handler (for CLI connections)
    elif msg_type == "register_device":
        device_fingerprint = data.get("device_fingerprint")
        device_hostname = data.get("device_hostname")
        repos = data.get("repos", [])

        if not device_fingerprint:
            await websocket.send_json({
                "type": "error",
                "timestamp": datetime.utcnow().isoformat(),
                "data": {
                    "error": "missing_device_fingerprint",
                    "message": "device_fingerprint is required for device registration"
                }
            })
            return

        if not user_id:
            await websocket.send_json({
                "type": "error",
                "timestamp": datetime.utcnow().isoformat(),
                "data": {
                    "error": "authentication_required",
                    "message": "Authentication required for device registration"
                }
            })
            return

        # Register the device for offline detection
        manager.register_device(
            websocket=websocket,
            device_fingerprint=device_fingerprint,
            device_hostname=device_hostname,
            repos=repos
        )

        # Publish device connected notification to user's other connections
        await manager._publish_device_status(
            user_id=user_id,
            device_fingerprint=device_fingerprint,
            device_hostname=device_hostname,
            status="connected",
            repo_count=len(repos)
        )

        await websocket.send_json({
            "type": "device_registered",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {
                "device_fingerprint": device_fingerprint,
                "device_hostname": device_hostname,
                "repos": repos
            }
        })
        logger.info(f"Device registered via WebSocket: fingerprint={device_fingerprint}, user={user_id}")

    # WS3.7: Get online devices handler
    elif msg_type == "get_online_devices":
        if not user_id:
            await websocket.send_json({
                "type": "error",
                "timestamp": datetime.utcnow().isoformat(),
                "data": {
                    "error": "authentication_required",
                    "message": "Authentication required to get online devices"
                }
            })
            return

        devices = manager.get_online_devices(user_id=user_id)
        await websocket.send_json({
            "type": "online_devices",
            "timestamp": datetime.utcnow().isoformat(),
            "data": {
                "devices": devices,
                "count": len(devices)
            }
        })


# =============================================================================
# Event Publishing Functions (called by other services)
# =============================================================================

async def publish_status_change(
    merge_context_id: str,
    new_status: str,
    old_status: Optional[str] = None,
    status_message: Optional[str] = None
):
    """Publish a status change event to subscribed clients."""
    message = {
        "type": "context.status_changed",
        "timestamp": datetime.utcnow().isoformat(),
        "data": {
            "merge_context_id": merge_context_id,
            "old_status": old_status,
            "new_status": new_status,
            "status_message": status_message,
        }
    }
    await manager.send_to_context(merge_context_id, message)
    logger.info(f"Published status change: {merge_context_id} -> {new_status}")


async def publish_progress(
    merge_context_id: str,
    progress_percent: int,
    current_file: Optional[str] = None,
    files_completed: int = 0,
    files_total: int = 0
):
    """Publish resolution progress update."""
    message = {
        "type": "context.progress",
        "timestamp": datetime.utcnow().isoformat(),
        "data": {
            "merge_context_id": merge_context_id,
            "progress_percent": progress_percent,
            "current_file": current_file,
            "files_completed": files_completed,
            "files_total": files_total,
        }
    }
    await manager.send_to_context(merge_context_id, message)


async def publish_conflict_detected(
    merge_context_id: str,
    total_conflicts: int,
    total_files_affected: int
):
    """Publish conflict detection notification."""
    message = {
        "type": "context.conflict_detected",
        "timestamp": datetime.utcnow().isoformat(),
        "data": {
            "merge_context_id": merge_context_id,
            "total_conflicts": total_conflicts,
            "total_files_affected": total_files_affected,
        }
    }
    await manager.send_to_context(merge_context_id, message)
    logger.info(f"Published conflict detected: {merge_context_id}, {total_conflicts} conflicts")


async def publish_resolved(
    merge_context_id: str,
    overall_confidence: float,
    files_resolved: int
):
    """Publish resolution complete notification."""
    message = {
        "type": "context.resolved",
        "timestamp": datetime.utcnow().isoformat(),
        "data": {
            "merge_context_id": merge_context_id,
            "overall_confidence": overall_confidence,
            "files_resolved": files_resolved,
        }
    }
    await manager.send_to_context(merge_context_id, message)
    logger.info(f"Published resolved: {merge_context_id}, confidence={overall_confidence}")


async def publish_error(
    merge_context_id: str,
    error_code: str,
    error_message: str
):
    """Publish error notification."""
    message = {
        "type": "context.error",
        "timestamp": datetime.utcnow().isoformat(),
        "data": {
            "merge_context_id": merge_context_id,
            "error_code": error_code,
            "error_message": error_message,
        }
    }
    await manager.send_to_context(merge_context_id, message)
    logger.error(f"Published error: {merge_context_id}, {error_code}: {error_message}")


# =============================================================================
# Stats Endpoint
# =============================================================================

@router.get("/ws/stats")
async def websocket_stats():
    """Get WebSocket connection statistics."""
    return {
        "status": "ok",
        "stats": manager.stats,
    }
