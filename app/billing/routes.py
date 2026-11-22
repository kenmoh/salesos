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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from app.core.dependencies import DbTenantDep, get_db
from app.core.platform_deps import PlatformDep, require_platform_permission
from app.core.responses import DataResponse, ok
from app.billing import fees as fee_service
from app.billing import invoices as invoice_service
from app.billing import subscriptions as sub_service
from app.billing.limits import invalidate_product_limit, invalidate_product_limit_for_plan
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
    UpgradeRequest,
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
        await invalidate_product_limit_for_plan(payload.plan_id, ctx.session)
        return feature
    feature = PlanFeature(**payload.model_dump())
    ctx.session.add(feature)
    await ctx.session.flush()
    await invalidate_product_limit_for_plan(payload.plan_id, ctx.session)
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
    await invalidate_product_limit_for_plan(feature.plan_id, ctx.session)
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


@tenant_billing_router.get(
    "/billing/limits",
)
async def tenant_plan_limits(ctx: DbTenantDep):
    """Current usage and caps for every plan-feature quota."""
    from app.billing.limits import (
        DOCUMENTS_FEATURE_KEY,
        PRODUCTS_FEATURE_KEY,
        STAFF_FEATURE_KEY,
        STORES_FEATURE_KEY,
        count_store_products,
        count_tenant_documents,
        count_tenant_stores,
        count_tenant_users,
        feature_limit_for_tenant,
    )

    tenant_id = ctx.user.business_id
    used_users = await count_tenant_users(tenant_id, ctx.session)
    used_stores = await count_tenant_stores(tenant_id, ctx.session)
    used_documents = await count_tenant_documents(tenant_id, ctx.session)

    return ok(
        {
            "users": {
                "used": used_users,
                "limit": await feature_limit_for_tenant(tenant_id, STAFF_FEATURE_KEY, ctx.session),
            },
            "stores": {
                "used": used_stores,
                "limit": await feature_limit_for_tenant(tenant_id, STORES_FEATURE_KEY, ctx.session),
            },
            "documents": {
                "used": used_documents,
                "limit": await feature_limit_for_tenant(tenant_id, DOCUMENTS_FEATURE_KEY, ctx.session),
            },
            "products": {
                "used": None,
                "limit": await feature_limit_for_tenant(tenant_id, PRODUCTS_FEATURE_KEY, ctx.session),
            },
        }
    )


@tenant_billing_router.post(
    "/billing/subscription/plan",
    response_model=SubscriptionOut,
)
async def tenant_change_plan(payload: PlanChangeRequest, ctx: DbTenantDep):
    from app.billing.upgrade import resolve_plan

    # Accept a slug or a plan UUID (the UI sends the plan id).
    plan = await resolve_plan(ctx.session, payload.plan_slug)
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    sub = await sub_service.change_plan(ctx.session, ctx.user.business_id, plan.slug)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    return sub


async def _tenant_display_currency(tenant_id, session) -> str:
    """Tenant's stored currency setting (USD tenants get USD pricing)."""
    from sqlalchemy import text

    from app.tenancy.settings import currency_code

    row = (
        await session.execute(text("SELECT settings FROM tenants WHERE id = :tid"))
        .mappings()
        .first()
    )
    raw = row["settings"] if row else None
    return currency_code(raw)


@tenant_billing_router.post(
    "/billing/subscription/upgrade/quote",
)
async def tenant_upgrade_quote(payload: UpgradeRequest, ctx: DbTenantDep):
    from uuid import UUID as _UUID

    from app.billing.upgrade import build_quote, resolve_plan

    plan = await resolve_plan(ctx.session, payload.plan)
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    sub = await sub_service.ensure_subscription(ctx.session, _UUID(ctx.user.business_id))
    currency = await _tenant_display_currency(_UUID(ctx.user.business_id), ctx.session)
    quote = await build_quote(
        ctx.session, _UUID(ctx.user.business_id), plan, payload.interval, sub, currency
    )
    return ok(quote)


