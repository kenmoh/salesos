import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import Date, and_, case, cast, desc, func, select, text
from sqlalchemy.orm import aliased

from app.core.platform_deps import PlatformDep, require_platform_permission
from app.core.ratelimit import auth_rate_limit
from app.core.responses import ok, paginated
from app.platform.access import assert_platform_role
from app.platform.models import FeeType, PlatformAdmin, PlatformSetting


def require_super_admin(ctx: PlatformDep):
    if ctx.user.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin required")


def _tenant_currency(tenant) -> str:
    """Read a tenant's currency from its settings JSON (defaults to NGN)."""
    from app.common.currency import DEFAULT_CURRENCY, normalize_currency

    try:
        settings = json.loads(tenant.settings) if isinstance(tenant.settings, str) else (tenant.settings or {})
    except (ValueError, TypeError):
        settings = {}
    return normalize_currency(settings.get("currency")) or DEFAULT_CURRENCY
from app.platform.auth import (
    PlatformAuthError,
    PlatformTotpRequired,
    platform_login,
    platform_refresh,
)

router = APIRouter(prefix="/platform", tags=["Platform"])

PLATFORM_ROLES = ("super_admin", "admin", "support", "readonly")


class PlatformLoginRequest(BaseModel):
    email: str
    password: str
    totp_code: str | None = None


class PlatformRefreshRequest(BaseModel):
    refresh_token: str


class PlatformTotpVerifyRequest(BaseModel):
    code: str


class PlatformTotpDisableRequest(BaseModel):
    password: str
    code: str


class CreateAdminRequest(BaseModel):
    email: str
    full_name: str
    password: str
    role: str = "admin"


class AdminUpdateRequest(BaseModel):
    status: str | None = None
    role: str | None = None
    full_name: str | None = None


class AdminPasswordResetRequest(BaseModel):
    new_password: str


class DeleteTenantRequest(BaseModel):
    """Hard delete confirmation: must match the tenant's business_name."""
    business_name: str


class ContactStatusRequest(BaseModel):
    status: str


class WaitlistStatusRequest(BaseModel):
    status: str


class TenantUpdateRequest(BaseModel):
    status: str | None = None
    # Subscriptions are gone — tier changes parked; PATCH now ignores tier.
    # tier: str | None = None
    business_name: str | None = None
    # Why the status changed; stored in the tenant status history.
    reason: str | None = None


# ── Auth ──────────────────────────────────────────────────────────


@router.post("/auth/login", dependencies=[Depends(auth_rate_limit(5, 300))])
async def login(payload: PlatformLoginRequest, request: Request):
    from app.common.bridge import _get_shared_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    engine = _get_shared_engine()
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            try:
                result = await platform_login(
                    session=session,
                    email=payload.email,
                    password=payload.password,
                    req=request,
                    totp_code=payload.totp_code,
                )
                return ok(result)
            except PlatformTotpRequired:
                return ok(
                    {
                        "tokens": {"access_token": "", "refresh_token": ""},
                        "admin": {},
                        "requires_totp": True,
                    }
                )
            except PlatformAuthError as e:
                raise HTTPException(status_code=401, detail=str(e))


@router.post("/auth/refresh")
async def refresh(payload: PlatformRefreshRequest):
    from app.common.bridge import _get_shared_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    engine = _get_shared_engine()
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            try:
                result = await platform_refresh(session=session, raw_token=payload.refresh_token)
                return ok(result)
            except PlatformAuthError as e:
                raise HTTPException(status_code=401, detail=str(e))


@router.get("/auth/me", dependencies=[Depends(require_platform_permission("platform:*"))])
async def me(ctx: PlatformDep):
    from app.common.bridge import _get_shared_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    engine = _get_shared_engine()
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                select(PlatformAdmin).where(PlatformAdmin.id == ctx.user.admin_id)
            )
            admin = result.scalar_one_or_none()
            if not admin:
                raise HTTPException(status_code=404, detail="Admin not found")
            return ok(
                {
                    "id": str(admin.id),
                    "email": admin.email,
                    "full_name": admin.full_name,
                    "role": admin.role,
                    "status": admin.status,
                    "totp_enabled": bool(admin.totp_enabled),
                    "last_login_at": admin.last_login_at.isoformat()
                    if admin.last_login_at
                    else None,
                    "last_device_type": admin.last_device_type,
                    "last_browser": admin.last_browser,
                    "last_seen_at": admin.last_seen_at.isoformat()
                    if admin.last_seen_at
                    else None,
                    "last_ip_address": admin.last_ip_address,
                }
            )


