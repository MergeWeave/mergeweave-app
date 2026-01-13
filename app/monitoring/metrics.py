"""
Prometheus Metrics Registry for MergeWeave GitHub App

This module defines ALL metrics used across the GitHub App service.
Metrics are organized by workstream responsibility:

- Workstream 1 (Infrastructure): Webhook, Database, HTTP metrics
- Workstream 2 (GitHub Integration): GitHub API, Installation metrics
- Workstream 3 (CRE Integration): Conflict detection and resolution metrics

All metrics are defined here for consistency, even if they're populated
by different workstreams. This prevents naming conflicts and ensures
consistent labeling across the codebase.
"""

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import APIRouter, Response

router = APIRouter()

# ============================================================================
# WEBHOOK METRICS (populated by Workstream 1)
# ============================================================================

webhook_requests_total = Counter(
    'webhook_requests_total',
    'Total webhook requests received from GitHub',
    ['event_type', 'action']
)
"""
Tracks all incoming webhook requests by type and action.

Labels:
- event_type: GitHub event type (push, pull_request, installation, etc.)
- action: Event action (opened, closed, synchronize, created, deleted, etc.)

Populated by: app/webhooks/router.py (Workstream 1)
Usage: Increment on every webhook receipt

Example:
    webhook_requests_total.labels(event_type="push", action="default").inc()
"""

webhook_processing_duration = Histogram(
    'webhook_processing_duration_seconds',
    'Time taken to process webhook events',
    ['event_type'],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0]
)
"""
Tracks webhook processing latency from receipt to completion.

Labels:
- event_type: GitHub event type

Buckets: 100ms to 120s (accommodates CRE processing time)

Populated by: app/webhooks/router.py (Workstream 1)
Usage: Observe duration at end of webhook processing

Example:
    start_time = time.time()
    # ... process webhook ...
    duration = time.time() - start_time
    webhook_processing_duration.labels(event_type="push").observe(duration)
"""

webhook_errors_total = Counter(
    'webhook_errors_total',
    'Total webhook processing errors',
    ['event_type', 'error_type']
)
"""
Tracks errors during webhook processing.

Labels:
- event_type: GitHub event type
- error_type: Exception class name (ValueError, HTTPException, etc.)

Populated by: app/webhooks/router.py (Workstream 1)
Usage: Increment on exception in webhook handler

Example:
    try:
        await process_webhook(payload)
    except Exception as e:
        webhook_errors_total.labels(
            event_type="push",
            error_type=type(e).__name__
        ).inc()
"""

# ============================================================================
# CONFLICT DETECTION METRICS (populated by Workstream 3)
# ============================================================================

conflicts_detected_total = Counter(
    'conflicts_detected_total',
    'Total merge conflicts detected',
    ['repository', 'branch']
)
"""
Tracks conflict detection events.

Labels:
- repository: Full repository name (owner/repo)
- branch: Branch name where conflict detected

Populated by: app/services/conflict_detector.py (Workstream 3)
Usage: Increment when conflict detected via git or file analysis

Example:
    if has_conflicts:
        conflicts_detected_total.labels(
            repository="org/repo",
            branch="feature-branch"
        ).inc()
"""

conflicts_resolved_total = Counter(
    'conflicts_resolved_total',
    'Total conflicts auto-resolved by CRE',
    ['repository', 'strategy']
)
"""
Tracks successful conflict resolutions.

Labels:
- repository: Full repository name
- strategy: CRE strategy used (ours, theirs, syntactic, semantic)

Populated by: app/services/cre_integration.py (Workstream 3)
Usage: Increment when CRE successfully resolves conflict

Example:
    if resolution.status == "success":
        conflicts_resolved_total.labels(
            repository="org/repo",
            strategy="syntactic"
        ).inc()
"""

