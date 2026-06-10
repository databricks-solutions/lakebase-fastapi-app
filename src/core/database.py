import asyncio
import logging
import os
import time
from typing import AsyncGenerator

from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv
from fastapi import HTTPException
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

# Lakebase OAuth credentials last 60 minutes. Refresh proactively at 45 min and
# retry on failure so a single transient error can't leave us past expiry.
TOKEN_LIFETIME_SECONDS = 3600
REFRESH_AT_SECONDS = 45 * 60
REFRESH_RETRY_BACKOFF = [5, 15, 30, 60, 120]


def _generate_token() -> None:
    """Generate a fresh PostgreSQL OAuth token (synchronous SDK call)."""
    global postgres_password, last_password_refresh
    cred = workspace_client.postgres.generate_database_credential(
        endpoint=database_endpoint_name
    )
    postgres_password = cred.token
    last_password_refresh = time.time()


async def refresh_token_background():
    """Proactively refresh the OAuth token before expiry, with bounded retries."""
    while True:
        sleep_for = max(0, (last_password_refresh + REFRESH_AT_SECONDS) - time.time())
        await asyncio.sleep(sleep_for)
        for attempt, backoff in enumerate([0, *REFRESH_RETRY_BACKOFF]):
            if backoff:
                await asyncio.sleep(backoff)
            try:
                _generate_token()
                logger.info("Token refresh succeeded (attempt %d)", attempt + 1)
                break
            except Exception:
                logger.exception("Token refresh failed (attempt %d)", attempt + 1)
        else:
            logger.critical(
                "Token refresh exhausted retries; connections will fail at expiry"
            )


def _resolve_endpoint_name() -> str:
    """Lakebase endpoint resource path: projects/<p>/branches/<b>/endpoints/<e>.

    Used to mint OAuth tokens. Resolution order:
      1. ENDPOINT_NAME env — set by the bundle (databricks.yml config.env) when
         deployed, or in .env locally.
      2. Self-derive from the app's own DB binding — when running as a deployed
         Databricks App (PGAPPNAME is injected), read the bound branch and append
         the endpoint id. Keeps app.yaml free of project/branch names.
      3. Legacy LAKEBASE_PROJECT_ID/BRANCH/ENDPOINT (local/back-compat).
    """
    name = os.getenv("ENDPOINT_NAME")
    if name:
        return name

    endpoint_id = os.getenv("LAKEBASE_ENDPOINT", "primary")

    app_name = os.getenv("PGAPPNAME") or os.getenv("DATABRICKS_APP_NAME")
    if app_name:
        try:
            app = WorkspaceClient().apps.get(name=app_name)
            branch = next(
                (
                    r.postgres.branch
                    for r in (app.resources or [])
                    if getattr(r, "postgres", None) and r.postgres.branch
                ),
                None,
            )
            if branch:
                return f"{branch}/endpoints/{endpoint_id}"
        except Exception as e:  # noqa: BLE001 - fall through to env-based resolution
            logger.warning(f"Could not derive endpoint from app binding: {e}")

    project_id = os.getenv("LAKEBASE_PROJECT_ID")
    if not project_id:
        raise RuntimeError(
            "Set ENDPOINT_NAME (or LAKEBASE_PROJECT_ID) so the app can reach Lakebase"
        )
    branch = os.getenv("LAKEBASE_BRANCH", "main")
    return f"projects/{project_id}/branches/{branch}/endpoints/{endpoint_id}"


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

        database_endpoint_name = _resolve_endpoint_name()

        # Connection details: prefer the PG* env vars the Lakebase app binding injects
        # at runtime (PGHOST/PGPORT/PGDATABASE/PGUSER/PGSSLMODE); locally fall back to
        # deriving the host from the endpoint and to DATABRICKS_CLIENT_ID/current user.
        host = os.getenv("PGHOST") or workspace_client.postgres.get_endpoint(
            name=database_endpoint_name
        ).status.hosts.host
        port = int(os.getenv("PGPORT") or os.getenv("DATABRICKS_DATABASE_PORT", "5432"))
        database_name = os.getenv("PGDATABASE") or os.getenv(
            "LAKEBASE_DATABASE_NAME", "databricks_postgres"
        )
        username = (
            os.getenv("PGUSER")
            or os.getenv("DATABRICKS_CLIENT_ID")
            or workspace_client.current_user.me().user_name
            or None
        )
        sslmode = os.getenv("PGSSLMODE", "require")

        # Generate initial credentials (OAuth token used as the Postgres password)
        _generate_token()
        logger.info(
            f"Database: connecting to {host}/{database_name} as {username} "
            f"(endpoint {database_endpoint_name})"
        )

        url = URL.create(
            drivername="postgresql+psycopg",
            username=username,
            password="",  # Will be set by event handler
            host=host,
            port=port,
            database=database_name,
        )

        command_timeout_ms = int(os.getenv("DB_COMMAND_TIMEOUT", "10")) * 1000
        engine = create_async_engine(
            url,
            # The autoscaling endpoint suspends after idle (scale-to-zero), which kills
            # pooled connections. pre_ping validates each connection on checkout so the
            # first request after a wake doesn't hit a dead socket.
            pool_pre_ping=True,
            echo=False,
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
            pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
            # Recycle below the 60-min token lifetime (and any server idle limit).
            pool_recycle=int(os.getenv("DB_POOL_RECYCLE_INTERVAL", "2700")),
            # Reuse hot connections; let idle ones age out instead of round-robining.
            pool_use_lifo=True,
            connect_args={
                # psycopg3 (async) connection kwargs
                "sslmode": sslmode,
                "application_name": "fastapi_orders_app",
                # Per-statement server-side timeout so one slow query can't pin a
                # pooled connection indefinitely (DB_COMMAND_TIMEOUT is in seconds).
                "options": f"-c statement_timeout={command_timeout_ms}",
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
            # Safety net: if the background refresh task died, never hand out a token
            # that's near/past its lifetime — refresh synchronously at connect time.
            if time.time() - last_password_refresh > TOKEN_LIFETIME_SECONDS - 60:
                try:
                    _generate_token()
                    logger.warning("Token was stale at connect; refreshed synchronously")
                except Exception:
                    logger.exception("Synchronous token refresh at connect failed")
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


def require_db() -> None:
    """FastAPI dependency: return 503 until the database engine is initialized.

    Lets data routers register unconditionally (static API surface) while still
    failing cleanly when Lakebase isn't provisioned/reachable yet.
    """
    if AsyncSessionLocal is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Database not initialized. Provision Lakebase via "
                "`databricks bundle deploy` (its postdeploy hook grants the app "
                "service principal access)."
            ),
        )


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session with automatic token refresh"""
    if AsyncSessionLocal is None:
        raise RuntimeError("Engine not initialized; call init_engine() first")
    async with AsyncSessionLocal() as session:
        yield session


def check_database_exists() -> bool:
    """Check if the Lakebase endpoint is reachable (resolves ENDPOINT_NAME)."""
    try:
        endpoint_name = _resolve_endpoint_name()
    except RuntimeError as e:
        logger.warning(f"Endpoint not configured - check skipped: {e}")
        return False
    try:
        WorkspaceClient().postgres.get_endpoint(name=endpoint_name)
        logger.info(f"Lakebase endpoint '{endpoint_name}' exists")
        return True
    except Exception as e:
        if "not found" in str(e).lower() or "resource not found" in str(e).lower():
            logger.info(f"Lakebase endpoint '{endpoint_name}' does not exist")
        else:
            logger.error(f"Error checking endpoint existence: {e}")
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
