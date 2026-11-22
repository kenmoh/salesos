"""Billing API routes.

Three routers:
- ``platform_billing_router`` — platform-wide pricing/subscription management
  (SUPER_ADMIN only; mounted under ``/platform/billing``).
- ``tenant_billing_router`` — per-tenant billing views (mounted under tenant
  routes at ``/billing``).
- ``public_pricing_router`` — unauthenticated pricing for landing pages
  (mounted directly at ``/pricing``).
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from app.core.dependencies import DbTenantDep, get_db
from app.core.platform_deps import PlatformDep, require_platform_permission
from app.core.responses import DataResponse, ok
from app.billing import fees as fee_service
from app.billing import invoices as invoice_service
from app.billing import subscriptions as sub_service
from app.platform.models import PlatformSetting
from app.billing.models import (
    BillingInvoice,
    BillingPayment,
    BillableTransaction,
    BillingPeriod,
    MerchantSubscription,
    PlanFeature,
    PricingVersion,
    SubscriptionPlan,
    TenantWallet,
    TransactionFeeTier,
    WalletTransaction,
)
from app.billing.schemas import (
    AutoDebitToggle,
    FeeTierCreate,
    FeeTierOut,
    InvoiceOut,
    PlanChangeRequest,
    PlanCreate,
    PlanFeatureCreate,
    PlanFeatureOut,
    PlanFeatureUpdate,
    PlanOut,
    PlanUpdate,
    PricingVersionCreate,
    PricingVersionOut,
    SubscriptionOut,
    WalletFundLinkRequest,
    WalletFundRequest,
    WalletOut,
    WalletTransactionOut,
    BillableTransactionOut,
)

platform_billing_router = APIRouter(prefix="/billing", tags=["Platform Billing"])
tenant_billing_router = APIRouter(tags=["Tenant Billing"])
public_pricing_router = APIRouter(prefix="/pricing", tags=["Public Pricing"])


# ── Platform admin: require SUPER_ADMIN ─────────────────────────────────────

def require_super_admin(ctx: PlatformDep):
    if ctx.user.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin required")


# ── Platform: plans ─────────────────────────────────────────────────────────

@platform_billing_router.get(
    "/plans",
    response_model=list[PlanOut],
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_list_plans(ctx: PlatformDep):
    result = await ctx.session.execute(
        select(SubscriptionPlan)
        .options(
            selectinload(SubscriptionPlan.fee_tiers),
            selectinload(SubscriptionPlan.plan_features),
        )
        .order_by(SubscriptionPlan.sort_order, SubscriptionPlan.name)
    )
    return result.scalars().all()


@platform_billing_router.post(
    "/plans",
    response_model=PlanOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_create_plan(payload: PlanCreate, ctx: PlatformDep):
    exists = await ctx.session.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.slug == payload.slug)
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Plan slug already exists")
    plan = SubscriptionPlan(**payload.model_dump())
    ctx.session.add(plan)
    await ctx.session.flush()
    return plan


@platform_billing_router.put(
    "/plans/{plan_id}",
    response_model=PlanOut,
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_update_plan(plan_id: UUID, payload: PlanUpdate, ctx: PlatformDep):
    result = await ctx.session.execute(
        select(SubscriptionPlan)
        .options(
            selectinload(SubscriptionPlan.fee_tiers),
            selectinload(SubscriptionPlan.plan_features),
        )
        .where(SubscriptionPlan.id == plan_id)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(plan, field, value)
    await ctx.session.flush()
    return plan


# ── Platform: plan features ─────────────────────────────────────────────────

@platform_billing_router.get(
    "/plans/{plan_id}/features",
    response_model=list[PlanFeatureOut],
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_list_plan_features(plan_id: UUID, ctx: PlatformDep):
    result = await ctx.session.execute(
        select(PlanFeature)
        .where(PlanFeature.plan_id == plan_id)
        .order_by(PlanFeature.sort_order, PlanFeature.feature_key)
    )
    return result.scalars().all()


@platform_billing_router.get(
    "/plan-features",
    response_model=list[PlanFeatureOut],
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_list_all_plan_features(ctx: PlatformDep):
    result = await ctx.session.execute(
        select(PlanFeature)
        .order_by(PlanFeature.sort_order, PlanFeature.feature_key)
    )
    return result.scalars().all()


@platform_billing_router.post(
    "/plan-features",
    response_model=PlanFeatureOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_upsert_plan_feature(payload: PlanFeatureCreate, ctx: PlatformDep):
    plan = await ctx.session.get(SubscriptionPlan, payload.plan_id)
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    feature = await ctx.session.scalar(
        select(PlanFeature).where(
            PlanFeature.plan_id == payload.plan_id,
            PlanFeature.feature_key == payload.feature_key,
        )
    )
    if feature:
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(feature, field, value)
        await ctx.session.flush()
        return feature
    feature = PlanFeature(**payload.model_dump())
    ctx.session.add(feature)
    await ctx.session.flush()
    return feature


@platform_billing_router.put(
    "/plan-features/{feature_id}",
    response_model=PlanFeatureOut,
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_update_plan_feature(feature_id: UUID, payload: PlanFeatureUpdate, ctx: PlatformDep):
    feature = await ctx.session.get(PlanFeature, feature_id)
    if not feature:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan feature not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(feature, field, value)
    await ctx.session.flush()
    return feature


# ── Platform: pricing versions ──────────────────────────────────────────────

@platform_billing_router.get(
    "/pricing-versions",
    response_model=list[PricingVersionOut],
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_list_pricing_versions(ctx: PlatformDep):
    result = await ctx.session.execute(
        select(PricingVersion).order_by(desc(PricingVersion.version_number))
    )
    return result.scalars().all()


@platform_billing_router.post(
    "/pricing-versions",
    response_model=PricingVersionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_create_pricing_version(payload: PricingVersionCreate, ctx: PlatformDep):
    exists = await ctx.session.scalar(
        select(PricingVersion).where(PricingVersion.version_number == payload.version_number)
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Version number already exists")
    version = PricingVersion(**payload.model_dump())
    ctx.session.add(version)
    await ctx.session.flush()
    return version


# ── Platform: fee tiers ─────────────────────────────────────────────────────

@platform_billing_router.get(
    "/fee-tiers",
    response_model=list[FeeTierOut],
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_list_fee_tiers(ctx: PlatformDep):
    result = await ctx.session.execute(
        select(TransactionFeeTier).order_by(
            TransactionFeeTier.plan_id, TransactionFeeTier.minimum_amount
        )
    )
    return result.scalars().all()


@platform_billing_router.post(
    "/fee-tiers",
    response_model=FeeTierOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_create_fee_tier(payload: FeeTierCreate, ctx: PlatformDep):
    tier = TransactionFeeTier(**payload.model_dump())
    ctx.session.add(tier)
    await ctx.session.flush()
    return tier


# ── Platform: subscriptions & invoices ──────────────────────────────────────

@platform_billing_router.get(
    "/subscriptions",
    response_model=list[SubscriptionOut],
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_list_subscriptions(ctx: PlatformDep):
    result = await ctx.session.execute(
        select(MerchantSubscription).order_by(MerchantSubscription.created_at.desc())
    )
    return result.scalars().all()


@platform_billing_router.get(
    "/invoices",
    response_model=list[InvoiceOut],
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_list_invoices(
    ctx: PlatformDep,
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    stmt = select(BillingInvoice).order_by(BillingInvoice.issued_at.desc())
    if status:
        stmt = stmt.where(BillingInvoice.status == status)
    total = await ctx.session.scalar(select(func.count()).select_from(stmt.subquery()))
    result = await ctx.session.execute(
        stmt.offset((page - 1) * page_size).limit(page_size)
    )
    return result.scalars().all()


@platform_billing_router.get(
    "/invoices/{invoice_id}",
    response_model=InvoiceOut,
    dependencies=[Depends(require_platform_permission("platform:*")), Depends(require_super_admin)],
)
async def platform_get_invoice(invoice_id: UUID, ctx: PlatformDep):
    invoice = await ctx.session.get(BillingInvoice, invoice_id)
    if not invoice:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return invoice


# ── Tenant: subscription & billing views ────────────────────────────────────

@tenant_billing_router.get(
    "/billing/subscription",
    response_model=SubscriptionOut,
)
async def tenant_get_subscription(ctx: DbTenantDep):
    sub = await sub_service.ensure_subscription(ctx.session, ctx.user.business_id)
    return sub


@tenant_billing_router.post(
    "/billing/subscription/plan",
    response_model=SubscriptionOut,
)
async def tenant_change_plan(payload: PlanChangeRequest, ctx: DbTenantDep):
    sub = await sub_service.change_plan(ctx.session, ctx.user.business_id, payload.plan_slug)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    return sub


@tenant_billing_router.patch(
    "/billing/subscription/auto-debit",
    response_model=SubscriptionOut,
)
async def tenant_toggle_auto_debit(
    payload: AutoDebitToggle,
    ctx: DbTenantDep,
):
    """Toggle wallet auto-debit on the tenant's subscription."""
    sub = await sub_service.ensure_subscription(ctx.session, ctx.user.business_id)
    sub.auto_debit_enabled = payload.auto_debit_enabled
    await ctx.session.flush()
    return sub


