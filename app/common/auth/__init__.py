"""Shared auth utilities for service packages.

Service packages should use these helpers when they need to validate tokens,
extract tenant context, or work with JWT claims. The full auth service
implementation lives in `apps/api` since it owns the HTTP layer.
"""

from typing import Any

from jose import JWTError, jwt

from settings import CommonSettings, get_common_settings


class ServiceTokenVerifier:
    """Verifies JWT tokens inside service packages without depending on the API app."""

    def __init__(self, settings: CommonSettings | None = None):
        self._settings = settings or get_common_settings()
        self._access_secret = getattr(
            self._settings, "jwt_access_secret", "access-secret-change-me"
        )
        self._algorithm = getattr(self._settings, "jwt_algorithm", "HS256")

    def decode(self, token: str) -> dict[str, Any]:
        return jwt.decode(token, self._access_secret, algorithms=[self._algorithm])

    def verify(self, token: str) -> dict[str, Any] | None:
        try:
            return self.decode(token)
        except JWTError:
            return None
