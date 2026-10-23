import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.core.dependencies import TenantDep, DbTenantDep, require_permission
from app.core.responses import DataResponse, ok
from app.auth.schemas.schema import PaymentCreate, SplitPaymentCreate
from app.auth.schemas.responses import (
    BankInfo,
    CardPaymentResult,
    CashPaymentResult,
    PaymentIntentStatus,
    PaymentStatusResponse,
    PendingPaymentSummary,
    ResolvedAccount,
    SplitPaymentResult,
    SplitSuggestion,
    SubaccountCreated,
    SuccessResponse,
    TransferPaymentResult,
)
import app.common.bridge as bridge
from app.common.bridge import _get_sdb
from app.payments.schemas import SwitchMethodRequest
from app.common import flutterwave_service


class CashPayment(BaseModel):
    sale_id: str
    amount: Decimal


class CardPaymentInit(BaseModel):
    sale_id: str
    amount: Decimal
    customer_email: str
    customer_name: str | None = None


class TransferPaymentInit(BaseModel):
    sale_id: str
    amount: Decimal
    customer_email: str
    customer_name: str | None = None


class SubaccountCreate(BaseModel):
    account_bank: str
    account_number: str
    business_name: str
    business_mobile: str
    business_email: str | None = None
    business_contact: str | None = None
    split_value: float = 0.5
    split_type: str = "percentage"


class SubaccountUpdate(BaseModel):
    account_bank: str | None = None
    account_number: str | None = None
    business_name: str | None = None
    business_mobile: str | None = None
    business_email: str | None = None
    split_value: float | None = None
    split_type: str | None = None


class AccountResolve(BaseModel):
    account_number: str
    bank_code: str


router = APIRouter(prefix="/payments", tags=["Payments"])
webhook_router = APIRouter(tags=["Payments"])


