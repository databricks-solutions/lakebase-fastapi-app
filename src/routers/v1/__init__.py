"""V1 API routes."""

import logging

from fastapi import APIRouter

from .healthcheck import router as healthcheck_router
from .orders import router as orders_router

logger = logging.getLogger(__name__)


def create_router() -> APIRouter:
    """Create the API router with a static, deterministic surface.

    All routers register unconditionally so the OpenAPI contract doesn't depend
    on infra state. Data endpoints (orders) are guarded by the `require_db`
    dependency, which returns 503 until the database engine is initialized.
    """
    router = APIRouter()
    router.include_router(healthcheck_router)
    router.include_router(orders_router)
    return router
