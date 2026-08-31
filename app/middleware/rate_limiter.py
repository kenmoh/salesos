import logging
import time

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.redis_client import cache_get, cache_set

logger = logging.getLogger("storeflow.rate_limiter")

# Default rate limits per tier
TIER_LIMITS = {
    "anonymous": (30, 60),  # 30 requests per 60s
    "default": (100, 60),  # 100 requests per 60s
}


class DistributedRateLimiterMiddleware(BaseHTTPMiddleware):
    """Redis-backed distributed rate limiter using sliding window counters.

    Rate limits are applied per IP address. Authenticated requests get
    a higher limit than anonymous ones.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/ready"):
            return await call_next(request)

        ip = (
            request.headers.get(
                "x-forwarded-for", request.client.host if request.client else "0.0.0.0"
            )
            .split(",")[0]
            .strip()
        )

        user_id = getattr(request.state, "user_id", None)
        limit, window = TIER_LIMITS.get("default") if user_id else TIER_LIMITS["anonymous"]

        key = f"sf:ratelimit:{ip}"
        now = time.time()

        try:
            data = await cache_get(key)
            window_data: dict = {
                "count": 0,
                "window_start": now,
            }
            if data:
                import json

                window_data = json.loads(data)

            if now - window_data["window_start"] > window:
                window_data["count"] = 0
                window_data["window_start"] = now

            window_data["count"] += 1
            remaining = max(0, limit - window_data["count"])

            import json

            await cache_set(key, json.dumps(window_data), window)

            response: Response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-IP"] = ip

            if window_data["count"] > limit:
                retry_after = int(window - (now - window_data["window_start"]))
                response.headers["Retry-After"] = str(retry_after)
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. Retry after {retry_after}s",
                    headers={"Retry-After": str(retry_after)},
                )

            return response
        except HTTPException:
            raise
        except Exception:
            return await call_next(request)
