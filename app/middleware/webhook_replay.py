import hashlib
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.redis_client import cache_get, cache_set

logger = logging.getLogger("storeflow.webhook_replay")

# Deduplication window in seconds
DEDUP_WINDOW = 300


class WebhookReplayMiddleware(BaseHTTPMiddleware):
    """Prevents duplicate processing of idempotent webhook deliveries.

    Webhook providers (e.g., Flutterwave) may deliver the same event multiple times.
    This middleware deduplicates based on a content hash + webhook ID header.
    Duplicates within the dedup window are silently acknowledged (200) without
    reaching the route handler.
    """

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.endswith("/webhook/flutterwave"):
            return await call_next(request)

        body = await request.body()
        body_hash = hashlib.sha256(body).hexdigest()

        event_id = request.headers.get("x-flutterwave-event-id", "") or body_hash
        dedup_key = f"sf:webhook:dedup:{event_id}"

        try:
            existing = await cache_get(dedup_key)
            if existing:
                logger.info("Duplicate webhook %s, acknowledging silently", event_id)
                from fastapi.responses import JSONResponse

                return JSONResponse(content={"received": True, "duplicate": True})

            await cache_set(dedup_key, body_hash, DEDUP_WINDOW)
        except Exception:
            pass

        response = await call_next(request)
        return response