conflict_resolution_duration = Histogram(
    'conflict_resolution_duration_seconds',
    'Time taken by CRE to resolve conflicts',
    ['repository'],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0]
)
"""
Tracks CRE processing duration.

Labels:
- repository: Full repository name

Buckets: 500ms to 120s

Populated by: app/services/cre_integration.py (Workstream 3)
Usage: Observe CRE processing time

Example:
    start_time = time.time()
    result = await cre_client.resolve_conflict(dag)
    duration = time.time() - start_time
    conflict_resolution_duration.labels(repository="org/repo").observe(duration)
"""

# ============================================================================
# GITHUB API METRICS (populated by Workstream 2)
# ============================================================================

github_api_requests_total = Counter(
    'github_api_requests_total',
    'Total requests made to GitHub API',
    ['endpoint', 'status_code']
)
"""
Tracks all GitHub API requests.

Labels:
- endpoint: API endpoint pattern (/repos/{owner}/{repo}/commits, /app/installations, etc.)
- status_code: HTTP status code (200, 201, 404, 403, etc.)

Populated by: app/clients/github_api.py (Workstream 2)
Usage: Increment after every GitHub API request

Example:
    response = await client.get(f"/repos/{owner}/{repo}")
    github_api_requests_total.labels(
        endpoint="/repos/{owner}/{repo}",
        status_code=response.status_code
    ).inc()
"""

github_api_duration = Histogram(
    'github_api_duration_seconds',
    'GitHub API request duration',
    ['endpoint'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
)
"""
Tracks GitHub API request latency.

Labels:
- endpoint: API endpoint pattern

Buckets: 100ms to 10s

Populated by: app/clients/github_api.py (Workstream 2)
Usage: Observe request duration

Example:
    start_time = time.time()
    response = await client.get(endpoint)
    duration = time.time() - start_time
    github_api_duration.labels(endpoint=endpoint_pattern).observe(duration)
"""

github_rate_limit_remaining = Gauge(
    'github_rate_limit_remaining',
    'Remaining GitHub API rate limit quota',
    ['resource']
)
"""
Tracks GitHub API rate limit quota.

Labels:
- resource: Rate limit resource type (core, search, graphql)

Populated by: app/clients/github_api.py (Workstream 2)
Usage: Set from X-RateLimit-Remaining header

Example:
    remaining = int(response.headers.get("X-RateLimit-Remaining", 0))
    github_rate_limit_remaining.labels(resource="core").set(remaining)
"""

# ============================================================================
# DATABASE METRICS (populated by Workstream 1)
# ============================================================================

database_query_duration = Histogram(
    'database_query_duration_seconds',
    'Database query execution time',
    ['query_type'],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0]
)
"""
Tracks database query performance.

Labels:
- query_type: Type of query (select_installation, insert_conflict, update_resolution, etc.)

Buckets: 1ms to 2s

Populated by: app/database/session.py or individual models (Workstream 1)
Usage: Observe query execution time

Example:
    start_time = time.time()
    result = await db.execute(query)
    duration = time.time() - start_time
    database_query_duration.labels(query_type="select_installation").observe(duration)
"""

database_connections_active = Gauge(
    'database_connections_active',
    'Number of active database connections'
)
"""
Tracks active database connections in the pool.

Populated by: app/database/session.py (Workstream 1)
Usage: Set from connection pool stats

Example:
    pool_size = engine.pool.size()
    database_connections_active.set(pool_size)
"""

# ============================================================================
# INSTALLATION METRICS (populated by Workstream 2)
# ============================================================================

active_installations = Gauge(
    'active_installations',
    'Number of active GitHub App installations'
)
"""
Tracks count of active installations.

Populated by: app/services/installation_manager.py (Workstream 2)
Usage: Set from database count

Example:
    count = await db.execute(
        select(func.count()).select_from(GitHubInstallation).where(is_active=True)
    )
    active_installations.set(count.scalar())
"""

