from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings

_ALLOWED_ORIGINS = frozenset(origin.lower().rstrip("/") for origin in settings.allowed_origins)

_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class CSRFTokenMiddleware(BaseHTTPMiddleware):
    """CSRF protection for Bearer-token API.

    Classical CSRF relies on browser cookie attachment. Since this API
    uses Bearer tokens (Authorization header), automatic CSRF via cookies
    is not possible. An attacker's site cannot read the victim's Bearer
    token due to same-origin policy.

    However, anonymous endpoints (register, login, forgot-password) have
    no token to validate. For these, we enforce Origin/Referer checking
    as defense-in-depth.

    Authenticated requests are inherently protected — the Bearer token
    acts as an implicit anti-CSRF token (the attacker's page cannot
    programmatically set the Authorization header for a cross-origin
    request without the victim's token).
    """

    async def dispatch(self, request, call_next):
        if request.method in _MUTATION_METHODS:
            user_id = getattr(request.state, "user_id", None)
            if user_id is None:
                origin = request.headers.get("origin", "")
                referer = request.headers.get("referer", "")

                allowed = _is_allowed(origin) or _is_allowed(referer)
                if not allowed and (origin or referer):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF check failed: unknown origin"},
                    )

        return await call_next(request)


def _is_allowed(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = f"{parsed.scheme}://{parsed.netloc}".lower()
    return host in _ALLOWED_ORIGINS or not host
