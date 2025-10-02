"""Authentication module for handling user-based and service principal authentication."""

import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from databricks.sdk import WorkspaceClient
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Cache for user credentials (email -> UserCredentials)
# TTL: 55 minutes (tokens expire at 60 minutes)
_user_credentials_cache: dict[str, "UserCredentials"] = {}
TOKEN_TTL_SECONDS = 55 * 60


@dataclass
class UserCredentials:
    """Stores user authentication credentials and metadata."""

    email: str
    access_token: str
    workspace_client: WorkspaceClient
    db_oauth_token: str
    token_created_at: float

    def is_token_expired(self) -> bool:
        """Check if the database OAuth token needs refresh."""
        return (time.time() - self.token_created_at) > TOKEN_TTL_SECONDS


def get_user_credentials_from_request(request: Request) -> tuple[str, str]:
    """
    Extract user email and access token from Databricks Apps headers.

    Args:
        request: FastAPI request object

    Returns:
        Tuple of (email, access_token)

    Raises:
        HTTPException: If required headers are missing
    """
    email = request.headers.get("X-Forwarded-Email")
    access_token = request.headers.get("X-Forwarded-Access-Token")

    if not email or not access_token:
        logger.error("Missing required Databricks Apps authentication headers")
        raise HTTPException(
            status_code=401,
            detail="Missing authentication headers. This endpoint requires Databricks Apps authentication.",
        )

    return email, access_token


def get_or_create_user_credentials(
    request: Request, instance_name: str
) -> UserCredentials:
    """
    Get cached user credentials or create new ones.

    Args:
        request: FastAPI request object
        instance_name: Lakebase database instance name

    Returns:
        UserCredentials object with valid tokens

    Raises:
        HTTPException: If authentication fails
    """
    email, access_token = get_user_credentials_from_request(request)

    # Check cache for existing valid credentials
    if email in _user_credentials_cache:
        cached_creds = _user_credentials_cache[email]
        if not cached_creds.is_token_expired():
            logger.debug(f"Using cached credentials for user: {email}")
            return cached_creds
        else:
            logger.info(f"Cached credentials expired for user: {email}, refreshing...")

    # Create new credentials
    try:
        # Get Databricks workspace host - required for user auth
        workspace_host = os.getenv("DATABRICKS_HOST")
        if not workspace_host:
            raise RuntimeError(
                "DATABRICKS_HOST environment variable must be set for user-based authentication"
            )

        # Create user-specific WorkspaceClient with ONLY token auth
        # Temporarily remove OAuth env vars to prevent conflict
        saved_client_id = os.environ.pop("DATABRICKS_CLIENT_ID", None)
        saved_client_secret = os.environ.pop("DATABRICKS_CLIENT_SECRET", None)

        try:
            user_workspace_client = WorkspaceClient(
                host=workspace_host,
                token=access_token,
            )
        finally:
            # Restore OAuth env vars for service principal use
            if saved_client_id:
                os.environ["DATABRICKS_CLIENT_ID"] = saved_client_id
            if saved_client_secret:
                os.environ["DATABRICKS_CLIENT_SECRET"] = saved_client_secret

        # Generate database OAuth token for this user
        cred = user_workspace_client.database.generate_database_credential(
            request_id=str(uuid.uuid4()),
            instance_names=[instance_name],
        )

        db_oauth_token = cred.token
        logger.info(f"Generated new database credentials for user: {email}")

        # Create and cache credentials
        user_creds = UserCredentials(
            email=email,
            access_token=access_token,
            workspace_client=user_workspace_client,
            db_oauth_token=db_oauth_token,
            token_created_at=time.time(),
        )

        _user_credentials_cache[email] = user_creds
        return user_creds

    except Exception as e:
        logger.error(f"Failed to create user credentials for {email}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to authenticate user: {str(e)}",
        )


def clear_user_credentials_cache(email: Optional[str] = None):
    """
    Clear the user credentials cache.

    Args:
        email: If provided, clear only this user's credentials. Otherwise clear all.
    """
    global _user_credentials_cache

    if email:
        if email in _user_credentials_cache:
            del _user_credentials_cache[email]
            logger.info(f"Cleared cached credentials for user: {email}")
    else:
        _user_credentials_cache.clear()
        logger.info("Cleared all cached user credentials")
