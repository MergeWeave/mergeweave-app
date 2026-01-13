"""
GitHub Webhook API Endpoints.

Receives GitHub webhook events, verifies signatures, and routes to handlers.
"""

import logging
from fastapi import APIRouter, Request, HTTPException, status, Header
from typing import Optional

from app.webhooks.router import verify_webhook_signature, route_webhook_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/github")
async def receive_github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header(None),
    x_github_delivery: Optional[str] = Header(None),
    x_hub_signature_256: Optional[str] = Header(None)
):
    """
    Receive and process GitHub webhook events.

    This endpoint:
    1. Verifies webhook signature (HMAC-SHA256)
    2. Extracts event type and payload
    3. Routes to appropriate handler
    4. Returns 200 OK immediately (async processing)

    Args:
        request: FastAPI request object
        x_github_event: GitHub event type header
        x_github_delivery: GitHub delivery ID header
        x_hub_signature_256: GitHub signature header

    Returns:
        Response dictionary with status

    Raises:
        HTTPException: If signature is invalid or processing fails
    """
    import hmac
    import hashlib
    from app.config import get_config

    # Validate headers
    if not x_github_event:
        logger.error("Missing X-GitHub-Event header")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Event header"
        )

    if not x_github_delivery:
        logger.error("Missing X-GitHub-Delivery header")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Delivery header"
        )

    # Get raw body for signature verification
    body = await request.body()

    # Verify signature
    if x_hub_signature_256:
        config = get_config()
        secret = config.github_webhook_secret.encode()

        # Validate signature format
        if not x_hub_signature_256.startswith("sha256="):
            logger.error("Invalid signature format")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature format"
            )

        provided_signature = x_hub_signature_256[7:]  # Remove "sha256=" prefix

        # Compute expected signature
        expected_signature = hmac.new(
            secret,
            body,
            hashlib.sha256
        ).hexdigest()

        # Constant-time comparison
        if not hmac.compare_digest(expected_signature, provided_signature):
            logger.error(
                "Webhook signature validation failed",
                extra={"delivery_id": x_github_delivery}
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature"
            )

        logger.debug("Webhook signature verified successfully")
    else:
        logger.warning(
            "Webhook received without signature",
            extra={"delivery_id": x_github_delivery}
        )
        # In development, might allow unsigned webhooks
        # In production, should reject

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

    logger.info(
        f"Received GitHub webhook",
        extra={
            "event": x_github_event,
            "delivery_id": x_github_delivery,
            "action": payload.get("action")
        }
    )

    # Route to handler
    response = await route_webhook_event(
        event_type=x_github_event,
        payload=payload,
        delivery_id=x_github_delivery
    )

    return response


@router.get("/handlers")
async def list_webhook_handlers():
    """
    List all registered webhook handlers.

    Useful for debugging and documentation.

    Returns:
        Dictionary of registered handlers
    """
    from app.webhooks.router import list_registered_handlers

    handlers = list_registered_handlers()

    return {
        "registered_handlers": handlers,
        "count": len(handlers)
    }
