import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError, TimeoutError
from sqlmodel import SQLModel

from src.routers import api_router

from src.core.database import (
    check_database_exists,
    database_health,
    init_engine,
    start_token_refresh,
    stop_token_refresh,
    user_based_auth_enabled,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events."""
    logger.info("Application startup initiated")

    # Log authentication mode
    auth_mode = "user-based" if user_based_auth_enabled else "app-level"
    logger.info(f"Authentication mode: {auth_mode}")

    # Check if database exists before initializing
    database_exists = check_database_exists()
    health_check_task = None

    if database_exists:
        try:
            if user_based_auth_enabled:
                # User-based auth: Skip engine initialization and token refresh
                # Database connections will be created per-request
                logger.info("User-based authentication enabled - per-request database connections will be used")
                logger.info("Database engine will be initialized on first request with user credentials")
                # Note: Still create health check task using app-level credentials for monitoring
                # This is acceptable as health checks don't query user data
                health_check_task = asyncio.create_task(check_database_health(300))
            else:
                # App-level auth: Initialize shared engine and start token refresh
                init_engine()
                from src.core.database import engine

                async with engine.begin() as conn:
                    await conn.run_sync(SQLModel.metadata.create_all)
                await start_token_refresh()
                health_check_task = asyncio.create_task(check_database_health(300))
                logger.info("Database engine initialized and health monitoring started")
        except Exception as e:
            logger.error(f"Failed to initialize database engine: {e}")
            logger.info("Application will start without database functionality")
    else:
        logger.info(
            "No Lakebase database instance found - starting with limited functionality"
        )
        logger.info(
            "Use POST /api/v1/resources/create-lakebase-resources to create database resources"
        )

    logger.info("Application startup complete")

    yield

    logger.info("Application shutdown initiated")
    if health_check_task:
        health_check_task.cancel()
        try:
            await health_check_task
        except asyncio.CancelledError:
            logger.info("Database health check task cancelled successfully")

    # Only stop token refresh if using app-level auth
    if not user_based_auth_enabled:
        await stop_token_refresh()

    logger.info("Application shutdown complete")


app = FastAPI(
    title="Lakebase Orders API",
    description="Scalable FastAPI app with multiple data domains",
    lifespan=lifespan,
)


# Global exception handlers
@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    logger.error(f"Database error on {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Database error occurred. Please try again later."},
    )


@app.exception_handler(OperationalError)
async def operational_error_handler(request: Request, exc: OperationalError):
    logger.error(
        f"Database connection error on {request.method} {request.url.path}: {exc}"
    )
    return JSONResponse(
        status_code=503,
        content={"detail": "Database temporarily unavailable. Please try again later."},
    )


@app.exception_handler(TimeoutError)
async def timeout_error_handler(request: Request, exc: TimeoutError):
    logger.error(f"Database timeout on {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=504, content={"detail": "Request timed out. Please try again."}
    )


# Performance monitoring middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    logger.info(
        f"Request: {request.method} {request.url.path} - {process_time * 1000:.1f}ms"
    )
    return response


app.include_router(api_router)


@app.get("/health", tags=["health"])
async def health_check():
    """Simple health check endpoint."""
    return {"status": "healthy", "timestamp": time.time()}


async def check_database_health(interval: int):
    while True:
        try:
            is_healthy = await database_health()
            if not is_healthy:
                logger.warning(
                    "Database Health check failed. Connection is not healthy."
                )
        except Exception as e:
            logger.error(f"Exception during health check: {e}")
        await asyncio.sleep(interval)


@app.get("/", tags=["root"])
async def home():
    """Root endpoint to verify API is running."""
    return {
        "message": """Welcome to the Lakebase API! \n
            Append '/docs' to the end of your URL to explore and test available endpoints."""
    }