@router.post(
    "/auth/totp/setup", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def totp_setup(ctx: PlatformDep):
    from app.platform.auth import platform_totp_setup

    return ok(await platform_totp_setup(session=ctx.session, admin_id=ctx.user.admin_id))


@router.post(
    "/auth/totp/verify", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def totp_verify(payload: PlatformTotpVerifyRequest, ctx: PlatformDep):
    from app.platform.auth import platform_totp_enable

    try:
        await platform_totp_enable(session=ctx.session, admin_id=ctx.user.admin_id, code=payload.code)
    except PlatformAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ok(None, message="TOTP enabled")


@router.post(
    "/auth/totp/disable", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def totp_disable(payload: PlatformTotpDisableRequest, ctx: PlatformDep):
    from app.platform.auth import platform_totp_disable

    try:
        await platform_totp_disable(
            session=ctx.session,
            admin_id=ctx.user.admin_id,
            password=payload.password,
            code=payload.code,
        )
    except PlatformAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ok(None, message="TOTP disabled")


# ── Platform admins ───────────────────────────────────────────────


@router.get("/admins", dependencies=[Depends(require_platform_permission("platform:*"))])
async def list_admins(
    ctx: PlatformDep,
    search: str | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    query = select(PlatformAdmin)
    count_query = select(func.count(PlatformAdmin.id))
    if status_filter:
        query = query.where(PlatformAdmin.status == status_filter)
        count_query = count_query.where(PlatformAdmin.status == status_filter)
    if search:
        query = query.where(
            PlatformAdmin.email.ilike(f"%{search}%") | PlatformAdmin.full_name.ilike(f"%{search}%")
        )
        count_query = count_query.where(
            PlatformAdmin.email.ilike(f"%{search}%") | PlatformAdmin.full_name.ilike(f"%{search}%")
        )

    total = (await ctx.session.execute(count_query)).scalar()
    rows = (
        await ctx.session.execute(
            query.order_by(PlatformAdmin.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return ok(
        {
            "items": [
                {
                    "id": str(a.id),
                    "email": a.email,
                    "full_name": a.full_name,
                    "role": a.role,
                    "status": a.status,
                    "totp_enabled": bool(a.totp_enabled),
                    "last_login_at": a.last_login_at.isoformat() if a.last_login_at else None,
                    "last_device_type": a.last_device_type,
                    "last_browser": a.last_browser,
                    "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
                    "last_ip_address": a.last_ip_address,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.post("/admins", status_code=201, dependencies=[Depends(require_platform_permission("platform:*"))])
async def create_admin(payload: CreateAdminRequest, ctx: PlatformDep):
    from app.core.security import hash_password
    from app.platform.audit import log_platform_audit

    assert_platform_role(ctx, "admin")

    if payload.role not in PLATFORM_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    exists = await ctx.session.scalar(
        select(PlatformAdmin.id).where(PlatformAdmin.email == payload.email)
    )
    if exists:
        raise HTTPException(status_code=409, detail="An admin with this email already exists")

    admin = PlatformAdmin(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    ctx.session.add(admin)
    await ctx.session.flush()

    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="admin.create",
        resource="platform_admin",
        resource_id=str(admin.id),
        details={"email": payload.email, "role": payload.role},
    )
    return ok(
        {
            "id": str(admin.id),
            "email": admin.email,
            "full_name": admin.full_name,
            "role": admin.role,
            "status": admin.status,
        }
    )


@router.patch(
    "/admins/{admin_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def update_admin(admin_id: str, payload: AdminUpdateRequest, ctx: PlatformDep):
    from app.platform.audit import log_platform_audit

    assert_platform_role(ctx, "admin")

    admin_row = (
        await ctx.session.execute(select(PlatformAdmin).where(PlatformAdmin.id == admin_id))
    ).scalar_one_or_none()
    if not admin_row:
        raise HTTPException(status_code=404, detail="Admin not found")

    changes: dict = {}

    if payload.role is not None and payload.role != admin_row.role:
        assert_platform_role(ctx, "super_admin")
        if payload.role not in PLATFORM_ROLES:
            raise HTTPException(status_code=400, detail="Invalid role")
        if admin_row.role == "super_admin" and payload.role != "super_admin":
            active_supers = (
                await ctx.session.execute(
                    select(func.count(PlatformAdmin.id)).where(
                        PlatformAdmin.role == "super_admin",
                        PlatformAdmin.status == "active",
                    )
                )
            ).scalar() or 0
            if active_supers <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot demote the last active super admin",
                )
        changes["role"] = {"from": admin_row.role, "to": payload.role}
        admin_row.role = payload.role

    if payload.status is not None and payload.status != admin_row.status:
        if payload.status not in ("active", "disabled"):
            raise HTTPException(status_code=400, detail="Invalid status")
        if admin_row.id == ctx.user.admin_id and payload.status != "active":
            raise HTTPException(status_code=400, detail="You cannot disable your own account")
        if admin_row.role == "super_admin" and payload.status != "active":
            active_supers = (
                await ctx.session.execute(
                    select(func.count(PlatformAdmin.id)).where(
                        PlatformAdmin.role == "super_admin",
                        PlatformAdmin.status == "active",
                    )
                )
            ).scalar() or 0
            if active_supers <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot disable the last active super admin",
                )
        changes["status"] = {"from": admin_row.status, "to": payload.status}
        admin_row.status = payload.status
        if payload.status == "disabled":
            from app.core.security import revoke_all_sessions

            await revoke_all_sessions(str(admin_row.id))

    if payload.full_name is not None and payload.full_name != admin_row.full_name:
        changes["full_name"] = {"from": admin_row.full_name, "to": payload.full_name}
        admin_row.full_name = payload.full_name

    await ctx.session.flush()

    if changes:
        await log_platform_audit(
            ctx.session,
            admin_id=ctx.user.admin_id,
            action="admin.update",
            resource="platform_admin",
            resource_id=str(admin_row.id),
            details={"email": admin_row.email, **changes},
        )

    return ok(
        {
            "id": str(admin_row.id),
            "email": admin_row.email,
            "full_name": admin_row.full_name,
            "role": admin_row.role,
            "status": admin_row.status,
        }
    )


@router.get(
    "/admins/{admin_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def get_admin(admin_id: str, ctx: PlatformDep):
    """One admin with their recent actions and live sessions."""
    from app.core.security import list_user_sessions
    from app.platform.models import PlatformAuditLog

    admin_row = (
        await ctx.session.execute(select(PlatformAdmin).where(PlatformAdmin.id == admin_id))
    ).scalar_one_or_none()
    if not admin_row:
        raise HTTPException(status_code=404, detail="Admin not found")

    actions = (
        await ctx.session.execute(
            select(PlatformAuditLog)
            .where(PlatformAuditLog.admin_id == admin_row.id)
            .order_by(desc(PlatformAuditLog.created_at))
            .limit(20)
        )
    ).scalars().all()
    sessions = await list_user_sessions(str(admin_row.id))

    return ok(
        {
            "id": str(admin_row.id),
            "email": admin_row.email,
            "full_name": admin_row.full_name,
            "role": admin_row.role,
            "status": admin_row.status,
            "totp_enabled": bool(admin_row.totp_enabled),
            "last_login_at": admin_row.last_login_at.isoformat()
            if admin_row.last_login_at
            else None,
            "last_device_type": admin_row.last_device_type,
            "last_browser": admin_row.last_browser,
            "last_seen_at": admin_row.last_seen_at.isoformat()
            if admin_row.last_seen_at
            else None,
            "last_ip_address": admin_row.last_ip_address,
            "created_at": admin_row.created_at.isoformat() if admin_row.created_at else None,
            "recent_actions": [
                {
                    "id": str(a.id),
                    "action": a.action,
                    "resource": a.resource,
                    "resource_id": a.resource_id,
                    "ip_address": a.ip_address,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in actions
            ],
            "sessions": sessions,
        }
    )


@router.post(
    "/admins/{admin_id}/revoke-sessions",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def revoke_admin_sessions(admin_id: str, ctx: PlatformDep):
    from app.core.security import revoke_all_sessions
    from app.platform.audit import log_platform_audit

    assert_platform_role(ctx, "admin")
    admin_row = (
        await ctx.session.execute(select(PlatformAdmin).where(PlatformAdmin.id == admin_id))
    ).scalar_one_or_none()
    if not admin_row:
        raise HTTPException(status_code=404, detail="Admin not found")

    await revoke_all_sessions(str(admin_row.id))
    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="admin.revoke_sessions",
        resource="platform_admin",
        resource_id=str(admin_row.id),
        details={"email": admin_row.email},
    )
    return ok({"admin_id": str(admin_row.id), "revoked": True})


@router.post(
    "/admins/{admin_id}/reset-password",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def reset_admin_password(
    admin_id: str, payload: AdminPasswordResetRequest, ctx: PlatformDep
):
    from app.core.security import hash_password, revoke_all_sessions, validate_password_strength
    from app.platform.audit import log_platform_audit

    assert_platform_role(ctx, "super_admin")
    admin_row = (
        await ctx.session.execute(select(PlatformAdmin).where(PlatformAdmin.id == admin_id))
    ).scalar_one_or_none()
    if not admin_row:
        raise HTTPException(status_code=404, detail="Admin not found")

    problems = validate_password_strength(payload.new_password)
    if problems:
        raise HTTPException(status_code=400, detail="; ".join(problems))

    admin_row.password_hash = hash_password(payload.new_password)
    await revoke_all_sessions(str(admin_row.id))
    await ctx.session.flush()
    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="admin.reset_password",
        resource="platform_admin",
        resource_id=str(admin_row.id),
        details={"email": admin_row.email},
    )
    return ok({"admin_id": str(admin_row.id), "sessions_revoked": True})


# ── Tenants ───────────────────────────────────────────────────────


class CreateTenantRequest(BaseModel):
    business_name: str
    business_email: str
    owner_name: str
    owner_email: str
    owner_phone: str | None = None
    password: str
    tier: str = "starter"


@router.post(
    "/tenants", status_code=201, dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def create_tenant(payload: CreateTenantRequest, ctx: PlatformDep):
    from app.common.bridge import create_tenant as bridge_create_tenant
    from app.core.security import hash_password

    assert_platform_role(ctx, "admin")

    result = await bridge_create_tenant(
        business_name=payload.business_name,
        business_email=payload.business_email,
        owner_name=payload.owner_name,
        owner_email=payload.owner_email,
        owner_phone=payload.owner_phone,
        owner_password_hash=hash_password(payload.password),
        actor_id=ctx.user.admin_id,
    )

    from app.platform.audit import log_platform_audit

    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="tenant.create",
        resource="tenant",
        resource_id=result.get("id"),
        details={"business_name": payload.business_name, "tier": payload.tier},
    )
    return ok(result)


@router.get("/tenants", dependencies=[Depends(require_platform_permission("platform:*"))])
async def list_tenants(
    ctx: PlatformDep,
    status_filter: str | None = None,
    tier: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    from app.identity.models import User
    from app.tenancy.models import Tenant

    # The owner row carries the device the business signed up and signs in
    # from; users.email is unique, so this join never fans out.
    owner = aliased(User)
    query = select(
        Tenant,
        owner.last_device_type,
        owner.last_browser,
        owner.last_seen_at,
    ).outerjoin(owner, owner.email == Tenant.owner_email)
    count_query = select(func.count(Tenant.id))

    if status_filter:
        query = query.where(Tenant.status == status_filter)
        count_query = count_query.where(Tenant.status == status_filter)
    # if tier:
    #     query = query.where(Tenant.tier == tier)
    #     count_query = count_query.where(Tenant.tier == tier)
    if search:
        query = query.where(
            Tenant.business_name.ilike(f"%{search}%")
            | Tenant.slug.ilike(f"%{search}%")
            | Tenant.owner_email.ilike(f"%{search}%")
        )
        count_query = count_query.where(
            Tenant.business_name.ilike(f"%{search}%")
            | Tenant.slug.ilike(f"%{search}%")
            | Tenant.owner_email.ilike(f"%{search}%")
        )

    total_result = await ctx.session.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(Tenant.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await ctx.session.execute(query)
    rows = result.all()

    return ok(
        {
            "items": [
                {
                    "id": str(t.id),
                    "slug": t.slug,
                    "subdomain": t.subdomain,
                    "business_name": t.tier and t.business_name or t.business_name,
                    "business_email": t.business_email,
                    "owner_name": t.owner_name,
                    "owner_email": t.owner_email,
                    "tier": t.tier,
                    "status": t.status,
                    "owner_device_type": device_type,
                    "owner_browser": browser,
                    "owner_last_seen_at": last_seen.isoformat() if last_seen else None,
                    "currency": _tenant_currency(t),
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t, device_type, browser, last_seen in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get("/tenants/stats", dependencies=[Depends(require_platform_permission("platform:*"))])
async def tenant_stats(ctx: PlatformDep):
    from app.tenancy.models import Tenant

    total = await ctx.session.execute(select(func.count(Tenant.id)))
    active = await ctx.session.execute(
        select(func.count(Tenant.id)).where(Tenant.status == "active")
    )
    suspended = await ctx.session.execute(
        select(func.count(Tenant.id)).where(Tenant.status == "suspended")
    )
    # Tiers parked (no subscriptions) — by_tier removed from stats.
    # tiers = await ctx.session.execute(
    #     select(Tenant.tier, func.count(Tenant.id)).group_by(Tenant.tier)
    # )

    return ok(
        {
            "total": total.scalar(),
            "active": active.scalar(),
            "suspended": suspended.scalar(),
        }
    )


@router.get(
    "/tenants/{tenant_id}", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def get_tenant(tenant_id: str, ctx: PlatformDep):
    """Full tenant profile: identity, settlement, owner activity, billing,
    fee debt and entity counts, in one call for the admin detail page."""
    from datetime import datetime, timezone

    from app.billing.models import (
        BillingInvoice,
        MerchantSubscription,
        SubscriptionPlan,
        TenantWallet,
    )
    from app.catalog.models import Product
    from app.customers.models import Customer
    from app.documents.models import Document
    from app.identity.models import User
    from app.platform.models import (
        PlatformDataRequest,
        PlatformFeeLedger,
        PlatformTenantNote,
        PlatformTenantTag,
    )
    from app.sales.models import Sale
    from app.stores.models import Store
    from app.tenancy.models import Tenant

    tenant = (
        await ctx.session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    now = datetime.now(timezone.utc)

    def _count(model, *where):
        return (
            select(func.count(model.id))
            .where(model.tenant_id == Tenant.id, *where)
            .correlate(Tenant)
            .scalar_subquery()
        )

    stats = (
        await ctx.session.execute(
            select(
                _count(User).label("users"),
                _count(Store).label("stores"),
                _count(Product).label("products"),
                _count(Customer).label("customers"),
                _count(Sale).label("sales"),
                _count(Document).label("documents"),
                _count(PlatformTenantNote).label("notes"),
                _count(
                    PlatformDataRequest,
                    PlatformDataRequest.status.in_(("pending", "in_progress")),
                ).label("open_data_requests"),
                select(func.max(Sale.created_at))
                .where(Sale.tenant_id == Tenant.id, Sale.status != "voided")
                .correlate(Tenant)
                .scalar_subquery()
                .label("last_sale_at"),
                select(func.max(User.last_login_at))
                .where(User.tenant_id == Tenant.id)
                .correlate(Tenant)
                .scalar_subquery()
                .label("last_login_at"),
            ).where(Tenant.id == tenant.id)
        )
    ).one()

    fee_stats = (
        await ctx.session.execute(
            select(
                func.coalesce(
                    func.sum(case((PlatformFeeLedger.status == "pending", PlatformFeeLedger.amount), else_=0)),
                    0,
                ).label("pending"),
                func.coalesce(
                    func.sum(case((PlatformFeeLedger.status == "deducted", PlatformFeeLedger.amount), else_=0)),
                    0,
                ).label("settled"),
                func.count(PlatformFeeLedger.id)
                .filter(PlatformFeeLedger.status == "pending")
                .label("pending_count"),
                func.max(PlatformFeeLedger.created_at).label("last_fee_at"),
            ).where(PlatformFeeLedger.tenant_id == tenant.id)
        )
    ).one()

    invoice_stats = (
        await ctx.session.execute(
            select(
                func.count(BillingInvoice.id).label("total"),
                func.coalesce(func.sum(BillingInvoice.amount_due), 0).label("outstanding"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    BillingInvoice.due_at < now,
                                    BillingInvoice.status.notin_(("paid", "void", "cancelled")),
                                ),
                                BillingInvoice.amount_due,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("overdue"),
                func.count(BillingInvoice.id)
                .filter(BillingInvoice.status == "paid")
                .label("paid_count"),
            ).where(BillingInvoice.tenant_id == tenant.id)
        )
    ).one()

    owner = (
        await ctx.session.execute(select(User).where(User.email == tenant.owner_email))
    ).scalar_one_or_none()

    sub_row = (
        await ctx.session.execute(
            select(MerchantSubscription, SubscriptionPlan)
            .outerjoin(SubscriptionPlan, SubscriptionPlan.id == MerchantSubscription.plan_id)
            .where(MerchantSubscription.tenant_id == tenant.id)
        )
    ).first()
    wallet = (
        await ctx.session.execute(
            select(TenantWallet).where(TenantWallet.tenant_id == tenant.id)
        )
    ).scalar_one_or_none()
    tags = (
        (
            await ctx.session.execute(
                select(PlatformTenantTag.tag)
                .where(PlatformTenantTag.tenant_id == tenant.id)
                .order_by(PlatformTenantTag.tag)
            )
        )
        .scalars()
        .all()
    )

    def _settings(raw):
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}

    last_sale_at = stats.last_sale_at
    last_login_at = stats.last_login_at
    subscription = None
    plan = None
    if sub_row:
        sub, plan_row = sub_row
        subscription = {
            "id": str(sub.id),
            "status": sub.status,
            "billing_interval": sub.billing_interval,
            "current_period_start": sub.current_period_start.isoformat()
            if sub.current_period_start
            else None,
            "current_period_end": sub.current_period_end.isoformat()
            if sub.current_period_end
            else None,
            "next_billing_date": sub.next_billing_date.isoformat()
            if sub.next_billing_date
            else None,
            "grace_period_end": sub.grace_period_end.isoformat()
            if sub.grace_period_end
            else None,
            "auto_debit_enabled": bool(sub.auto_debit_enabled),
            "accrued_debt": float(sub.accrued_debt or 0),
            "debt_cap": float(sub.debt_cap or 0),
            "pending_plan_id": str(sub.pending_plan_id) if sub.pending_plan_id else None,
            "plan_change_requested_at": sub.plan_change_requested_at.isoformat()
            if sub.plan_change_requested_at
            else None,
        }
        if plan_row:
            plan = {
                "id": str(plan_row.id),
                "name": plan_row.name,
                "slug": plan_row.slug,
                "monthly_price": float(plan_row.monthly_price or 0),
                "currency": plan_row.currency,
                "is_active": bool(plan_row.is_active),
            }

    return ok(
        {
            "id": str(tenant.id),
            "slug": tenant.slug,
            "subdomain": tenant.subdomain,
            "business_name": tenant.business_name,
            "business_description": tenant.business_description,
            "business_email": tenant.business_email,
            "owner_name": tenant.owner_name,
            "owner_email": tenant.owner_email,
            "owner_phone": tenant.owner_phone,
            "tier": tenant.tier,
            "status": tenant.status,
            "user_count": int(stats.users or 0),
            "currency": _tenant_currency(tenant),
            "settings": _settings(tenant.settings),
            "settlement": {
                "bank_name": tenant.bank_name,
                "account_name": tenant.account_name,
                "account_number": tenant.account_number,
            },
            "owner_activity": {
                "last_login_at": owner.last_login_at.isoformat()
                if owner and owner.last_login_at
                else None,
                "last_device_type": owner.last_device_type if owner else None,
                "last_browser": owner.last_browser if owner else None,
                "last_seen_at": owner.last_seen_at.isoformat()
                if owner and owner.last_seen_at
                else None,
                "last_ip_address": owner.last_ip_address if owner else None,
            },
            "counts": {
                "users": int(stats.users or 0),
                "stores": int(stats.stores or 0),
                "products": int(stats.products or 0),
                "customers": int(stats.customers or 0),
                "sales": int(stats.sales or 0),
                "documents": int(stats.documents or 0),
                "notes": int(stats.notes or 0),
                "open_data_requests": int(stats.open_data_requests or 0),
            },
            "activity": {
                "last_sale_at": last_sale_at.isoformat() if last_sale_at else None,
                "last_login_at": last_login_at.isoformat() if last_login_at else None,
                "days_since_last_sale": (now - last_sale_at).days if last_sale_at else None,
                "days_since_last_login": (now - last_login_at).days if last_login_at else None,
            },
            "billing": {
                "subscription": subscription,
                "plan": plan,
                "wallet": {
                    "balance": float(wallet.balance or 0),
                    "currency": wallet.currency,
                    "status": wallet.status,
                }
                if wallet
                else None,
                "invoices": {
                    "total": int(invoice_stats.total or 0),
                    "paid": int(invoice_stats.paid_count or 0),
                    "outstanding_amount": float(invoice_stats.outstanding or 0),
                    "overdue_amount": float(invoice_stats.overdue or 0),
                },
            },
            "fees": {
                "pending_total": float(fee_stats.pending or 0),
                "pending_count": int(fee_stats.pending_count or 0),
                "settled_total": float(fee_stats.settled or 0),
                "last_fee_at": fee_stats.last_fee_at.isoformat()
                if fee_stats.last_fee_at
                else None,
            },
            "tags": list(tags),
            "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
            "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
        }
    )


async def _suspend_tenant_users(session, tenant_id: str) -> int:
    """Suspend every member of a business and kill their refresh sessions.

    Existing access tokens still pass one more status check (the tenant dep
    gates on Tenant.status); revoked refresh tokens stop re-entry afterwards.
    """
    from sqlalchemy import update

    from app.core.security import revoke_all_sessions
    from app.identity.models import User

    result = await session.execute(
        update(User)
        .where(User.tenant_id == tenant_id, User.status != "suspended")
        .values(status="suspended")
    )
    member_ids = (
        (await session.execute(select(User.id).where(User.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    for uid in member_ids:
        await revoke_all_sessions(str(uid))
    return int(result.rowcount or 0)


async def _reinstate_tenant_users(session, tenant_id: str) -> int:
    from sqlalchemy import update

    from app.identity.models import User

    result = await session.execute(
        update(User)
        .where(User.tenant_id == tenant_id, User.status == "suspended")
        .values(status="active")
    )
    return int(result.rowcount or 0)


@router.patch(
    "/tenants/{tenant_id}", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def update_tenant(tenant_id: str, payload: TenantUpdateRequest, ctx: PlatformDep):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformTenantStatusHistory
    from app.tenancy.models import Tenant

    assert_platform_role(ctx, "admin")

    result = await ctx.session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    changes: dict[str, dict] = {}
    if payload.status is not None and payload.status != tenant.status:
        if payload.status not in ("active", "suspended", "cancelled"):
            raise HTTPException(status_code=400, detail="Invalid status")
        changes["status"] = {"from": tenant.status, "to": payload.status}
        tenant.status = payload.status

        if payload.status in ("suspended", "cancelled"):
            users_affected = await _suspend_tenant_users(ctx.session, tenant_id)
            details_extra = {"users_suspended": users_affected}
        else:
            users_affected = await _reinstate_tenant_users(ctx.session, tenant_id)
            details_extra = {"users_reinstated": users_affected}
    else:
        details_extra = {}
    # Tier changes parked — subscriptions are gone; tier stays only as a
    # stored label. Reinstate together with the tier-distribution endpoint.
    # if payload.tier is not None and payload.tier != tenant.tier:
    #     if payload.tier not in ("starter", "growth", "enterprise"):
    #         raise HTTPException(status_code=400, detail="Invalid tier")
    #
    #     from app.tenancy.schemas import TIER_LIMITS
    #
    #     limits = TIER_LIMITS.get(payload.tier)
    #     if not limits:
    #         raise HTTPException(status_code=400, detail="Invalid tier")
    #     changes["tier"] = {"from": tenant.tier, "to": payload.tier}
    #     tenant.tier = payload.tier
    #
    #     from app.tenancy.models import TenantTierProjection
    #
    #     proj = await ctx.session.scalar(
    #         select(TenantTierProjection).where(TenantTierProjection.tenant_id == tenant.id)
    #     )
    #     if proj:
    #         proj.tier = payload.tier
    #         proj.max_terminals = limits["max_terminals"]
    #         proj.max_products = limits["max_products"]
    #         proj.max_users = limits["max_users"]
    #     else:
    #         ctx.session.add(
    #             TenantTierProjection(
    #                 tenant_id=tenant.id,
    #                 tier=payload.tier,
    #                 max_terminals=limits["max_terminals"],
    #                 max_products=limits["max_products"],
    #                 max_users=limits["max_users"],
    #             )
    #         )
    if payload.business_name is not None and payload.business_name != tenant.business_name:
        changes["business_name"] = {"to": payload.business_name}
        tenant.business_name = payload.business_name

    await ctx.session.flush()

    if "status" in changes:
        ctx.session.add(
            PlatformTenantStatusHistory(
                tenant_id=tenant.id,
                from_status=changes["status"].get("from"),
                to_status=changes["status"].get("to"),
                reason=payload.reason,
                admin_id=ctx.user.admin_id,
            )
        )
        if payload.reason:
            details_extra["reason"] = payload.reason

    if changes:
        await log_platform_audit(
            ctx.session,
            admin_id=ctx.user.admin_id,
            action="tenant.update",
            resource="tenant",
            resource_id=str(tenant.id),
            details={**changes, **details_extra},
        )

    return ok(
        {
            "id": str(tenant.id),
            "slug": tenant.slug,
            "business_name": tenant.business_name,
            "tier": tenant.tier,
            "status": tenant.status,
        }
    )


@router.delete(
    "/tenants/{tenant_id}", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def delete_tenant(
    tenant_id: str, payload: DeleteTenantRequest, ctx: PlatformDep
):
    from app.common.db.session import set_rls_context
    from app.common.tenant_sweep import sweep_tenant_data
    from app.core.security import revoke_all_sessions
    from app.identity.models import User
    from app.platform.audit import log_platform_audit
    from app.tenancy.models import Tenant

    assert_platform_role(ctx, "admin")

    result = await ctx.session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if payload.business_name.strip().lower() != tenant.business_name.strip().lower():
        raise HTTPException(
            status_code=400,
            detail="Confirmation failed: business_name does not match this tenant",
        )

    # Capture member ids before the sweep erases their rows.
    member_ids = [
        str(uid)
        for uid in (
            await ctx.session.execute(
                select(User.id).where(User.tenant_id == tenant.id)
            )
        )
        .scalars()
        .all()
    ]

    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="tenant.delete",
        resource="tenant",
        resource_id=str(tenant.id),
        details={"business_name": tenant.business_name, "owner_email": tenant.owner_email},
    )

    # The platform session satisfies the forced-RLS tenant policies the same
    # way an owner flow does: business_id GUC pinned to this tenant.
    await set_rls_context(ctx.session, ctx.user.admin_id, str(tenant.id), "owner")
    swept = await sweep_tenant_data(ctx.session, tenant.id)

    # Cache revocation before commit is safe; a failed commit leaves live
    # rows whose users simply establish fresh sessions.
    for uid in member_ids:
        await revoke_all_sessions(uid)

    return {"success": True, "deleted": tenant_id, "swept_tables": swept}


# ── Users (cross-tenant) ─────────────────────────────────────────


@router.get("/users", dependencies=[Depends(require_platform_permission("platform:*"))])
async def list_all_users(
    ctx: PlatformDep,
    tenant_id: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    from app.identity.models import User

    query = select(User)
    count_query = select(func.count(User.id))

    if tenant_id:
        query = query.where(User.tenant_id == tenant_id)
        count_query = count_query.where(User.tenant_id == tenant_id)
    if search:
        query = query.where(User.email.ilike(f"%{search}%") | User.full_name.ilike(f"%{search}%"))
        count_query = count_query.where(
            User.email.ilike(f"%{search}%") | User.full_name.ilike(f"%{search}%")
        )

    total_result = await ctx.session.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(User.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await ctx.session.execute(query)
    users = result.scalars().all()

    return ok(
        {
            "items": [
                {
                    "id": str(u.id),
                    "tenant_id": str(u.tenant_id),
                    "email": u.email,
                    "full_name": u.full_name,
                    "status": u.status,
                    "totp_enabled": bool(u.totp_enabled),
                    "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                    "last_device_type": u.last_device_type,
                    "last_browser": u.last_browser,
                    "last_seen_at": u.last_seen_at.isoformat() if u.last_seen_at else None,
                    "last_ip_address": u.last_ip_address,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
                for u in users
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get("/users/stats", dependencies=[Depends(require_platform_permission("platform:*"))])
async def user_stats(ctx: PlatformDep):
    from app.identity.models import User

    total = await ctx.session.execute(select(func.count(User.id)))
    active = await ctx.session.execute(select(func.count(User.id)).where(User.status == "active"))
    tenants_with_users = await ctx.session.execute(
        select(func.count(func.distinct(User.tenant_id)))
    )

    return ok(
        {
            "total_users": total.scalar(),
            "active_users": active.scalar(),
            "tenants_with_users": tenants_with_users.scalar(),
        }
    )


# ── Tenant drill-down: Employees ──────────────────────────────────


@router.get(
    "/tenants/{tenant_id}/employees",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def tenant_employees(tenant_id: str, ctx: PlatformDep):
    from sqlalchemy import text

    result = await ctx.session.execute(
        text("""
            SELECT u.id, u.email, u.full_name, u.phone, u.status,
                   u.last_login_at, u.created_at,
                   u.last_device_type, u.last_browser, u.last_seen_at,
                   u.last_ip_address,
                   STRING_AGG(r.name, ',') AS roles
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
            WHERE u.tenant_id = :tid
            GROUP BY u.id
            ORDER BY u.created_at DESC
        """),
        {"tid": tenant_id},
    )
    rows = result.mappings().all()
    return ok(
        [
            {
                "id": str(r["id"]),
                "email": r["email"],
                "full_name": r["full_name"],
                "phone": r["phone"],
                "roles": r["roles"] or "",
                "status": r["status"],
                "last_login_at": r["last_login_at"].isoformat() if r["last_login_at"] else None,
                "last_device_type": r["last_device_type"],
                "last_browser": r["last_browser"],
                "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
                "last_ip_address": r["last_ip_address"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    )


# ── Tenant drill-down: Sales ──────────────────────────────────────


@router.get(
    "/tenants/{tenant_id}/sales",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def tenant_sales(
    tenant_id: str,
    ctx: PlatformDep,
    store_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    from app.sales.models import Sale

    count_q = select(func.count(Sale.id)).where(Sale.tenant_id == tenant_id)
    if store_id:
        count_q = count_q.where(Sale.store_id == store_id)
    total = (await ctx.session.execute(count_q)).scalar()

    query = select(Sale).where(Sale.tenant_id == tenant_id)
    if store_id:
        query = query.where(Sale.store_id == store_id)
    query = (
        query.order_by(Sale.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await ctx.session.execute(query)
    sales = result.scalars().all()

    return ok(
        {
            "items": [
                {
                    "id": str(s.id),
                    "sale_number": s.sale_number,
                    "store_id": str(s.store_id) if s.store_id else None,
                    "cashier_id": str(s.cashier_id) if s.cashier_id else None,
                    "total": float(s.total),
                    "discount": float(s.discount) if s.discount else 0,
                    "status": s.status,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in sales
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


# ── Tenant drill-down: Products ───────────────────────────────────


@router.get(
    "/tenants/{tenant_id}/products",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def tenant_products(
    tenant_id: str,
    ctx: PlatformDep,
    page: int = 1,
    page_size: int = 50,
):
    from app.catalog.models import Product

    count_q = select(func.count(Product.id)).where(Product.tenant_id == tenant_id)
    total = (await ctx.session.execute(count_q)).scalar()

    query = (
        select(Product)
        .where(Product.tenant_id == tenant_id)
        .order_by(Product.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await ctx.session.execute(query)
    products = result.scalars().all()

    return ok(
        {
            "items": [
                {
                    "id": str(p.id),
                    "public_id": p.public_id,
                    "name": p.name,
                    "sku": p.sku,
                    "selling_price": float(p.selling_price),
                    "status": p.status,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                }
                for p in products
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


# ── Tenant drill-down: Payments summary ───────────────────────────


@router.get(
    "/tenants/{tenant_id}/payments",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def tenant_payments(
    tenant_id: str,
    ctx: PlatformDep,
    page: int = 1,
    page_size: int = 50,
):
    from app.payments.models import Payment

    count_q = select(func.count(Payment.id)).where(Payment.tenant_id == tenant_id)
    total = (await ctx.session.execute(count_q)).scalar()

    query = (
        select(Payment)
        .where(Payment.tenant_id == tenant_id)
        .order_by(Payment.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await ctx.session.execute(query)
    payments = result.scalars().all()

    return ok(
        {
            "items": [
                {
                    "id": str(p.id),
                    "sale_id": str(p.sale_id) if p.sale_id else None,
                    "method": p.method,
                    "amount": float(p.amount),
                    "status": p.status,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                }
                for p in payments
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


# ── Tenant drill-down: Documents ──────────────────────────────────


@router.get(
    "/tenants/{tenant_id}/documents",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def tenant_documents(
    tenant_id: str,
    ctx: PlatformDep,
    doc_type: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    from app.documents.models import Document

    q = select(Document).where(Document.tenant_id == tenant_id)
    count_q = select(func.count(Document.id)).where(Document.tenant_id == tenant_id)

    if doc_type:
        q = q.where(Document.doc_type == doc_type)
        count_q = count_q.where(Document.doc_type == doc_type)

    total = (await ctx.session.execute(count_q)).scalar()

    query = q.order_by(Document.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await ctx.session.execute(query)
    docs = result.scalars().all()

    return ok(
        {
            "items": [
                {
                    "id": str(d.id),
                    "doc_number": d.doc_number,
                    "doc_type": d.doc_type,
                    "status": d.status,
                    "customer_name": d.customer_name,
                    "total": float(d.total),
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in docs
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


# ── Tenant drill-down: Overview summary ───────────────────────────


@router.get(
    "/tenants/{tenant_id}/overview",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def tenant_overview(tenant_id: str, ctx: PlatformDep, days: int = 30):
    """Deep per-tenant profile: people, ops, sales, payments, fees, risk.

    Every block reads its own aggregate; no per-row loops, no N+1.
    ``days`` (default 30, capped 365) drives the sales window and its
    change-versus-previous-window comparison.
    """
    from datetime import datetime, timedelta, timezone

    from app.catalog.models import Product
    from app.customers.models import Customer
    from app.documents.models import Document
    from app.identity.models import Role, User, UserRole
    from app.payments.models import Payment
    from app.platform.models import PlatformFeeLedger as Fee
    from app.sales.models import Sale
    from app.stores.models import Store
    from app.tenancy.models import Tenant

    tenant_r = await ctx.session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_r.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    now = datetime.now(timezone.utc)
    days = min(max(days, 1), 365)
    window_start = now - timedelta(days=days)
    prev_window_start = now - timedelta(days=days * 2)

    # ── People: roles via the join table, statuses in one row.
    role_rows = (
        await ctx.session.execute(
            select(Role.name, func.count(UserRole.user_id))
            .select_from(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                UserRole.user_id.in_(select(User.id).where(User.tenant_id == tenant_id))
            )
            .group_by(Role.name)
        )
    ).all()
    people = (
        await ctx.session.execute(
            select(
                func.coalesce(func.sum(case((User.status != "suspended", 1), else_=0)), 0).label(
                    "active"
                ),
                func.coalesce(func.sum(case((User.status == "suspended", 1), else_=0)), 0).label(
                    "suspended"
                ),
                func.max(User.last_login_at).label("last_login"),
            ).where(User.tenant_id == tenant_id)
        )
    ).one()
    user_count = (
        await ctx.session.execute(
            select(func.count(User.id)).where(User.tenant_id == tenant_id)
        )
    ).scalar()

    product_count = (
        await ctx.session.execute(
            select(func.count(Product.id)).where(Product.tenant_id == tenant_id)
        )
    ).scalar()

    sale_count = (
        await ctx.session.execute(select(func.count(Sale.id)).where(Sale.tenant_id == tenant_id))
    ).scalar()

    total_revenue = (
        await ctx.session.execute(
            select(func.coalesce(func.sum(Sale.total), 0)).where(
                Sale.tenant_id == tenant_id, Sale.status != "voided"
            )
        )
    ).scalar()

    def _sales_window(start, end):
        return select(
            func.count(Sale.id).label("cnt"),
            func.coalesce(func.sum(Sale.total), 0).label("vol"),
        ).where(
            Sale.tenant_id == tenant_id,
            Sale.status != "voided",
            Sale.created_at >= start,
            Sale.created_at < end,
        )

    cur_stats = (await ctx.session.execute(_sales_window(window_start, now))).one()
    prev_stats = (await ctx.session.execute(_sales_window(prev_window_start, window_start))).one()
    cur_vol = float(cur_stats.vol)
    prev_vol = float(prev_stats.vol)
    vol_change = (
        round((cur_vol - prev_vol) / prev_vol * 100, 1)
        if prev_vol
        else (100.0 if cur_vol else 0.0)
    )
    avg_basket = round(cur_vol / cur_stats.cnt, 2) if cur_stats.cnt else 0.0
    last_sale_at = (
        await ctx.session.execute(
            select(func.max(Sale.created_at)).where(Sale.tenant_id == tenant_id)
        )
    ).scalar()

    daily_activity = (
        await ctx.session.execute(
            select(
                cast(Sale.created_at, Date).label("day"),
                func.count(Sale.id).label("cnt"),
                func.coalesce(func.sum(Sale.total), 0).label("vol"),
            )
            .where(
                Sale.tenant_id == tenant_id,
                Sale.status != "voided",
                Sale.created_at >= window_start,
            )
            .group_by(text("1"))
            .order_by(text("1"))
        )
    ).all()

    payment_count = (
        await ctx.session.execute(
            select(func.count(Payment.id)).where(Payment.tenant_id == tenant_id)
        )
    ).scalar()
    method_rows = (
        await ctx.session.execute(
            select(
                Payment.method.label("method"),
                func.count(Payment.id).label("cnt"),
                func.sum(Payment.amount).label("vol"),
            )
            .where(Payment.tenant_id == tenant_id)
            .group_by(Payment.method)
            .order_by(func.sum(Payment.amount).desc())
        )
    ).all()

    fee_stats = (
        await ctx.session.execute(
            select(
                func.coalesce(
                    func.sum(case((Fee.status == "deducted", Fee.amount), else_=0)), 0
                ).label("settled"),
                func.coalesce(
                    func.sum(case((Fee.status == "pending", Fee.amount), else_=0)), 0
                ).label("pending"),
                func.count(Fee.id).filter(Fee.status == "pending").label("pending_count"),
                func.max(Fee.created_at).label("last_fee"),
            ).where(Fee.tenant_id == tenant_id)
        )
    ).one()

    doc_rows = (
        await ctx.session.execute(
            select(Document.doc_type, func.count(Document.id))
            .where(Document.tenant_id == tenant_id)
            .group_by(Document.doc_type)
        )
    ).all()
    doc_count = sum(int(d[1]) for d in doc_rows)

    customer_count = (
        await ctx.session.execute(
            select(func.count(Customer.id)).where(Customer.tenant_id == tenant_id)
        )
    ).scalar()

    store_count = (
        await ctx.session.execute(select(func.count(Store.id)).where(Store.tenant_id == tenant_id))
    ).scalar()
    warehouse_count = (
        await ctx.session.execute(
            select(func.count(Store.id)).where(
                Store.tenant_id == tenant_id, Store.is_warehouse.is_(True)
            )
        )
    ).scalar()

    def _days_since(ts):
        return (now - ts).days if ts else None

    last_sale_days = _days_since(last_sale_at)
    last_login_days = _days_since(people.last_login)
    risk_level = "low"
    if (last_sale_days or 999) > 30 or (last_login_days or 999) > 30:
        risk_level = "high"
    elif (last_sale_days or 999) > 7:
        risk_level = "medium"

    return ok(
        {
            "tenant": {
                "id": str(tenant.id),
                "slug": tenant.slug,
                "business_name": tenant.business_name,
                "tier": tenant.tier,
                "status": tenant.status,
            },
            "counts": {
                "users": user_count,
                "products": product_count,
                "sales": sale_count,
                "payments": payment_count,
                "documents": doc_count,
                "stores": store_count,
            },
            "revenue": float(total_revenue),
            "employees": {
                "total": user_count,
                "active": int(people.active),
                "suspended": int(people.suspended),
                "by_role": [
                    {"role": r[0] or "unassigned", "count": int(r[1])} for r in role_rows
                ],
                "last_login_at": people.last_login.isoformat()
                if people.last_login
                else None,
            },
            "stores": {"total": store_count, "warehouses": warehouse_count},
            "sales": {
                "lifetime_count": sale_count,
                "lifetime_revenue": float(total_revenue),
                "window_days": days,
                "window_count": int(cur_stats.cnt),
                "window_revenue": cur_vol,
                "prev_window_revenue": prev_vol,
                "revenue_change_pct": vol_change,
                "avg_basket": avg_basket,
                "last_sale_at": last_sale_at.isoformat() if last_sale_at else None,
            },
            "payments": {
                "by_method": [
                    {"method": r.method, "count": int(r.cnt), "total": float(r.vol)}
                    for r in method_rows
                ]
            },
            "fees": {
                "settled_total": float(fee_stats.settled),
                "pending_total": float(fee_stats.pending),
                "pending_count": int(fee_stats.pending_count or 0),
                "last_fee_at": fee_stats.last_fee.isoformat()
                if fee_stats.last_fee
                else None,
            },
            "documents": {
                "by_type": [{"doc_type": d[0], "count": int(d[1])} for d in doc_rows]
            },
            "customers": {"total": customer_count},
            "risk": {
                "days_since_last_sale": last_sale_days if last_sale_days is not None else 999,
                "days_since_last_login": last_login_days
                if last_login_days is not None
                else 999,
                "level": risk_level,
            },
            "activity": [
                {
                    "date": r.day.isoformat() if r.day else None,
                    "count": int(r.cnt),
                    "revenue": float(r.vol),
                }
                for r in daily_activity
            ],
        }
    )


@router.get(
    "/tenants/{tenant_id}/stores",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def tenant_stores(tenant_id: str, ctx: PlatformDep, days: int = 30):
    """Store-level breakdown for one tenant.

    OUTLET rows carry product/employee counts, revenue for the window with
    change versus the previous window, payment-method mix, last-sale date
    and a low-stock signal.
    """
    from datetime import datetime, timedelta, timezone

    from app.inventory.models import StockBalance
    from app.identity.models import User
    from app.payments.models import Payment
    from app.sales.models import Sale
    from app.stores.models import Store, StoreProduct
    from app.tenancy.models import Tenant

    tenant_r = await ctx.session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_r.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    now = datetime.now(timezone.utc)
    days = min(max(days, 1), 365)
    window_start = now - timedelta(days=days)
    prev_window_start = now - timedelta(days=days * 2)

    sale_stats_sq = (
        select(
            Sale.store_id.label("store_id"),
            func.count(Sale.id).label("lt_count"),
            func.coalesce(func.sum(Sale.total), 0).label("lt_revenue"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                Sale.created_at >= window_start,
                                Sale.created_at < now,
                                Sale.status != "voided",
                            ),
                            Sale.total,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("cur_revenue"),
            func.count(Sale.id)
            .filter(Sale.created_at >= window_start)
            .label("cur_count"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                Sale.created_at >= prev_window_start,
                                Sale.created_at < window_start,
                                Sale.status != "voided",
                            ),
                            Sale.total,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("prev_revenue"),
            func.max(Sale.created_at).label("last_sale"),
        )
        .where(Sale.tenant_id == tenant.id)
        .group_by(Sale.store_id)
        .subquery()
    )

    product_sq = (
        select(StoreProduct.store_id, func.count(StoreProduct.id).label("products"))
        .where(StoreProduct.tenant_id == tenant.id)
        .group_by(StoreProduct.store_id)
        .subquery()
    )
    employees_sq = (
        select(User.store_id, func.count(User.id).label("employees"))
        .where(User.tenant_id == tenant.id, User.store_id.isnot(None))
        .group_by(User.store_id)
        .subquery()
    )
    # Store products whose available stock has fallen to/below reorder point.
    low_stock_sq = (
        select(StoreProduct.store_id, func.count(StoreProduct.id).label("low_stock"))
        .select_from(StoreProduct)
        .join(
            StockBalance,
            and_(
                StockBalance.store_id == StoreProduct.store_id,
                StockBalance.product_id == StoreProduct.product_id,
            ),
        )
        .where(
            StoreProduct.tenant_id == tenant.id,
            StockBalance.qty - StockBalance.reserved_qty <= StoreProduct.reorder_point,
        )
        .group_by(StoreProduct.store_id)
        .subquery()
    )

    rows = (
        await ctx.session.execute(
            select(
                Store.id,
                Store.name,
                Store.address,
                Store.status,
                Store.is_warehouse,
                func.coalesce(sale_stats_sq.c.lt_count, 0).label("lt_count"),
                func.coalesce(sale_stats_sq.c.lt_revenue, 0).label("lt_revenue"),
                func.coalesce(sale_stats_sq.c.cur_revenue, 0).label("cur_revenue"),
                func.coalesce(sale_stats_sq.c.cur_count, 0).label("cur_count"),
                func.coalesce(sale_stats_sq.c.prev_revenue, 0).label("prev_revenue"),
                sale_stats_sq.c.last_sale,
                func.coalesce(product_sq.c.products, 0).label("products"),
                func.coalesce(employees_sq.c.employees, 0).label("employees"),
                func.coalesce(low_stock_sq.c.low_stock, 0).label("low_stock"),
            )
            .outerjoin(sale_stats_sq, sale_stats_sq.c.store_id == Store.id)
            .outerjoin(product_sq, product_sq.c.store_id == Store.id)
            .outerjoin(employees_sq, employees_sq.c.store_id == Store.id)
            .outerjoin(low_stock_sq, low_stock_sq.c.store_id == Store.id)
            .where(Store.tenant_id == tenant.id)
        )
    ).all()

    method_splits = (
        await ctx.session.execute(
            select(
                Sale.store_id.label("store_id"),
                Payment.method.label("method"),
                func.count(Payment.id).label("cnt"),
                func.sum(Payment.amount).label("vol"),
            )
            .join(Sale, Sale.id == Payment.sale_id)
            .where(Payment.tenant_id == tenant.id, Payment.created_at >= window_start)
            .group_by(Sale.store_id, Payment.method)
        )
    ).all()
    split_map: dict = {}
    for r in method_splits:
        split_map.setdefault(r.store_id, []).append(
            {"method": r.method, "count": int(r.cnt), "total": float(r.vol)}
        )

    items = []
    for r in rows:
        cur_rev = float(r.cur_revenue)
        prev_rev = float(r.prev_revenue)
        change = (
            round((cur_rev - prev_rev) / prev_rev * 100, 1)
            if prev_rev
            else (100.0 if cur_rev else 0.0)
        )
        items.append(
            {
                "store_id": str(r.id),
                "name": r.name,
                "address": r.address,
                "status": r.status,
                "is_warehouse": bool(r.is_warehouse),
                "products": int(r.products),
                "employees": int(r.employees),
                "low_stock": int(r.low_stock),
                "lifetime_sales": int(r.lt_count),
                "lifetime_revenue": float(r.lt_revenue),
                "window_sales": int(r.cur_count),
                "window_revenue": cur_rev,
                "prev_window_revenue": prev_rev,
                "revenue_change_pct": change,
                "avg_basket": round(cur_rev / int(r.cur_count), 2) if r.cur_count else 0.0,
                "last_sale_at": r.last_sale.isoformat() if r.last_sale else None,
                "payment_methods": split_map.get(r.id, []),
            }
        )

    items.sort(key=lambda s: s["window_revenue"], reverse=True)
    return ok({"items": items, "total": len(items), "window_days": days})


@router.get(
    "/tenants/{tenant_id}/audit-trail",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def tenant_audit_trail(
    tenant_id: str,
    ctx: PlatformDep,
    days: int = 90,
    source: str | None = None,
    page: int = 1,
    page_size: int = 100,
):
    """Unified audit trail for one tenant across all three layers.

    - ``platform``: actions platform admins took against this tenant
      (platform_audit_log where resource='tenant', resource_id=tenant).
    - ``auth``: member authentication events (auth_audit_logs by tenant_id).
    - ``tenant``: the tenant's own business-action log (audit_events by
      business_id).
    """
    from datetime import datetime, timedelta, timezone
    import json

    from app.auth.audit import AuditEvent
    from app.identity.models import AuthAuditLog
    from app.platform.models import PlatformAuditLog
    from app.tenancy.models import Tenant

    tenant_r = await ctx.session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_r.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    now = datetime.now(timezone.utc)
    days = min(max(days, 1), 365)
    since = now - timedelta(days=days)

    allowed_sources = {"platform", "auth", "tenant"}
    if source is not None and source not in allowed_sources:
        raise HTTPException(status_code=422, detail=f"source must be one of {sorted(allowed_sources)}")

    def _norm_details(raw):
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    rows_data = []

    def _collect(resource, when, actor, action, detail, status, resource_type=None, resource_id=None, approver=None):
        rows_data.append(
            {
                "at": when,
                "source": resource,
                "actor": actor,
                "action": action,
                "detail": _norm_details(detail),
                "status": status,
                "resource": resource_type,
                "resource_id": resource_id,
                "approver": approver,
            }
        )

    include_platform = source in (None, "platform")
    include_auth = source in (None, "auth")
    include_business = source in (None, "tenant")

    if include_platform:
        plat_r = await ctx.session.execute(
            select(
                PlatformAuditLog.admin_id,
                PlatformAuditLog.action,
                PlatformAuditLog.resource,
                PlatformAuditLog.resource_id,
                PlatformAuditLog.details,
                PlatformAuditLog.created_at,
            )
            .where(
                PlatformAuditLog.resource == "tenant",
                PlatformAuditLog.resource_id == tenant_id,
                PlatformAuditLog.created_at >= since,
            )
            .order_by(PlatformAuditLog.created_at.desc())
        )
        for r in plat_r.all():
            _collect(
                "platform",
                r.created_at,
                str(r.admin_id) if r.admin_id else None,
                r.action,
                r.details,
                "ok",
                resource_type=r.resource,
                resource_id=r.resource_id,
            )

    if include_auth:
        auth_r = await ctx.session.execute(
            select(
                AuthAuditLog.user_id,
                AuthAuditLog.action,
                AuthAuditLog.details,
                AuthAuditLog.created_at,
            )
            .where(
                AuthAuditLog.tenant_id == tenant.id,
                AuthAuditLog.created_at >= since,
            )
            .order_by(AuthAuditLog.created_at.desc())
        )
        for r in auth_r.all():
            actor = str(r.user_id) if r.user_id else None
            # For cart_void_approved, also extract the approver from details
            approver = None
            if r.action == "cart_void_approved" and r.details:
                import json
                try:
                    det = json.loads(r.details) if isinstance(r.details, str) else r.details
                    if isinstance(det, dict):
                        sup = det.get("supervisor_id")
                        if sup:
                            approver = str(sup)
                except Exception:
                    pass
            _collect("auth", r.created_at, actor, r.action, r.details, "ok", approver=approver)

    if include_business:
        biz_r = await ctx.session.execute(
            select(
                AuditEvent.user_id,
                AuditEvent.action,
                AuditEvent.resource_type,
                AuditEvent.resource_id,
                AuditEvent.details,
                AuditEvent.status_code,
                AuditEvent.created_at,
            )
            .where(AuditEvent.business_id == tenant_id, AuditEvent.created_at >= since)
            .order_by(AuditEvent.created_at.desc())
        )
        for r in biz_r.all():
            _collect(
                "tenant",
                r.created_at,
                str(r.user_id) if r.user_id else None,
                r.action,
                r.details,
                r.status_code,
                resource_type=r.resource_type,
                resource_id=r.resource_id,
            )

    rows_data.sort(key=lambda x: x["at"], reverse=True)

    total = len(rows_data)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    start = (page - 1) * page_size
    window_rows = rows_data[start : start + page_size]

    for row in window_rows:
        row["at"] = row["at"].isoformat()

    return ok({"items": window_rows, "total": total, "page": page, "page_size": page_size, "window_days": days})


@router.get(
    "/audit/actor/{actor_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def audit_actor_details(actor_id: str, ctx: PlatformDep):
    """Resolve an audit-trail actor id into a human profile.

    Tries tenant users first, then platform admins; "system" actors are
    handled client-side and never hit this route with a real id.
    """
    from uuid import UUID as _UUID

    from app.identity.models import Role, User, UserRole
    from app.stores.models import Store
    from app.tenancy.models import Tenant

    try:
        aid = _UUID(actor_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Actor not found")

    row = (
        await ctx.session.execute(select(User).where(User.id == aid))
    ).scalar_one_or_none()
    if row:
        tenant_name = None
        store_name = None
        if row.tenant_id:
            t = (
                await ctx.session.execute(select(Tenant).where(Tenant.id == row.tenant_id))
            ).scalar_one_or_none()
            tenant_name = t.business_name if t else None
        if row.store_id:
            s = (
                await ctx.session.execute(select(Store).where(Store.id == row.store_id))
            ).scalar_one_or_none()
            store_name = s.name if s else None

        role_rows = (
            await ctx.session.execute(
                select(Role.name)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == aid)
            )
        ).all()
        roles = [r[0] for r in role_rows]

        return ok(
            {
                "actor_type": "user",
                "id": str(row.id),
                "full_name": row.full_name,
                "email": row.email,
                "phone": row.phone,
                "status": row.status,
                "roles": roles,
                "tenant_id": str(row.tenant_id) if row.tenant_id else None,
                "tenant_name": tenant_name,
                "store_id": str(row.store_id) if row.store_id else None,
                "store_name": store_name,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
                "last_device_type": row.last_device_type,
                "last_browser": row.last_browser,
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                "last_ip_address": row.last_ip_address,
            }
        )

    from app.platform.models import PlatformAdmin

    admin = (
        await ctx.session.execute(select(PlatformAdmin).where(PlatformAdmin.id == aid))
    ).scalar_one_or_none()
    if admin:
        return ok(
            {
                "actor_type": "admin",
                "id": str(admin.id),
                "full_name": admin.full_name,
                "email": admin.email,
                "phone": None,
                "status": admin.status,
                "roles": [admin.role],
                "tenant_id": None,
                "tenant_name": None,
                "store_id": None,
                "store_name": None,
                "created_at": admin.created_at.isoformat() if admin.created_at else None,
                "last_login_at": admin.last_login_at.isoformat() if admin.last_login_at else None,
                "last_device_type": admin.last_device_type,
                "last_browser": admin.last_browser,
                "last_seen_at": admin.last_seen_at.isoformat() if admin.last_seen_at else None,
                "last_ip_address": admin.last_ip_address,
            }
        )

    raise HTTPException(status_code=404, detail="Actor not found")


@router.get(
    "/audit-log",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def platform_audit_log_feed(
    ctx: PlatformDep,
    resource: str | None = None,
    admin_id: str | None = None,
    action: str | None = None,
    search: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    format: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    """Global platform action feed with filters, resolved admin names and CSV."""
    from datetime import datetime

    from app.platform.exporting import csv_response, parse_iso_date
    from app.platform.models import PlatformAdmin, PlatformAuditLog

    def _parse_details(raw):
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    def _iso(value):
        return value.isoformat() if isinstance(value, datetime) else value

    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    d_from = parse_iso_date(from_date, "from_date")
    d_to = parse_iso_date(to_date, "to_date")

    conditions = []
    if resource:
        conditions.append(PlatformAuditLog.resource == resource)
    if admin_id:
        conditions.append(PlatformAuditLog.admin_id == admin_id)
    if action:
        conditions.append(PlatformAuditLog.action.ilike(f"%{action}%"))
    if search:
        like = f"%{search}%"
        conditions.append(
            PlatformAuditLog.action.ilike(like)
            | PlatformAuditLog.resource.ilike(like)
            | PlatformAuditLog.resource_id.ilike(like)
            | PlatformAuditLog.details.ilike(like)
        )
    if d_from:
        conditions.append(cast(PlatformAuditLog.created_at, Date) >= d_from)
    if d_to:
        conditions.append(cast(PlatformAuditLog.created_at, Date) <= d_to)

    count_q = select(func.count(PlatformAuditLog.id)).select_from(PlatformAuditLog)
    query = (
        select(
            PlatformAuditLog.id,
            PlatformAuditLog.admin_id,
            PlatformAuditLog.action,
            PlatformAuditLog.resource,
            PlatformAuditLog.resource_id,
            PlatformAuditLog.details,
            PlatformAuditLog.ip_address,
            PlatformAuditLog.created_at,
            PlatformAdmin.full_name.label("admin_name"),
            PlatformAdmin.email.label("admin_email"),
        )
        .select_from(PlatformAuditLog)
        .outerjoin(PlatformAdmin, PlatformAdmin.id == PlatformAuditLog.admin_id)
        .order_by(PlatformAuditLog.created_at.desc(), PlatformAuditLog.id.desc())
    )
    for condition in conditions:
        count_q = count_q.where(condition)
        query = query.where(condition)

    total = (await ctx.session.execute(count_q)).scalar()
    rows = (
        await ctx.session.execute(
            query.offset((page - 1) * page_size).limit(page_size)
        )
    ).all()

    if format == "csv":
        return csv_response(
            "platform-audit-log.csv",
            [
                "created_at",
                "action",
                "resource",
                "resource_id",
                "admin_id",
                "admin_name",
                "admin_email",
                "ip_address",
                "details",
            ],
            [
                {
                    "created_at": _iso(r.created_at),
                    "action": r.action,
                    "resource": r.resource,
                    "resource_id": r.resource_id,
                    "admin_id": str(r.admin_id) if r.admin_id else None,
                    "admin_name": r.admin_name,
                    "admin_email": r.admin_email,
                    "ip_address": r.ip_address,
                    "details": json.dumps(_parse_details(r.details))
                    if _parse_details(r.details) is not None
                    else None,
                }
                for r in rows
            ],
        )

    return ok(
        {
            "items": [
                {
                    "id": str(r.id),
                    "admin_id": str(r.admin_id) if r.admin_id else None,
                    "admin_name": r.admin_name,
                    "admin_email": r.admin_email,
                    "action": r.action,
                    "resource": r.resource,
                    "resource_id": r.resource_id,
                    "details": _parse_details(r.details),
                    "ip_address": r.ip_address,
                    "created_at": _iso(r.created_at),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


# ═══════════════════════════════════════════════════════════════════
#  PLATFORM ANALYTICS
# ═══════════════════════════════════════════════════════════════════


@router.get(
    "/analytics/overview", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def analytics_overview(ctx: PlatformDep, days: int = 30):
    from app.tenancy.models import Tenant
    from app.identity.models import User
    from app.sales.models import Sale
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    period_start = now - timedelta(days=days)
    prev_start = now - timedelta(days=days * 2)
    prev_end = period_start

    tenant_stats = (
        await ctx.session.execute(
            select(
                func.count(Tenant.id).label("total"),
                func.count(Tenant.id).filter(Tenant.status == "active").label("active"),
                func.count(Tenant.id).filter(Tenant.status == "suspended").label("suspended"),
                func.count(Tenant.id).filter(Tenant.created_at >= period_start).label("new"),
            )
        )
    ).one()

    user_stats = (
        await ctx.session.execute(
            select(
                func.count(User.id).label("total"),
                func.count(User.id).filter(
                    User.status == "active",
                    User.last_login_at >= period_start,
                ).label("active"),
                func.count(User.id).filter(User.created_at >= period_start).label("new"),
            )
        )
    ).one()

    sales_stats = (
        await ctx.session.execute(
            select(
                func.coalesce(func.sum(Sale.total), 0).label("cur_volume"),
                func.coalesce(func.count(Sale.id), 0).label("cur_count"),
                func.coalesce(
                    func.sum(Sale.total).filter(
                        Sale.created_at >= prev_start, Sale.created_at < prev_end
                    ),
                    0,
                ).label("prev_volume"),
            )
            .where(Sale.status != "voided")
            .where(Sale.created_at >= period_start)
        )
    ).one()

    cur_vol = float(sales_stats.cur_volume)
    prev_vol = float(sales_stats.prev_volume)
    vol_change = round((cur_vol - prev_vol) / prev_vol * 100, 1) if prev_vol else 0

    avg_per_tenant = round(cur_vol / tenant_stats.active, 2) if tenant_stats.active else 0

    return ok(
        {
            "tenants": {
                "total": tenant_stats.total,
                "active": tenant_stats.active,
                "suspended": tenant_stats.suspended,
                "new_in_period": tenant_stats.new,
            },
            "users": {
                "total": user_stats.total,
                "active_in_period": user_stats.active,
                "new_in_period": user_stats.new,
            },
            "sales": {
                "total_volume": cur_vol,
                "total_transactions": int(sales_stats.cur_count),
                "volume_change_pct": vol_change,
                "avg_per_tenant": avg_per_tenant,
            },
        }
    )


@router.get(
    "/analytics/tenant-growth", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def analytics_tenant_growth(
    ctx: PlatformDep,
    from_date: str | None = None,
    to_date: str | None = None,
    group_by: str = "week",
):
    from app.tenancy.models import Tenant
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    if not from_date:
        from_date = (now - timedelta(days=90)).date().isoformat()
    if not to_date:
        to_date = now.date().isoformat()

    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    trunc_map = {"day": "day", "week": "week", "month": "month"}
    trunc = trunc_map.get(group_by, "week")

    rows = await ctx.session.execute(
        select(
            func.date_trunc(trunc, Tenant.created_at).label("period"),
            func.count(Tenant.id).label("new_tenants"),
        )
        .where(cast(Tenant.created_at, Date) >= d_from, cast(Tenant.created_at, Date) <= d_to)
        .group_by(text("1"))
        .order_by(text("1"))
    )

    items = []
    cumulative = (
        await ctx.session.execute(select(func.count(Tenant.id)).where(Tenant.created_at < d_from))
    ).scalar() or 0

    for r in rows.mappings():
        new = int(r["new_tenants"])
        cumulative += new
        items.append(
            {
                "period": r["period"].isoformat() if r["period"] else None,
                "new_tenants": new,
                "cumulative": cumulative,
            }
        )

    total = (await ctx.session.execute(select(func.count(Tenant.id)))).scalar() or 0
    return ok(
        {
            "items": items,
            "totals": {"new_tenants": sum(i["new_tenants"] for i in items), "total": total},
        }
    )


@router.get(
    "/analytics/top-tenants", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def analytics_top_tenants(
    ctx: PlatformDep,
    from_date: str | None = None,
    to_date: str | None = None,
    sort: str = "revenue",
    limit: int = 10,
):
    """Top tenants by GMV, platform fee revenue or transaction count."""
    from datetime import datetime, timedelta, timezone

    from app.billing.models import MerchantSubscription, SubscriptionPlan
    from app.platform.exporting import parse_iso_date
    from app.platform.models import PlatformFeeLedger as Fee
    from app.sales.models import Sale
    from app.tenancy.models import Tenant

    now = datetime.now(timezone.utc)
    d_from = parse_iso_date(from_date, "from_date") or (now - timedelta(days=30)).date()
    d_to = parse_iso_date(to_date, "to_date") or now.date()
    limit = min(max(limit, 1), 100)

    agg = (
        select(
            Sale.tenant_id.label("tid"),
            func.coalesce(func.sum(Sale.total), 0).label("total_revenue"),
            func.coalesce(func.count(Sale.id), 0).label("total_transactions"),
        )
        .where(Sale.status != "voided")
        .where(cast(Sale.created_at, Date) >= d_from, cast(Sale.created_at, Date) <= d_to)
        .group_by(Sale.tenant_id)
        .subquery()
    )
    fee_agg = (
        select(
            Fee.tenant_id.label("tid"),
            func.coalesce(
                func.sum(case((Fee.status == "deducted", Fee.amount), else_=0)), 0
            ).label("settled_fees"),
            func.coalesce(
                func.sum(case((Fee.status == "pending", Fee.amount), else_=0)), 0
            ).label("pending_fees"),
        )
        .where(cast(Fee.created_at, Date) >= d_from, cast(Fee.created_at, Date) <= d_to)
        .group_by(Fee.tenant_id)
        .subquery()
    )

    sort_map = {
        "revenue": func.coalesce(agg.c.total_revenue, 0),
        "fees": func.coalesce(fee_agg.c.settled_fees, 0),
        "transactions": func.coalesce(agg.c.total_transactions, 0),
    }
    sort_col = sort_map.get(sort, sort_map["revenue"])

    rows = await ctx.session.execute(
        select(
            Tenant.id.label("tenant_id"),
            Tenant.business_name.label("business_name"),
            Tenant.slug.label("slug"),
            Tenant.tier.label("tier"),
            Tenant.status.label("status"),
            agg.c.total_revenue.label("total_revenue"),
            agg.c.total_transactions.label("total_transactions"),
            fee_agg.c.settled_fees.label("settled_fees"),
            fee_agg.c.pending_fees.label("pending_fees"),
            SubscriptionPlan.name.label("plan_name"),
            MerchantSubscription.status.label("subscription_status"),
        )
        .outerjoin(agg, agg.c.tid == Tenant.id)
        .outerjoin(fee_agg, fee_agg.c.tid == Tenant.id)
        .outerjoin(
            MerchantSubscription, MerchantSubscription.tenant_id == Tenant.id
        )
        .outerjoin(SubscriptionPlan, SubscriptionPlan.id == MerchantSubscription.plan_id)
        .order_by(desc(sort_col))
        .limit(limit)
    )

    items = []
    for r in rows.mappings():
        items.append(
            {
                "tenant_id": str(r["tenant_id"]),
                "business_name": r["business_name"],
                "slug": r["slug"],
                "tier": r["tier"],
                "status": r["status"],
                "plan": r["plan_name"],
                "subscription_status": r["subscription_status"],
                "total_revenue": float(r["total_revenue"] or 0),
                "total_transactions": int(r["total_transactions"] or 0),
                "fee_revenue": float(r["settled_fees"] or 0),
                "pending_fees": float(r["pending_fees"] or 0),
            }
        )

    return ok({"items": items, "from_date": d_from.isoformat(), "to_date": d_to.isoformat()})


@router.get(
    "/analytics/platform-revenue", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def analytics_platform_revenue(
    ctx: PlatformDep,
    from_date: str | None = None,
    to_date: str | None = None,
    group_by: str = "month",
):
    """What the platform actually earns: transaction fees.

    Built from platform_fee_ledger — settled ("deducted") and pending
    ("pending") amounts, split by payment method, with a per-bucket trend.
    """
    from app.platform.models import PlatformFeeLedger as Fee
    from app.tenancy.models import Tenant
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    if not from_date:
        from_date = (now - timedelta(days=90)).date().isoformat()
    if not to_date:
        to_date = now.date().isoformat()

    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    def _window(q):
        return q.where(
            cast(Fee.created_at, Date) >= d_from,
            cast(Fee.created_at, Date) <= d_to,
        )

    settled_total = (
        await ctx.session.execute(
            _window(
                select(func.coalesce(func.sum(Fee.amount), 0)).where(Fee.status == "deducted")
            )
        )
    ).scalar() or 0
    settled_count = (
        await ctx.session.execute(
            _window(
                select(func.count(Fee.id)).where(Fee.status == "deducted")
            )
        )
    ).scalar() or 0

    pending_total = (
        await ctx.session.execute(
            select(func.coalesce(func.sum(Fee.amount), 0)).where(Fee.status == "pending")
        )
    ).scalar() or 0
    pending_count = (
        await ctx.session.execute(
            select(func.count(Fee.id)).where(Fee.status == "pending")
        )
    ).scalar() or 0
    oldest_pending = (
        await ctx.session.execute(
            select(func.min(Fee.created_at)).where(Fee.status == "pending")
        )
    ).scalar()

    by_method = (
        await ctx.session.execute(
            _window(
                select(
                    Fee.payment_method.label("method"),
                    func.count(Fee.id).label("count"),
                    func.sum(Fee.amount).label("total"),
                )
                .where(Fee.status == "deducted")
                .group_by(Fee.payment_method)
                .order_by(func.sum(Fee.amount).desc())
            )
        )
    ).all()

    trunc_map = {"day": "day", "week": "week", "month": "month"}
    bucket = trunc_map.get(group_by, "month")

    def _period_label(dt):
        if dt is None:
            return None
        if bucket == "month":
            return dt.strftime("%b %Y")
        if bucket == "week":
            return dt.strftime("%d %b")
        return dt.strftime("%d %b %Y")

    trend_rows = (
        await ctx.session.execute(
            _window(
                select(
                    func.date_trunc(bucket, Fee.created_at).label("period"),
                    func.count(Fee.id).label("count"),
                    func.sum(case((Fee.status == "deducted", Fee.amount), else_=0)).label(
                        "settled"
                    ),
                    func.sum(case((Fee.status == "pending", Fee.amount), else_=0)).label(
                        "pending"
                    ),
                )
                .group_by(text("1"))
                .order_by(text("1"))
            )
        )
    ).all()

    tenant_count = (
        await ctx.session.execute(
            select(func.count(Tenant.id)).where(Tenant.status == "active")
        )
    ).scalar() or 0

    return ok(
        {
            "summary": {
                "settled_total": float(settled_total),
                "settled_count": int(settled_count),
                "pending_total": float(pending_total),
                "pending_count": int(pending_count),
                "oldest_pending": oldest_pending.isoformat() if oldest_pending else None,
                "active_tenants": tenant_count,
            },
            "by_method": [
                {
                    "method": r.method,
                    "count": int(r.count),
                    "total": float(r.total),
                }
                for r in by_method
            ],
            "trend": [
                {
                    "period": r.period.isoformat() if r.period else None,
                    "label": _period_label(r.period),
                    "count": int(r.count),
                    "settled": float(r.settled),
                    "pending": float(r.pending),
                }
                for r in trend_rows
            ],
        }
    )


@router.get(
    "/analytics/system-health", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def analytics_system_health(
    ctx: PlatformDep,
    from_date: str | None = None,
    to_date: str | None = None,
    group_by: str = "day",
):
    from datetime import datetime, timedelta, timezone

    from app.auth.audit import AuditEvent
    from app.common.events.outbox import OutboxEvent
    from app.notifications.models import Notification
    from app.payments.models import WebhookLog
    from app.platform.exporting import parse_iso_date

    now = datetime.now(timezone.utc)
    d_from = parse_iso_date(from_date, "from_date") or (now - timedelta(days=7)).date()
    d_to = parse_iso_date(to_date, "to_date") or now.date()

    total = (
        await ctx.session.execute(
            select(func.count(AuditEvent.id)).where(
                cast(AuditEvent.created_at, Date) >= d_from,
                cast(AuditEvent.created_at, Date) <= d_to,
            )
        )
    ).scalar() or 0

    errors = (
        await ctx.session.execute(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.status_code >= 400,
                cast(AuditEvent.created_at, Date) >= d_from,
                cast(AuditEvent.created_at, Date) <= d_to,
            )
        )
    ).scalar() or 0

    by_status = (
        await ctx.session.execute(
            select(
                AuditEvent.status_code.label("status_code"),
                func.count(AuditEvent.id).label("count"),
            )
            .where(
                cast(AuditEvent.created_at, Date) >= d_from,
                cast(AuditEvent.created_at, Date) <= d_to,
            )
            .group_by(AuditEvent.status_code)
            .order_by(desc("count"))
        )
    ).all()

    status_breakdown = [
        {
            "status_code": int(r[0]),
            "count": int(r[1]),
            "percentage": round(int(r[1]) / total * 100, 1) if total else 0,
        }
        for r in by_status
    ]

    error_trend = (
        await ctx.session.execute(
            select(
                cast(AuditEvent.created_at, Date).label("period"),
                func.count(AuditEvent.id).label("total"),
                func.count(AuditEvent.id).filter(AuditEvent.status_code >= 400).label("errors"),
            )
            .where(
                cast(AuditEvent.created_at, Date) >= d_from,
                cast(AuditEvent.created_at, Date) <= d_to,
            )
            .group_by(text("1"))
            .order_by(text("1"))
        )
    ).all()

    error_trend_data = [
        {
            "period": r[0].isoformat(),
            "label": r[0].strftime("%d %b %Y"),
            "total": int(r[1]),
            "errors": int(r[2]),
            "error_rate": round(int(r[2]) / int(r[1]) * 100, 1) if int(r[1]) else 0,
        }
        for r in error_trend
    ]

    top_error_paths = (
        await ctx.session.execute(
            select(
                AuditEvent.path.label("path"),
                func.count(AuditEvent.id).label("count"),
                func.max(AuditEvent.created_at).label("last_seen"),
            )
            .where(
                AuditEvent.status_code >= 400,
                cast(AuditEvent.created_at, Date) >= d_from,
                cast(AuditEvent.created_at, Date) <= d_to,
            )
            .group_by(AuditEvent.path)
            .order_by(desc("count"))
            .limit(10)
        )
    ).all()

    outbox = (
        await ctx.session.execute(
            select(
                func.count(OutboxEvent.id).label("total"),
                func.count(OutboxEvent.id).filter(OutboxEvent.status == "pending").label("pending"),
                func.count(OutboxEvent.id).filter(OutboxEvent.status == "failed").label("failed"),
                func.min(OutboxEvent.available_at)
                .filter(OutboxEvent.status == "pending")
                .label("oldest_pending"),
                func.max(OutboxEvent.attempts).label("max_attempts"),
            )
        )
    ).one()
    webhooks = (
        await ctx.session.execute(
            select(
                func.count(WebhookLog.id).label("total"),
                func.count(WebhookLog.id).filter(WebhookLog.processed == 0).label("unprocessed"),
                func.count(WebhookLog.id).filter(WebhookLog.error.isnot(None)).label("errors"),
                func.max(WebhookLog.created_at).label("last_seen"),
            )
        )
    ).one()
    notifications = (
        await ctx.session.execute(
            select(
                func.count(Notification.id).label("total"),
                func.count(Notification.id).filter(Notification.status == "pending").label("pending"),
                func.count(Notification.id).filter(Notification.status == "failed").label("failed"),
                func.min(Notification.created_at)
                .filter(Notification.status == "pending")
                .label("oldest_pending"),
            )
        )
    ).one()

    return ok(
        {
            "summary": {
                "total_requests": total,
                "error_rate_pct": round(errors / total * 100, 1) if total else 0,
            },
            "by_status": status_breakdown,
            "error_trend": error_trend_data,
            "top_error_paths": [
                {
                    "path": r.path,
                    "count": int(r.count),
                    "last_seen": r.last_seen.isoformat() if r.last_seen else None,
                }
                for r in top_error_paths
            ],
            "ops": {
                "outbox": {
                    "total": int(outbox.total or 0),
                    "pending": int(outbox.pending or 0),
                    "failed": int(outbox.failed or 0),
                    "oldest_pending": outbox.oldest_pending.isoformat()
                    if outbox.oldest_pending
                    else None,
                    "max_attempts": int(outbox.max_attempts or 0),
                },
                "webhooks": {
                    "total": int(webhooks.total or 0),
                    "unprocessed": int(webhooks.unprocessed or 0),
                    "errors": int(webhooks.errors or 0),
                    "last_seen": webhooks.last_seen.isoformat() if webhooks.last_seen else None,
                },
                "notifications": {
                    "total": int(notifications.total or 0),
                    "pending": int(notifications.pending or 0),
                    "failed": int(notifications.failed or 0),
                    "oldest_pending": notifications.oldest_pending.isoformat()
                    if notifications.oldest_pending
                    else None,
                },
            },
        }
    )


@router.get(
    "/analytics/user-engagement", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def analytics_user_engagement(
    ctx: PlatformDep,
    from_date: str | None = None,
    to_date: str | None = None,
    group_by: str = "day",
):
    from app.identity.models import User
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    if not from_date:
        from_date = (now - timedelta(days=30)).date().isoformat()
    if not to_date:
        to_date = now.date().isoformat()

    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    total_users = (await ctx.session.execute(select(func.count(User.id)))).scalar() or 0

    active = (
        await ctx.session.execute(
            select(func.count(User.id)).where(
                User.status == "active",
                User.last_login_at >= d_from,
            )
        )
    ).scalar() or 0

    login_trend = (
        await ctx.session.execute(
            select(
                cast(User.last_login_at, Date).label("period"),
                func.count(User.id).label("unique_logins"),
            )
            .where(
                User.last_login_at.isnot(None),
                cast(User.last_login_at, Date) >= d_from,
                cast(User.last_login_at, Date) <= d_to,
            )
            .group_by(text("1"))
            .order_by(text("1"))
        )
    ).all()

    return ok(
        {
            "summary": {
                "total_users": total_users,
                "active_users": active,
                "activation_rate_pct": round(active / total_users * 100, 1) if total_users else 0,
            },
            "login_trend": [
                {
                    "period": r[0].isoformat(),
                    "label": r[0].strftime("%d %b %Y"),
                    "unique_logins": int(r[1]),
                }
                for r in login_trend
            ],
        }
    )


@router.get(
    "/analytics/churn-risk", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def analytics_churn_risk(
    ctx: PlatformDep,
    inactive_days: int = 30,
):
    """Per-tenant staleness plus the revenue and fee exposure behind it."""
    from datetime import datetime, timedelta, timezone

    from app.identity.models import User
    from app.platform.models import PlatformFeeLedger as Fee
    from app.sales.models import Sale
    from app.tenancy.models import Tenant

    now = datetime.now(timezone.utc)
    inactive_days = min(max(inactive_days, 1), 365)
    cutoff = now - timedelta(days=inactive_days)
    rev_cutoff = now - timedelta(days=30)

    last_sale_sq = (
        select(func.max(Sale.created_at))
        .where(Sale.tenant_id == Tenant.id, Sale.status != "voided")
        .correlate(Tenant)
        .scalar_subquery()
    )
    last_login_sq = (
        select(func.max(User.last_login_at))
        .where(User.tenant_id == Tenant.id, User.status == "active")
        .correlate(Tenant)
        .scalar_subquery()
    )
    rev_30_sq = (
        select(func.coalesce(func.sum(Sale.total), 0))
        .where(
            Sale.tenant_id == Tenant.id,
            Sale.status != "voided",
            Sale.created_at >= rev_cutoff,
        )
        .correlate(Tenant)
        .scalar_subquery()
    )
    fee_30_sq = (
        select(func.coalesce(func.sum(Fee.amount), 0))
        .where(Fee.tenant_id == Tenant.id, Fee.created_at >= rev_cutoff)
        .correlate(Tenant)
        .scalar_subquery()
    )
    fee_pending_sq = (
        select(func.coalesce(func.sum(Fee.amount), 0))
        .where(Fee.tenant_id == Tenant.id, Fee.status == "pending")
        .correlate(Tenant)
        .scalar_subquery()
    )

    user_stats_sq = (
        select(
            User.tenant_id.label("tenant_id"),
            func.count(User.id).label("total_users"),
            func.sum(
                case((and_(User.status == "active", User.last_login_at >= cutoff), 1), else_=0)
            ).label("active_users"),
        )
        .group_by(User.tenant_id)
        .subquery()
    )

    rows = (
        await ctx.session.execute(
            select(
                Tenant.id,
                Tenant.business_name,
                Tenant.slug,
                Tenant.tier,
                last_sale_sq.label("last_sale"),
                last_login_sq.label("last_login"),
                rev_30_sq.label("rev_30"),
                fee_30_sq.label("fee_30"),
                fee_pending_sq.label("fee_pending"),
                func.coalesce(user_stats_sq.c.active_users, 0).label("active_users"),
                func.coalesce(user_stats_sq.c.total_users, 0).label("total_users"),
            )
            .outerjoin(user_stats_sq, user_stats_sq.c.tenant_id == Tenant.id)
            .where(Tenant.status == "active")
        )
    ).all()

    items = []
    healthy = at_risk = churned = 0
    revenue_at_risk = 0.0
    for r in rows:
        last_sale = r.last_sale
        days_inactive = (now - last_sale).days if last_sale else 999
        days_since_login = (now - r.last_login).days if r.last_login else 999
        rev_30 = float(r.rev_30 or 0)

        if days_inactive <= 7:
            risk = "low"
            healthy += 1
        elif days_inactive <= inactive_days:
            risk = "medium"
            at_risk += 1
            revenue_at_risk += rev_30
        else:
            risk = "high"
            churned += 1
            revenue_at_risk += rev_30

        if risk == "high":
            action = "Call the owner; offer a re-onboarding session"
        elif risk == "medium":
            action = "Check in on stock, pricing and staff activity"
        elif int(r.active_users or 0) == 0:
            action = "No recent user logins; verify access"
        else:
            action = "Healthy; leave to normal account management"

        items.append(
            {
                "tenant_id": str(r.id),
                "business_name": r.business_name,
                "slug": r.slug,
                "tier": r.tier,
                "last_sale": last_sale.date().isoformat() if last_sale else None,
                "days_inactive": days_inactive,
                "last_user_login": r.last_login.date().isoformat() if r.last_login else None,
                "days_since_login": days_since_login,
                "risk_level": risk,
                "revenue_30d": round(rev_30, 2),
                "fee_revenue_30d": round(float(r.fee_30 or 0), 2),
                "pending_fees": round(float(r.fee_pending or 0), 2),
                "total_users": int(r.total_users or 0),
                "active_users": int(r.active_users or 0),
                "recommended_action": action,
            }
        )

    return ok(
        {
            "items": sorted(items, key=lambda x: x["days_inactive"], reverse=True),
            "summary": {
                "healthy": healthy,
                "at_risk": at_risk,
                "churned": churned,
                "revenue_at_risk_30d": round(revenue_at_risk, 2),
                "pending_fees_at_risk": round(
                    sum(i["pending_fees"] for i in items if i["risk_level"] != "low"), 2
                ),
            },
        }
    )


# ── Tiers parked (no subscriptions). Reinstated the day we bill by tier: ──
# @router.get(
#     "/analytics/tier-distribution",
#     dependencies=[Depends(require_platform_permission("platform:*"))],
# )
async def _tier_distribution_disabled(ctx: PlatformDep):  # noqa: F841 - kept for reference
    """Tier mix + who is pressing against their limits — no subscription math."""
    from app.tenancy.models import Tenant, TenantTierProjection
    from app.catalog.models import Product
    from app.identity.models import User

    user_stats_sq = (
        select(
            User.tenant_id.label("tenant_id"),
            func.count(User.id).label("users"),
        )
        .group_by(User.tenant_id)
        .subquery()
    )
    product_stats_sq = (
        select(
            Product.tenant_id.label("tenant_id"),
            func.count(Product.id).label("products"),
        )
        .group_by(Product.tenant_id)
        .subquery()
    )

    rows = (
        await ctx.session.execute(
            select(
                Tenant.id,
                Tenant.tier,
                Tenant.business_name,
                func.coalesce(user_stats_sq.c.users, 0).label("users"),
                func.coalesce(product_stats_sq.c.products, 0).label("products"),
            )
            .outerjoin(user_stats_sq, user_stats_sq.c.tenant_id == Tenant.id)
            .outerjoin(product_stats_sq, product_stats_sq.c.tenant_id == Tenant.id)
            .where(Tenant.status == "active")
        )
    ).all()

    tier_limits = {
        "starter": {"products": 100, "users": 2},
        "growth": {"products": 1000, "users": 10},
        "enterprise": {"products": 10000, "users": 100},
    }
    next_tier = {"starter": "growth", "growth": "enterprise", "enterprise": "enterprise"}

    total_tenants = len(rows)
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r.tier, []).append(r)

    items = []
    upgrade_candidates = []
    for tier_name, tenants_in_tier in grouped.items():
        cnt = len(tenants_in_tier)
        pct = round(cnt / total_tenants * 100, 1) if total_tenants else 0
        avg_users = round(sum(r.users for r in tenants_in_tier) / cnt, 1)
        avg_products = round(sum(r.products for r in tenants_in_tier) / cnt, 1)
        items.append(
            {
                "tier": tier_name,
                "count": cnt,
                "percentage": pct,
                "avg_users": avg_users,
                "avg_products": avg_products,
            }
        )

        limits = tier_limits.get(tier_name)
        if limits:
            for r in tenants_in_tier:
                reasons = []
                if r.users >= limits["users"]:
                    reasons.append(f"At user limit ({r.users}/{limits['users']})")
                if r.products >= limits["products"]:
                    reasons.append(f"At product limit ({r.products}/{limits['products']})")
                if reasons:
                    upgrade_candidates.append(
                        {
                            "tenant_id": str(r.id),
                            "business_name": r.business_name,
                            "current_tier": tier_name,
                            "suggested_tier": next_tier.get(tier_name, tier_name),
                            "reason": "; ".join(reasons),
                        }
                    )

    # Preserve tier ordering even when some tiers have no active tenants.
    order = {t: i for i, t in enumerate(tier_limits)}
    items.sort(key=lambda i: order.get(i["tier"], 99))

    return ok(
        {
            "items": items,
            "upgrade_candidates": upgrade_candidates,
        }
    )


@router.get(
    "/subaccounts",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def list_all_subaccounts(ctx: PlatformDep):
    from app.common import bridge

    subs = await bridge.list_all_subaccounts()
    return ok({"items": subs, "total": len(subs)})


@router.get(
    "/subaccounts/{subaccount_code}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def get_subaccount_by_code(subaccount_code: str, ctx: PlatformDep):
    from app.common import bridge

    sub = await bridge.get_subaccount_by_code(subaccount_code=subaccount_code)
    if not sub:
        raise HTTPException(status_code=404, detail="Subaccount not found")
    return ok(sub)


# --- Platform Commissions ---


class CommissionCreate(BaseModel):
    label: str
    fee_type: str = FeeType.FLAT.value
    amount: float
    cash_amount: float | None = None
    min_threshold: float = 0
    max_pending_balance: float = 1000
    document_fee: float = 0


class CommissionUpdate(BaseModel):
    label: str | None = None
    fee_type: str | None = None
    amount: float | None = None
    cash_amount: float | None = None
    min_threshold: float | None = None
    max_pending_balance: float | None = None
    document_fee: float | None = None


def _get_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/commissions",
    status_code=201,
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def create_commission(payload: CommissionCreate, request: Request, ctx: PlatformDep):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformCommission

    assert_platform_role(ctx, "admin")

    if payload.fee_type not in [e.value for e in FeeType]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid fee_type. Must be one of: {[e.value for e in FeeType]}",
        )

    commission = PlatformCommission(
        label=payload.label,
        fee_type=payload.fee_type,
        amount=payload.amount,
        cash_amount=payload.cash_amount,
        min_threshold=payload.min_threshold,
        max_pending_balance=payload.max_pending_balance,
        document_fee=payload.document_fee,
    )
    ctx.session.add(commission)
    await ctx.session.flush()

    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="commission.create",
        resource="commission",
        resource_id=str(commission.id),
        details={
            "label": payload.label,
            "fee_type": payload.fee_type,
            "amount": payload.amount,
            "cash_amount": payload.cash_amount,
            "min_threshold": payload.min_threshold,
            "max_pending_balance": payload.max_pending_balance,
            "document_fee": payload.document_fee,
        },
        ip_address=_get_client_ip(request),
    )

    return ok(
        {
            "id": str(commission.id),
            "label": commission.label,
            "fee_type": commission.fee_type,
            "amount": float(commission.amount),
            "cash_amount": (
                float(commission.cash_amount) if commission.cash_amount is not None else None
            ),
            "min_threshold": float(commission.min_threshold),
            "max_pending_balance": float(commission.max_pending_balance),
            "document_fee": float(commission.document_fee) if commission.document_fee is not None else 0,
            "created_at": commission.created_at.isoformat(),
            "updated_at": commission.updated_at.isoformat(),
        }
    )


@router.get(
    "/contact/messages",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def contact_inbox(
    ctx: PlatformDep, page: int = 1, page_size: int = 50, status_filter: str | None = None
):
    from app.contact.routes import _message_as_dict, platform_list_messages

    items, total = await platform_list_messages(
        ctx.session, page=page, page_size=page_size, status_filter=status_filter
    )
    return paginated(
        [_message_as_dict(m) for m in items], total=total, page=page, page_size=page_size
    )


@router.patch(
    "/contact/messages/{message_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def contact_mark(message_id: str, payload: ContactStatusRequest, ctx: PlatformDep):
    from app.contact import routes as contact_routes

    assert_platform_role(ctx, "support")

    row = await contact_routes.platform_update_message_status(
        ctx.session, message_id, payload.status
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return ok(contact_routes._message_as_dict(row))


@router.get(
    "/waitlist/signups",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def waitlist_signups(
    ctx: PlatformDep, page: int = 1, page_size: int = 50, status_filter: str | None = None
):
    from app.waitlist.routes import _signup_as_dict, platform_list_signups

    items, total = await platform_list_signups(
        ctx.session, page=page, page_size=page_size, status_filter=status_filter
    )
    return paginated(
        [_signup_as_dict(s) for s in items], total=total, page=page, page_size=page_size
    )


@router.patch(
    "/waitlist/signups/{signup_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def waitlist_mark(signup_id: str, payload: WaitlistStatusRequest, ctx: PlatformDep):
    from app.waitlist import routes as waitlist_routes

    assert_platform_role(ctx, "support")

    try:
        row = await waitlist_routes.platform_update_signup_status(
            ctx.session, signup_id, payload.status
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail="Signup not found")
    return ok(waitlist_routes._signup_as_dict(row))


@router.get(
    "/commissions",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def list_commissions(ctx: PlatformDep):
    from app.platform.models import PlatformCommission

    result = await ctx.session.execute(
        select(PlatformCommission).order_by(desc(PlatformCommission.created_at))
    )
    items = result.scalars().all()
    return paginated(
        [
            {
                "id": str(c.id),
                "label": c.label,
                "fee_type": c.fee_type,
                "amount": float(c.amount),
                "cash_amount": float(c.cash_amount) if c.cash_amount is not None else None,
                "min_threshold": float(c.min_threshold),
                "max_pending_balance": float(c.max_pending_balance),
                "document_fee": float(c.document_fee) if c.document_fee is not None else 0,
                "created_at": c.created_at.isoformat(),
                "updated_at": c.updated_at.isoformat(),
            }
            for c in items
        ],
        total=len(items),
        page=1,
        page_size=len(items),
    )


@router.get(
    "/commissions/{commission_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def get_commission(commission_id: str, ctx: PlatformDep):
    from app.platform.models import PlatformCommission

    result = await ctx.session.execute(
        select(PlatformCommission).where(PlatformCommission.id == commission_id)
    )
    commission = result.scalar_one_or_none()
    if not commission:
        raise HTTPException(status_code=404, detail="Commission not found")

    return ok(
        {
            "id": str(commission.id),
            "label": commission.label,
            "fee_type": commission.fee_type,
            "amount": float(commission.amount),
            "cash_amount": (
                float(commission.cash_amount) if commission.cash_amount is not None else None
            ),
            "min_threshold": float(commission.min_threshold),
            "max_pending_balance": float(commission.max_pending_balance),
            "document_fee": float(commission.document_fee) if commission.document_fee is not None else 0,
            "created_at": commission.created_at.isoformat(),
            "updated_at": commission.updated_at.isoformat(),
        }
    )


@router.patch(
    "/commissions/{commission_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def update_commission(
    commission_id: str, payload: CommissionUpdate, request: Request, ctx: PlatformDep
):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformCommission

    assert_platform_role(ctx, "admin")

    result = await ctx.session.execute(
        select(PlatformCommission).where(PlatformCommission.id == commission_id)
    )
    commission = result.scalar_one_or_none()
    if not commission:
        raise HTTPException(status_code=404, detail="Commission not found")

    changes = {}
    if payload.label is not None:
        changes["label"] = payload.label
        commission.label = payload.label
    if payload.fee_type is not None:
        if payload.fee_type not in [e.value for e in FeeType]:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid fee_type. Must be one of: {[e.value for e in FeeType]}",
            )
        changes["fee_type"] = payload.fee_type
        commission.fee_type = payload.fee_type
    if payload.amount is not None:
        changes["amount"] = payload.amount
        commission.amount = payload.amount
    if payload.cash_amount is not None:
        changes["cash_amount"] = payload.cash_amount
        commission.cash_amount = payload.cash_amount
    if payload.min_threshold is not None:
        changes["min_threshold"] = payload.min_threshold
        commission.min_threshold = payload.min_threshold
    if payload.max_pending_balance is not None:
        changes["max_pending_balance"] = payload.max_pending_balance
        commission.max_pending_balance = payload.max_pending_balance
    if payload.document_fee is not None:
        changes["document_fee"] = payload.document_fee
        commission.document_fee = payload.document_fee

    await ctx.session.flush()

    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="commission.update",
        resource="commission",
        resource_id=str(commission.id),
        details=changes,
        ip_address=_get_client_ip(request),
    )

    return ok(
        {
            "id": str(commission.id),
            "label": commission.label,
            "fee_type": commission.fee_type,
            "amount": float(commission.amount),
            "cash_amount": (
                float(commission.cash_amount) if commission.cash_amount is not None else None
            ),
            "min_threshold": float(commission.min_threshold),
            "max_pending_balance": float(commission.max_pending_balance),
            "document_fee": float(commission.document_fee) if commission.document_fee is not None else 0,
            "created_at": commission.created_at.isoformat(),
            "updated_at": commission.updated_at.isoformat(),
        }
    )


@router.delete(
    "/commissions/{commission_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def delete_commission(commission_id: str, request: Request, ctx: PlatformDep):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformCommission

    assert_platform_role(ctx, "admin")

    result = await ctx.session.execute(
        select(PlatformCommission).where(PlatformCommission.id == commission_id)
    )
    commission = result.scalar_one_or_none()
    if not commission:
        raise HTTPException(status_code=404, detail="Commission not found")

    deleted_label = commission.label
    deleted_fee_type = commission.fee_type
    deleted_amount = float(commission.amount)
    deleted_cash_amount = (
        float(commission.cash_amount) if commission.cash_amount is not None else None
    )
    deleted_min_threshold = float(commission.min_threshold)
    deleted_max_pending_balance = float(commission.max_pending_balance)

    await ctx.session.delete(commission)
    await ctx.session.flush()

    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="commission.delete",
        resource="commission",
        resource_id=commission_id,
        details={
            "label": deleted_label,
            "fee_type": deleted_fee_type,
            "amount": deleted_amount,
            "cash_amount": deleted_cash_amount,
            "min_threshold": deleted_min_threshold,
            "max_pending_balance": deleted_max_pending_balance,
        },
        ip_address=_get_client_ip(request),
    )

    return {"success": True, "deleted": commission_id}


# --- Fee Ledger ---


class ClearDebtRequest(BaseModel):
    tenant_id: str
    amount: float | None = None


@router.get(
    "/fee-ledger",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def list_fee_ledger(
    ctx: PlatformDep,
    tenant_id: str | None = None,
    status: str | None = None,
    payment_method: str | None = None,
    fee_category: str | None = None,
    search: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    format: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    """Paginated fee ledger with filters, per-status summary and CSV export."""
    from app.platform.exporting import csv_response, parse_iso_date
    from app.platform.models import PlatformFeeLedger as Fee
    from app.tenancy.models import Tenant

    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    d_from = parse_iso_date(from_date, "from_date")
    d_to = parse_iso_date(to_date, "to_date")

    conditions = []
    if tenant_id:
        conditions.append(Fee.tenant_id == tenant_id)
    if status:
        conditions.append(Fee.status == status)
    if payment_method:
        conditions.append(Fee.payment_method == payment_method)
    if fee_category:
        conditions.append(Fee.fee_category == fee_category)
    if d_from:
        conditions.append(cast(Fee.created_at, Date) >= d_from)
    if d_to:
        conditions.append(cast(Fee.created_at, Date) <= d_to)

    base = (
        select(
            Fee.id,
            Fee.tenant_id,
            Fee.sale_id,
            Fee.document_id,
            Fee.fee_category,
            Fee.amount,
            Fee.fee_type,
            Fee.rate,
            Fee.payment_method,
            Fee.status,
            Fee.settled_at,
            Fee.created_at,
            Tenant.business_name.label("business_name"),
            Tenant.slug.label("tenant_slug"),
        )
        .select_from(Fee)
        .outerjoin(Tenant, Tenant.id == Fee.tenant_id)
    )
    count_q = select(func.count(Fee.id)).select_from(Fee)
    summary_q = select(
        func.coalesce(func.sum(case((Fee.status == "pending", Fee.amount), else_=0)), 0).label(
            "pending_total"
        ),
        func.coalesce(func.sum(case((Fee.status == "deducted", Fee.amount), else_=0)), 0).label(
            "settled_total"
        ),
        func.count(Fee.id).label("total_count"),
        func.count(Fee.id).filter(Fee.status == "pending").label("pending_count"),
    ).select_from(Fee)

    if search:
        like = f"%{search}%"
        matching_tenants = select(Tenant.id).where(
            Tenant.business_name.ilike(like) | Tenant.slug.ilike(like)
        )
        conditions.append(
            Fee.tenant_id.in_(matching_tenants)
            | Fee.fee_type.ilike(like)
            | Fee.payment_method.ilike(like)
        )

    for condition in conditions:
        base = base.where(condition)
        count_q = count_q.where(condition)
        summary_q = summary_q.where(condition)

    total = (await ctx.session.execute(count_q)).scalar() or 0
    summary = (await ctx.session.execute(summary_q)).one()
    rows = (
        await ctx.session.execute(
            base.order_by(desc(Fee.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    if format == "csv":
        return csv_response(
            "platform-fee-ledger.csv",
            [
                "created_at",
                "tenant_id",
                "business_name",
                "sale_id",
                "document_id",
                "fee_category",
                "fee_type",
                "rate",
                "amount",
                "payment_method",
                "status",
                "settled_at",
            ],
            [
                {
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "tenant_id": str(r.tenant_id),
                    "business_name": r.business_name,
                    "sale_id": str(r.sale_id) if r.sale_id else None,
                    "document_id": str(r.document_id) if r.document_id else None,
                    "fee_category": r.fee_category,
                    "fee_type": r.fee_type,
                    "rate": float(r.rate),
                    "amount": float(r.amount),
                    "payment_method": r.payment_method,
                    "status": r.status,
                    "settled_at": r.settled_at.isoformat() if r.settled_at else None,
                }
                for r in rows
            ],
        )

    return ok(
        {
            "items": [
                {
                    "id": str(r.id),
                    "tenant_id": str(r.tenant_id),
                    "business_name": r.business_name,
                    "tenant_slug": r.tenant_slug,
                    "sale_id": str(r.sale_id) if r.sale_id else None,
                    "document_id": str(r.document_id) if r.document_id else None,
                    "fee_category": r.fee_category,
                    "amount": float(r.amount),
                    "fee_type": r.fee_type,
                    "rate": float(r.rate),
                    "payment_method": r.payment_method,
                    "status": r.status,
                    "settled_at": r.settled_at.isoformat() if r.settled_at else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
            "summary": {
                "total_count": int(summary.total_count or 0),
                "pending_count": int(summary.pending_count or 0),
                "pending_total": float(summary.pending_total or 0),
                "settled_total": float(summary.settled_total or 0),
            },
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }
    )


@router.post(
    "/fee-ledger/clear",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def clear_debt(payload: ClearDebtRequest, request: Request, ctx: PlatformDep):
    from datetime import UTC, datetime

    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformFeeLedger

    assert_platform_role(ctx, "admin")

    query = (
        select(PlatformFeeLedger)
        .where(
            PlatformFeeLedger.tenant_id == payload.tenant_id,
            PlatformFeeLedger.status == "pending",
        )
        .order_by(PlatformFeeLedger.created_at)
    )

    result = await ctx.session.execute(query)
    entries = result.scalars().all()

    if not entries:
        raise HTTPException(status_code=404, detail="No pending debts found for this tenant")

    remaining = payload.amount
    cleared = []
    for entry in entries:
        if remaining is not None and remaining <= 0:
            break

        if remaining is not None:
            deduct = min(float(entry.amount), remaining)
            entry.amount = round(float(entry.amount) - deduct, 2)
            remaining = round(remaining - deduct, 2)
            if entry.amount <= 0:
                entry.status = "deducted"
                entry.settled_at = datetime.now(UTC)
        else:
            entry.status = "deducted"
            entry.settled_at = datetime.now(UTC)

        cleared.append(
            {
                "id": str(entry.id),
                "amount_cleared": deduct if remaining is not None else float(entry.amount),
                "remaining_amount": entry.amount,
                "status": entry.status,
            }
        )

    await ctx.session.flush()

    total_cleared = sum(c["amount_cleared"] for c in cleared)
    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="fee_ledger.clear",
        resource="fee_ledger",
        resource_id=payload.tenant_id,
        details={
            "tenant_id": payload.tenant_id,
            "total_cleared": total_cleared,
            "entries_cleared": len(cleared),
        },
        ip_address=_get_client_ip(request),
    )

    return ok(
        {
            "tenant_id": payload.tenant_id,
            "total_cleared": total_cleared,
            "entries": cleared,
        }
    )


# ── Platform settings ─────────────────────────────────────────────


class PlatformSettingOut(BaseModel):
    key: str
    value: str
    description: str | None = None


class PlatformSettingUpdate(BaseModel):
    key: str
    value: str
    description: str | None = None


@router.get(
    "/settings",
    response_model=list[PlatformSettingOut],
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def list_platform_settings(ctx: PlatformDep):
    result = await ctx.session.execute(
        select(PlatformSetting).order_by(PlatformSetting.key)
    )
    return result.scalars().all()


@router.patch(
    "/settings",
    response_model=list[PlatformSettingOut],
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def update_platform_settings(
    payload: list[PlatformSettingUpdate],
    ctx: PlatformDep,
    request: Request,
):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformSettingHistory

    updated: list[PlatformSetting] = []
    changes: dict[str, dict] = {}
    for item in payload:
        key = (item.key or "").strip()
        if not key:
            raise HTTPException(status_code=400, detail="Setting key cannot be empty")
        try:
            json.loads(item.value)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail=f"Setting '{key}' must be valid JSON (quote plain strings)",
            )

        setting = await ctx.session.scalar(
            select(PlatformSetting).where(PlatformSetting.key == key)
        )
        if setting:
            if setting.value != item.value:
                ctx.session.add(
                    PlatformSettingHistory(
                        key=key,
                        old_value=setting.value,
                        new_value=item.value,
                        admin_id=ctx.user.admin_id,
                    )
                )
                changes[key] = {"from": setting.value, "to": item.value}
            setting.value = item.value
            setting.description = item.description
        else:
            setting = PlatformSetting(
                key=key,
                value=item.value,
                description=item.description,
            )
            ctx.session.add(setting)
            changes[key] = {"from": None, "to": item.value}
        updated.append(setting)

    await ctx.session.flush()

    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="platform_settings.update",
        resource="platform_settings",
        details={"keys": [item.key for item in payload], "changes": changes},
        ip_address=_get_client_ip(request),
    )

    return updated


@router.get(
    "/settings/history",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def platform_settings_history(
    ctx: PlatformDep,
    key: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    from app.platform.models import PlatformSettingHistory

    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    query = select(PlatformSettingHistory).order_by(desc(PlatformSettingHistory.created_at))
    count_q = select(func.count(PlatformSettingHistory.id))
    if key:
        query = query.where(PlatformSettingHistory.key == key)
        count_q = count_q.where(PlatformSettingHistory.key == key)
    total = (await ctx.session.execute(count_q)).scalar() or 0
    rows = (
        await ctx.session.execute(
            query.offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return ok(
        {
            "items": [
                {
                    "id": str(r.id),
                    "key": r.key,
                    "old_value": r.old_value,
                    "new_value": r.new_value,
                    "admin_id": str(r.admin_id) if r.admin_id else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }
    )
