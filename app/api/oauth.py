"""
OAuth Flow API Endpoints.

Handles GitHub App installation OAuth flow:
1. User initiates installation
2. Redirected to GitHub with state parameter
3. GitHub redirects back with installation_id
4. Link installation to user account
"""

import logging
from uuid import UUID
from fastapi import APIRouter, Request, Depends, Query, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database.session import get_db
from app.models.installation import GitHubInstallation
from app.auth.state import StateManager, get_state_manager
from app.config import get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/github", tags=["oauth"])


@router.get("/install")
async def initiate_github_install(
    user_id: str = Query(..., description="User ID (UUID) from Public API"),
    redirect_url: Optional[str] = Query(None, description="URL to redirect after installation"),
    state_manager: StateManager = Depends(get_state_manager)
):
    """
    Initiate GitHub App installation flow.

    This endpoint is called by the Public API when a user wants to install
    the GitHub App. It generates a secure state parameter and redirects
    to GitHub's installation page.

    Args:
        user_id: User ID from Public API
        redirect_url: Optional URL to redirect to after installation
        state_manager: State manager dependency

    Returns:
        Redirect to GitHub App installation page
    """
    config = get_config()

    # Generate state parameter
    extra_data = {}
    if redirect_url:
        extra_data["redirect_url"] = redirect_url

    state = state_manager.generate_state(user_id, **extra_data)

    # Build GitHub App installation URL
    # Note: github_app_slug should be configured (e.g., "mergeweave")
    github_app_slug = getattr(config, "github_app_slug", "mergeweave")

    github_install_url = (
        f"https://github.com/apps/{github_app_slug}/installations/new"
        f"?state={state}"
    )

    logger.info(
        f"Initiating GitHub App installation",
        extra={
            "user_id": user_id,
            "github_app_slug": github_app_slug
        }
    )

    return RedirectResponse(url=github_install_url, status_code=302)


@router.get("/callback")
async def github_callback(
    request: Request,
    installation_id: int = Query(..., description="GitHub installation ID"),
    setup_action: str = Query(..., description="Setup action (install or update)"),
    state: Optional[str] = Query(None, description="State parameter for verification (optional for direct installs)"),
    code: Optional[str] = Query(None, description="OAuth code from GitHub (not used)"),
    db: AsyncSession = Depends(get_db),
    state_manager: StateManager = Depends(get_state_manager)
):
    """
    Handle GitHub App installation callback.

    Called by GitHub after user completes installation. Supports two flows:

    1. **Initiated from MergeWeave** (has state parameter):
       - Verify state parameter
       - Extract user_id from state
       - Link installation to user in database
       - Redirect to custom success page

    2. **Direct GitHub installation** (no state parameter):
       - Installation is created via webhook
       - Redirect to GitHub repo or default success page

    Args:
        request: FastAPI request object
        installation_id: GitHub installation ID
        setup_action: "install" or "update"
        state: State parameter for verification (optional)
        code: OAuth code from GitHub (not used, but included in callback)
        db: Database session
        state_manager: State manager dependency

    Returns:
        Redirect to success page
    """
    config = get_config()

    logger.info(
        f"GitHub App installation callback received",
        extra={
            "installation_id": installation_id,
            "setup_action": setup_action,
            "has_state": state is not None
        }
    )

    # Handle direct GitHub installation (no state parameter)
    if state is None:
        logger.info(
            f"Direct GitHub installation (no state parameter)",
            extra={
                "installation_id": installation_id,
                "setup_action": setup_action
            }
        )
        # Redirect to GitHub - user can see their installation there
        # The installation webhook will create the record in our database
        return RedirectResponse(
            url=f"https://github.com/settings/installations/{installation_id}",
            status_code=302
        )

    # Flow with state parameter (initiated from MergeWeave)
    # Verify state and extract user_id
    try:
        state_data = state_manager.verify_state(state)
        user_id = state_data["user_id"]
        redirect_url = state_data.get("redirect_url")
    except HTTPException as e:
        logger.error(
            f"State verification failed",
            extra={"error": e.detail}
        )
        raise

    logger.info(
        f"OAuth state verified",
        extra={
            "user_id": user_id,
            "installation_id": installation_id
        }
    )

    # Link installation to user
    # Note: installation.created webhook already created the installation record
    # We just need to update the user_id to ensure correct linking
    try:
        result = await db.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == installation_id
            )
        )
        installation = result.scalar_one_or_none()

        if installation:
            # Update user_id
            await db.execute(
                update(GitHubInstallation)
                .where(GitHubInstallation.installation_id == installation_id)
                .values(user_id=user_id)
            )
            await db.commit()

            logger.info(
                f"Installation linked to user",
                extra={
                    "installation_id": installation_id,
                    "user_id": user_id
                }
            )
        else:
            logger.warning(
                f"Installation not found in database",
                extra={
                    "installation_id": installation_id,
                    "note": "Webhook may not have arrived yet"
                }
            )

    except Exception as e:
        await db.rollback()
        logger.error(
            f"Error linking installation to user",
            extra={
                "installation_id": installation_id,
                "user_id": user_id,
                "error": str(e)
            },
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to link installation to user"
        )

    # Determine redirect URL
    # Include setup_action so frontend can detect successful installation
    if redirect_url:
        success_url = f"{redirect_url}?installation=success&installation_id={installation_id}&setup_action={setup_action}"
    else:
        # Default: redirect to Public API dashboard
        public_api_url = getattr(config, "public_api_url", "https://api.mergeweave.cloud")
        success_url = f"{public_api_url}/dashboard?installation=success&installation_id={installation_id}&setup_action={setup_action}"

    logger.info(
        f"GitHub App installed successfully",
        extra={
            "installation_id": installation_id,
            "user_id": user_id,
            "setup_action": setup_action,
            "redirect_url": success_url
        }
    )

    return RedirectResponse(url=success_url, status_code=302)


@router.get("/status")
async def installation_status(
    user_id: str = Query(..., description="User ID (UUID)"),
    db: AsyncSession = Depends(get_db)
):
    """
    Check GitHub App installation status for a user.

    Returns information about the user's active installations.

    Args:
        user_id: User ID (UUID as string)
        db: Database session

    Returns:
        Installation status
    """
    # Convert string UUID to UUID object for database query
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user_id format (must be UUID)"
        )

    result = await db.execute(
        select(GitHubInstallation).where(
            GitHubInstallation.user_id == user_uuid,
            GitHubInstallation.is_active == True
        )
    )
    installations = result.scalars().all()

    return {
        "user_id": user_id,
        "has_installation": len(installations) > 0,
        "installation_count": len(installations),
        "installations": [
            {
                "installation_id": inst.installation_id,
                "account_login": inst.account_login,
                "account_type": inst.account_type,
                "created_at": inst.created_at.isoformat() if inst.created_at else None
            }
            for inst in installations
        ]
    }
