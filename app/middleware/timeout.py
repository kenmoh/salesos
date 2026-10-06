import asyncio

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Responses that stay open until the client disconnects. Wrapping one in
# asyncio.wait_for would sever it at the timeout, and the client would reconnect
# on a loop forever, so these paths opt out of the cap entirely.
STREAM_PATHS = ("/api/v1/events/stream", "/events/stream")


class TimeoutMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, timeout_seconds: int = 30):
        super().__init__(app)
        self.timeout_seconds = timeout_seconds

    async def dispatch(self, request, call_next):
        if request.url.path in STREAM_PATHS:
            return await call_next(request)
        try:
            return await asyncio.wait_for(call_next(request), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT, content={"detail": "Request timed out"}
            )