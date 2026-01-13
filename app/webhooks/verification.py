"""
GitHub webhook signature verification.

Implements HMAC SHA-256 signature verification for GitHub webhooks.
"""

import hmac
import hashlib
from fastapi import HTTPException, status
from app.config import config
import logging

logger = logging.getLogger(__name__)


def verify_webhook_signature(payload: bytes, signature: str, secret: str = None) -> None:
    """
    Verify GitHub webhook signature.

    Args:
        payload: Raw request body (bytes)
        signature: X-Hub-Signature-256 header value
        secret: Webhook secret (defaults to config value)

    Raises:
        HTTPException: If signature verification fails
    """
    if secret is None:
        secret = config.github_webhook_secret

    if not signature:
        logger.warning("Webhook received without signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing webhook signature"
        )

    # Extract hash from signature (format: "sha256=<hash>")
    try:
        algorithm, provided_hash = signature.split("=", 1)
    except ValueError:
        logger.warning(f"Invalid signature format: {signature}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature format"
        )

    if algorithm != "sha256":
        logger.warning(f"Unsupported signature algorithm: {algorithm}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unsupported signature algorithm"
        )

    # Compute expected hash
    mac = hmac.new(secret.encode(), msg=payload, digestmod=hashlib.sha256)
    expected_hash = mac.hexdigest()

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(expected_hash, provided_hash):
        logger.warning(
            "Webhook signature verification failed",
            extra={"signature_provided": provided_hash[:8] + "..."}
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook signature verification failed"
        )

    logger.debug("Webhook signature verified successfully")
