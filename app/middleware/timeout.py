import asyncio

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class TimeoutMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, timeout_seconds: int = 30):
        super().__init__(app)
        self.timeout_seconds = timeout_seconds

    async def dispatch(self, request, call_next):
        try:
            return await asyncio.wait_for(call_next(request), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT, content={"detail": "Request timed out"}
            )
