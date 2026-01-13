"""
GitHub Webhook Router.

Handles webhook signature verification and event routing to appropriate handlers.
Provides decorator pattern for registering webhook event handlers.
"""

import hmac
import hashlib
import logging
import time
from typing import Dict, Callable, Awaitable
from fastapi import APIRouter, Request, HTTPException, status, BackgroundTasks, Depends

from app.config import get_config
from app.cache.redis_client import RedisClient, get_redis_client
from app.webhooks.deduplication import WebhookDeduplicator
from app.webhooks.verification import verify_webhook_signature as verify_signature_func
from app.monitoring import metrics

logger = logging.getLogger(__name__)

# Import handlers to register them (must be after logger and webhook_handler decorator)
# This import MUST be at the end of the file or after the @webhook_handler decorator is defined
# It will be imported below after the router is fully set up

# Create router
router = APIRouter()

# Event handler registry
_webhook_handlers: Dict[str, Callable[[Dict, str], Awaitable[None]]] = {}


def webhook_handler(event_type: str):
    """
    Decorator to register a webhook event handler.

    Usage:
        @webhook_handler("installation")
        async def handle_installation(payload: Dict, delivery_id: str):
            action = payload.get("action")
            # Handle installation event

    Args:
        event_type: GitHub webhook event type (e.g., "installation", "push")
    """
    def decorator(func: Callable[[Dict, str], Awaitable[None]]):
        _webhook_handlers[event_type] = func
        logger.info(f"Registered webhook handler: {event_type} -> {func.__name__}")
        return func
    return decorator


def get_handler(event_type: str) -> Callable[[Dict, str], Awaitable[None]]:
    """
    Get registered handler for an event type.

    Args:
        event_type: GitHub webhook event type

    Returns:
        Handler function or None if not registered
    """
    return _webhook_handlers.get(event_type)


async def verify_webhook_signature(request: Request) -> bool:
    """
    Verify GitHub webhook signature.

    GitHub signs webhooks with HMAC-SHA256 using the webhook secret.
    Signature is sent in X-Hub-Signature-256 header.

    Args:
        request: FastAPI request object

    Returns:
        True if signature is valid, False otherwise

    Raises:
        HTTPException: If signature is missing or invalid
    """
    config = get_config()
    secret = config.github_webhook_secret.encode()

    # Get signature from header
    signature_header = request.headers.get("X-Hub-Signature-256")
    if not signature_header:
        logger.error("Webhook signature missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook signature missing"
        )

    # Extract signature (format: "sha256=<signature>")
    if not signature_header.startswith("sha256="):
        logger.error("Invalid signature format")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature format"
        )

    provided_signature = signature_header[7:]  # Remove "sha256=" prefix

    # Get request body
    body = await request.body()

    # Compute expected signature
    expected_signature = hmac.new(
        secret,
        body,
        hashlib.sha256
    ).hexdigest()

    # Constant-time comparison to prevent timing attacks
    is_valid = hmac.compare_digest(expected_signature, provided_signature)

    if not is_valid:
        logger.error(
            "Webhook signature validation failed",
            extra={
                "delivery_id": request.headers.get("X-GitHub-Delivery"),
                "event": request.headers.get("X-GitHub-Event")
            }
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature"
        )

    logger.debug("Webhook signature verified successfully")
    return True


async def route_webhook_event(
    event_type: str,
    payload: Dict,
    delivery_id: str
) -> Dict[str, str]:
    """
    Route webhook event to appropriate handler.

    Args:
        event_type: GitHub event type (e.g., "installation", "push")
        payload: Webhook payload
        delivery_id: GitHub delivery ID (for logging and deduplication)

    Returns:
        Response dictionary with status

    Raises:
        HTTPException: If no handler is registered for the event type
    """
    handler = get_handler(event_type)

    if not handler:
        logger.warning(
            f"No handler registered for event type: {event_type}",
            extra={
                "event_type": event_type,
                "delivery_id": delivery_id
            }
        )
        # Return 200 OK even for unhandled events (GitHub requirement)
        return {
            "status": "ignored",
            "message": f"No handler for event type: {event_type}"
        }

    logger.info(
        f"Routing webhook event to handler",
        extra={
            "event_type": event_type,
            "delivery_id": delivery_id,
            "handler": handler.__name__
        }
    )

    try:
        await handler(payload, delivery_id)

        logger.info(
            f"Webhook event processed successfully",
            extra={
                "event_type": event_type,
                "delivery_id": delivery_id
            }
        )

        return {
            "status": "success",
            "message": f"Event {event_type} processed successfully"
        }

    except Exception as e:
        logger.error(
            f"Error processing webhook event",
            extra={
                "event_type": event_type,
                "delivery_id": delivery_id,
                "error": str(e)
            },
            exc_info=True
        )

        # Re-raise to return 500 to GitHub (will trigger retry)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing webhook: {str(e)}"
        )


