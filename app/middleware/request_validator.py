import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("storeflow.request_validator")

# Content types that are allowed for POST/PUT/PATCH requests
ALLOWED_CONTENT_TYPES = {
    "application/json",
    "multipart/form-data",
    "application/x-www-form-urlencoded",
    "text/plain",
}

# Maximum request body size in bytes (10 MB)
MAX_BODY_SIZE = 10 * 1024 * 1024


class RequestValidatorMiddleware(BaseHTTPMiddleware):
    """Validates incoming request structure before it reaches route handlers.

    Checks content type, body size, and JSON parseability. Rejects malformed
    requests early with a 400 response.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            content_type = request.headers.get("content-type", "").split(";")[0].strip()

            if content_type and content_type not in ALLOWED_CONTENT_TYPES:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=415,
                    content={"detail": f"Unsupported media type: {content_type}"},
                )

            if content_type == "application/json":
                try:
                    body = await request.body()
                    if len(body) > MAX_BODY_SIZE:
                        from fastapi.responses import JSONResponse

                        return JSONResponse(
                            status_code=413,
                            content={"detail": "Request body too large"},
                        )
                    if body:
                        json.loads(body)
                except json.JSONDecodeError:
                    from fastapi.responses import JSONResponse

                    return JSONResponse(
                        status_code=400,
                        content={"detail": "Invalid JSON in request body"},
                    )

        return await call_next(request)
