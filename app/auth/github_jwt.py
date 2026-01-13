"""
GitHub App JWT Generation.

Generates JWTs signed with the GitHub App private key
for authenticating with GitHub's REST API.
"""

import jwt
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

logger = logging.getLogger(__name__)


class GitHubJWTGenerator:
    """Generates JWTs for GitHub App authentication."""

    def __init__(self, app_id: int, private_key: str):
        """
        Initialize JWT generator.

        Args:
            app_id: GitHub App ID
            private_key: RSA private key (PEM format)
        """
        self.app_id = app_id
        self.private_key = private_key

    def generate_jwt(self, expiration_seconds: int = 600) -> str:
        """
        Generate a JWT for GitHub App authentication.

        GitHub requires:
        - Algorithm: RS256
        - iat (issued at): Current time minus 60 seconds (clock skew)
        - exp (expiration): Max 10 minutes from iat
        - iss (issuer): GitHub App ID

        Args:
            expiration_seconds: JWT expiration (max 600 seconds)

        Returns:
            Signed JWT string

        Raises:
            ValueError: If expiration > 600 seconds
        """
        if expiration_seconds > 600:
            raise ValueError("JWT expiration must be ≤ 600 seconds (GitHub limit)")

        # Use UTC time and adjust for potential clock skew
        now = datetime.now(timezone.utc)
        issued_at = now - timedelta(seconds=60)  # 60 seconds ago (clock skew buffer)
        expires_at = now + timedelta(seconds=expiration_seconds)

        payload = {
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": str(self.app_id)
        }

        logger.debug(
            f"Generating GitHub App JWT",
            extra={
                "app_id": self.app_id,
                "iat": payload["iat"],
                "exp": payload["exp"],
                "ttl_seconds": expiration_seconds
            }
        )

        try:
            token = jwt.encode(
                payload,
                self.private_key,
                algorithm="RS256"
            )

            logger.info(
                f"GitHub App JWT generated successfully",
                extra={
                    "app_id": self.app_id,
                    "expires_at": expires_at.isoformat()
                }
            )

            return token

        except Exception as e:
            logger.error(
                f"Failed to generate GitHub App JWT",
                extra={
                    "app_id": self.app_id,
                    "error": str(e)
                },
                exc_info=True
            )
            raise