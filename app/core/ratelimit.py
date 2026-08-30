import json
import time

from fastapi import HTTPException, Request

from storeflow_api.core.redis_client import cache_get, cache_set


async def check_rate_limit(
    request: Request,
    *,
    namespace: str,
    max_requests: int,
    window_seconds: int,
    key_suffix: str | None = None,
) -> None:
    ip = (
        request.headers.get("x-forwarded-for", request.client.host if request.client else "0.0.0.0")
        .split(",")[0]
        .strip()
    )

    suffix = key_suffix or ip
    cache_key = f"sf:ratelimit:{namespace}:{suffix}"
    now = time.time()

    data = await cache_get(cache_key)
    window_data = {"count": 0, "window_start": now}
    if data:
        window_data = json.loads(data)

    if now - window_data["window_start"] > window_seconds:
        window_data["count"] = 0
        window_data["window_start"] = now

    window_data["count"] += 1
    remaining = max(0, max_requests - window_data["count"])

    await cache_set(cache_key, json.dumps(window_data), window_seconds)

    if window_data["count"] > max_requests:
        retry_after = int(window_seconds - (now - window_data["window_start"]))
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry_after}s",
            headers={"Retry-After": str(retry_after)},
        )


def auth_rate_limit(max_requests: int = 5, window_seconds: int = 900):
    async def limiter(request: Request):
        await check_rate_limit(
            request,
            namespace="auth",
            max_requests=max_requests,
            window_seconds=window_seconds,
        )

    return limiter