repositories_monitored = Gauge(
    'repositories_monitored',
    'Number of repositories being monitored for conflicts'
)
"""
Tracks count of active repositories under monitoring.

Populated by: app/services/repository_manager.py (Workstream 2)
Usage: Set from database count

Example:
    count = await db.execute(
        select(func.count()).select_from(GitHubRepository).where(is_active=True)
    )
    repositories_monitored.set(count.scalar())
"""

# ============================================================================
# HTTP METRICS (populated by Workstream 1 middleware)
# ============================================================================

http_requests_total = Counter(
    'http_requests_total',
    'Total HTTP requests received',
    ['method', 'endpoint', 'status_code']
)
"""
Tracks all HTTP requests to the service.

Labels:
- method: HTTP method (GET, POST, PUT, DELETE)
- endpoint: Normalized endpoint path (/github-app/webhook, /github-app/health, etc.)
- status_code: HTTP response status code

Populated by: app/middleware/metrics_middleware.py (Workstream 1)
Usage: Increment after every HTTP request

Example:
    http_requests_total.labels(
        method="POST",
        endpoint="/github-app/webhook",
        status_code=200
    ).inc()
"""

http_request_duration = Histogram(
    'http_request_duration_seconds',
    'HTTP request processing duration',
    ['method', 'endpoint'],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
)
"""
Tracks HTTP request latency.

Labels:
- method: HTTP method
- endpoint: Normalized endpoint path

Buckets: 1ms to 10s

Populated by: app/middleware/metrics_middleware.py (Workstream 1)
Usage: Observe request duration

Example:
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    http_request_duration.labels(
        method=request.method,
        endpoint=normalize_path(request.url.path)
    ).observe(duration)
"""

# ============================================================================
# CACHE METRICS (populated by Workstream 1)
# ============================================================================

cache_operations_total = Counter(
    'cache_operations_total',
    'Total Redis cache operations',
    ['operation', 'status']
)
"""
Tracks Redis cache operations.

Labels:
- operation: Operation type (get, set, delete, exists)
- status: Operation result (hit, miss, error, success)

Populated by: app/cache/redis_client.py (Workstream 1)
Usage: Increment on cache operations

Example:
    result = await redis.get(key)
    if result:
        cache_operations_total.labels(operation="get", status="hit").inc()
    else:
        cache_operations_total.labels(operation="get", status="miss").inc()
"""

cache_operation_duration = Histogram(
    'cache_operation_duration_seconds',
    'Redis operation duration',
    ['operation'],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1]
)
"""
Tracks Redis operation latency.

Labels:
- operation: Operation type

Buckets: 0.1ms to 100ms

Populated by: app/cache/redis_client.py (Workstream 1)
Usage: Observe operation duration
"""

# ============================================================================
# RESOLUTION PACKAGE METRICS (populated by Workstream 3)
# ============================================================================

resolution_packages_generated = Counter(
    'resolution_packages_generated_total',
    'Total resolution packages generated',
    ['status']
)
"""
Tracks resolution package generation.

Labels:
- status: Generation status (success, failed)

Populated by: app/services/cre_integration.py (Workstream 3)
Usage: Increment when resolution package created
"""

resolution_packages_applied = Counter(
    'resolution_packages_applied_total',
    'Total resolution packages applied by users',
    ['repository']
)
"""
Tracks user application of resolution packages.

Labels:
- repository: Full repository name

Populated by: app/api/resolutions.py (Workstream 2)
Usage: Increment when user clicks "Apply Fix"
"""

# ============================================================================
# CONFIGURATION METRICS (Phase 4)
# ============================================================================

config_loads_total = Counter(
    'config_loads_total',
    'Total configuration loads from repositories',
    ['repository', 'source']
)
"""
Tracks configuration loading operations.

Labels:
- repository: Full repository name (owner/repo)
- source: Config source ("file" or "default")

Populated by: app/services/config_parser.py (Phase 4)
Usage: Increment when config is loaded

Example:
    config_loads_total.labels(repository="org/repo", source="file").inc()
"""

