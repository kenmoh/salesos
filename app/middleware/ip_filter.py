import logging
from ipaddress import ip_address, ip_network

from fastapi import HTTPException
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.redis_client import get_cache_redis

logger = logging.getLogger("storeflow.ip_filter")

DENIED_NETWORKS: list[str] = []
ALLOWED_NETWORKS: list[str] = []
BAN_CACHE_TTL = 300  # 5 minutes


class IPFilterMiddleware(BaseHTTPMiddleware):
    """IP allow/deny list middleware.

    Checks the client IP against configured denied/allowed networks
    and database-stored bans. Denied requests receive a 403 response.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/ready"):
            return await call_next(request)

        ip_str = (
            request.headers.get(
                "x-forwarded-for", request.client.host if request.client else "0.0.0.0"
            )
            .split(",")[0]
            .strip()
        )

        try:
            client_ip = ip_address(ip_str)
        except ValueError:
            return await call_next(request)

        for network in DENIED_NETWORKS:
            if client_ip in ip_network(network, strict=False):
                logger.warning("Blocked request from denied IP: %s", ip_str)
                raise HTTPException(status_code=403, detail="Access denied")

        if ALLOWED_NETWORKS:
            allowed = any(client_ip in ip_network(net, strict=False) for net in ALLOWED_NETWORKS)
            if not allowed:
                logger.warning("Blocked request from unallowed IP: %s", ip_str)
                raise HTTPException(status_code=403, detail="Access denied")

        if await self._is_ip_banned(ip_str):
            logger.warning("Blocked request from banned IP: %s", ip_str)
            raise HTTPException(status_code=403, detail="Access denied")

        return await call_next(request)

    async def _is_ip_banned(self, ip_str: str) -> bool:
        cache = await get_cache_redis()
        cache_key = f"sf:ipban:{ip_str}"

        if cache:
            try:
                cached = await cache.get(cache_key)
                if cached is not None:
                    return cached == "1"
            except Exception:
                pass

        try:
            from app.common.bridge import _get_shared_engine
            from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
            engine = _get_shared_engine()
            factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                async with session.begin():
                    result = await session.execute(
                        text("""
                            SELECT 1 FROM ip_bans
                            WHERE ip = :ip
                            AND (expires_at IS NULL OR expires_at > now())
                            LIMIT 1
                        """),
                        {"ip": ip_str},
                    )
                    is_banned = result.scalar_one_or_none() is not None
        except Exception:
            is_banned = False

        if cache:
            try:
                await cache.set(cache_key, "1" if is_banned else "0", ex=BAN_CACHE_TTL)
            except Exception:
                pass

        return is_banned
