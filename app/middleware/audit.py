import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from sqlalchemy import text

logger = logging.getLogger("storeflow.audit")

_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _resource_type(path: str, method: str) -> str:
    parts = [p for p in path.strip("/").split("/") if p and p != "api" and p != "v1"]
    if not parts:
        return "unknown"
    if parts[0] in ("health", "ready"):
        return "system"
    return parts[-1] if parts[-1] != "v1" else parts[-2] if len(parts) > 1 else "unknown"


def _action(method: str, status: int) -> str:
    if method == "POST":
        return "create" if status < 300 else "create.fail"
    if method == "PUT":
        return "update" if status < 300 else "update.fail"
    if method == "PATCH":
        return "patch" if status < 300 else "patch.fail"
    if method == "DELETE":
        return "delete" if status < 300 else "delete.fail"
    return "read"


class AuditMiddleware(BaseHTTPMiddleware):
    """Logs request metadata and persists audit events for data mutations."""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        method = request.method
        path = request.url.path

        response: Response = await call_next(request)
        elapsed = time.time() - start

        user_id = getattr(request.state, "user_id", None)
        business_id = getattr(request.state, "business_id", None)

        logger.info(
            "AUDIT: user=%s biz=%s %s %s -> %d (%.3fs)",
            user_id or "anonymous",
            business_id or "none",
            method,
            path,
            response.status_code,
            elapsed,
        )

        if method in _MUTATION_METHODS:
            try:
                ip = request.headers.get(
                    "x-forwarded-for",
                    request.client.host if request.client else "0.0.0.0",
                )
                from app.common.bridge import _get_shared_engine
                from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
                engine = _get_shared_engine()
                factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
                async with factory() as session:
                    async with session.begin():
                        await session.execute(
                            text("""
                                INSERT INTO audit_events
                                    (user_id, business_id, action, resource_type,
                                     method, path, status_code, ip_address, user_agent)
                                VALUES
                                    (:user_id, :business_id, :action, :resource_type,
                                     :method, :path, :status_code, :ip_address, :user_agent)
                            """),
                            {
                                "user_id": user_id,
                                "business_id": business_id,
                                "action": _action(method, response.status_code),
                                "resource_type": _resource_type(path, method),
                                "method": method,
                                "path": path,
                                "status_code": response.status_code,
                                "ip_address": ip.split(",")[0].strip(),
                                "user_agent": request.headers.get("user-agent", ""),
                            },
                        )
            except Exception:
                logger.exception("Failed to write audit event for %s %s", method, path)

        return response
