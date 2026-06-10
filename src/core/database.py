import asyncio
import logging
import os
import time
from typing import AsyncGenerator

from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv
from sqlalchemy import URL, event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()
logger = logging.getLogger(__name__)

# Global variables
engine: AsyncEngine | None = None
AsyncSessionLocal: sessionmaker | None = None
workspace_client: WorkspaceClient | None = None
# Full endpoint resource path, e.g. projects/<id>/branches/<branch>/endpoints/<endpoint>
database_endpoint_name: str | None = None

# Token management for background refresh
postgres_password: str | None = None
last_password_refresh: float = 0
token_refresh_task: asyncio.Task | None = None


async def refresh_token_background():
    """Background task to refresh tokens every 50 minutes"""
    global postgres_password, last_password_refresh, workspace_client, database_endpoint_name

    while True:
        try:
            await asyncio.sleep(50 * 60)  # Wait 50 minutes
            logger.info(
                "Background token refresh: Generating fresh PostgreSQL OAuth token"
            )

            cred = workspace_client.postgres.generate_database_credential(
                endpoint=database_endpoint_name,
            )
            postgres_password = cred.token
            last_password_refresh = time.time()
            logger.info("Background token refresh: Token updated successfully")

        except Exception as e:
            logger.error(f"Background token refresh failed: {e}")


def init_engine():
    """Initialize database connection using SQLAlchemy with automatic token refresh"""
    global \
        engine, \
        AsyncSessionLocal, \
        workspace_client, \
        database_endpoint_name, \
        postgres_password, \
        last_password_refresh

    try:
        workspace_client = WorkspaceClient()

        project_id = os.getenv("LAKEBASE_PROJECT_ID")
        if not project_id:
            raise RuntimeError(
                "LAKEBASE_PROJECT_ID environment variable is required"
            )

        # Autoscaling Lakebase: connect via the endpoint's host, not the
        # provisioned database-instance API.
        branch = os.getenv("LAKEBASE_BRANCH", "main")
        endpoint_id = os.getenv("LAKEBASE_ENDPOINT", "primary")
        database_endpoint_name = (
            f"projects/{project_id}/branches/{branch}/endpoints/{endpoint_id}"
        )

        endpoint = workspace_client.postgres.get_endpoint(name=database_endpoint_name)
        host = endpoint.status.hosts.host

        # Generate initial credentials (OAuth token used as the Postgres password)
        cred = workspace_client.postgres.generate_database_credential(
            endpoint=database_endpoint_name
        )
        postgres_password = cred.token
        last_password_refresh = time.time()
        logger.info("Database: Initial credentials generated")

        # Create Engine
        database_name = os.getenv("LAKEBASE_DATABASE_NAME", "databricks_postgres")
        username = (
            os.getenv("DATABRICKS_CLIENT_ID")
            or workspace_client.current_user.me().user_name
            or None
        )

        url = URL.create(
            drivername="postgresql+psycopg",
            username=username,
            password="",  # Will be set by event handler
            host=host,
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
                # psycopg3 (async) connection kwargs
                "sslmode": "require",
                "application_name": "fastapi_orders_app",
                # Server-side prepared statements left ENABLED (psycopg default: prepare
                # after ~5 executions per connection) for lower latency on repeated
                # queries. Safe here because Lakebase OAuth apps connect to the DIRECT
                # endpoint, not a transaction pooler. If you ever route through the
                # Lakebase pooler, set "prepare_threshold": None to disable them.
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
    """Get a database session with automatic token refresh"""
    if AsyncSessionLocal is None:
        raise RuntimeError("Engine not initialized; call init_engine() first")
    async with AsyncSessionLocal() as session:
        yield session


def check_database_exists() -> bool:
    """Check if the Lakebase autoscaling project exists"""
    project_id = os.getenv("LAKEBASE_PROJECT_ID")
    try:
        workspace_client = WorkspaceClient()

        if not project_id:
            logger.warning(
                "LAKEBASE_PROJECT_ID not set - project check skipped"
            )
            return False

        workspace_client.postgres.get_project(name=f"projects/{project_id}")
        logger.info(f"Lakebase project '{project_id}' exists")
        return True
    except Exception as e:
        if "not found" in str(e).lower() or "resource not found" in str(e).lower():
            logger.info(f"Lakebase project '{project_id}' does not exist")
        else:
            logger.error(f"Error checking project existence: {e}")
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
