from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Date, cast, desc, func, select, text

from app.core.platform_deps import PlatformDep, require_platform_permission
from app.core.ratelimit import auth_rate_limit
from app.core.responses import ok, paginated
from app.platform.models import FeeType, PlatformAdmin
from app.platform.auth import platform_login, platform_refresh, PlatformAuthError

router = APIRouter(prefix="/platform", tags=["Platform"])


class PlatformLoginRequest(BaseModel):
    email: str
    password: str


class PlatformRefreshRequest(BaseModel):
    refresh_token: str


class TenantUpdateRequest(BaseModel):
    status: str | None = None
    tier: str | None = None
    business_name: str | None = None


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
                )
                return ok(result)
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
                    "last_login_at": admin.last_login_at.isoformat()
                    if admin.last_login_at
                    else None,
                }
            )


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

    result = await bridge_create_tenant(
        business_name=payload.business_name,
        business_email=payload.business_email,
        owner_name=payload.owner_name,
        owner_email=payload.owner_email,
        owner_phone=payload.owner_phone,
        owner_password_hash=hash_password(payload.password),
        actor_id=ctx.user.admin_id,
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
    from app.tenancy.models import Tenant

    query = select(Tenant)
    count_query = select(func.count(Tenant.id))

    if status_filter:
        query = query.where(Tenant.status == status_filter)
        count_query = count_query.where(Tenant.status == status_filter)
    if tier:
        query = query.where(Tenant.tier == tier)
        count_query = count_query.where(Tenant.tier == tier)
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
    tenants = result.scalars().all()

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
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in tenants
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
    tiers = await ctx.session.execute(
        select(Tenant.tier, func.count(Tenant.id)).group_by(Tenant.tier)
    )

    return ok(
        {
            "total": total.scalar(),
            "active": active.scalar(),
            "suspended": suspended.scalar(),
            "by_tier": {row[0]: row[1] for row in tiers.all()},
        }
    )


@router.get(
    "/tenants/{tenant_id}", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def get_tenant(tenant_id: str, ctx: PlatformDep):
    from app.tenancy.models import Tenant
    from app.identity.models import User

    result = await ctx.session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    user_count = await ctx.session.execute(
        select(func.count(User.id)).where(User.tenant_id == tenant.id)
    )

    return ok(
        {
            "id": str(tenant.id),
            "slug": tenant.slug,
            "subdomain": tenant.subdomain,
            "business_name": tenant.business_name,
            "business_email": tenant.business_email,
            "owner_name": tenant.owner_name,
            "owner_email": tenant.owner_email,
            "owner_phone": tenant.owner_phone,
            "tier": tenant.tier,
            "status": tenant.status,
            "user_count": user_count.scalar(),
            "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
            "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
        }
    )


@router.patch(
    "/tenants/{tenant_id}", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def update_tenant(tenant_id: str, payload: TenantUpdateRequest, ctx: PlatformDep):
    from app.tenancy.models import Tenant

    result = await ctx.session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if payload.status is not None:
        if payload.status not in ("active", "suspended", "cancelled"):
            raise HTTPException(status_code=400, detail="Invalid status")
        tenant.status = payload.status
    if payload.tier is not None:
        if payload.tier not in ("starter", "growth", "enterprise"):
            raise HTTPException(status_code=400, detail="Invalid tier")
        tenant.tier = payload.tier
    if payload.business_name is not None:
        tenant.business_name = payload.business_name

    await ctx.session.flush()

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
async def delete_tenant(tenant_id: str, ctx: PlatformDep):
    from app.tenancy.models import Tenant

    result = await ctx.session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    await ctx.session.delete(tenant)
    await ctx.session.flush()

    return {"success": True, "deleted": tenant_id}


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
    page: int = 1,
    page_size: int = 50,
):
    from app.sales.models import Sale

    count_q = select(func.count(Sale.id)).where(Sale.tenant_id == tenant_id)
    total = (await ctx.session.execute(count_q)).scalar()

    query = (
        select(Sale)
        .where(Sale.tenant_id == tenant_id)
        .order_by(Sale.created_at.desc())
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
async def tenant_overview(tenant_id: str, ctx: PlatformDep):
    from app.identity.models import User
    from app.catalog.models import Product
    from app.sales.models import Sale
    from app.payments.models import Payment
    from app.documents.models import Document
    from app.tenancy.models import Tenant

    tenant_r = await ctx.session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_r.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    user_count = (
        await ctx.session.execute(select(func.count(User.id)).where(User.tenant_id == tenant_id))
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

    payment_count = (
        await ctx.session.execute(
            select(func.count(Payment.id)).where(Payment.tenant_id == tenant_id)
        )
    ).scalar()

    doc_count = (
        await ctx.session.execute(
            select(func.count(Document.id)).where(Document.tenant_id == tenant_id)
        )
    ).scalar()

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
            },
            "revenue": float(total_revenue),
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
    from app.sales.models import Sale
    from app.tenancy.models import Tenant
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    if not from_date:
        from_date = (now - timedelta(days=30)).date().isoformat()
    if not to_date:
        to_date = now.date().isoformat()

    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

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

    sort_col = agg.c.total_revenue if sort == "revenue" else agg.c.total_transactions

    rows = await ctx.session.execute(
        select(
            Tenant.id.label("tenant_id"),
            Tenant.business_name.label("business_name"),
            Tenant.slug.label("slug"),
            Tenant.tier.label("tier"),
            agg.c.total_revenue.label("total_revenue"),
            agg.c.total_transactions.label("total_transactions"),
        )
        .join(agg, agg.c.tenant_id == Tenant.id)
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
                "total_revenue": float(r["total_revenue"]),
                "total_transactions": int(r["total_transactions"]),
            }
        )

    return ok({"items": items})


@router.get(
    "/analytics/platform-revenue", dependencies=[Depends(require_platform_permission("platform:*"))]
)
async def analytics_platform_revenue(
    ctx: PlatformDep,
    from_date: str | None = None,
    to_date: str | None = None,
    group_by: str = "month",
):
    from app.tenancy.models import Tenant
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    if not from_date:
        from_date = (now - timedelta(days=365)).date().isoformat()
    if not to_date:
        to_date = now.date().isoformat()

    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    tier_pricing = {"starter": 15000, "growth": 45000, "enterprise": 150000}

    tiers = (
        await ctx.session.execute(
            select(
                Tenant.tier,
                func.count(Tenant.id).label("count"),
            )
            .where(Tenant.status == "active")
            .group_by(Tenant.tier)
        )
    ).all()

    by_tier = {}
    total_mrr = 0
    for tier_name, cnt in tiers:
        price = tier_pricing.get(tier_name, 0)
        mrr = price * cnt
        by_tier[tier_name] = {"tenants": cnt, "mrr_per": price, "total_mrr": mrr}
        total_mrr += mrr

    trend_rows = await ctx.session.execute(
        select(
            func.date_trunc(group_by, Tenant.created_at).label("period"),
            func.count(Tenant.id).label("new_tenants"),
        )
        .where(
            cast(Tenant.created_at, Date) >= d_from,
            cast(Tenant.created_at, Date) <= d_to,
        )
        .group_by(text("1"))
        .order_by(text("1"))
    )

    trend = []
    running_mrr = 0
    for r in trend_rows.mappings():
        new_cnt = int(r["new_tenants"])
        new_mrr = new_cnt * tier_pricing.get("starter", 15000)
        running_mrr += new_mrr
        trend.append(
            {
                "period": r["period"].isoformat() if r["period"] else None,
                "mrr": running_mrr,
                "new": new_mrr,
                "churn": 0,
            }
        )

    return ok(
        {
            "summary": {"mrr": total_mrr, "by_tier": by_tier},
            "trend": trend,
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
    from app.common.models.audit import AuditEvent
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    if not from_date:
        from_date = (now - timedelta(days=7)).date().isoformat()
    if not to_date:
        to_date = now.date().isoformat()

    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

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
            "total": int(r[1]),
            "errors": int(r[2]),
            "error_rate": round(int(r[2]) / int(r[1]) * 100, 1) if int(r[1]) else 0,
        }
        for r in error_trend
    ]

    return ok(
        {
            "summary": {
                "total_requests": total,
                "error_rate_pct": round(errors / total * 100, 1) if total else 0,
            },
            "by_status": status_breakdown,
            "error_trend": error_trend_data,
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
    from app.tenancy.models import Tenant
    from app.sales.models import Sale
    from app.identity.models import User
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=inactive_days)
    at_risk_cutoff = now - timedelta(days=7)

    tenants = await ctx.session.execute(select(Tenant).where(Tenant.status == "active"))
    items = []
    healthy = at_risk = churned = 0
    for tenant in tenants.scalars():
        last_sale = (
            await ctx.session.execute(
                select(func.max(Sale.created_at)).where(
                    Sale.tenant_id == tenant.id,
                    Sale.status != "voided",
                )
            )
        ).scalar()

        last_login = (
            await ctx.session.execute(
                select(func.max(User.last_login_at)).where(
                    User.tenant_id == tenant.id,
                    User.status == "active",
                )
            )
        ).scalar()

        days_inactive = (now - last_sale).days if last_sale else 999
        active_user_count = (
            await ctx.session.execute(
                select(func.count(User.id)).where(
                    User.tenant_id == tenant.id,
                    User.status == "active",
                    User.last_login_at >= cutoff,
                )
            )
        ).scalar() or 0

        total_users = (
            await ctx.session.execute(
                select(func.count(User.id)).where(User.tenant_id == tenant.id)
            )
        ).scalar() or 0

        if days_inactive <= 7:
            risk = "low"
            healthy += 1
        elif days_inactive <= inactive_days:
            risk = "medium"
            at_risk += 1
        else:
            risk = "high"
            churned += 1

        items.append(
            {
                "tenant_id": str(tenant.id),
                "business_name": tenant.business_name,
                "slug": tenant.slug,
                "tier": tenant.tier,
                "status": tenant.status,
                "last_sale": last_sale.date().isoformat() if last_sale else None,
                "days_inactive": days_inactive,
                "last_user_login": last_login.date().isoformat() if last_login else None,
                "risk_level": risk,
                "total_users": total_users,
                "active_users": active_user_count,
            }
        )

    return ok(
        {
            "items": sorted(items, key=lambda x: x["days_inactive"], reverse=True),
            "summary": {"healthy": healthy, "at_risk": at_risk, "churned": churned},
        }
    )


@router.get(
    "/analytics/tier-distribution",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def analytics_tier_distribution(ctx: PlatformDep):
    from app.tenancy.models import Tenant, TenantTierProjection
    from app.catalog.models import Product
    from app.identity.models import User

    tier_pricing = {"starter": 15000, "growth": 45000, "enterprise": 150000}

    tiers = (
        await ctx.session.execute(
            select(
                Tenant.tier,
                func.count(Tenant.id).label("count"),
            )
            .where(Tenant.status == "active")
            .group_by(Tenant.tier)
        )
    ).all()

    total_tenants = sum(r[1] for r in tiers)
    items = []
    upgrade_candidates = []

    for tier_name, cnt in tiers:
        pct = round(cnt / total_tenants * 100, 1) if total_tenants else 0
        mrr = tier_pricing.get(tier_name, 0) * cnt

        tenants_in_tier = await ctx.session.execute(
            select(Tenant.id).where(Tenant.tier == tier_name, Tenant.status == "active")
        )
        tids = [r[0] for r in tenants_in_tier.all()]

        avg_users = 0
        avg_products = 0
        if tids:
            user_count = (
                await ctx.session.execute(
                    select(func.count(User.id)).where(User.tenant_id.in_(tids))
                )
            ).scalar() or 0
            avg_users = round(user_count / len(tids), 1)

            prod_count = (
                await ctx.session.execute(
                    select(func.count(Product.id)).where(Product.tenant_id.in_(tids))
                )
            ).scalar() or 0
            avg_products = round(prod_count / len(tids), 1)

        items.append(
            {
                "tier": tier_name,
                "count": cnt,
                "percentage": pct,
                "mrr": mrr,
                "avg_users": avg_users,
                "avg_products": avg_products,
            }
        )

        tier_limits = {
            "starter": {"products": 100, "users": 2},
            "growth": {"products": 1000, "users": 10},
            "enterprise": {"products": 10000, "users": 100},
        }
        limits = tier_limits.get(tier_name, {})
        if tids and limits:
            for tid in tids:
                tu = (
                    await ctx.session.execute(
                        select(func.count(User.id)).where(User.tenant_id == tid)
                    )
                ).scalar() or 0
                tp = (
                    await ctx.session.execute(
                        select(func.count(Product.id)).where(Product.tenant_id == tid)
                    )
                ).scalar() or 0

                reasons = []
                if tu >= limits["users"]:
                    reasons.append(f"At user limit ({tu}/{limits['users']})")
                if tp >= limits["products"]:
                    reasons.append(f"At product limit ({tp}/{limits['products']})")

                if reasons:
                    tenant_info = (
                        await ctx.session.execute(
                            select(Tenant.business_name).where(Tenant.id == tid)
                        )
                    ).scalar()
                    next_tier = {
                        "starter": "growth",
                        "growth": "enterprise",
                        "enterprise": "enterprise",
                    }
                    upgrade_candidates.append(
                        {
                            "tenant_id": str(tid),
                            "business_name": tenant_info or "",
                            "current_tier": tier_name,
                            "suggested_tier": next_tier.get(tier_name, tier_name),
                            "reason": "; ".join(reasons),
                        }
                    )

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
    min_threshold: float = 0
    max_pending_balance: float = 1000


class CommissionUpdate(BaseModel):
    label: str | None = None
    fee_type: str | None = None
    amount: float | None = None
    min_threshold: float | None = None
    max_pending_balance: float | None = None


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

    if payload.fee_type not in [e.value for e in FeeType]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid fee_type. Must be one of: {[e.value for e in FeeType]}",
        )

    commission = PlatformCommission(
        label=payload.label,
        fee_type=payload.fee_type,
        amount=payload.amount,
        min_threshold=payload.min_threshold,
        max_pending_balance=payload.max_pending_balance,
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
            "min_threshold": payload.min_threshold,
            "max_pending_balance": payload.max_pending_balance,
        },
        ip_address=_get_client_ip(request),
    )

    return ok(
        {
            "id": str(commission.id),
            "label": commission.label,
            "fee_type": commission.fee_type,
            "amount": float(commission.amount),
            "min_threshold": float(commission.min_threshold),
            "max_pending_balance": float(commission.max_pending_balance),
            "created_at": commission.created_at.isoformat(),
            "updated_at": commission.updated_at.isoformat(),
        }
    )


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
                "min_threshold": float(c.min_threshold),
                "max_pending_balance": float(c.max_pending_balance),
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
            "min_threshold": float(commission.min_threshold),
            "max_pending_balance": float(commission.max_pending_balance),
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
    if payload.min_threshold is not None:
        changes["min_threshold"] = payload.min_threshold
        commission.min_threshold = payload.min_threshold
    if payload.max_pending_balance is not None:
        changes["max_pending_balance"] = payload.max_pending_balance
        commission.max_pending_balance = payload.max_pending_balance

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
            "min_threshold": float(commission.min_threshold),
            "max_pending_balance": float(commission.max_pending_balance),
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

    result = await ctx.session.execute(
        select(PlatformCommission).where(PlatformCommission.id == commission_id)
    )
    commission = result.scalar_one_or_none()
    if not commission:
        raise HTTPException(status_code=404, detail="Commission not found")

    deleted_label = commission.label
    deleted_fee_type = commission.fee_type
    deleted_amount = float(commission.amount)
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
    tenant_id: str | None = None,
    status: str | None = None,
    ctx: PlatformDep = None,
):
    from app.platform.models import PlatformFeeLedger

    query = select(PlatformFeeLedger).order_by(desc(PlatformFeeLedger.created_at))
    if tenant_id:
        query = query.where(PlatformFeeLedger.tenant_id == tenant_id)
    if status:
        query = query.where(PlatformFeeLedger.status == status)

    result = await ctx.session.execute(query)
    items = result.scalars().all()
    return ok(
        {
            "items": [
                {
                    "id": str(e.id),
                    "tenant_id": str(e.tenant_id),
                    "sale_id": str(e.sale_id),
                    "amount": float(e.amount),
                    "fee_type": e.fee_type,
                    "rate": float(e.rate),
                    "payment_method": e.payment_method,
                    "status": e.status,
                    "settled_at": e.settled_at.isoformat() if e.settled_at else None,
                    "created_at": e.created_at.isoformat(),
                }
                for e in items
            ],
            "total": len(items),
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
