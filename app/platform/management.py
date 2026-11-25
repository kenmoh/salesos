"""Platform tenant drill-downs, support tooling and exports.

This router adds the depth the admin UI needs beyond the original tenant
list: store-level product inventories, billing snapshots, internal notes and
tags, status history, audited impersonation, data requests, bulk actions and
CSV exports.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Date, and_, case, cast, desc, func, or_, select

from app.core.platform_deps import PlatformDep, require_platform_permission
from app.core.responses import ok
from app.platform.access import assert_platform_role
from app.platform.exporting import csv_response, parse_iso_date

router = APIRouter(prefix="/platform", tags=["Platform Management"])


# ── Small helpers ───────────────────────────────────────────────────


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _normalize_tag(tag: str) -> str:
    value = (tag or "").strip().lower().replace(" ", "-")
    if not value:
        raise HTTPException(status_code=400, detail="Tag cannot be empty")
    return value[:50]


async def _tenant_or_404(ctx: PlatformDep, tenant_id: str):
    from app.tenancy.models import Tenant

    tenant = (
        await ctx.session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


async def _store_or_404(ctx: PlatformDep, tenant_id: str, store_id: str):
    from app.stores.models import Store

    store = (
        await ctx.session.execute(
            select(Store).where(Store.id == store_id, Store.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail="Store not found for this tenant")
    return store


def _product_row(r) -> dict:
    """Map one (Product, StoreProduct, StockBalance, category_name) row."""
    product, store_product, stock, category_name = r
    available = (
        float(stock.qty or 0) - float(stock.reserved_qty or 0) if stock else 0.0
    )
    selling = float(store_product.selling_price or 0)
    cost = float(store_product.cost_price or 0)
    margin = round(((selling - cost) / selling) * 100, 1) if selling else 0.0
    reorder = int(store_product.reorder_point or 0)
    return {
        "product_id": str(product.id),
        "public_id": product.public_id,
        "name": store_product.name or product.name,
        "catalog_name": product.name,
        "sku": store_product.sku or product.sku,
        "barcode": product.barcode,
        "category": category_name,
        "unit": product.unit,
        "selling_price": selling,
        "catalog_price": float(product.selling_price or 0),
        "cost_price": cost,
        "margin_pct": margin,
        "tax_ids": product.tax_ids or [],
        "status": store_product.status,
        "catalog_status": product.status,
        "track_inventory": bool(product.track_inventory),
        "image_url": store_product.image_url or product.image_url,
        "stock": {
            "qty": float(stock.qty or 0) if stock else 0.0,
            "reserved": float(stock.reserved_qty or 0) if stock else 0.0,
            "available": available,
            "reorder_point": reorder,
            "min_stock_level": float(stock.min_stock_level or 0) if stock else 0.0,
            "unit_cost": float(stock.unit_cost or 0) if stock and stock.unit_cost else None,
            "low_stock": available <= reorder,
            "out_of_stock": available <= 0,
        },
        "expiry_date": product.expiry_date.isoformat() if product.expiry_date else None,
        "created_at": store_product.created_at.isoformat()
        if store_product.created_at
        else None,
        "updated_at": store_product.updated_at.isoformat()
        if store_product.updated_at
        else None,
    }


def _store_product_query(tenant_id: str, store_id: str):
    from app.catalog.models import Category, Product
    from app.inventory.models import StockBalance
    from app.stores.models import StoreProduct

    return (
        select(
            Product,
            StoreProduct,
            StockBalance,
            Category.name.label("category_name"),
        )
        .select_from(StoreProduct)
        .join(Product, Product.id == StoreProduct.product_id)
        .outerjoin(
            StockBalance,
            and_(
                StockBalance.store_id == StoreProduct.store_id,
                StockBalance.product_id == StoreProduct.product_id,
            ),
        )
        .outerjoin(Category, Category.id == Product.category_id)
        .where(
            StoreProduct.tenant_id == tenant_id,
            StoreProduct.store_id == store_id,
        )
    )


# ── Store inventory ─────────────────────────────────────────────────


@router.get(
    "/tenants/{tenant_id}/stores/{store_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def store_detail(tenant_id: str, store_id: str, ctx: PlatformDep, days: int = 30):
    """One store with inventory, staffing and sales context."""
    from app.identity.models import User
    from app.inventory.models import StockBalance
    from app.sales.models import Sale
    from app.stores.models import StoreProduct

    store = await _store_or_404(ctx, tenant_id, store_id)
    now = datetime.now(UTC)
    days = min(max(days, 1), 365)
    window_start = now - timedelta(days=days)

    available = func.coalesce(StockBalance.qty, 0) - func.coalesce(
        StockBalance.reserved_qty, 0
    )
    inventory = (
        await ctx.session.execute(
            select(
                func.count(StoreProduct.id).label("products"),
                func.count(StoreProduct.id)
                .filter(StoreProduct.status == "active")
                .label("active_products"),
                func.count(StoreProduct.id)
                .filter(available <= StoreProduct.reorder_point)
                .label("low_stock"),
                func.count(StoreProduct.id)
                .filter(available <= 0)
                .label("out_of_stock"),
                func.coalesce(
                    func.sum(available * StoreProduct.cost_price), 0
                ).label("stock_value"),
            )
            .select_from(StoreProduct)
            .outerjoin(
                StockBalance,
                and_(
                    StockBalance.store_id == StoreProduct.store_id,
                    StockBalance.product_id == StoreProduct.product_id,
                ),
            )
            .where(
                StoreProduct.tenant_id == tenant_id,
                StoreProduct.store_id == store_id,
            )
        )
    ).one()

    employees = (
        await ctx.session.execute(
            select(func.count(User.id)).where(
                User.tenant_id == tenant_id, User.store_id == store_id
            )
        )
    ).scalar() or 0
    sales = (
        await ctx.session.execute(
            select(
                func.count(Sale.id).label("lifetime_count"),
                func.coalesce(func.sum(Sale.total), 0).label("lifetime_revenue"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    Sale.created_at >= window_start,
                                    Sale.status != "voided",
                                ),
                                Sale.total,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("window_revenue"),
                func.max(Sale.created_at).label("last_sale_at"),
            ).where(Sale.tenant_id == tenant_id, Sale.store_id == store_id)
        )
    ).one()

    top_products = (
        await ctx.session.execute(
            _store_product_query(tenant_id, store_id)
            .order_by(desc(available * StoreProduct.cost_price))
            .limit(5)
        )
    ).all()

    return ok(
        {
            "store": {
                "id": str(store.id),
                "name": store.name,
                "address": store.address,
                "status": store.status,
                "is_warehouse": bool(store.is_warehouse),
                "tax_enabled": bool(store.tax_enabled),
                "created_at": store.created_at.isoformat() if store.created_at else None,
            },
            "inventory": {
                "products": int(inventory.products or 0),
                "active_products": int(inventory.active_products or 0),
                "low_stock": int(inventory.low_stock or 0),
                "out_of_stock": int(inventory.out_of_stock or 0),
                "stock_value": float(inventory.stock_value or 0),
            },
            "employees": int(employees),
            "sales": {
                "window_days": days,
                "lifetime_count": int(sales.lifetime_count or 0),
                "lifetime_revenue": float(sales.lifetime_revenue or 0),
                "window_revenue": float(sales.window_revenue or 0),
                "last_sale_at": sales.last_sale_at.isoformat()
                if sales.last_sale_at
                else None,
            },
            "top_products_by_stock_value": [
                {
                    "product_id": row[0].id and str(row[0].id),
                    "name": row[1].name,
                    "available": float(row[2].qty or 0) - float(row[2].reserved_qty or 0)
                    if row[2]
                    else 0.0,
                    "stock_value": round(
                        (float(row[2].qty or 0) - float(row[2].reserved_qty or 0))
                        * float(row[1].cost_price or 0),
                        2,
                    )
                    if row[2]
                    else 0.0,
                }
                for row in top_products
            ],
        }
    )


@router.get(
    "/tenants/{tenant_id}/stores/{store_id}/products",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def store_products(
    tenant_id: str,
    store_id: str,
    ctx: PlatformDep,
    search: str | None = None,
    status_filter: str | None = None,
    category_id: str | None = None,
    low_stock: bool = False,
    in_stock: bool = False,
    out_of_stock: bool = False,
    sort: str = "name",
    order: str = "asc",
    page: int = 1,
    page_size: int = 50,
):
    """Products carried by one store, with stock, pricing and filters."""
    from app.catalog.models import Product
    from app.inventory.models import StockBalance
    from app.stores.models import StoreProduct

    await _store_or_404(ctx, tenant_id, store_id)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    available = func.coalesce(StockBalance.qty, 0) - func.coalesce(
        StockBalance.reserved_qty, 0
    )

    conditions = [
        StoreProduct.tenant_id == tenant_id,
        StoreProduct.store_id == store_id,
    ]
    if search:
        like = f"%{search}%"
        conditions.append(
            StoreProduct.name.ilike(like)
            | StoreProduct.sku.ilike(like)
            | StoreProduct.product_id.in_(
                select(Product.id).where(
                    or_(
                        Product.name.ilike(like),
                        Product.sku.ilike(like),
                        Product.barcode.ilike(like),
                    )
                )
            )
        )
    if status_filter:
        conditions.append(StoreProduct.status == status_filter)
    if category_id:
        conditions.append(
            StoreProduct.product_id.in_(
                select(Product.id).where(Product.category_id == category_id)
            )
        )
    if low_stock:
        conditions.append(available <= StoreProduct.reorder_point)
    if in_stock:
        conditions.append(available > 0)
    if out_of_stock:
        conditions.append(available <= 0)

    sort_map = {
        "name": StoreProduct.name,
        "price": StoreProduct.selling_price,
        "cost": StoreProduct.cost_price,
        "stock": available,
        "created": StoreProduct.created_at,
        "updated": StoreProduct.updated_at,
    }
    sort_col = sort_map.get(sort, StoreProduct.name)
    order_by = desc(sort_col) if order == "desc" else sort_col.asc()

    query = _store_product_query(tenant_id, store_id)
    count_q = (
        select(func.count(StoreProduct.id))
        .select_from(StoreProduct)
        .outerjoin(
            StockBalance,
            and_(
                StockBalance.store_id == StoreProduct.store_id,
                StockBalance.product_id == StoreProduct.product_id,
            ),
        )
    )
    summary_q = select(
        func.count(StoreProduct.id).label("total"),
        func.count(StoreProduct.id)
        .filter(StoreProduct.status == "active")
        .label("active"),
        func.count(StoreProduct.id)
        .filter(available <= StoreProduct.reorder_point)
        .label("low_stock"),
        func.count(StoreProduct.id)
        .filter(available <= 0)
        .label("out_of_stock"),
        func.coalesce(
            func.sum(available * StoreProduct.cost_price), 0
        ).label("stock_value"),
    ).select_from(StoreProduct).outerjoin(
        StockBalance,
        and_(
            StockBalance.store_id == StoreProduct.store_id,
            StockBalance.product_id == StoreProduct.product_id,
        ),
    )
    for condition in conditions:
        query = query.where(condition)
        count_q = count_q.where(condition)
        summary_q = summary_q.where(condition)

    total = (await ctx.session.execute(count_q)).scalar() or 0
    summary = (await ctx.session.execute(summary_q)).one()
    rows = (
        await ctx.session.execute(
            query.order_by(order_by).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()

    return ok(
        {
            "items": [_product_row(r) for r in rows],
            "summary": {
                "total": int(summary.total or 0),
                "active": int(summary.active or 0),
                "low_stock": int(summary.low_stock or 0),
                "out_of_stock": int(summary.out_of_stock or 0),
                "stock_value": float(summary.stock_value or 0),
            },
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }
    )


@router.get(
    "/tenants/{tenant_id}/stores/{store_id}/products/{product_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def store_product_detail(
    tenant_id: str,
    store_id: str,
    product_id: str,
    ctx: PlatformDep,
    days: int = 30,
):
    """One product in one store: stock, pricing and recent sales."""
    from app.sales.models import Sale, SaleItem
    from app.stores.models import StoreProduct

    await _store_or_404(ctx, tenant_id, store_id)
    row = (
        await ctx.session.execute(
            _store_product_query(tenant_id, store_id).where(
                StoreProduct.product_id == product_id
            )
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found in this store")

    now = datetime.now(UTC)
    days = min(max(days, 1), 365)
    window_start = now - timedelta(days=days)

    sales_stats = (
        await ctx.session.execute(
            select(
                func.coalesce(func.sum(SaleItem.qty), 0).label("qty"),
                func.coalesce(func.sum(SaleItem.line_total), 0).label("revenue"),
                func.count(func.distinct(Sale.id)).label("sales"),
            )
            .select_from(SaleItem)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                SaleItem.product_id == product_id,
                Sale.tenant_id == tenant_id,
                Sale.store_id == store_id,
                Sale.status != "voided",
                Sale.created_at >= window_start,
            )
        )
    ).one()
    recent = (
        await ctx.session.execute(
            select(
                Sale.sale_number,
                Sale.created_at,
                SaleItem.qty,
                SaleItem.unit_price,
                SaleItem.line_total,
            )
            .select_from(SaleItem)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                SaleItem.product_id == product_id,
                Sale.tenant_id == tenant_id,
                Sale.store_id == store_id,
                Sale.status != "voided",
            )
            .order_by(desc(Sale.created_at))
            .limit(10)
        )
    ).all()

    return ok(
        {
            "product": _product_row(row),
            "window": {
                "days": days,
                "qty_sold": float(sales_stats.qty or 0),
                "revenue": float(sales_stats.revenue or 0),
                "sales": int(sales_stats.sales or 0),
            },
            "recent_sales": [
                {
                    "sale_number": r.sale_number,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "qty": float(r.qty or 0),
                    "unit_price": float(r.unit_price or 0),
                    "line_total": float(r.line_total or 0),
                }
                for r in recent
            ],
        }
    )


# ── Billing snapshot ────────────────────────────────────────────────


@router.get(
    "/tenants/{tenant_id}/billing",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def tenant_billing(tenant_id: str, ctx: PlatformDep):
    """Subscription, wallet, recent invoices and wallet movements."""
    from app.billing.models import (
        BillingInvoice,
        MerchantSubscription,
        SubscriptionPlan,
        TenantWallet,
        WalletTransaction,
    )
    from app.platform.models import PlatformFeeLedger

    await _tenant_or_404(ctx, tenant_id)
    sub_row = (
        await ctx.session.execute(
            select(MerchantSubscription, SubscriptionPlan)
            .outerjoin(SubscriptionPlan, SubscriptionPlan.id == MerchantSubscription.plan_id)
            .where(MerchantSubscription.tenant_id == tenant_id)
        )
    ).first()
    wallet = (
        await ctx.session.execute(
            select(TenantWallet).where(TenantWallet.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    invoices = (
        await ctx.session.execute(
            select(BillingInvoice)
            .where(BillingInvoice.tenant_id == tenant_id)
            .order_by(desc(BillingInvoice.issued_at))
            .limit(20)
        )
    ).scalars().all()
    movements = (
        await ctx.session.execute(
            select(WalletTransaction)
            .where(WalletTransaction.tenant_id == tenant_id)
            .order_by(desc(WalletTransaction.created_at))
            .limit(20)
        )
    ).scalars().all()
    fees = (
        await ctx.session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case((PlatformFeeLedger.status == "pending", PlatformFeeLedger.amount), else_=0)
                    ),
                    0,
                ).label("pending"),
                func.coalesce(
                    func.sum(
                        case((PlatformFeeLedger.status == "deducted", PlatformFeeLedger.amount), else_=0)
                    ),
                    0,
                ).label("settled"),
            ).where(PlatformFeeLedger.tenant_id == tenant_id)
        )
    ).one()

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
        }
        if plan_row:
            plan = {
                "id": str(plan_row.id),
                "name": plan_row.name,
                "slug": plan_row.slug,
                "monthly_price": float(plan_row.monthly_price or 0),
                "currency": plan_row.currency,
            }

    return ok(
        {
            "subscription": subscription,
            "plan": plan,
            "wallet": {
                "balance": float(wallet.balance or 0),
                "currency": wallet.currency,
                "status": wallet.status,
            }
            if wallet
            else None,
            "fees": {
                "pending": float(fees.pending or 0),
                "settled": float(fees.settled or 0),
            },
            "invoices": [
                {
                    "id": str(i.id),
                    "invoice_number": i.invoice_number,
                    "status": i.status,
                    "total_amount": float(i.total_amount or 0),
                    "amount_paid": float(i.amount_paid or 0),
                    "amount_due": float(i.amount_due or 0),
                    "issued_at": i.issued_at.isoformat() if i.issued_at else None,
                    "due_at": i.due_at.isoformat() if i.due_at else None,
                    "paid_at": i.paid_at.isoformat() if i.paid_at else None,
                }
                for i in invoices
            ],
            "wallet_transactions": [
                {
                    "id": str(t.id),
                    "entry_type": t.entry_type,
                    "amount": float(t.amount or 0),
                    "currency": t.currency,
                    "reference": t.reference,
                    "description": t.description,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in movements
            ],
        }
    )


# ── Internal notes and tags ─────────────────────────────────────────


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    category: str = "general"
    is_pinned: bool = False


class NoteUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=5000)
    category: str | None = None
    is_pinned: bool | None = None


class TagCreate(BaseModel):
    tag: str = Field(min_length=1, max_length=50)


@router.get(
    "/tenants/{tenant_id}/notes",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def list_tenant_notes(
    tenant_id: str,
    ctx: PlatformDep,
    page: int = 1,
    page_size: int = 50,
):
    from app.platform.models import PlatformAdmin, PlatformTenantNote

    await _tenant_or_404(ctx, tenant_id)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    total = (
        await ctx.session.execute(
            select(func.count(PlatformTenantNote.id)).where(
                PlatformTenantNote.tenant_id == tenant_id
            )
        )
    ).scalar() or 0
    rows = (
        await ctx.session.execute(
            select(
                PlatformTenantNote,
                PlatformAdmin.full_name.label("admin_name"),
                PlatformAdmin.email.label("admin_email"),
            )
            .outerjoin(PlatformAdmin, PlatformAdmin.id == PlatformTenantNote.admin_id)
            .where(PlatformTenantNote.tenant_id == tenant_id)
            .order_by(
                desc(PlatformTenantNote.is_pinned),
                desc(PlatformTenantNote.created_at),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return ok(
        {
            "items": [
                {
                    "id": str(n.id),
                    "body": n.body,
                    "category": n.category,
                    "is_pinned": bool(n.is_pinned),
                    "admin_id": str(n.admin_id) if n.admin_id else None,
                    "admin_name": admin_name,
                    "admin_email": admin_email,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                    "updated_at": n.updated_at.isoformat() if n.updated_at else None,
                }
                for n, admin_name, admin_email in rows
            ],
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }
    )


@router.post(
    "/tenants/{tenant_id}/notes",
    status_code=201,
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def create_tenant_note(
    tenant_id: str, payload: NoteCreate, request: Request, ctx: PlatformDep
):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformTenantNote

    assert_platform_role(ctx, "support")
    await _tenant_or_404(ctx, tenant_id)
    note = PlatformTenantNote(
        tenant_id=tenant_id,
        admin_id=ctx.user.admin_id,
        body=payload.body,
        category=payload.category,
        is_pinned=payload.is_pinned,
    )
    ctx.session.add(note)
    await ctx.session.flush()
    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="tenant.note.create",
        resource="tenant",
        resource_id=tenant_id,
        details={"note_id": str(note.id), "category": note.category},
        ip_address=_client_ip(request),
    )
    return ok(
        {
            "id": str(note.id),
            "body": note.body,
            "category": note.category,
            "is_pinned": bool(note.is_pinned),
            "admin_id": str(note.admin_id) if note.admin_id else None,
            "created_at": note.created_at.isoformat() if note.created_at else None,
        }
    )


@router.patch(
    "/tenants/{tenant_id}/notes/{note_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def update_tenant_note(
    tenant_id: str,
    note_id: str,
    payload: NoteUpdate,
    request: Request,
    ctx: PlatformDep,
):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformTenantNote

    assert_platform_role(ctx, "support")
    note = (
        await ctx.session.execute(
            select(PlatformTenantNote).where(
                PlatformTenantNote.id == note_id,
                PlatformTenantNote.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(note, key, value)
    await ctx.session.flush()
    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="tenant.note.update",
        resource="tenant",
        resource_id=tenant_id,
        details={"note_id": note_id, "fields": list(changes)},
        ip_address=_client_ip(request),
    )
    return ok(
        {
            "id": str(note.id),
            "body": note.body,
            "category": note.category,
            "is_pinned": bool(note.is_pinned),
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        }
    )


@router.delete(
    "/tenants/{tenant_id}/notes/{note_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def delete_tenant_note(
    tenant_id: str, note_id: str, request: Request, ctx: PlatformDep
):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformTenantNote

    assert_platform_role(ctx, "support")
    note = (
        await ctx.session.execute(
            select(PlatformTenantNote).where(
                PlatformTenantNote.id == note_id,
                PlatformTenantNote.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    await ctx.session.delete(note)
    await ctx.session.flush()
    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="tenant.note.delete",
        resource="tenant",
        resource_id=tenant_id,
        details={"note_id": note_id},
        ip_address=_client_ip(request),
    )
    return ok({"deleted": note_id})


@router.get(
    "/tenants/{tenant_id}/tags",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def list_tenant_tags(tenant_id: str, ctx: PlatformDep):
    from app.platform.models import PlatformTenantTag

    await _tenant_or_404(ctx, tenant_id)
    tags = (
        await ctx.session.execute(
            select(PlatformTenantTag.tag)
            .where(PlatformTenantTag.tenant_id == tenant_id)
            .order_by(PlatformTenantTag.tag)
        )
    ).scalars().all()
    return ok({"items": list(tags), "total": len(tags)})


@router.post(
    "/tenants/{tenant_id}/tags",
    status_code=201,
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def add_tenant_tag(
    tenant_id: str, payload: TagCreate, request: Request, ctx: PlatformDep
):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformTenantTag

    assert_platform_role(ctx, "support")
    await _tenant_or_404(ctx, tenant_id)
    tag = _normalize_tag(payload.tag)
    existing = (
        await ctx.session.execute(
            select(PlatformTenantTag).where(
                PlatformTenantTag.tenant_id == tenant_id,
                PlatformTenantTag.tag == tag,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return ok({"tag": tag, "created": False})
    row = PlatformTenantTag(
        tenant_id=tenant_id, tag=tag, created_by=ctx.user.admin_id
    )
    ctx.session.add(row)
    await ctx.session.flush()
    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="tenant.tag.add",
        resource="tenant",
        resource_id=tenant_id,
        details={"tag": tag},
        ip_address=_client_ip(request),
    )
    return ok({"tag": tag, "created": True})


@router.delete(
    "/tenants/{tenant_id}/tags/{tag}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def remove_tenant_tag(
    tenant_id: str, tag: str, request: Request, ctx: PlatformDep
):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformTenantTag

    assert_platform_role(ctx, "support")
    normalized = _normalize_tag(tag)
    row = (
        await ctx.session.execute(
            select(PlatformTenantTag).where(
                PlatformTenantTag.tenant_id == tenant_id,
                PlatformTenantTag.tag == normalized,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Tag not found")
    await ctx.session.delete(row)
    await ctx.session.flush()
    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="tenant.tag.remove",
        resource="tenant",
        resource_id=tenant_id,
        details={"tag": normalized},
        ip_address=_client_ip(request),
    )
    return ok({"tag": normalized, "deleted": True})


@router.get(
    "/tenants/{tenant_id}/status-history",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def tenant_status_history(
    tenant_id: str, ctx: PlatformDep, page: int = 1, page_size: int = 50
):
    from app.platform.models import PlatformAdmin, PlatformTenantStatusHistory

    await _tenant_or_404(ctx, tenant_id)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    total = (
        await ctx.session.execute(
            select(func.count(PlatformTenantStatusHistory.id)).where(
                PlatformTenantStatusHistory.tenant_id == tenant_id
            )
        )
    ).scalar() or 0
    rows = (
        await ctx.session.execute(
            select(
                PlatformTenantStatusHistory,
                PlatformAdmin.full_name.label("admin_name"),
            )
            .outerjoin(
                PlatformAdmin, PlatformAdmin.id == PlatformTenantStatusHistory.admin_id
            )
            .where(PlatformTenantStatusHistory.tenant_id == tenant_id)
            .order_by(desc(PlatformTenantStatusHistory.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return ok(
        {
            "items": [
                {
                    "id": str(h.id),
                    "from_status": h.from_status,
                    "to_status": h.to_status,
                    "reason": h.reason,
                    "admin_id": str(h.admin_id) if h.admin_id else None,
                    "admin_name": admin_name,
                    "created_at": h.created_at.isoformat() if h.created_at else None,
                }
                for h, admin_name in rows
            ],
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }
    )


# ── Audited impersonation ───────────────────────────────────────────


class ImpersonateRequest(BaseModel):
    user_id: str | None = None
    reason: str = Field(min_length=5, max_length=500)
    read_only: bool = True


@router.post(
    "/tenants/{tenant_id}/impersonate",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def impersonate_tenant(
    tenant_id: str, payload: ImpersonateRequest, request: Request, ctx: PlatformDep
):
    """Mint a short-lived tenant token for support, logged and read-only by default."""
    from app.common.auth_service import _get_perms, _get_role_names, role_claim
    from app.common.db.session import clear_rls_context, set_rls_context
    from app.core.config import settings
    from app.core.security import create_access_token
    from app.identity.models import User
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformImpersonationLog

    assert_platform_role(ctx, "admin")
    tenant = await _tenant_or_404(ctx, tenant_id)

    if payload.user_id:
        user = (
            await ctx.session.execute(
                select(User).where(
                    User.id == payload.user_id, User.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
    else:
        user = (
            await ctx.session.execute(
                select(User).where(User.email == tenant.owner_email)
            )
        ).scalar_one_or_none()
        if not user:
            user = (
                await ctx.session.execute(
                    select(User)
                    .where(User.tenant_id == tenant_id, User.status == "active")
                    .order_by(User.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No user available to impersonate")

    await set_rls_context(ctx.session, str(user.id), tenant_id, "")
    try:
        roles = await _get_role_names(str(user.id), ctx.session)
        perms = await _get_perms(str(user.id), ctx.session)
    finally:
        await clear_rls_context(ctx.session)

    token, _ = create_access_token(
        str(user.id),
        tenant_id,
        role_claim(roles),
        perms,
        roles=roles,
        extra_claims={
            "impersonated_by": str(ctx.user.admin_id),
            "read_only": bool(payload.read_only),
        },
    )

    log = PlatformImpersonationLog(
        admin_id=ctx.user.admin_id,
        tenant_id=tenant.id,
        user_id=user.id,
        reason=payload.reason,
        read_only=payload.read_only,
        ip_address=_client_ip(request),
    )
    ctx.session.add(log)
    await ctx.session.flush()
    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="tenant.impersonate",
        resource="tenant",
        resource_id=tenant_id,
        details={
            "user_id": str(user.id),
            "read_only": bool(payload.read_only),
            "reason": payload.reason,
        },
        ip_address=_client_ip(request),
    )

    return ok(
        {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": settings.access_token_expire_minutes * 60,
            "read_only": bool(payload.read_only),
            "impersonated_by": str(ctx.user.admin_id),
            "user": {
                "id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "roles": roles,
            },
            "tenant": {
                "id": str(tenant.id),
                "business_name": tenant.business_name,
                "slug": tenant.slug,
            },
        }
    )


@router.get(
    "/impersonation-log",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def impersonation_log(
    ctx: PlatformDep,
    tenant_id: str | None = None,
    admin_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    from app.platform.models import PlatformAdmin, PlatformImpersonationLog

    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    query = select(
        PlatformImpersonationLog,
        PlatformAdmin.full_name.label("admin_name"),
        PlatformAdmin.email.label("admin_email"),
    ).outerjoin(PlatformAdmin, PlatformAdmin.id == PlatformImpersonationLog.admin_id)
    count_q = select(func.count(PlatformImpersonationLog.id))
    if tenant_id:
        query = query.where(PlatformImpersonationLog.tenant_id == tenant_id)
        count_q = count_q.where(PlatformImpersonationLog.tenant_id == tenant_id)
    if admin_id:
        query = query.where(PlatformImpersonationLog.admin_id == admin_id)
        count_q = count_q.where(PlatformImpersonationLog.admin_id == admin_id)
    total = (await ctx.session.execute(count_q)).scalar() or 0
    rows = (
        await ctx.session.execute(
            query.order_by(desc(PlatformImpersonationLog.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return ok(
        {
            "items": [
                {
                    "id": str(log.id),
                    "admin_id": str(log.admin_id),
                    "admin_name": admin_name,
                    "admin_email": admin_email,
                    "tenant_id": str(log.tenant_id),
                    "user_id": str(log.user_id),
                    "reason": log.reason,
                    "read_only": bool(log.read_only),
                    "ip_address": log.ip_address,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
                for log, admin_name, admin_email in rows
            ],
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }
    )


# ── Data requests (export / deletion) ───────────────────────────────


class DataRequestCreate(BaseModel):
    tenant_id: str
    request_type: str = Field(pattern=r"^(export|deletion)$")
    notes: str | None = None


class DataRequestUpdate(BaseModel):
    status: str = Field(pattern=r"^(pending|in_progress|completed|rejected)$")
    notes: str | None = None


@router.get(
    "/data-requests",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def list_data_requests(
    ctx: PlatformDep,
    tenant_id: str | None = None,
    status_filter: str | None = None,
    request_type: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    from app.platform.models import PlatformDataRequest
    from app.tenancy.models import Tenant

    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    query = select(
        PlatformDataRequest,
        Tenant.business_name.label("business_name"),
    ).outerjoin(Tenant, Tenant.id == PlatformDataRequest.tenant_id)
    count_q = select(func.count(PlatformDataRequest.id))
    conditions = []
    if tenant_id:
        conditions.append(PlatformDataRequest.tenant_id == tenant_id)
    if status_filter:
        conditions.append(PlatformDataRequest.status == status_filter)
    if request_type:
        conditions.append(PlatformDataRequest.request_type == request_type)
    for condition in conditions:
        query = query.where(condition)
        count_q = count_q.where(condition)
    total = (await ctx.session.execute(count_q)).scalar() or 0
    rows = (
        await ctx.session.execute(
            query.order_by(desc(PlatformDataRequest.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return ok(
        {
            "items": [
                {
                    "id": str(r.id),
                    "tenant_id": str(r.tenant_id),
                    "business_name": business_name,
                    "request_type": r.request_type,
                    "status": r.status,
                    "requested_by": str(r.requested_by) if r.requested_by else None,
                    "notes": r.notes,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "completed_at": r.completed_at.isoformat()
                    if r.completed_at
                    else None,
                }
                for r, business_name in rows
            ],
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }
    )


@router.post(
    "/data-requests",
    status_code=201,
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def create_data_request(
    payload: DataRequestCreate, request: Request, ctx: PlatformDep
):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformDataRequest

    assert_platform_role(ctx, "admin")
    await _tenant_or_404(ctx, payload.tenant_id)
    row = PlatformDataRequest(
        tenant_id=payload.tenant_id,
        request_type=payload.request_type,
        status="pending",
        requested_by=ctx.user.admin_id,
        notes=payload.notes,
    )
    ctx.session.add(row)
    await ctx.session.flush()
    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="data_request.create",
        resource="tenant",
        resource_id=payload.tenant_id,
        details={"request_id": str(row.id), "type": payload.request_type},
        ip_address=_client_ip(request),
    )
    return ok(
        {
            "id": str(row.id),
            "tenant_id": str(row.tenant_id),
            "request_type": row.request_type,
            "status": row.status,
            "notes": row.notes,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
    )


@router.patch(
    "/data-requests/{request_id}",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def update_data_request(
    request_id: str,
    payload: DataRequestUpdate,
    request: Request,
    ctx: PlatformDep,
):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformDataRequest

    assert_platform_role(ctx, "admin")
    row = (
        await ctx.session.execute(
            select(PlatformDataRequest).where(PlatformDataRequest.id == request_id)
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Data request not found")
    row.status = payload.status
    if payload.notes is not None:
        row.notes = payload.notes
    if payload.status in ("completed", "rejected"):
        row.completed_at = datetime.now(UTC)
    await ctx.session.flush()
    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action="data_request.update",
        resource="tenant",
        resource_id=str(row.tenant_id),
        details={"request_id": request_id, "status": payload.status},
        ip_address=_client_ip(request),
    )
    return ok(
        {
            "id": str(row.id),
            "status": row.status,
            "notes": row.notes,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
    )


# ── Bulk actions ────────────────────────────────────────────────────


class BulkActionRequest(BaseModel):
    tenant_ids: list[str] = Field(min_length=1, max_length=100)
    action: str = Field(pattern=r"^(suspend|reinstate|add_tag|remove_tag)$")
    tag: str | None = None
    reason: str | None = None


@router.post(
    "/tenants/bulk",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def bulk_tenant_action(
    payload: BulkActionRequest, request: Request, ctx: PlatformDep
):
    from app.platform.audit import log_platform_audit
    from app.platform.models import PlatformTenantStatusHistory, PlatformTenantTag
    from app.platform.routes import _reinstate_tenant_users, _suspend_tenant_users
    from app.tenancy.models import Tenant

    assert_platform_role(ctx, "admin")
    results = []
    tag = _normalize_tag(payload.tag) if payload.tag else None
    if payload.action in ("add_tag", "remove_tag") and not tag:
        raise HTTPException(status_code=400, detail="tag is required for tag actions")

    for tenant_id in payload.tenant_ids:
        tenant = (
            await ctx.session.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if not tenant:
            results.append({"tenant_id": tenant_id, "ok": False, "error": "not found"})
            continue

        if payload.action in ("suspend", "reinstate"):
            target = "suspended" if payload.action == "suspend" else "active"
            if tenant.status == target:
                results.append(
                    {"tenant_id": tenant_id, "ok": True, "status": target, "changed": False}
                )
                continue
            previous = tenant.status
            tenant.status = target
            if target == "suspended":
                await _suspend_tenant_users(ctx.session, tenant_id)
            else:
                await _reinstate_tenant_users(ctx.session, tenant_id)
            ctx.session.add(
                PlatformTenantStatusHistory(
                    tenant_id=tenant.id,
                    from_status=previous,
                    to_status=target,
                    reason=payload.reason,
                    admin_id=ctx.user.admin_id,
                )
            )
            results.append(
                {"tenant_id": tenant_id, "ok": True, "status": target, "changed": True}
            )
        else:
            existing = (
                await ctx.session.execute(
                    select(PlatformTenantTag).where(
                        PlatformTenantTag.tenant_id == tenant_id,
                        PlatformTenantTag.tag == tag,
                    )
                )
            ).scalar_one_or_none()
            if payload.action == "add_tag":
                if not existing:
                    ctx.session.add(
                        PlatformTenantTag(
                            tenant_id=tenant.id,
                            tag=tag,
                            created_by=ctx.user.admin_id,
                        )
                    )
                results.append(
                    {"tenant_id": tenant_id, "ok": True, "tag": tag, "changed": not existing}
                )
            else:
                if existing:
                    await ctx.session.delete(existing)
                results.append(
                    {"tenant_id": tenant_id, "ok": True, "tag": tag, "changed": bool(existing)}
                )

    await ctx.session.flush()
    await log_platform_audit(
        ctx.session,
        admin_id=ctx.user.admin_id,
        action=f"tenant.bulk_{payload.action}",
        resource="tenant",
        resource_id=None,
        details={
            "tenant_ids": payload.tenant_ids,
            "tag": tag,
            "reason": payload.reason,
            "affected": sum(1 for r in results if r.get("ok")),
        },
        ip_address=_client_ip(request),
    )
    return ok({"results": results, "total": len(results)})


# ── Per-tenant health ───────────────────────────────────────────────


@router.get(
    "/tenants/{tenant_id}/health",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def tenant_health_detail(tenant_id: str, ctx: PlatformDep):
    from app.platform.health import fetch_health_rows

    await _tenant_or_404(ctx, tenant_id)
    rows = await fetch_health_rows(ctx.session, tenant_id=tenant_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Tenant health not available")
    return ok(rows[0])


# ── CSV exports ─────────────────────────────────────────────────────


@router.get(
    "/export/tenants.csv",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def export_tenants(
    ctx: PlatformDep,
    status_filter: str | None = None,
    search: str | None = None,
):
    from app.tenancy.models import Tenant

    query = select(
        Tenant.id,
        Tenant.slug,
        Tenant.subdomain,
        Tenant.business_name,
        Tenant.business_email,
        Tenant.owner_name,
        Tenant.owner_email,
        Tenant.owner_phone,
        Tenant.tier,
        Tenant.status,
        Tenant.bank_name,
        Tenant.account_name,
        Tenant.account_number,
        Tenant.created_at,
        Tenant.updated_at,
    )
    if status_filter:
        query = query.where(Tenant.status == status_filter)
    if search:
        like = f"%{search}%"
        query = query.where(
            Tenant.business_name.ilike(like)
            | Tenant.slug.ilike(like)
            | Tenant.owner_email.ilike(like)
        )
    rows = (await ctx.session.execute(query.order_by(Tenant.created_at.desc()))).all()
    return csv_response(
        "tenants.csv",
        [
            "id",
            "slug",
            "subdomain",
            "business_name",
            "business_email",
            "owner_name",
            "owner_email",
            "owner_phone",
            "tier",
            "status",
            "bank_name",
            "account_name",
            "account_number",
            "created_at",
            "updated_at",
        ],
        [
            {
                "id": str(r.id),
                "slug": r.slug,
                "subdomain": r.subdomain,
                "business_name": r.business_name,
                "business_email": r.business_email,
                "owner_name": r.owner_name,
                "owner_email": r.owner_email,
                "owner_phone": r.owner_phone,
                "tier": r.tier,
                "status": r.status,
                "bank_name": r.bank_name,
                "account_name": r.account_name,
                "account_number": r.account_number,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ],
    )


@router.get(
    "/export/users.csv",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def export_users(
    ctx: PlatformDep,
    tenant_id: str | None = None,
    status_filter: str | None = None,
):
    from app.identity.models import User

    query = select(
        User.id,
        User.tenant_id,
        User.email,
        User.full_name,
        User.phone,
        User.status,
        User.last_login_at,
        User.last_device_type,
        User.last_browser,
        User.created_at,
    )
    if tenant_id:
        query = query.where(User.tenant_id == tenant_id)
    if status_filter:
        query = query.where(User.status == status_filter)
    rows = (await ctx.session.execute(query.order_by(User.created_at.desc()))).all()
    return csv_response(
        "users.csv",
        [
            "id",
            "tenant_id",
            "email",
            "full_name",
            "phone",
            "status",
            "last_login_at",
            "last_device_type",
            "last_browser",
            "created_at",
        ],
        [
            {
                "id": str(r.id),
                "tenant_id": str(r.tenant_id),
                "email": r.email,
                "full_name": r.full_name,
                "phone": r.phone,
                "status": r.status,
                "last_login_at": r.last_login_at,
                "last_device_type": r.last_device_type,
                "last_browser": r.last_browser,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    )


@router.get(
    "/export/invoices.csv",
    dependencies=[Depends(require_platform_permission("platform:*"))],
)
async def export_invoices(
    ctx: PlatformDep,
    status_filter: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
):
    from app.billing.models import BillingInvoice
    from app.tenancy.models import Tenant

    d_from = parse_iso_date(from_date, "from_date")
    d_to = parse_iso_date(to_date, "to_date")
    query = select(BillingInvoice, Tenant.business_name.label("business_name")).outerjoin(
        Tenant, Tenant.id == BillingInvoice.tenant_id
    )
    if status_filter:
        query = query.where(BillingInvoice.status == status_filter)
    if d_from:
        query = query.where(cast(BillingInvoice.issued_at, Date) >= d_from)
    if d_to:
        query = query.where(cast(BillingInvoice.issued_at, Date) <= d_to)
    rows = (
        await ctx.session.execute(query.order_by(desc(BillingInvoice.issued_at)))
    ).all()
    return csv_response(
        "billing-invoices.csv",
        [
            "invoice_number",
            "tenant_id",
            "business_name",
            "status",
            "subscription_amount",
            "transaction_fee_total",
            "adjustment_total",
            "total_amount",
            "amount_paid",
            "amount_due",
            "issued_at",
            "due_at",
            "paid_at",
        ],
        [
            {
                "invoice_number": r.invoice_number,
                "tenant_id": str(r.tenant_id),
                "business_name": business_name,
                "status": r.status,
                "subscription_amount": float(r.subscription_amount or 0),
                "transaction_fee_total": float(r.transaction_fee_total or 0),
                "adjustment_total": float(r.adjustment_total or 0),
                "total_amount": float(r.total_amount or 0),
                "amount_paid": float(r.amount_paid or 0),
                "amount_due": float(r.amount_due or 0),
                "issued_at": r.issued_at,
                "due_at": r.due_at,
                "paid_at": r.paid_at,
            }
            for r, business_name in rows
        ],
    )
