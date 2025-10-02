"""FastAPI dependencies for database session management."""

import os
from typing import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_async_db, get_user_db_session


def is_user_based_auth() -> bool:
    """Check if user-based authentication is enabled."""
    return os.getenv("USER_BASED_AUTHENTICATION", "false").lower() == "true"


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """
    Get database session based on authentication mode.

    Routes to:
    - User-based session if USER_BASED_AUTHENTICATION=true
    - Service principal session if USER_BASED_AUTHENTICATION=false

    Args:
        request: FastAPI request object (used for user-based auth)

    Yields:
        AsyncSession: Database session
    """
    if is_user_based_auth():
        # User-based authentication: extract user credentials from headers
        async for session in get_user_db_session(request):
            yield session
    else:
        # Service principal authentication: use global engine
        async for session in get_async_db():
            yield session
