"""Role guards for the platform admin surface.

Every platform route is authenticated, but not every authenticated admin should
be able to suspend a tenant or change a fee rule. These guards sit inside the
handlers so read endpoints stay open to support staff while writes require a
higher role.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.platform.auth import PLATFORM_ROLE_RANK


def platform_role_rank(role: str | None) -> int:
    return PLATFORM_ROLE_RANK.get(role or "", 0)


def assert_platform_role(ctx, minimum: str = "admin") -> None:
    """Raise 403 unless the signed-in admin meets ``minimum``."""
    current = platform_role_rank(ctx.user.role)
    required = PLATFORM_ROLE_RANK.get(minimum, 0)
    if current < required:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{minimum.replace('_', ' ').title()} access required",
        )
