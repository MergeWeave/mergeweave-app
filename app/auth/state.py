"""
OAuth State Management.

Provides cryptographically secure state parameter generation and verification
for OAuth flows. Protects against CSRF attacks during GitHub App installation.
"""

import hmac
import hashlib
import time
import json
import base64
import logging
from typing import Dict, Any, Optional, Union
from uuid import UUID
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


class StateManager:
    """
    Manages OAuth state parameters with HMAC signing.

    State format: base64(json({user_id, timestamp}) + "|" + hmac_signature)

    The state parameter:
    - Prevents CSRF attacks
    - Links installation to correct user
    - Expires after max_age seconds
    """

    def __init__(self, secret_key: str, max_age: int = 600):
        """
        Initialize state manager.

        Args:
            secret_key: Secret key for HMAC signing
            max_age: Maximum age of state parameter in seconds (default: 10 minutes)
        """
        self.secret_key = secret_key
        self.max_age = max_age

    def generate_state(self, user_id: Union[UUID, str], **extra_data) -> str:
        """
        Generate signed state parameter.

        Args:
            user_id: User ID (UUID) to encode in state
            **extra_data: Additional data to include in state

        Returns:
            Base64-encoded signed state parameter

        Example:
            state = manager.generate_state(user_id=uuid_obj, redirect_url="/dashboard")
        """
        # Convert UUID to string for JSON serialization
        user_id_str = str(user_id) if isinstance(user_id, UUID) else user_id

        # Create state data
        state_data = {
            "user_id": user_id_str,
            "timestamp": int(time.time()),
            **extra_data
        }

        # Serialize to JSON
        state_json = json.dumps(state_data, sort_keys=True)

        # Generate HMAC signature
        signature = hmac.new(
            self.secret_key.encode(),
            state_json.encode(),
            hashlib.sha256
        ).hexdigest()

        # Combine data and signature
        combined = f"{state_json}|{signature}"

        # Base64 encode
        state = base64.urlsafe_b64encode(combined.encode()).decode()

        logger.debug(
            f"Generated OAuth state",
            extra={"user_id": user_id_str, "state_length": len(state)}
        )

        return state

    def verify_state(self, state: str) -> Dict[str, Any]:
        """
        Verify and decode state parameter.

        Args:
            state: State parameter from OAuth callback

        Returns:
            Dictionary containing state data (user_id, timestamp, etc.)

        Raises:
            HTTPException: If state is invalid, expired, or signature mismatch
        """
        try:
            # Base64 decode
            combined = base64.urlsafe_b64decode(state.encode()).decode()

            # Split data and signature
            if "|" not in combined:
                raise ValueError("Invalid state format: missing separator")

            state_json, provided_signature = combined.rsplit("|", 1)

            # Verify signature
            expected_signature = hmac.new(
                self.secret_key.encode(),
                state_json.encode(),
                hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(expected_signature, provided_signature):
                logger.warning("OAuth state signature mismatch")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid state parameter signature"
                )

            # Parse JSON
            state_data = json.loads(state_json)

            # Check required fields
            if "user_id" not in state_data or "timestamp" not in state_data:
                raise ValueError("Invalid state format: missing required fields")

            # Check expiration
            timestamp = state_data["timestamp"]
            age = int(time.time()) - timestamp

            if age > self.max_age:
                logger.warning(
                    f"OAuth state expired",
                    extra={"age_seconds": age, "max_age": self.max_age}
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"State parameter expired (age: {age}s, max: {self.max_age}s)"
                )

            user_id = state_data["user_id"]
            logger.debug(
                f"Verified OAuth state",
                extra={"user_id": user_id, "age_seconds": age}
            )

            return state_data

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse OAuth state JSON: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid state parameter format (JSON parse error)"
            )

        except (ValueError, KeyError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to decode OAuth state: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid state parameter format: {str(e)}"
            )

    def extract_user_id(self, state: str) -> str:
        """
        Convenience method to extract user_id from state.

        Args:
            state: State parameter from OAuth callback

        Returns:
            User ID (as string, can be converted to UUID)

        Raises:
            HTTPException: If state is invalid
        """
        state_data = self.verify_state(state)
        return state_data["user_id"]


# FastAPI dependency
def get_state_manager() -> StateManager:
    """
    FastAPI dependency to get state manager instance.

    Returns:
        StateManager instance
    """
    from app.config import get_config

    config = get_config()

    # OAuth state secret should be separate from other secrets
    # For backward compatibility, fall back to webhook secret if not set
    state_secret = config.github_oauth_state_secret
    if state_secret is None:
        state_secret = config.github_webhook_secret
        logger.warning(
            "GITHUB_OAUTH_STATE_SECRET not set, falling back to GITHUB_WEBHOOK_SECRET. "
            "This is insecure in production. Set a separate secret for OAuth state to limit "
            "the blast radius of potential secret compromise.",
            extra={
                "environment": getattr(config, "environment", "unknown"),
                "security_risk": "Using same secret for webhook signatures and OAuth state"
            }
        )

    return StateManager(
        secret_key=state_secret,
        max_age=600  # 10 minutes
    )
