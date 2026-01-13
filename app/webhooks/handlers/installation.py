"""
Installation Webhook Handlers.

Handles GitHub App installation lifecycle events:
- installation.created: App installed on account
- installation.deleted: App uninstalled
- installation.suspended: App access suspended
- installation.unsuspended: App access restored
"""

import logging
from typing import Dict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.webhooks.router import webhook_handler
from app.models.installation import GitHubInstallation
from app.models.user import User
from app.database.session import AsyncSessionLocal
from app.cache.redis_client import get_redis_client
from app.cache.invalidation import CacheInvalidator

logger = logging.getLogger(__name__)


@webhook_handler("installation")
async def handle_installation_event(payload: Dict, delivery_id: str):
    """
    Handle installation webhook events.

    Events:
    - installation.created: App installed on account
    - installation.deleted: App uninstalled
    - installation.suspended: App access suspended
    - installation.unsuspended: App access restored

    Args:
        payload: Webhook payload
        delivery_id: GitHub delivery ID
    """
    action = payload.get("action")
    installation_data = payload.get("installation", {})
    installation_id = installation_data.get("id")

    logger.info(
        f"Processing installation event",
        extra={
            "action": action,
            "installation_id": installation_id,
            "delivery_id": delivery_id
        }
    )

    # Get database session
    async with AsyncSessionLocal() as db:
        try:
            if action == "created":
                await handle_installation_created(payload, db)
            elif action == "deleted":
                await handle_installation_deleted(payload, db)
            elif action == "suspended":
                await handle_installation_suspended(payload, db)
            elif action == "unsuspended":
                await handle_installation_unsuspended(payload, db)
            else:
                logger.warning(
                    f"Unknown installation action",
                    extra={
                        "action": action,
                        "installation_id": installation_id
                    }
                )

            await db.commit()

        except Exception as e:
            await db.rollback()
            logger.error(
                f"Error handling installation event",
                extra={
                    "action": action,
                    "installation_id": installation_id,
                    "error": str(e)
                },
                exc_info=True
            )
            raise


async def handle_installation_created(payload: Dict, db: AsyncSession):
    """
    Handle installation.created event.

    Creates new installation record in database.
    If installation already exists (e.g., from previous installation),
    reactivates it.

    Args:
        payload: Webhook payload
        db: Database session
    """
    installation_data = payload["installation"]
    installation_id = installation_data["id"]
    account = installation_data["account"]
    sender = payload.get("sender", {})

    logger.info(
        f"Creating installation",
        extra={
            "installation_id": installation_id,
            "account_login": account["login"],
            "account_type": account["type"]
        }
    )

    # Check if installation already exists
    result = await db.execute(
        select(GitHubInstallation).where(
            GitHubInstallation.installation_id == installation_id
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        logger.info(
            f"Installation already exists, reactivating",
            extra={"installation_id": installation_id}
        )

        # Reactivate existing installation
        await db.execute(
            update(GitHubInstallation)
            .where(GitHubInstallation.installation_id == installation_id)
            .values(
                is_active=True,
                suspended_at=None,
                permissions=installation_data.get("permissions", {}),
                events=installation_data.get("events", [])
            )
        )

    else:
        # Create or get user
        # In OAuth flow, user will be linked via callback
        # For now, create placeholder user based on sender
        sender_email = sender.get("email") or f"{sender.get('login', 'unknown')}@github.user"

        result = await db.execute(
            select(User).where(User.email == sender_email)
        )
        user = result.scalar_one_or_none()

        if not user:
            # Create user matching Public API schema
            user = User(
                email=sender_email,
                tier="free",  # Default tier
                active=True,
                extra_data={"github_login": sender.get("login")}  # Store GitHub login in extra_data
            )
            db.add(user)
            await db.flush()  # Get user.id

            logger.info(
                f"Created placeholder user",
                extra={
                    "user_id": str(user.id),  # Convert UUID to string for logging
                    "email": sender_email
                }
            )

        # Create installation
        installation = GitHubInstallation(
            user_id=user.id,
            installation_id=installation_id,
            account_id=account["id"],
            account_login=account["login"],
            account_type=account["type"],
            target_type=installation_data["target_type"],
            permissions=installation_data.get("permissions", {}),
            events=installation_data.get("events", []),
            is_active=True
        )
        db.add(installation)

        logger.info(
            f"Created installation record",
            extra={
                "installation_id": installation_id,
                "user_id": str(user.id),  # Convert UUID to string for logging
                "account_login": account["login"]
            }
        )


async def handle_installation_deleted(payload: Dict, db: AsyncSession):
    """
    Handle installation.deleted event.

    Marks installation as inactive and invalidates cache.

    Args:
        payload: Webhook payload
        db: Database session
    """
    installation_data = payload["installation"]
    installation_id = installation_data["id"]

    logger.info(
        f"Deleting installation",
        extra={"installation_id": installation_id}
    )

    # Get installation to find user_id for cache invalidation
    result = await db.execute(
        select(GitHubInstallation).where(
            GitHubInstallation.installation_id == installation_id
        )
    )
    installation = result.scalar_one_or_none()

    # Mark as inactive
    await db.execute(
        update(GitHubInstallation)
        .where(GitHubInstallation.installation_id == installation_id)
        .values(is_active=False)
    )

    # Invalidate cache
    redis_client = await get_redis_client()
    invalidator = CacheInvalidator(redis_client)

    user_id = installation.user_id if installation else None
    await invalidator.on_installation_deleted(installation_id, user_id)

    logger.info(
        f"Marked installation as deleted",
        extra={
            "installation_id": installation_id,
            "user_id": str(user_id) if user_id else None  # Convert UUID to string for logging
        }
    )


async def handle_installation_suspended(payload: Dict, db: AsyncSession):
    """
    Handle installation.suspended event.

    Marks installation as suspended and invalidates token cache.

    Args:
        payload: Webhook payload
        db: Database session
    """
    installation_data = payload["installation"]
    installation_id = installation_data["id"]

    logger.warning(
        f"Suspending installation",
        extra={"installation_id": installation_id}
    )

    # Mark as suspended using SQLAlchemy's func.now()
    from sqlalchemy import func

    await db.execute(
        update(GitHubInstallation)
        .where(GitHubInstallation.installation_id == installation_id)
        .values(suspended_at=func.now())
    )

    # Invalidate token cache
    redis_client = await get_redis_client()
    invalidator = CacheInvalidator(redis_client)
    await invalidator.on_installation_suspended(installation_id)

    logger.warning(
        f"Installation suspended",
        extra={"installation_id": installation_id}
    )


async def handle_installation_unsuspended(payload: Dict, db: AsyncSession):
    """
    Handle installation.unsuspended event.

    Clears suspension timestamp.

    Args:
        payload: Webhook payload
        db: Database session
    """
    installation_data = payload["installation"]
    installation_id = installation_data["id"]

    logger.info(
        f"Unsuspending installation",
        extra={"installation_id": installation_id}
    )

    # Clear suspension
    await db.execute(
        update(GitHubInstallation)
        .where(GitHubInstallation.installation_id == installation_id)
        .values(suspended_at=None)
    )

    logger.info(
        f"Installation unsuspended",
        extra={"installation_id": installation_id}
    )
