"""SalesOS API entry point.

This module creates the FastAPI application, configures middleware,
and registers all route modules.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.redis_client import close_redis

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
)
logger = logging.getLogger("salesos")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("SalesOS %s starting [env=%s]", settings.app_version, settings.env)
    settings.validate_production_secrets()

    # Start celery worker subprocess
    import subprocess
    import sys
    import os

    celery_proc = None
    try:
        celery_proc = subprocess.Popen(
            [sys.executable, "-m", "celery", "-A", "app.worker.celery_app", "worker",
             "--loglevel=info", "--pool=solo", "--concurrency=1"],
            cwd=os.getcwd(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Celery worker started (pid=%s)", celery_proc.pid)
    except Exception as e:
        logger.warning("Failed to start celery worker: %s", e)

    yield

    # Shutdown celery worker
    if celery_proc and celery_proc.poll() is None:
        logger.info("Stopping celery worker (pid=%s)", celery_proc.pid)
        celery_proc.terminate()
        try:
            celery_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            celery_proc.kill()
            celery_proc.wait()
        logger.info("Celery worker stopped")

    logger.info("SalesOS shutting down")
    await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(
        title="SalesOS API",
        description="SaaS POS, Inventory, Invoicing and Accounting for the Nigerian market.",
        version=settings.app_version,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    @app.exception_handler(Exception)
    async def global_exc_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "")
        logger.exception("Unhandled exception: rid=%s path=%s", request_id, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal error occurred", "request_id": request_id},
        )

    # --- Middleware (order matters: last added = first executed) ---

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "Accept",
            "Origin",
            "X-Requested-With",
        ],
        expose_headers=["X-Request-ID", "X-Process-Time", "X-RateLimit-IP", "Retry-After"],
        max_age=600,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    if settings.is_production:
        app.add_middleware(
            TrustedHostMiddleware, allowed_hosts=["api.salesos.ng", "*.salesos.ng"]
        )

    from app.middleware.csrf import CSRFTokenMiddleware
    from app.middleware.audit import AuditMiddleware
    from app.middleware.timeout import TimeoutMiddleware
    from app.middleware.security_headers import SecurityHeadersMiddleware
    from app.middleware.rate_limiter import DistributedRateLimiterMiddleware
    from app.middleware.webhook_replay import WebhookReplayMiddleware
    from app.middleware.request_validator import RequestValidatorMiddleware
    from app.middleware.ip_filter import IPFilterMiddleware
    from app.middleware.request_id import RequestIDMiddleware

    app.add_middleware(CSRFTokenMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(TimeoutMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(DistributedRateLimiterMiddleware)
    app.add_middleware(WebhookReplayMiddleware)
    app.add_middleware(RequestValidatorMiddleware)
    app.add_middleware(IPFilterMiddleware)
    app.add_middleware(RequestIDMiddleware)

    # --- Routers ---

    prefix = "/api/v1"

    from app.auth.routes import router as auth_router
    from app.inventory.routes import router as inventory_router
    from app.stores.routes import router as stores_router
    from app.sales.routes import router as sales_router
    from app.payments.routes import router as payments_router
    from app.accounting.routes import router as accounting_router
    from app.documents.routes import router as documents_router
    from app.reporting.routes import router as reports_router
    from app.identity.routes import router as security_router
    from app.cart.routes import router as cart_router
    from app.customers.routes import router as customers_router
    from app.ai.routes import router as ai_router
    from app.discounts.routes import router as discounts_router
    from app.discounts.routes import coupon_router

    for router in (
        auth_router,
        inventory_router,
        stores_router,
        sales_router,
        payments_router,
        accounting_router,
        documents_router,
        reports_router,
        security_router,
        cart_router,
        customers_router,
        ai_router,
        discounts_router,
        coupon_router,
    ):
        app.include_router(router, prefix=prefix)

    from app.catalog.routes import scan_router as scan_products_router

    app.include_router(scan_products_router, prefix=prefix)

    from app.platform.routes import router as platform_router

    app.include_router(platform_router, prefix=prefix)

    from app.stores.routes_sync import sync_router

    app.include_router(sync_router, prefix=prefix)

    # --- Health / Readiness ---

    @app.get("/health", tags=["System"], include_in_schema=False)
    async def health():
        from sqlalchemy import text

        health_status = {
            "status": "ok",
            "version": settings.app_version,
            "env": settings.env,
            "dependencies": {},
        }

        try:
            from app.common.db.session import SessionLocal

            async with SessionLocal() as session:
                await session.execute(text("SELECT 1"))
            health_status["dependencies"]["database"] = "ok"
        except Exception as e:
            health_status["status"] = "degraded"
            health_status["dependencies"]["database"] = f"error: {str(e)[:50]}"

        try:
            from app.core.redis_client import get_cache_redis

            redis = await get_cache_redis()
            if redis:
                await redis.ping()
                health_status["dependencies"]["redis"] = "ok"
            else:
                health_status["dependencies"]["redis"] = "unavailable"
        except Exception as e:
            health_status["status"] = "degraded"
            health_status["dependencies"]["redis"] = f"error: {str(e)[:50]}"

        return health_status

    @app.get("/ready", tags=["System"], include_in_schema=False)
    async def ready():
        return {"status": "ready"}

    return app


app = create_app()


def run() -> None:
    """Console entrypoint for local API development."""
    import uvicorn

    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=8000, reload=not settings.is_production
    )