@router.post(
    "/cash",
    response_model=DataResponse[CashPaymentResult],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def cash(payload: CashPayment, ctx: TenantDep):
    return ok(
        await bridge.record_payment(
            business_id=ctx.user.business_id,
            sale_id=str(payload.sale_id),
            method="cash",
            amount=payload.amount,
            reference=None,
            recorded_by=ctx.user.user_id,
        )
    )


@router.post(
    "/card",
    response_model=DataResponse[CardPaymentResult],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def card(payload: CardPaymentInit, ctx: TenantDep):
    return ok(
        await bridge.initiate_card_payment(
            business_id=ctx.user.business_id,
            sale_id=str(payload.sale_id),
            amount=payload.amount,
            customer_email=payload.customer_email,
            customer_name=payload.customer_name,
        )
    )


@router.post(
    "/transfer",
    response_model=DataResponse[TransferPaymentResult],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def transfer(payload: TransferPaymentInit, ctx: TenantDep):
    return ok(
        await bridge.initiate_transfer_payment(
            business_id=ctx.user.business_id,
            sale_id=str(payload.sale_id),
            amount=payload.amount,
            customer_email=payload.customer_email,
            customer_name=payload.customer_name,
        )
    )


class InitiatePaymentRequest(BaseModel):
    cart_id: str
    method: str
    customer_email: str
    customer_name: str | None = None
    customer_phone: str | None = None
    coupon_code: str | None = None
    store_id: str | None = None


class ConfirmPaymentRequest(BaseModel):
    intent_id: str


@router.post(
    "/initiate",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def initiate_payment_route(payload: InitiatePaymentRequest, ctx: TenantDep):
    try:
        return ok(
            await bridge.initiate_payment(
                business_id=ctx.user.business_id,
                cart_id=payload.cart_id,
                method=payload.method,
                customer_email=payload.customer_email,
                customer_name=payload.customer_name,
                customer_phone=payload.customer_phone,
                coupon_code=payload.coupon_code,
                store_id=payload.store_id,
                actor_id=ctx.user.user_id,
            )
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"message": str(e)},
        )


@router.post(
    "/confirm",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def confirm_payment_route(payload: ConfirmPaymentRequest, ctx: TenantDep):
    try:
        return ok(
            await bridge.confirm_payment(
                business_id=ctx.user.business_id,
                intent_id=payload.intent_id,
            )
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"message": str(e)},
        )


@router.get(
    "/split/suggest/{sale_id}",
    response_model=DataResponse[SplitSuggestion],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def suggest_split(sale_id: str, ctx: TenantDep):
    return ok(await bridge.suggest_even_split(sale_id=sale_id))


@router.post(
    "/split",
    response_model=DataResponse[SplitPaymentResult],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def split(payload: SplitPaymentCreate, ctx: TenantDep):
    return ok(
        await bridge.process_split_payment(
            business_id=ctx.user.business_id,
            sale_id=str(payload.sale_id),
            splits=payload.splits,
            customer_email=payload.customer_email,
            customer_name=payload.customer_name,
        )
    )


@router.post(
    "/setup/subaccount",
    response_model=DataResponse[SubaccountCreated],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def setup_subaccount(payload: SubaccountCreate, ctx: TenantDep):
    result = await bridge.create_subaccount_on_flutterwave(
        tenant_id=ctx.user.business_id,
        account_bank=payload.account_bank,
        account_number=payload.account_number,
        business_name=payload.business_name,
        business_mobile=payload.business_mobile,
        business_email=payload.business_email,
        business_contact=payload.business_contact,
        split_value=payload.split_value,
        split_type=payload.split_type,
    )
    return ok(result)


@router.get(
    "/setup/subaccount",
    response_model=DataResponse[SubaccountCreated],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def get_my_subaccount(ctx: TenantDep):
    result = await bridge.get_subaccount(tenant_id=ctx.user.business_id)
    if not result:
        raise HTTPException(status_code=404, detail="No subaccount found")
    return ok(result)


@router.patch(
    "/setup/subaccount",
    response_model=DataResponse[SubaccountCreated],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def update_my_subaccount(payload: SubaccountUpdate, ctx: TenantDep):
    result = await bridge.update_subaccount_on_flutterwave(
        tenant_id=ctx.user.business_id,
        account_bank=payload.account_bank,
        account_number=payload.account_number,
        business_name=payload.business_name,
        business_mobile=payload.business_mobile,
        business_email=payload.business_email,
        split_value=payload.split_value,
        split_type=payload.split_type,
    )
    return ok(result)


@router.delete(
    "/setup/subaccount",
    response_model=DataResponse[SuccessResponse],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def delete_my_subaccount(ctx: TenantDep):
    await bridge.delete_subaccount_on_flutterwave(tenant_id=ctx.user.business_id)
    return ok({"status": "deleted"})


@router.get("/setup/banks", response_model=DataResponse[list[BankInfo]])
async def banks(country: str = "NG"):
    return ok(await flutterwave_service.list_banks(country=country))


@router.post("/setup/verify-account", response_model=DataResponse[ResolvedAccount])
async def resolve_account(payload: AccountResolve):
    return ok(
        await flutterwave_service.resolve_account(
            account_number=payload.account_number,
            bank_code=payload.bank_code,
        )
    )


@router.post(
    "/cancel-pending",
    dependencies=[Depends(require_permission("payments:manage"))],
)
async def cancel_pending_intents(ctx: TenantDep, sale_id: str = Body(..., embed=True)):
    from uuid import UUID
    from app.payments.repository import cancel_pending_intents_by_sale

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        count = await cancel_pending_intents_by_sale(session, UUID(sale_id))
        await session.commit()

    return {"cancelled": count}


@router.post(
    "/cancel/{intent_id}",
    dependencies=[Depends(require_permission("payments:manage"))],
)
async def cancel_intent(intent_id: str, ctx: TenantDep):
    from uuid import UUID
    from app.payments.repository import cancel_intent as cancel_intent_fn

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        cancelled = await cancel_intent_fn(session, UUID(intent_id))
        await session.commit()

    if not cancelled:
        raise HTTPException(status_code=404, detail="Pending intent not found")
    return {"cancelled": True}


@router.post(
    "/switch-method/{intent_id}",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def switch_payment_method(intent_id: str, payload: SwitchMethodRequest, ctx: TenantDep):
    """Cancel the current intent and re-initiate with a new payment method.

    Returns the new intent details (intent_id, payment_url, etc.) so the
    frontend can show the appropriate UI for the new method.
    """
    from uuid import UUID
    from app.payments.repository import cancel_intent as cancel_intent_fn
    from app.payments.models import PaymentIntent

    new_method = payload.method
    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        try:
            intent_uuid = UUID(intent_id)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="Invalid intent ID")
        intent = await session.get(PaymentIntent, intent_uuid)
        if not intent:
            raise HTTPException(status_code=404, detail="Intent not found")
        if intent.status != "pending":
            raise HTTPException(status_code=400, detail=f"Intent is {intent.status}, not pending")

        cart_id = str(intent.cart_id) if intent.cart_id else None
        customer_name = intent.customer_name
        customer_phone = intent.customer_phone
        coupon_code = intent.coupon_code
        store_id = None
        actor_id = None

        snapshot = json.loads(intent.cart_snapshot or "{}")
        store_id = snapshot.get("store_id")
        actor_id = snapshot.get("actor_id")

        # Cancel the old intent
        await cancel_intent_fn(session, UUID(intent_id))
        await session.commit()

    if not cart_id:
        raise HTTPException(status_code=400, detail="Intent has no cart")

    # Re-initiate with the new method
    from app.common.bridge import initiate_payment

    result = await initiate_payment(
        business_id=ctx.user.business_id,
        cart_id=cart_id,
        method=new_method,
        customer_email=payload.customer_email or "",
        customer_name=customer_name,
        customer_phone=customer_phone,
        coupon_code=coupon_code,
        store_id=store_id,
        actor_id=actor_id,
    )

    return ok(result)


@router.get(
    "/pending",
    response_model=DataResponse[list[PendingPaymentSummary]],
    dependencies=[Depends(require_permission("payments:read"))],
)
async def pending_payments(ctx: TenantDep):
    from uuid import UUID
    from app.payments.repository import get_pending_intents_by_tenant
    from app.sales.repository import get_sale_by_id
    from app.catalog.qr import generate_qr_base64

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        intents = await get_pending_intents_by_tenant(session, UUID(ctx.user.business_id))

    results = []
    for intent in intents:
        # Fetch sale details from sales DB (may be null for intent-first flow)
        sale = None
        if intent.sale_id:
            sdb_sales = _get_sdb("sales")
            async with sdb_sales.session() as sales_session:
                sale = await get_sale_by_id(sales_session, intent.sale_id)

        # Extract sale number from cart snapshot if no sale yet
        sale_number = sale.sale_number if sale else ""
        if not sale_number and intent.cart_snapshot:
            try:
                snap = json.loads(intent.cart_snapshot)
                sale_number = snap.get("sale_number", "")
            except Exception:
                pass

        summary = PendingPaymentSummary(
            intent_id=str(intent.id),
            sale_id=str(intent.sale_id) if intent.sale_id else "",
            sale_number=sale_number,
            method=intent.method,
            amount=float(intent.amount),
            created_at=intent.created_at.isoformat() if intent.created_at else "",
            authorization_url=intent.authorization_url,
            tx_ref=intent.gateway_reference,
            account_number=intent.dva_account_number,
            bank_name=intent.bank_name,
        )

        # Generate QR code for card payments if we have authorization_url
        if intent.method == "card" and intent.authorization_url:
            try:
                summary.qr_code_base64 = generate_qr_base64(intent.authorization_url)
            except Exception:
                pass

        # Parse metadata for transfer details
        if intent.method == "transfer" and intent.intent_metadata:
            try:
                import json
                meta = json.loads(intent.intent_metadata) if isinstance(intent.intent_metadata, str) else intent.intent_metadata
                summary.expiry_date = meta.get("expiry_date")
                if not summary.account_number:
                    summary.account_number = meta.get("account_number")
                if not summary.bank_name:
                    summary.bank_name = meta.get("bank_name")
            except Exception:
                pass

        results.append(summary)

    return ok(results)


@router.get(
    "/status/{sale_id}",
    response_model=DataResponse[PaymentStatusResponse],
    dependencies=[Depends(require_permission("payments:read"))],
)
async def payment_status(sale_id: str, ctx: TenantDep):
    from uuid import UUID
    from sqlalchemy import select
    from app.payments.models import PaymentIntent, Payment
    from app.sales.repository import get_sale_by_id

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        # Try as intent_id first (for intent-first flow polling)
        intent = None
        try:
            intent_result = await session.execute(
                select(PaymentIntent).where(PaymentIntent.id == UUID(sale_id))
            )
            intent = intent_result.scalar_one_or_none()
        except (ValueError, AttributeError):
            pass

        if intent:
            intent_status = PaymentIntentStatus(
                method=intent.method,
                status=intent.status,
                tx_ref=intent.gateway_reference,
                amount=intent.amount,
            )

            if intent.sale_id:
                sale = await get_sale_by_id(session, intent.sale_id)
                payments_result = await session.execute(
                    select(Payment).where(Payment.sale_id == intent.sale_id)
                )
                payments = payments_result.scalars().all()
                amount_paid = sum(float(p.amount) for p in payments)
                sale_total = float(sale.total) if sale else float(intent.amount)
            else:
                payments = []
                amount_paid = 0.0
                sale_total = float(intent.amount)

            return ok(
                PaymentStatusResponse(
                    sale_id=sale_id,
                    status=intent.status,
                    amount_paid=round(amount_paid, 2),
                    total=sale_total,
                    intents=[intent_status],
                )
            )

        # Legacy: look up by sale_id
        try:
            sale_uuid = UUID(sale_id)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=404, detail="Sale not found")

        sale = await get_sale_by_id(session, sale_uuid)
        if not sale:
            raise HTTPException(status_code=404, detail="Sale not found")

        intents_result = await session.execute(
            select(PaymentIntent).where(PaymentIntent.sale_id == UUID(sale_id))
        )
        intents = intents_result.scalars().all()

        payments_result = await session.execute(
            select(Payment).where(Payment.sale_id == UUID(sale_id))
        )
        payments = payments_result.scalars().all()

        intent_statuses = []
        for i in intents:
            intent_statuses.append(
                PaymentIntentStatus(
                    method=i.method,
                    status=i.status,
                    tx_ref=i.gateway_reference,
                    amount=i.amount,
                )
            )

        amount_paid = sum(float(p.amount) for p in payments)
        status = "completed" if amount_paid >= float(sale.total) else "pending" if amount_paid > 0 else "pending"

        return ok(
            PaymentStatusResponse(
                sale_id=sale_id,
                status=status,
                amount_paid=round(amount_paid, 2),
                total=float(sale.total),
                intents=intent_statuses,
            )
        )


@webhook_router.post("/webhook/flutterwave", include_in_schema=False)
@router.post("/webhook/flutterwave", include_in_schema=False)
async def flutterwave_webhook(request: Request, verif_hash: str = Header(default="")):
    body = await request.body()

    if not flutterwave_service.verify_webhook_signature(body, verif_hash):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)
    event = payload.get("event")
    data = payload.get("data", {})
    event_id = data.get("id") or data.get("flw_ref") or data.get("reference", "")

    if event_id:
        from sqlalchemy import text
        from app.common.bridge import _get_shared_engine
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        engine = _get_shared_engine()
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as session:
            async with session.begin():
                existing = await session.execute(
                    text("""
                        SELECT 1 FROM webhook_logs
                        WHERE event_id = :event_id AND event_type = :event_type
                    """),
                    {"event_id": str(event_id), "event_type": event},
                )
            if existing.scalar_one_or_none() is not None:
                return ok({"received": True, "duplicate": True})

    if event == "charge.completed":
        tx_ref = data.get("tx_ref", "")
        meta = data.get("meta", {}) or {}
        business_id = meta.get("business_id")
        sale_id = meta.get("sale_id")
        intent_id = meta.get("intent_id")

        # Flutterwave does not reliably echo link metadata back through
        # bank-transfer charges (a hosted-checkout transfer completes as a
        # BANK_TRANSFER_TRANSACTION without meta), so routing on meta alone
        # confirms nothing. The tx_ref stamped on the link is always present,
        # so resolve the intent by its gateway reference and let the intent
        # carry business and kind.
        resolved_intent = None
        if intent_id or tx_ref:
            from uuid import UUID as _UUID
            from sqlalchemy import select as _sa_select
            from app.payments.models import PaymentIntent
            sdb = _get_sdb("payments")
            async with sdb.session() as session:
                if intent_id:
                    try:
                        resolved_intent = (
                            await session.execute(
                                _sa_select(PaymentIntent).where(
                                    PaymentIntent.id == _UUID(intent_id),
                                    PaymentIntent.status == "pending",
                                )
                            )
                        ).scalar_one_or_none()
                    except ValueError:
                        resolved_intent = None
                if resolved_intent is None and tx_ref:
                    resolved_intent = (
                        await session.execute(
                            _sa_select(PaymentIntent).where(
                                PaymentIntent.gateway_reference == tx_ref,
                                PaymentIntent.status == "pending",
                            )
                        )
                    ).scalar_one_or_none()

        # Settlement flow: a link standing for the tenant's whole pending
        # fee balance. The frozen ledger rows must be swept before the
        # checkout confirmation path sees the same intent and misreads it
        # as a cart payment.
        if resolved_intent is not None:
            try:
                intent_meta = json.loads(resolved_intent.intent_metadata or "{}")
            except (TypeError, ValueError):
                intent_meta = {}
            is_settlement = intent_meta.get("type") == "settlement"

            if is_settlement:
                try:
                    await bridge.confirm_settlement_payment(
                        business_id=str(resolved_intent.tenant_id),
                        intent_id=str(resolved_intent.id),
                        gateway_data=data,
                    )
                    await _log_webhook_event(event, event_id, payload)
                    return ok({"received": True, "confirmed": True, "kind": "settlement"})
                except ValueError as e:
                    if str(e) != "not_settlement":
                        pass

            # Intent-first flow: confirm the intent which creates the sale
            try:
                await bridge.confirm_payment(
                    business_id=str(resolved_intent.tenant_id),
                    intent_id=str(resolved_intent.id),
                )
                await _log_webhook_event(event, event_id, payload)
                return ok({"received": True, "confirmed": True})
            except ValueError:
                pass

        # Legacy flow
        if business_id and sale_id:
            try:
                await bridge.confirm_split_card_payment(
                    business_id=business_id,
                    sale_id=sale_id,
                    tx_ref=tx_ref,
                    gateway_data=data,
                )
                await _log_webhook_event(event, event_id, payload)
            except ValueError:
                pass

    elif event == "charge.failed":
        tx_ref = data.get("tx_ref", "")
        meta = data.get("meta", {}) or {}
        business_id = meta.get("business_id")
        intent_id = meta.get("intent_id")

        # Mark intent as failed
        if intent_id:
            try:
                from uuid import UUID
                from app.payments.models import PaymentIntent
                sdb = _get_sdb("payments")
                async with sdb.session() as session:
                    intent = await session.get(PaymentIntent, UUID(intent_id))
                    if intent:
                        intent.status = "failed"
                        await session.commit()
            except Exception:
                pass

        import logging
        logger = logging.getLogger("storeflow.payments.webhook")
        logger.warning("Payment failed: tx_ref=%s business_id=%s", tx_ref, business_id)

    elif event == "transfer.completed":
        reference = data.get("reference", "")
        meta = data.get("meta", {}) or {}
        business_id = meta.get("business_id")
        sale_id = meta.get("sale_id")
        intent_id = meta.get("intent_id")

        # Intent-first flow
        if business_id and intent_id:
            try:
                await bridge.confirm_payment(
                    business_id=business_id,
                    intent_id=intent_id,
                )
                return ok({"received": True, "confirmed": True})
            except ValueError:
                pass

        # Legacy flow
        if business_id and sale_id:
            try:
                await bridge.confirm_split_transfer_payment(
                    business_id=business_id,
                    sale_id=sale_id,
                    tx_ref=reference,
                    gateway_data=data,
                )
            except ValueError:
                pass

    elif event == "transfer.failed":
        reference = data.get("reference", "")
        import logging
        logger = logging.getLogger("storeflow.payments.webhook")
        logger.warning("Transfer failed: reference=%s", reference)

    return ok({"received": True})


async def _log_webhook_event(event: str, event_id: str, payload: dict) -> None:
    """Record a webhook as processed once a branch actually confirmed it.

    Logging happens after a successful confirm, never on arrival: an event
    logged on arrival is shielded from retries by the dedupe check even
    when nothing acted on it, which is how a confirmed-nowhere payment
    goes permanently stuck.
    """
    from sqlalchemy import text
    from app.common.bridge import _get_shared_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    if not event_id:
        return
    engine = _get_shared_engine()
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("""
                    INSERT INTO webhook_logs
                        (id, event_type, event_id, payload, processed, created_at)
                    VALUES
                        (:id, :event_type, :event_id, :payload, 1, now())
                    ON CONFLICT DO NOTHING
                """),
                {
                    # Raw SQL bypasses the ORM default, so the uuid
                    # primary key must be supplied here.
                    "id": uuid4(),
                    "event_type": event,
                    "event_id": str(event_id),
                    "payload": json.dumps(payload),
                },
            )
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════════════
#  SETTLEMENT ENDPOINTS (tenant-facing)
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/settlement/link",
    dependencies=[Depends(require_permission("payments:create"))],
)
async def create_settlement_link(ctx: DbTenantDep):
    """A Flutterwave link for the tenant's whole outstanding fee balance.

    Pressing it again while a link is still unpaid re-issues the same link
    instead of stacking a second one. Confirmation arrives by webhook, which
    flips the pending ledger rows that were summed into the link.
    """
    from app.common.bridge import create_settlement_payment

    try:
        result = await create_settlement_payment(
            business_id=ctx.user.business_id,
            actor_id=ctx.user.user_id,
        )
    except ValueError as e:
        code = 400 if str(e) == "nothing_pending" else 409
        raise HTTPException(status_code=code, detail=str(e))
    return ok(result)


@router.post(
    "/settlement/reconcile",
    response_model=DataResponse[dict],
    dependencies=[Depends(require_permission("payments:create"))],
)
async def reconcile_settlement_payment(ctx: TenantDep, tx_ref: str = Body(default="", embed=True)):
    """Manually confirm a paid settlement whose webhook never got through.

    Asks Flutterwave directly whether the transaction for this reference is
    successful, and only then sweeps the pending ledger rows — the exact
    confirmation the webhook would have carried, for events the gateway
    swallows or our side rejects before they could act. Without a
    reference the newest pending settlement intent for the tenant is used,
    which is what the app holds when someone says "I've paid".
    """
    from uuid import UUID as _UUID
    from sqlalchemy import select
    from app.common.bridge import confirm_settlement_payment
    from app.payments.models import PaymentIntent

    tx_ref = (tx_ref or "").strip()
    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        stmt = (
            select(PaymentIntent)
            .where(
                PaymentIntent.tenant_id == _UUID(ctx.user.business_id),
                PaymentIntent.status == "pending",
                PaymentIntent.gateway_reference.like("SF-SETTLE-%"),
            )
            .order_by(PaymentIntent.created_at.desc())
            .limit(1)
        )
        if tx_ref:
            stmt = stmt.where(PaymentIntent.gateway_reference == tx_ref)
        intent = (await session.execute(stmt)).scalar_one_or_none()
        if intent is None:
            raise HTTPException(
                status_code=404,
                detail="No pending settlement found with this reference",
            )
        try:
            intent_meta = json.loads(intent.intent_metadata or "{}")
        except (TypeError, ValueError):
            intent_meta = {}
        if intent_meta.get("type") != "settlement":
            raise HTTPException(
                status_code=400,
                detail="This reference is not a settlement payment",
            )

    try:
        tx_data = await flutterwave_service.verify_transaction(tx_ref=tx_ref)
    except flutterwave_service.FlutterwaveError as e:
        raise HTTPException(
            status_code=409,
            detail=f"Flutterwave has not marked this transaction successful: {e}",
        )

    charged = float(tx_data.get("amount") or 0)
    if charged + 0.01 < float(intent.amount):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Amount mismatch: gateway charged {charged:,.2f} but the "
                f"link stands for {float(intent.amount):,.2f}"
            ),
        )

    result = await confirm_settlement_payment(
        business_id=ctx.user.business_id,
        intent_id=str(intent.id),
        gateway_data={
            "source": "manual_reconcile",
            "tx_ref": tx_ref,
            "flw_ref": tx_data.get("flw_ref"),
            "flw_id": tx_data.get("id"),
            "status": tx_data.get("status"),
        },
    )
    return ok({
        "cleared": result["cleared"],
        "amount": float(intent.amount),
        "intent_id": str(intent.id),
    })


@router.get(
    "/settlement",
    dependencies=[Depends(require_permission("payments:read"))],
)
async def list_settlement(
    ctx: DbTenantDep,
    status: str | None = Query(None, description="Filter: pending, deducted"),
):
    from sqlalchemy import desc, func, select

    from app.platform.models import PlatformFeeLedger

    query = (
        select(PlatformFeeLedger)
        .where(PlatformFeeLedger.tenant_id == ctx.user.business_id)
        .order_by(desc(PlatformFeeLedger.created_at))
    )
    if status:
        query = query.where(PlatformFeeLedger.status == status)

    result = await ctx.session.execute(query)
    items = result.scalars().all()

    pending_result = await ctx.session.execute(
        select(func.coalesce(func.sum(PlatformFeeLedger.amount), 0)).where(
            PlatformFeeLedger.tenant_id == ctx.user.business_id,
            PlatformFeeLedger.status == "pending",
        )
    )
    total_pending = float(pending_result.scalar())

    deducted_result = await ctx.session.execute(
        select(func.coalesce(func.sum(PlatformFeeLedger.amount), 0)).where(
            PlatformFeeLedger.tenant_id == ctx.user.business_id,
            PlatformFeeLedger.status == "deducted",
        )
    )
    total_deducted = float(deducted_result.scalar())

    return ok(
        {
            "items": [
                {
                    "id": str(e.id),
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
            "total_pending": total_pending,
            "total_deducted": total_deducted,
            "total": len(items),
        }
    )


@router.get(
    "/settlement/balance",
    dependencies=[Depends(require_permission("payments:read"))],
)
async def settlement_balance(ctx: DbTenantDep):
    from app.platform.fee_calculator import get_max_pending_balance, get_pending_fee_balance

    pending = await get_pending_fee_balance(ctx.session, ctx.user.business_id)
    max_pending = await get_max_pending_balance(ctx.session)

    return ok(
        {
            "pending_balance": pending,
            "max_pending_balance": max_pending,
            "is_blocked": pending >= max_pending,
        }
    )

@webhook_router.get(
    "/payment/callback",
    include_in_schema=False,
)
async def payment_callback(
    status: str = "",
    tx_ref: str = "",
    transaction_id: str = "",
):
    """Browser landing page after a hosted payment completes.

    The webhook, not this page, confirms the payment — Flutterwave appends
    `status`/`tx_ref`/`transaction_id` to the redirect, and those say what
    happened. The intent behind the tx_ref supplies the amount and what the
    payment was standing for, so the page can show a real receipt rather
    than a bare "thanks".
    """
    from sqlalchemy import select

    from app.common.bridge import _get_sdb
    from app.payments.models import PaymentIntent

    display_status = (status or "").lower() or "unknown"
    paid_amount = None
    paid_for = None

    if tx_ref:
        try:
            async with _get_sdb("payments").session() as session:
                row = (
                    await session.execute(
                        select(PaymentIntent).where(
                            PaymentIntent.gateway_reference == tx_ref
                        )
                    )
                ).scalar_one_or_none()
            if row:
                paid_amount = float(row.amount)
                try:
                    metadata = json.loads(row.intent_metadata or "{}")
                    if metadata.get("type") == "settlement":
                        paid_for = "Clear outstanding settlement"
                    else:
                        paid_for = "Purchase"
                except (TypeError, ValueError):
                    pass
        except Exception:
            pass  # the page degrades to "amount unknown" without the intent

    if display_status in ("successful", "completed"):
        badge, color, headline, sub = (
            "✓",
            "#059669",
            "Payment received",
            "The app will refresh on its own." + (" Amount is being confirmed by the webhook now." if paid_amount else ""),
        )
    elif display_status == "cancelled":
        badge, color, headline, sub = (
            "!",
            "#D97706",
            "Payment cancelled",
            "You were not charged. You can try again in the app.",
        )
    elif display_status in ("failed", "declined"):
        badge, color, headline, sub = (
            "✕",
            "#DC2626",
            "Payment failed",
            "Nothing was charged. Try another method in the app.",
        )
    else:
        badge, color, headline, sub = (
            "?",
            "#6B7280",
            "Status unknown",
            "If you completed the payment, the app will reflect it shortly.",
        )

    deep_link = f"sellmoh://payment/callback?tx_ref={tx_ref}&status={status}"
    amount_html = (
        f"<div class='amount'>₦{paid_amount:,.2f}</div>"
        if paid_amount is not None
        else ""
    )
    purpose_html = (
        f"<div class='purpose'>{paid_for}</div>" if paid_for else ""
    )
    ref_html = (
        f"<div class='ref'>Ref · {tx_ref or transaction_id or '—'}</div>"
        if (tx_ref or transaction_id)
        else ""
    )

    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StoreFlow — {headline}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    margin: 0; min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
    background: linear-gradient(160deg, #f8fafc 0%, #eef2f7 100%);
    color: #0f172a; padding: 24px;
  }}
  .card {{
    background: #fff; border-radius: 20px; box-shadow: 0 10px 40px rgba(15,23,42,.10);
    max-width: 420px; width: 100%; padding: 40px 28px; text-align: center;
  }}
  .badge {{
    width: 64px; height: 64px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 18px; font-size: 30px; font-weight: 700;
    color: #fff; background: {color};
  }}
  h1 {{ font-size: 22px; margin: 0 0 6px; }}
  .sub {{ color: #64748b; font-size: 14px; margin: 0 0 4px; }}
  .purpose {{ color: #334155; font-size: 13px; font-weight: 600; margin: 10px 0 0; }}
  .amount {{ font-size: 34px; font-weight: 800; margin: 14px 0 2px; letter-spacing: -0.5px; }}
  .ref {{ color: #94a3b8; font-size: 11px; margin-top: 12px; word-break: break-all; }}
  .divider {{ height: 1px; background: #e2e8f0; margin: 20px 0; }}
  a.btn {{
    display: block; text-decoration: none; background: #1D4ED8; color: #fff;
    font-weight: 700; font-size: 15px; padding: 14px; border-radius: 12px;
  }}
  .footnote {{ font-size: 11px; color: #94a3b8; margin-top: 14px; }}
</style>
</head>
<body>
  <main class="card">
    <div class="badge">{badge}</div>
    <h1>{headline}</h1>
    <p class="sub">{sub}</p>
    {purpose_html}
    {amount_html}
    {ref_html}
    <div class="divider"></div>
    <a class="btn" href="{deep_link}">Return to StoreFlow</a>
    <p class="footnote">Opens the StoreFlow app — if nothing happens, just reopen the app.</p>
  </main>
</body>
</html>"""
    return HTMLResponse(html)


@webhook_router.get(
    "/payment/transfer/{token}",
    include_in_schema=False,
)
async def transfer_details_page(token: str):
    """Public page behind the transfer QR code.

    New QRs carry the payment intent id. The details live on that intent,
    so the URL stays short enough for a low-density, scannable QR. Longer
    self-contained tokens from older builds still work and are checked by
    signature, without touching the database.
    """
    from app.payments.transfer_page import (
        parse_transfer_token,
        render_expired_page,
        render_not_found_page,
        render_transfer_page,
    )

    if "." in token:
        payload, expired = parse_transfer_token(token)
        if payload is None:
            return HTMLResponse(render_not_found_page(), status_code=404)
        if expired:
            return HTMLResponse(render_expired_page(), status_code=410)
        return HTMLResponse(render_transfer_page(payload))

    try:
        intent_id = UUID(token)
    except ValueError:
        return HTMLResponse(render_not_found_page(), status_code=404)

    from app.payments.models import PaymentIntent

    sdb = _get_sdb("payments")
    async with sdb.session() as session:
        intent = await session.get(PaymentIntent, intent_id)

    if not intent or not intent.dva_account_number:
        return HTMLResponse(render_not_found_page(), status_code=404)

    if intent.expires_at and intent.expires_at < datetime.now(UTC):
        return HTMLResponse(render_expired_page(), status_code=410)

    meta = intent.intent_metadata
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    if not isinstance(meta, dict):
        meta = {}

    payload = {
        "account_number": intent.dva_account_number,
        "bank_name": intent.bank_name or "",
        "amount": float(intent.amount),
        "business_name": str(meta.get("business_name") or ""),
        "store_name": str(meta.get("store_name") or ""),
        "expiry_date": str(
            meta.get("expiry_date") or (intent.expires_at.isoformat() if intent.expires_at else "")
        ),
        "tx_ref": str(meta.get("tx_ref") or intent.gateway_reference or ""),
    }
    return HTMLResponse(render_transfer_page(payload))
