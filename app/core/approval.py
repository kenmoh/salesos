"""Supervisor approval gate for high-impact corrections.

Creating a document is ordinary work; rewriting where a document was booked is
not. Re-attributing a receivable or payable to another store moves the cash,
receivable and payable figures behind it, so it gets a second pair of eyes.

Owners (and higher) may correct alone. Anyone else — a manager or cashier who
holds the permission guarding the action — must supply a supervisor PIN
belonging to an active, unexpired supervisor in the same tenant who holds that
same permission. Repeated bad attempts are throttled: a 4-6 digit PIN is cheap
to brute force without a counter.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, Request
from sqlalchemy import select

from app.core.security import verify_pin

PIN_MAX_ATTEMPTS = 5
PIN_LOCKOUT_SECONDS = 60


def _rate_key(request: Request, user_id: str) -> str:
    ip = request.client.host if request.client else "0.0.0.0"
    return f"sf:pin_attempts:{ip}:{user_id}"


async def _locked_out(request: Request, user_id: str) -> bool:
    from app.core.redis_client import get_cache_redis

    client = await get_cache_redis()
    if not client:
        return False
    try:
        count = await client.get(_rate_key(request, user_id))
        return bool(count and int(count) >= PIN_MAX_ATTEMPTS)
    except Exception:
        return False


async def _note_failure(request: Request, user_id: str) -> None:
    from app.core.redis_client import get_cache_redis

    client = await get_cache_redis()
    if not client:
        return
    key = _rate_key(request, user_id)
    try:
        pipe = client.pipeline()
        await pipe.incr(key)
        await pipe.expire(key, PIN_LOCKOUT_SECONDS)
        await pipe.execute()
    except Exception:
        pass


async def _clear_failures(request: Request, user_id: str) -> None:
    from app.core.redis_client import get_cache_redis

    client = await get_cache_redis()
    if not client:
        return
    try:
        await client.delete(_rate_key(request, user_id))
    except Exception:
        pass


async def _find_approver(session, tenant_id: str, supervisor_pin: str, permission: str):
    """Return the user id of a supervisor in ``tenant_id`` matching the PIN.

    The PIN is checked against every active, unexpired PIN in the tenant, then
    the match is confirmed to hold ``permission``. Matching by permission rather
    than by role name keeps this working for custom roles: approving a store
    correction requires the same right the correction needs.
    """
    from app.identity.models import (
        Permission,
        Role,
        RolePermission,
        SupervisorPin,
        User,
        UserRole,
    )

    now = datetime.now(UTC)
    pins = (
        (
            await session.execute(
                select(SupervisorPin)
                .join(User, User.id == SupervisorPin.user_id)
                .where(
                    SupervisorPin.expires_at > now,
                    User.tenant_id == tenant_id,
                    User.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )

    for record in pins:
        if not verify_pin(supervisor_pin, record.pin_hash):
            continue
        permitted = (
            await session.execute(
                select(Permission.id)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .join(Role, Role.id == RolePermission.role_id)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == record.user_id, Permission.name == permission)
                .limit(1)
            )
        ).scalar_one_or_none()
        if permitted:
            return record.user_id
    return None


async def require_supervisor_approval(
    ctx,
    *,
    permission: str,
    supervisor_pin: str | None,
    request: Request,
) -> str | None:
    """Return the approving supervisor's id, or None when none was needed.

    The actor is trusted only if they are an owner or higher — holding
    ``permission`` is not enough, because the endpoint already demands it and
    anyone passing the route check is by definition not an owner.

    Raises 403 when the actor may not correct alone and the PIN is missing,
    wrong, expired, or belongs to a supervisor without ``permission``; 429 once
    too many attempts have failed.
    """
    if await ctx.user.min_role(session=ctx.session, role="owner"):
        return None

    if not supervisor_pin:
        raise HTTPException(status_code=403, detail="supervisor_pin_required")

    if await _locked_out(request, ctx.user.user_id):
        raise HTTPException(
            status_code=429,
            detail=f"Too many PIN attempts. Try again in {PIN_LOCKOUT_SECONDS} seconds.",
        )

    approver = await _find_approver(ctx.session, ctx.user.business_id, supervisor_pin, permission)
    if approver is None:
        await _note_failure(request, ctx.user.user_id)
        raise HTTPException(status_code=403, detail="invalid_supervisor_pin")

    await _clear_failures(request, ctx.user.user_id)
    return str(approver)