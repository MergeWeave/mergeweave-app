"""
Configuration management for GitHub App Service.

Loads and validates all environment variables on startup.
"""

import os
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field, validator


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # GitHub App Configuration (per Workstream 1 spec)
    github_app_id: int = Field(..., env="GITHUB_APP_ID")
    github_app_private_key_path: Path = Field(..., env="GITHUB_APP_PRIVATE_KEY_PATH")
    github_webhook_secret: str = Field(..., env="GITHUB_WEBHOOK_SECRET")
    github_app_slug: str = Field(..., env="GITHUB_APP_SLUG")
    # OAuth state secret (optional, falls back to webhook_secret for backward compatibility)
    github_oauth_state_secret: Optional[str] = Field(None, env="GITHUB_OAUTH_STATE_SECRET")

    # Service Integration Configuration (per Workstream 1 spec)
    operations_api_key: str = Field(..., env="OPERATIONS_API_KEY")
    public_api_callback_secret: str = Field(..., env="PUBLIC_API_CALLBACK_SECRET")
    public_api_url: str = Field(..., env="PUBLIC_API_URL")

    # Database
    database_url: str = Field(..., env="DATABASE_URL")

    # Redis
    redis_url: str = Field(..., env="REDIS_URL")

    # Service
    service_name: str = Field("github-app", env="SERVICE_NAME")
    service_port: int = Field(8000, env="SERVICE_PORT")
    environment: str = Field("development", env="ENVIRONMENT")

    # APP_BASE_URL (environment-based, per spec)
    app_base_url: Optional[str] = Field(None, env="APP_BASE_URL")
    ngrok_url: Optional[str] = Field(None, env="NGROK_URL")  # For development

    # Logging
    log_level: str = Field("INFO", env="LOG_LEVEL")
    log_format: str = Field("json", env="LOG_FORMAT")

    # CORS
    cors_origins: str = Field("", env="CORS_ORIGINS")

    # Feature Flags
    enable_write_back: bool = Field(False, env="ENABLE_WRITE_BACK")

    # Branch Loading Configuration (per Phase 1 P1-F2 spec)
    max_branches_fetch: int = Field(500, env="MAX_BRANCHES_FETCH")

    # S3/MinIO Configuration (per Workstream 3 spec)
    # Note: Using S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET to match project conventions (Public API, backup scripts, deployment/env.production)
    # Field name must match env var name (pydantic-settings v2 uses field name, not env= parameter)
    s3_endpoint: Optional[str] = Field(None)  # S3_ENDPOINT - For MinIO/Vultr compatibility
    s3_access_key: str = Field(..., env="S3_ACCESS_KEY")
    s3_secret_key: str = Field(..., env="S3_SECRET_KEY")
    s3_bucket: str = Field("mergeweave-resolutions", env="S3_BUCKET")
    s3_region: str = Field("us-east-1", env="S3_REGION")

    @validator("github_app_private_key_path")
    def validate_private_key_exists(cls, v):
        """Ensure private key file exists and is readable."""
        if not v.exists():
            raise ValueError(f"Private key file not found: {v}")
        if not os.access(v, os.R_OK):
            raise ValueError(f"Private key file not readable: {v}")

        # Optionally check file permissions (should be 400 or 600)
        stat_info = v.stat()
        permissions = oct(stat_info.st_mode)[-3:]
        if permissions not in ['400', '600']:
            # Log warning but don't fail (file permissions check is optional)
            import logging
            logging.getLogger(__name__).warning(
                f"Private key file has overly permissive permissions: {permissions}. "
                f"Recommended: 400 or 600 for security."
            )

        return v

    @validator("cors_origins")
    def parse_cors_origins(cls, v):
        """Parse comma-separated CORS origins."""
        if not v:
            return []
        return [origin.strip() for origin in v.split(",")]

    @property
    def github_app_private_key(self) -> str:
        """Load private key from file."""
        return self.github_app_private_key_path.read_text()

    def _get_app_base_url(self) -> str:
        """
        Get the application base URL based on environment.

        Priority (per Workstream 1 spec):
        1. Explicit APP_BASE_URL environment variable
        2. NGROK_URL for development
        3. Environment-based defaults

        Returns:
            str: Base URL for the application (e.g., https://webhook.mergeweave.cloud)
        """
        if self.app_base_url:
            return self.app_base_url

        if self.environment == "development" and self.ngrok_url:
            return self.ngrok_url

        # Environment-based defaults
        if self.environment == "production":
            return "https://webhook.mergeweave.cloud"
        elif self.environment == "staging":
            return "https://webhook-staging.mergeweave.cloud"
        else:  # development
            return "http://localhost:8000"

    @property
    def debug(self) -> bool:
        """Debug mode flag (True for development environment)."""
        return self.environment == "development"

    @property
    def port(self) -> int:
        """Alias for service_port."""
        return self.service_port

    @property
    def host(self) -> str:
        """Host to bind to."""
        return "0.0.0.0" if self.environment == "production" else "127.0.0.1"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # Allow extra environment variables without error


# Global config instance
_config: Settings = None


def get_config() -> Settings:
    """Get global configuration instance."""
    global _config
    if _config is None:
        _config = Settings()
    return _config


# Create singleton instance for direct import
config = get_config()