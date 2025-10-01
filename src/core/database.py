import asyncio
import logging
import os
import time
import uuid
from typing import AsyncGenerator

from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv
from fastapi import Request
from sqlalchemy import URL, event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()
logger = logging.getLogger(__name__)

# Global variables
engine: AsyncEngine | None = None
AsyncSessionLocal: sessionmaker | None = None
workspace_client: WorkspaceClient | None = None
database_instance = None

# Token management for background refresh (app-level auth)
postgres_password: str | None = None
last_password_refresh: float = 0
token_refresh_task: asyncio.Task | None = None

# User-based authentication
user_based_auth_enabled: bool = os.getenv("USER_BASED_AUTHENTICATION", "false").lower() == "true"

# User credential cache: {user_email: {"token": str, "username": str, "expires_at": float}}
user_credential_cache: dict[str, dict] = {}
USER_TOKEN_CACHE_DURATION = 45 * 60  # 45 minutes (before 50-min token expiry)


async def refresh_token_background():
    """Background task to refresh tokens every 50 minutes"""
    global postgres_password, last_password_refresh, workspace_client, database_instance

    while True:
        try:
            await asyncio.sleep(50 * 60)  # Wait 50 minutes
            logger.info(
                "Background token refresh: Generating fresh PostgreSQL OAuth token"
            )

            cred = workspace_client.database.generate_database_credential(
                request_id=str(uuid.uuid4()),
                instance_names=[database_instance.name],
            )
            postgres_password = cred.token
            last_password_refresh = time.time()
            logger.info("Background token refresh: Token updated successfully")

        except Exception as e:
            logger.error(f"Background token refresh failed: {e}")


def init_engine():
    """
    Initialize database connection using SQLAlchemy with automatic token refresh.
    Only used for app-level authentication mode.
    """
    global \
        engine, \
        AsyncSessionLocal, \
        workspace_client, \
        database_instance, \
        postgres_password, \
        last_password_refresh

    if user_based_auth_enabled:
        logger.info("User-based authentication enabled - skipping app-level engine initialization")
        return

    try:
        workspace_client = WorkspaceClient()

        instance_name = os.getenv("LAKEBASE_INSTANCE_NAME")
        if not instance_name:
            raise RuntimeError(
                "LAKEBASE_INSTANCE_NAME environment variable is required"
            )

        database_instance = workspace_client.database.get_database_instance(
            name=instance_name
        )

        # Generate initial credentials
        cred = workspace_client.database.generate_database_credential(
            request_id=str(uuid.uuid4()), instance_names=[database_instance.name]
        )
        postgres_password = cred.token
        last_password_refresh = time.time()
        logger.info("Database: Initial credentials generated")

        # Create Engine
        database_name = os.getenv("LAKEBASE_DATABASE_NAME", database_instance.name)
        username = (
            os.getenv("DATABRICKS_CLIENT_ID")
            or workspace_client.current_user.me().user_name
            or None
        )

        url = URL.create(
            drivername="postgresql+asyncpg",
            username=username,
            password="",  # Will be set by event handler
            host=database_instance.read_write_dns,
            port=int(os.getenv("DATABRICKS_DATABASE_PORT", "5432")),
            database=database_name,
        )

        engine = create_async_engine(
            url,
            pool_pre_ping=False,
            echo=False,
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
            pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
            # OPTIONAL: Recycle connections every hour (before token expires)
            pool_recycle=int(os.getenv("DB_POOL_RECYCLE_INTERVAL", "3600")),
            connect_args={
                "command_timeout": int(os.getenv("DB_COMMAND_TIMEOUT", "10")),
                "server_settings": {
                    "application_name": "fastapi_orders_app",
                },
                "ssl": "require",
            },
        )

        # Register token provider for new connections
        @event.listens_for(engine.sync_engine, "do_connect")
        def provide_token(dialect, conn_rec, cargs, cparams):
            global postgres_password
            # Use current token from background refresh
            cparams["password"] = postgres_password

        AsyncSessionLocal = sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False
        )
        logger.info(
            f"Database engine initialized for {database_name} with background token refresh"
        )

    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise RuntimeError(f"Failed to initialize database: {e}") from e


async def start_token_refresh():
    """Start the background token refresh task"""
    global token_refresh_task
    if token_refresh_task is None or token_refresh_task.done():
        token_refresh_task = asyncio.create_task(refresh_token_background())
        logger.info("Background token refresh task started")