def list_registered_handlers() -> Dict[str, str]:
    """
    Get list of all registered webhook handlers.

    Returns:
        Dictionary mapping event types to handler function names
    """
    return {
        event_type: handler.__name__
        for event_type, handler in _webhook_handlers.items()
    }


async def process_webhook_async(
    event_type: str,
    payload: Dict,
    delivery_id: str,
    start_time: float
):
    """
    Process webhook event asynchronously in background.

    Args:
        event_type: GitHub event type
        payload: Webhook payload
        delivery_id: GitHub delivery ID
        start_time: Request start timestamp
    """
    try:
        # Route to handler
        await route_webhook_event(event_type, payload, delivery_id)

        # Record success duration
        duration = time.time() - start_time
        metrics.webhook_processing_duration.labels(
            event_type=event_type
        ).observe(duration)

        logger.info(
            f"Webhook processed successfully in background",
            extra={
                "event_type": event_type,
                "delivery_id": delivery_id,
                "duration_seconds": duration
            }
        )

    except Exception as e:
        # Record error
        metrics.webhook_errors_total.labels(
            event_type=event_type,
            error_type=type(e).__name__
        ).inc()

        logger.error(
            f"Error processing webhook in background",
            extra={
                "event_type": event_type,
                "delivery_id": delivery_id,
                "error": str(e)
            },
            exc_info=True
        )


@router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    redis: RedisClient = Depends(get_redis_client)
):
    """
    Receive GitHub webhook events (per Workstream 1 spec).

    This is the main webhook endpoint that:
    1. Verifies webhook signature (HMAC-SHA256)
    2. Checks for duplicate delivery IDs (deduplication)
    3. Returns 200 OK immediately
    4. Processes event in background task
    5. Records metrics

    Args:
        request: FastAPI request object
        background_tasks: FastAPI background tasks
        redis: Redis client for deduplication

    Returns:
        200 OK response immediately

    Raises:
        HTTPException: If signature is invalid or webhook is duplicate
    """
    start_time = time.time()

    # Extract headers
    event_type = request.headers.get("X-GitHub-Event")
    delivery_id = request.headers.get("X-GitHub-Delivery")
    signature = request.headers.get("X-Hub-Signature-256")

    # Validate required headers
    if not event_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Event header"
        )

    if not delivery_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Delivery header"
        )

    # Get raw body
    body = await request.body()

    # Verify signature
    try:
        verify_signature_func(body, signature)
    except HTTPException:
        # Record failed verification
        metrics.webhook_errors_total.labels(
            event_type=event_type,
            error_type="SignatureVerificationError"
        ).inc()
        raise

    # Parse payload
    try:
        import json
        payload = json.loads(body)
    except Exception as e:
        logger.error(f"Failed to parse webhook payload: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload"
        )

    # Extract action for metrics
    action = payload.get("action", "default")

    # Check for duplicates
    deduplicator = WebhookDeduplicator(redis)
    if await deduplicator.is_duplicate(delivery_id):
        logger.warning(
            f"Duplicate webhook delivery ignored",
            extra={
                "event_type": event_type,
                "delivery_id": delivery_id
            }
        )
        # Return 200 OK for duplicates (already processed)
        return {
            "status": "duplicate",
            "message": "Webhook already processed"
        }

    # Mark as processed
    await deduplicator.mark_processed(delivery_id)

    # Record webhook receipt
    metrics.webhook_requests_total.labels(
        event_type=event_type,
        action=action
    ).inc()

    logger.info(
        f"Webhook received",
        extra={
            "event_type": event_type,
            "delivery_id": delivery_id,
            "action": action
        }
    )

    # Process in background
    background_tasks.add_task(
        process_webhook_async,
        event_type,
        payload,
        delivery_id,
        start_time
    )

    # Return 200 OK immediately
    return {
        "status": "accepted",
        "message": "Webhook accepted for processing"
    }