config_cache_hits = Counter(
    'config_cache_hits_total',
    'Total configuration cache hits',
    ['cache_level']
)
"""
Tracks config cache effectiveness.

Labels:
- cache_level: Cache level ("memory" or "redis")

Populated by: app/services/config_parser.py (Phase 4)
"""

config_validation_errors = Counter(
    'config_validation_errors_total',
    'Total configuration validation errors',
    ['error_type']
)
"""
Tracks config parsing/validation failures.

Labels:
- error_type: Type of error (yaml_parse, schema_validation, etc.)

Populated by: app/services/config_parser.py (Phase 4)
"""

# ============================================================================
# RATE LIMITING METRICS (Phase 4)
# ============================================================================

rate_limit_requests_total = Counter(
    'rate_limit_requests_total',
    'Total requests checked by rate limiter',
    ['endpoint_pattern', 'result']
)
"""
Tracks rate limit check results.

Labels:
- endpoint_pattern: Endpoint prefix (/api/dashboard, /webhooks, etc.)
- result: Check result ("allowed" or "blocked")

Populated by: app/middleware/rate_limit.py (Phase 4)
"""

rate_limit_blocked_total = Counter(
    'rate_limit_blocked_total',
    'Total requests blocked by rate limiting',
    ['client_type', 'endpoint_pattern']
)
"""
Tracks rate-limited requests.

Labels:
- client_type: Type of client ("ip", "user", "installation")
- endpoint_pattern: Endpoint that was rate limited

Populated by: app/middleware/rate_limit.py (Phase 4)
"""

rate_limit_bucket_usage = Gauge(
    'rate_limit_bucket_usage',
    'Current rate limit bucket usage percentage',
    ['endpoint_pattern']
)
"""
Tracks rate limit bucket fill level.

Labels:
- endpoint_pattern: Endpoint prefix

Populated by: app/middleware/rate_limit.py (Phase 4)
"""

# ============================================================================
# AUDIT LOGGING METRICS (Phase 4)
# ============================================================================

audit_events_total = Counter(
    'audit_events_total',
    'Total audit events logged',
    ['event_type', 'severity']
)
"""
Tracks audit event logging.

Labels:
- event_type: Type of audit event (auth.login, config.update, etc.)
- severity: Event severity (info, warning, error, critical)

Populated by: app/services/audit_log.py (Phase 4)
"""

audit_events_by_actor = Counter(
    'audit_events_by_actor_total',
    'Audit events by actor type',
    ['actor_type', 'outcome']
)
"""
Tracks audit events by actor.

Labels:
- actor_type: Type of actor (user, installation, system)
- outcome: Event outcome (success, failure)

Populated by: app/services/audit_log.py (Phase 4)
"""

# ============================================================================
# DASHBOARD API METRICS (Phase 4)
# ============================================================================

dashboard_api_requests = Counter(
    'dashboard_api_requests_total',
    'Total dashboard API requests',
    ['endpoint', 'status_code']
)
"""
Tracks dashboard-specific API usage.

Labels:
- endpoint: Dashboard API endpoint
- status_code: HTTP response status

Populated by: app/api/dashboard.py, app/api/config.py (Phase 4)
"""

merge_context_operations = Counter(
    'merge_context_operations_total',
    'Total merge context operations',
    ['operation', 'status']
)
"""
Tracks merge context lifecycle.

Labels:
- operation: Operation type (create, resolve, apply)
- status: Operation status (success, failure)

Populated by: app/services/merge_context_service.py (Phase 4)
"""

websocket_connections = Gauge(
    'websocket_connections_active',
    'Active WebSocket connections',
    ['client_type']
)
"""
Tracks active WebSocket connections.

Labels:
- client_type: Type of client (dashboard, cli)

Populated by: app/api/websocket.py (Phase 4)
"""