async def stop_token_refresh():
    """Stop the background token refresh task"""
    global token_refresh_task
    if token_refresh_task and not token_refresh_task.done():
        token_refresh_task.cancel()
        try:
            await token_refresh_task
        except asyncio.CancelledError:
            pass
        logger.info("Background token refresh task stopped")


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session with automatic token refresh (app-level auth)"""
    if AsyncSessionLocal is None:
        raise RuntimeError("Engine not initialized; call init_engine() first")
    async with AsyncSessionLocal() as session:
        yield session


def get_user_credentials_from_cache(user_email: str) -> dict | None:
    """Get cached user credentials if still valid"""
    if user_email in user_credential_cache:
        cached = user_credential_cache[user_email]
        if time.time() < cached["expires_at"]:
            return cached
        else:
            # Expired, remove from cache
            del user_credential_cache[user_email]
    return None


async def generate_user_db_credentials(user_token: str, user_email: str) -> dict:
    """
    Generate PostgreSQL credentials for a specific user using their access token.

    Args:
        user_token: The user's Databricks access token from X-Forwarded-Access-Token header
        user_email: The user's email from X-Forwarded-Email header (for caching)

    Returns:
        dict with keys: token (password), username, expires_at
    """
    global database_instance

    # Check cache first
    cached = get_user_credentials_from_cache(user_email)
    if cached:
        logger.debug(f"Using cached credentials for user: {user_email}")
        return cached

    try:
        # Create WorkspaceClient with user's token using PAT auth type
        # auth_type="pat" forces SDK to use ONLY the token and ignore env vars
        workspace_host = os.getenv("DATABRICKS_HOST")
        user_workspace_client = WorkspaceClient(
            host=workspace_host,
            token=user_token,
            auth_type="pat"
        )

        # Get database instance if not already loaded
        if database_instance is None:
            instance_name = os.getenv("LAKEBASE_INSTANCE_NAME")
            if not instance_name:
                raise RuntimeError("LAKEBASE_INSTANCE_NAME environment variable is required")
            database_instance = user_workspace_client.database.get_database_instance(name=instance_name)

        # Generate database credentials for this user
        cred = user_workspace_client.database.generate_database_credential(
            request_id=str(uuid.uuid4()),
            instance_names=[database_instance.name]
        )

        # Get username from the user's workspace client
        username = user_workspace_client.current_user.me().user_name

        # Cache the credentials
        credentials = {
            "token": cred.token,
            "username": username,
            "expires_at": time.time() + USER_TOKEN_CACHE_DURATION
        }
        user_credential_cache[user_email] = credentials

        logger.info(f"Generated new database credentials for user: {user_email}")
        return credentials

    except Exception as e:
        logger.error(f"Failed to generate user credentials for {user_email}: {e}")
        raise RuntimeError(f"Failed to generate user database credentials: {e}") from e


async def get_user_async_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """
    Get a database session authenticated as the requesting user.
    Extracts user information from Databricks Apps forwarded headers.

    Args:
        request: FastAPI Request object containing X-Forwarded-* headers

    Yields:
        AsyncSession authenticated as the specific user
    """
    # Extract user information from headers
    user_token = request.headers.get("x-forwarded-access-token")
    user_email = request.headers.get("x-forwarded-email")
    user_name = request.headers.get("x-forwarded-user")

    # Validate required headers
    if not user_token:
        raise RuntimeError(
            "User-based authentication enabled but X-Forwarded-Access-Token header not found. "
            "Ensure app is running in Databricks Apps environment or provide header for local testing."
        )

    if not user_email:
        # Fallback to user_name if email not available
        user_email = user_name or "unknown_user"
        logger.warning(f"X-Forwarded-Email header not found, using fallback: {user_email}")

    # Generate or retrieve cached credentials
    credentials = await generate_user_db_credentials(user_token, user_email)

    # Create database connection URL for this user
    database_name = os.getenv("LAKEBASE_DATABASE_NAME", database_instance.name if database_instance else "")

    url = URL.create(
        drivername="postgresql+asyncpg",
        username=credentials["username"],
        password=credentials["token"],
        host=database_instance.read_write_dns if database_instance else os.getenv("LAKEBASE_HOST"),
        port=int(os.getenv("DATABRICKS_DATABASE_PORT", "5432")),
        database=database_name,
    )

    # Create a temporary engine for this user
    # Use smaller pool for per-user engines
    user_engine = create_async_engine(
        url,
        pool_pre_ping=False,
        echo=False,
        pool_size=2,  # Smaller pool per user
        max_overflow=3,
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE_INTERVAL", "3600")),
        connect_args={
            "command_timeout": int(os.getenv("DB_COMMAND_TIMEOUT", "10")),
            "server_settings": {
                "application_name": f"fastapi_orders_app_user_{user_email}",
            },
            "ssl": "require",
        },
    )

    # Create session factory for this user
    UserAsyncSessionLocal = sessionmaker(
        bind=user_engine, class_=AsyncSession, expire_on_commit=False
    )

    try:
        async with UserAsyncSessionLocal() as session:
            yield session
    finally:
        # Clean up the engine after use
        await user_engine.dispose()


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """
    Smart database dependency that automatically selects authentication mode.

    If USER_BASED_AUTHENTICATION=true:
        - Uses per-user authentication with X-Forwarded-* headers
        - Extracts user info from Request

    If USER_BASED_AUTHENTICATION=false (default):
        - Uses app-level shared authentication
        - Request parameter is ignored

    Args:
        request: FastAPI Request object (automatically injected by FastAPI)

    Yields:
        AsyncSession with appropriate authentication
    """
    if user_based_auth_enabled:
        async for session in get_user_async_db(request):
            yield session
    else:
        async for session in get_async_db():
            yield session


def check_database_exists() -> bool:
    """Check if the Lakebase database instance exists"""
    try:
        workspace_client = WorkspaceClient()
        instance_name = os.getenv("LAKEBASE_INSTANCE_NAME")

        if not instance_name:
            logger.warning(
                "LAKEBASE_INSTANCE_NAME not set - database instance check skipped"
            )
            return False

        workspace_client.database.get_database_instance(name=instance_name)
        logger.info(f"Lakebase database instance '{instance_name}' exists")
        return True
    except Exception as e:
        if "not found" in str(e).lower() or "resource not found" in str(e).lower():
            logger.info(f"Lakebase database instance '{instance_name}' does not exist")
        else:
            logger.error(f"Error checking database instance existence: {e}")
        return False


async def database_health() -> bool:
    global engine

    if engine is None:
        logger.error("Database engine failed to initialize.")
        return False

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            logger.info("Database connection is healthy.")
            return True
    except Exception as e:
        logger.error("Database health check failed: %s", e)
        return False
