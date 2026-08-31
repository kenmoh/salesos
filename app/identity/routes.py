from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, ok
from app.auth.schemas.responses import (
    AuditEvent,
    IPBanResult,
    RateLimitInfo,
    RateLimitResetResult,
    SuccessResponse,
)


class BanCreate(BaseModel):
    ip: str
    reason: str = ""


router = APIRouter(prefix="/security", tags=["Admin Security"])


@router.get(
    "/audit-stream",
    response_model=DataResponse[list[AuditEvent]],
    dependencies=[Depends(require_permission("admin:security"))],
)
async def audit_stream(ctx: TenantDep, limit: int = 50, offset: int = 0):
    from sqlalchemy import text

    result = await ctx.session.execute(
        text("""
            SELECT id AS event_id, action AS event_type, user_id AS actor_id, ip_address AS actor_ip, path, method, status_code, created_at
            FROM audit_events
            WHERE business_id = :tenant_id
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"tenant_id": ctx.user.business_id, "limit": limit, "offset": offset},
    )
    rows = result.mappings().all()
    events = [dict(r) for r in rows]
    for e in events:
        if e.get("created_at"):
            e["created_at"] = e["created_at"].isoformat()
    return ok(events)


@router.get(
    "/bans",
    response_model=DataResponse[list[IPBanResult]],
    dependencies=[Depends(require_permission("admin:security"))],
)
async def bans(ctx: TenantDep):
    from sqlalchemy import text

    result = await ctx.session.execute(
        text("""
            SELECT ip, reason, banned_at, expires_at, banned_by
            FROM ip_bans
            WHERE (expires_at IS NULL OR expires_at > now())
            ORDER BY banned_at DESC
        """)
    )
    rows = result.mappings().all()
    bans_list = [dict(r) for r in rows]
    for b in bans_list:
        if b.get("banned_at"):
            b["banned_at"] = b["banned_at"].isoformat()
        if b.get("expires_at"):
            b["expires_at"] = b["expires_at"].isoformat()
    return ok(bans_list)


@router.post(
    "/bans",
    status_code=201,
    response_model=DataResponse[IPBanResult],
    dependencies=[Depends(require_permission("admin:security"))],
)
async def create_ban(payload: BanCreate, ctx: TenantDep):
    from sqlalchemy import text

    await ctx.session.execute(
        text("""
            INSERT INTO ip_bans (ip, tenant_id, banned_by, banned_at)
            VALUES (:ip, :tenant_id, :banned_by, now())
            ON CONFLICT (ip, tenant_id) DO NOTHING
        """),
        {"ip": payload.ip, "tenant_id": ctx.user.business_id, "banned_by": ctx.user.user_id},
    )
    await ctx.session.commit()
    return ok({"ip": payload.ip, "banned": True})


@router.delete(
    "/bans/{ip}",
    response_model=DataResponse[SuccessResponse],
    dependencies=[Depends(require_permission("admin:security"))],
)
async def delete_ban(ip: str, ctx: TenantDep):
    from sqlalchemy import text

    await ctx.session.execute(
        text("DELETE FROM ip_bans WHERE ip = :ip AND tenant_id = :tenant_id"),
        {"ip": ip, "tenant_id": ctx.user.business_id},
    )
    await ctx.session.commit()
    return ok({"success": True, "ip": ip})


@router.get(
    "/bans/{ip}",
    response_model=DataResponse[IPBanResult],
    dependencies=[Depends(require_permission("admin:security"))],
)
async def get_ban(ip: str, ctx: TenantDep):
    from sqlalchemy import text

    result = await ctx.session.execute(
        text("""
            SELECT ip, reason, banned_at, expires_at
            FROM ip_bans
            WHERE ip = :ip AND tenant_id = :tenant_id
            AND (expires_at IS NULL OR expires_at > now())
        """),
        {"ip": ip, "tenant_id": ctx.user.business_id},
    )
    row = result.mappings().first()
    if row:
        data = dict(row)
        if data.get("banned_at"):
            data["banned_at"] = data["banned_at"].isoformat()
        if data.get("expires_at"):
            data["expires_at"] = data["expires_at"].isoformat()
        return ok(data)
    return ok({"ip": ip, "banned": False})


@router.get(
    "/rate-limits/{ip}",
    response_model=DataResponse[RateLimitInfo],
    dependencies=[Depends(require_permission("admin:security"))],
)
async def rate_limits(ip: str, ctx: TenantDep):
    from app.core.redis_client import get_cache_redis

    client = await get_cache_redis()
    if client is None:
        return ok({"ip": ip, "windows": []})
    try:
        keys = []
        async for key in client.scan_iter(f"*{ip}*"):
            ttl = await client.ttl(key)
            count = await client.get(key)
            keys.append({"key": key, "count": int(count or 0), "ttl": ttl})
        return ok({"ip": ip, "windows": keys})
    except Exception:
        return ok({"ip": ip, "windows": []})


@router.post(
    "/rate-limits/{ip}/reset",
    response_model=DataResponse[RateLimitResetResult],
    dependencies=[Depends(require_permission("admin:security"))],
)
async def reset_rate_limits(ip: str, ctx: TenantDep):
    from app.core.redis_client import get_cache_redis

    client = await get_cache_redis()
    if client is None:
        return ok({"success": True, "ip": ip, "reset": 0})
    try:
        count = 0
        async for key in client.scan_iter(f"*{ip}*"):
            await client.delete(key)
            count += 1
        return ok({"success": True, "ip": ip, "reset": count})
    except Exception:
        return ok({"success": True, "ip": ip, "reset": 0})
