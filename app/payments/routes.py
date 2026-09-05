import json
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.core.dependencies import TenantDep, require_permission
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
        # Fetch sale details from sales DB
        sdb_sales = _get_sdb("sales")
        async with sdb_sales.session() as sales_session:
            sale = await get_sale_by_id(sales_session, intent.sale_id)

        summary = PendingPaymentSummary(
            sale_id=str(intent.sale_id),
            sale_number=sale.sale_number if sale else "",
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
        sale = await get_sale_by_id(session, UUID(sale_id))
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

        if business_id and sale_id:
            try:
                await bridge.confirm_split_card_payment(
                    business_id=business_id,
                    sale_id=sale_id,
                    tx_ref=tx_ref,
                    gateway_data=data,
                )
            except ValueError:
                pass

    elif event == "charge.failed":
        tx_ref = data.get("tx_ref", "")
        meta = data.get("meta", {}) or {}
        business_id = meta.get("business_id")
        import logging
        logger = logging.getLogger("storeflow.payments.webhook")
        logger.warning("Payment failed: tx_ref=%s business_id=%s", tx_ref, business_id)

    elif event == "transfer.completed":
        reference = data.get("reference", "")
        meta = data.get("meta", {}) or {}
        business_id = meta.get("business_id")
        sale_id = meta.get("sale_id")

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

    if event_id:
        from sqlalchemy import text
        from app.common.bridge import _get_shared_engine
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        engine = _get_shared_engine()
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text("""
                        INSERT INTO webhook_logs
                            (event_type, event_id, payload, processed, created_at)
                        VALUES
                            (:event_type, :event_id, :payload, true, now())
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "event_type": event,
                        "event_id": str(event_id),
                        "payload": json.dumps(payload),
                    },
                )
            await session.commit()

    return ok({"received": True})