@tenant_billing_router.get(
    "/billing/invoices",
    response_model=list[InvoiceOut],
)
async def tenant_list_invoices(
    ctx: DbTenantDep,
    status: Optional[str] = Query(None),
):
    stmt = select(BillingInvoice).where(
        BillingInvoice.tenant_id == ctx.user.business_id
    ).order_by(BillingInvoice.issued_at.desc())
    if status:
        stmt = stmt.where(BillingInvoice.status == status)
    result = await ctx.session.execute(stmt)
    return result.scalars().all()


@tenant_billing_router.get(
    "/billing/wallet",
    response_model=WalletOut,
)
async def tenant_get_wallet(ctx: DbTenantDep):
    result = await ctx.session.execute(
        select(TenantWallet).where(TenantWallet.tenant_id == ctx.user.business_id)
    )
    wallet = result.scalar_one_or_none()
    if wallet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wallet not found")
    return wallet


@tenant_billing_router.get(
    "/billing/wallet/transactions",
    response_model=list[WalletTransactionOut],
)
async def tenant_list_wallet_transactions(ctx: DbTenantDep):
    result = await ctx.session.execute(
        select(WalletTransaction)
        .where(WalletTransaction.tenant_id == ctx.user.business_id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(100)
    )
    return result.scalars().all()


@tenant_billing_router.post(
    "/billing/wallet/fund",
    response_model=WalletTransactionOut,
    status_code=status.HTTP_201_CREATED,
)
async def tenant_fund_wallet(payload: WalletFundRequest, ctx: DbTenantDep):
    entry = await invoice_service.credit_wallet(
        ctx.session,
        tenant_id=ctx.user.business_id,
        amount=payload.amount,
        entry_type="funding",
        gateway_reference=payload.gateway_reference,
        description=payload.description,
    )
    return entry


@tenant_billing_router.post(
    "/billing/wallet/settle-link",
    response_model=DataResponse[dict],
    status_code=status.HTTP_201_CREATED,
)
async def tenant_billing_settle_link(ctx: DbTenantDep):
    """Create a Flutterwave link to clear the tenant's accrued billing debt.

    The link amount equals ``accrued_debt`` on the tenant's subscription.
    Confirmation (webhook) clears the debt in full.
    """
    try:
        result = await invoice_service.create_billing_settlement_link(
            ctx.session,
            tenant_id=ctx.user.business_id,
            actor_id=ctx.user.user_id,
        )
    except ValueError as e:
        if str(e) == "nothing_pending":
            raise HTTPException(status_code=400, detail="nothing_pending")
        raise HTTPException(status_code=400, detail=str(e))
    return ok(result, message="settlement_link_created")


@tenant_billing_router.post(
    "/billing/wallet/funding-link",
    response_model=DataResponse[dict],
    status_code=status.HTTP_201_CREATED,
)
async def tenant_wallet_funding_link(
    payload: WalletFundLinkRequest, ctx: DbTenantDep
):
    """Create a Flutterwave link to fund the tenant's wallet.

    The link amount is specified by the tenant. Confirmation (webhook)
    credits the wallet in full.
    """
    try:
        result = await invoice_service.create_wallet_funding_link(
            ctx.session,
            tenant_id=ctx.user.business_id,
            actor_id=ctx.user.user_id,
            amount=payload.amount,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ok(result, message="funding_link_created")


@tenant_billing_router.get(
    "/billing/billable-transactions",
    response_model=list[BillableTransactionOut],
)
async def tenant_list_billable_transactions(ctx: DbTenantDep):
    result = await ctx.session.execute(
        select(BillableTransaction)
        .where(BillableTransaction.tenant_id == ctx.user.business_id)
        .order_by(BillableTransaction.completed_at.desc())
        .limit(100)
    )
    return result.scalars().all()


# ── Public pricing (landing page) ───────────────────────────────────────────

@public_pricing_router.get(
    "/plans",
    response_model=list[PlanOut],
)
async def public_list_plans(session: Annotated[AsyncSession, Depends(get_db)]):
    result = await session.execute(
        select(SubscriptionPlan)
        .where(SubscriptionPlan.is_active.is_(True))
        .options(
            selectinload(SubscriptionPlan.plan_features),
            selectinload(SubscriptionPlan.fee_tiers),
        )
        .order_by(SubscriptionPlan.sort_order, SubscriptionPlan.name)
    )
    return result.scalars().all()


@public_pricing_router.get(
    "/settings",
    response_model=dict,
)
async def public_pricing_settings(session: Annotated[AsyncSession, Depends(get_db)]):
    """Public-safe pricing settings for the landing page (no auth required)."""
    result = await session.execute(
        select(PlatformSetting).where(PlatformSetting.key == "annual_discount_pct")
    )
    setting = result.scalar_one_or_none()
    try:
        discount = float(setting.value) if setting and setting.value else 17.0
    except (ValueError, TypeError):
        discount = 17.0
    return {"annual_discount_pct": discount}


@public_pricing_router.get(
    "/plans/{plan_id}/features",
    response_model=list[PlanFeatureOut],
)
async def public_plan_features(
    plan_id: UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    result = await session.execute(
        select(PlanFeature)
        .where(PlanFeature.plan_id == plan_id, PlanFeature.is_enabled.is_(True))
        .order_by(PlanFeature.sort_order, PlanFeature.feature_key)
    )
    return result.scalars().all()


@public_pricing_router.get(
    "/fee-tiers",
    response_model=list[FeeTierOut],
)
async def public_fee_tiers(session: Annotated[AsyncSession, Depends(get_db)]):
    result = await session.execute(
        select(TransactionFeeTier)
        .where(TransactionFeeTier.is_active.is_(True))
        .order_by(TransactionFeeTier.plan_id, TransactionFeeTier.minimum_amount)
    )
    return result.scalars().all()