@tenant_billing_router.post(
    "/billing/subscription/upgrade/pay",
)
async def tenant_upgrade_pay(payload: UpgradeRequest, ctx: DbTenantDep):
    from uuid import UUID as _UUID

    from app.billing import upgrade as upgrade_service
    from app.billing.invoices import debit_wallet
    from app.common.bridge import _get_sdb
    from app.payments.models import PaymentIntent
    from app.payments.repository import create_intent

    tenant_id = _UUID(ctx.user.business_id)
    plan = await upgrade_service.resolve_plan(ctx.session, payload.plan)
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    sub = await sub_service.ensure_subscription(ctx.session, tenant_id)
    tenant_currency = await _tenant_display_currency(tenant_id, ctx.session)
    quote = await upgrade_service.build_quote(
        ctx.session, tenant_id, plan, payload.interval, sub, tenant_currency
    )

    # Downgrades stay staged for period end — no immediate charge.
    current = await ctx.session.get(SubscriptionPlan, sub.plan_id)
    if current and float(current.monthly_price) > float(plan.monthly_price):
        sub = await sub_service.change_plan(ctx.session, ctx.user.business_id, plan.slug)
        return ok({"status": "scheduled", "subscription": sub})

    amount = Decimal(str(quote["amount_due"]))

    # Nothing to pay (same plan, or credit covers the whole price) — activate
    # directly instead of sending a zero-amount link to the gateway.
    if amount <= 0:
        await upgrade_service.apply_plan_upgrade(
            ctx.session,
            tenant_id=tenant_id,
            plan=plan,
            interval=payload.interval,
            actor_id=ctx.user.user_id,
        )
        await ctx.session.commit()
        return ok({"status": "completed", "amount": 0.0})

    if payload.payment_method == "wallet":
        debited = await debit_wallet(
            ctx.session,
            tenant_id=tenant_id,
            amount=amount,
            reference=f"plan-upgrade-{plan.slug}",
            description=f"Plan upgrade to {plan.name} ({payload.interval})",
        )
        if debited < amount:
            raise HTTPException(
                status_code=402,
                detail="Insufficient wallet balance. Fund your wallet or use a payment link.",
            )
        await upgrade_service.apply_plan_upgrade(
            ctx.session,
            tenant_id=tenant_id,
            plan=plan,
            interval=payload.interval,
            actor_id=ctx.user.user_id,
        )
        await ctx.session.commit()
        return ok({"status": "completed", "amount": float(amount)})

    # Gateway path: create a hosted payment link with typed intent metadata.
    import json
    import re
    from uuid import uuid4

    from app.common import flutterwave_service

    sdb = _get_sdb("payments")
    async with sdb.session() as pay_session:
        open_intent = (
            await pay_session.execute(
                select(PaymentIntent).where(
                    PaymentIntent.tenant_id == tenant_id,
                    PaymentIntent.status == "pending",
                    PaymentIntent.gateway_reference.like("SF-PLAN-UPGRADE-%"),
                ).order_by(PaymentIntent.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if open_intent:
            base_ref = re.sub(r"-R\d+$", "", open_intent.gateway_reference)
            attempt = re.search(r"-R(\d+)$", open_intent.gateway_reference)
            n = int(attempt.group(1)) + 1 if attempt else 1
            tx_ref = f"{base_ref}-R{n}"
            open_intent.amount = float(amount)
            open_intent.currency = quote["currency"]
            open_intent.gateway_reference = tx_ref
            open_intent.intent_metadata = json.dumps({
                "type": "plan_upgrade",
                "tenant_id": str(tenant_id),
                "plan_id": str(plan.id),
                "plan_slug": plan.slug,
                "interval": payload.interval,
                "gross": quote["gross"],
                "credit": quote["credit"],
                "amount": float(amount),
                "currency": quote["currency"],
                "created_by": ctx.user.user_id,
            })
            intent = open_intent
        else:
            tx_ref = f"SF-PLAN-UPGRADE-{uuid4().hex[:10].upper()}"
            intent = PaymentIntent(
                id=uuid4(),
                tenant_id=tenant_id,
                sale_id=None,
                cart_id=None,
                method="card",
                amount=float(amount),
                currency=quote["currency"],
                status="pending",
                gateway_reference=tx_ref,
                intent_metadata=json.dumps({
                    "type": "plan_upgrade",
                    "tenant_id": str(tenant_id),
                    "plan_id": str(plan.id),
                    "plan_slug": plan.slug,
                    "interval": payload.interval,
                    "gross": quote["gross"],
                    "credit": quote["credit"],
                    "amount": float(amount),
                    "currency": quote["currency"],
                    "created_by": ctx.user.user_id,
                }),
            )
        await create_intent(pay_session, intent) if not open_intent else None
        await pay_session.commit()
        intent_id = str(intent.id)

    from app.identity.repository import get_user_by_id as _get_user
    owner_email = None
    async with _get_sdb("identity").session() as id_session:
        actor_user = await _get_user(id_session, _UUID(ctx.user.user_id))
        owner_email = actor_user.email if actor_user else None

    link = await flutterwave_service.create_payment_link(
        amount=amount,
        currency=quote["currency"],
        tx_ref=tx_ref,
        customer_email=owner_email or "billing@storeflow.ng",
        title=f"Upgrade to {plan.name}",
        meta={"tenant_id": str(tenant_id), "intent_id": intent_id},
    )
    async with sdb.session() as pay_session:
        row = (
            await pay_session.execute(select(PaymentIntent).where(PaymentIntent.id == _UUID(intent_id)))
        ).scalar_one()
        row.authorization_url = link["link"]
        await pay_session.commit()
    return ok({"status": "pending", "link": link["link"], "amount": float(amount), "intent_id": intent_id})


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
async def public_list_plans(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    currency: str | None = Query(None),
):
    from app.common.geo import country_from_ip, display_currency
    from app.core.device import client_ip

    country = await country_from_ip(client_ip(request), request.headers)
    currency = (currency or display_currency(country)).upper()

    result = await session.execute(
        select(SubscriptionPlan)
        .where(SubscriptionPlan.is_active.is_(True))
        .options(
            selectinload(SubscriptionPlan.plan_features),
            selectinload(SubscriptionPlan.fee_tiers),
        )
        .order_by(SubscriptionPlan.sort_order, SubscriptionPlan.name)
    )
    plans = result.scalars().all()

    if currency == "USD":
        from app.common.fx import get_rate, ngn_to_usd

        snapshot = await get_rate()
        rate = snapshot["rate_decimal"]
        for plan in plans:
            plan.monthly_price = ngn_to_usd(plan.monthly_price, rate)
            plan.currency = "USD"
            for tier in plan.fee_tiers:
                tier.minimum_amount = ngn_to_usd(tier.minimum_amount, rate)
                if tier.maximum_amount is not None:
                    tier.maximum_amount = ngn_to_usd(tier.maximum_amount, rate)
                tier.fee_amount = ngn_to_usd(tier.fee_amount, rate)
                tier.currency = "USD"
    return plans


@public_pricing_router.get(
    "/settings",
    response_model=dict,
)
async def public_pricing_settings(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Public-safe pricing settings for the landing page (no auth required)."""
    from app.common.fx import get_rate, public_fx_settings
    from app.common.geo import country_from_ip, display_currency
    from app.core.device import client_ip

    result = await session.execute(
        select(PlatformSetting).where(PlatformSetting.key == "annual_discount_pct")
    )
    setting = result.scalar_one_or_none()
    try:
        discount = float(setting.value) if setting and setting.value else 17.0
    except (ValueError, TypeError):
        discount = 17.0

    country = await country_from_ip(client_ip(request), request.headers)
    snapshot = await get_rate()
    return {
        "annual_discount_pct": discount,
        "display_currency": display_currency(country),
        **public_fx_settings(snapshot),
    }


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
async def public_fee_tiers(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    currency: str | None = Query(None),
):
    from app.common.geo import country_from_ip, display_currency
    from app.core.device import client_ip

    country = await country_from_ip(client_ip(request), request.headers)
    currency = (currency or display_currency(country)).upper()
    result = await session.execute(
        select(TransactionFeeTier)
        .where(TransactionFeeTier.is_active.is_(True))
        .order_by(TransactionFeeTier.plan_id, TransactionFeeTier.minimum_amount)
    )
    tiers = result.scalars().all()
    if currency == "USD":
        from app.common.fx import get_rate, ngn_to_usd

        snapshot = await get_rate()
        rate = snapshot["rate_decimal"]
        for tier in tiers:
            tier.minimum_amount = ngn_to_usd(tier.minimum_amount, rate)
            if tier.maximum_amount is not None:
                tier.maximum_amount = ngn_to_usd(tier.maximum_amount, rate)
            tier.fee_amount = ngn_to_usd(tier.fee_amount, rate)
            tier.currency = "USD"
    return tiers