websocket_messages_total = Counter(
    'websocket_messages_total',
    'Total WebSocket messages sent',
    ['message_type', 'direction']
)
"""
Tracks WebSocket message traffic.

Labels:
- message_type: Type of message (conflict_update, resolution_complete, etc.)
- direction: Message direction (inbound, outbound)

Populated by: app/api/websocket.py (Phase 4)
"""

# ============================================================================
# WEBSOCKET METRICS - WS3.6 (Phase 3 Observability)
# ============================================================================

websocket_connections_total = Counter(
    'websocket_connections_total',
    'Total WebSocket connections established'
)
"""
Tracks total connections established since service start.

Populated by: app/api/websocket.py (WS3.6)
Usage: Increment on every successful WebSocket connection
"""

websocket_disconnections_total = Counter(
    'websocket_disconnections_total',
    'Total WebSocket disconnections',
    ['reason']
)
"""
Tracks disconnection events by reason.

Labels:
- reason: Disconnection reason (normal, error, timeout, auth_failed, limit_exceeded)

Populated by: app/api/websocket.py (WS3.6)
"""

websocket_errors_total = Counter(
    'websocket_errors_total',
    'WebSocket errors encountered',
    ['error_type']
)
"""
Tracks WebSocket-related errors.

Labels:
- error_type: Type of error (send_failed, auth_failed, invalid_message, connection_error)

Populated by: app/api/websocket.py (WS3.6)
"""

websocket_connection_duration_seconds = Histogram(
    'websocket_connection_duration_seconds',
    'WebSocket connection duration in seconds',
    buckets=[1, 5, 10, 30, 60, 300, 600, 1800, 3600, 7200]
)
"""
Tracks how long WebSocket connections stay open.

Buckets: 1s to 2 hours

Populated by: app/api/websocket.py (WS3.6)
Usage: Observe duration when connection closes
"""

websocket_message_size_bytes = Histogram(
    'websocket_message_size_bytes',
    'Size of WebSocket messages sent',
    buckets=[100, 500, 1000, 5000, 10000, 50000, 100000]
)
"""
Tracks the size of outbound WebSocket messages.

Buckets: 100B to 100KB

Populated by: app/api/websocket.py (WS3.6)
"""

device_offline_events_total = Counter(
    'device_offline_events_total',
    'Total device offline events detected via WebSocket'
)
"""
Tracks when devices go offline (detected via WebSocket disconnect).

Populated by: app/api/websocket.py (WS3.7)
Usage: Increment when device disconnect triggers offline status
"""

device_status_updates_total = Counter(
    'device_status_updates_total',
    'Total device status updates published',
    ['status']
)
"""
Tracks device status change events.

Labels:
- status: Device status (online, offline)

Populated by: app/services/device_manager.py (WS3.7)
"""

# ============================================================================
# METRICS ENDPOINT
# ============================================================================

@router.get("/metrics")
async def metrics():
    """
    Expose Prometheus metrics endpoint.

    This endpoint exposes all metrics defined in this module.
    Metrics are populated by different workstreams:

    Workstream 1 (Infrastructure):
    - webhook_* (requests, duration, errors)
    - database_* (query duration, connections)
    - http_* (requests, duration)
    - cache_* (operations, duration)

    Workstream 2 (GitHub Integration):
    - github_api_* (requests, duration, rate limits)
    - active_installations
    - repositories_monitored
    - resolution_packages_applied (API endpoint)

    Workstream 3 (CRE Integration):
    - conflicts_* (detected, resolved, resolution duration)
    - resolution_packages_generated

    Phase 4 (Configuration & Production):
    - config_* (loads, cache hits, validation errors)
    - rate_limit_* (requests, blocked, bucket usage)
    - audit_* (events by type, by actor)
    - dashboard_api_* (requests, merge context operations)
    - websocket_* (connections, messages)

    Returns:
        Prometheus-formatted metrics text
    """
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )
