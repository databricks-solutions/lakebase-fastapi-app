import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError, TimeoutError
from sqlmodel import SQLModel

from .core.database import (
    database_health,
    init_engine,
    start_token_refresh,
    stop_token_refresh,
)
from .routers import orders

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup initiated")
    init_engine()
    from .core.database import engine

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await start_token_refresh()
    health_check_task = asyncio.create_task(check_database_health(300))
    logger.info("Application startup complete")

    yield

    logger.info("Application shutdown initiated")
    health_check_task.cancel()
    try:
        await health_check_task
    except asyncio.CancelledError:
        logger.info("Database health check task cancelled successfully")
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


app.include_router(orders.router)


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
