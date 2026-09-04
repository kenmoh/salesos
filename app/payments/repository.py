"""Payment service repository layer.

This module provides database access functions for payment-related models.
All functions use async SQLAlchemy sessions for non-blocking database operations.

Functions are organized by model:
- PaymentIntent: Intent creation and status updates
- Payment: Payment creation and queries
- WebhookLog: Webhook event logging
- Subaccount: Subaccount management (CRUD operations)
- DedicatedVirtualAccount: Virtual account management

All repository functions follow the pattern:
1. Accept an async session and parameters
2. Execute database queries
3. Return model instances or None

Note: All functions require an active database session.
Caller is responsible for committing the transaction.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.payments.models import (
    DedicatedVirtualAccount,
    Payment,
    PaymentIntent,
    Subaccount,
    WebhookLog,
)


async def get_intent_by_reference(session: AsyncSession, reference: str) -> PaymentIntent | None:
    """Retrieve a payment intent by its gateway reference.

    Args:
        session: Database session for querying.
        reference: Gateway transaction reference (e.g., "SF-ABC123").

    Returns:
        The matching PaymentIntent, or None if not found.
    """
    result = await session.execute(
        select(PaymentIntent).where(PaymentIntent.gateway_reference == reference)
    )
    return result.scalar_one_or_none()


async def get_intent_by_id(session: AsyncSession, intent_id: UUID) -> PaymentIntent | None:
    """Retrieve a payment intent by its unique identifier.

    Args:
        session: Database session for querying.
        intent_id: Unique identifier of the payment intent.

    Returns:
        The matching PaymentIntent, or None if not found.
    """
    result = await session.execute(select(PaymentIntent).where(PaymentIntent.id == intent_id))
    return result.scalar_one_or_none()


async def create_intent(session: AsyncSession, intent: PaymentIntent) -> PaymentIntent:
    """Create a new payment intent.

    Args:
        session: Database session for the operation.
        intent: PaymentIntent instance to persist.

    Returns:
        The created PaymentIntent with generated fields.
    """
    session.add(intent)
    await session.flush()
    return intent


async def update_intent_status(
    session: AsyncSession, intent_id: UUID, status: str, gateway_data: dict | None = None
) -> None:
    """Update the status of a payment intent.

    Args:
        session: Database session for the operation.
        intent_id: Unique identifier of the intent to update.
        status: New status value (e.g., "completed", "failed", "expired").
        gateway_data: Optional gateway response data (currently unused).

    Raises:
        ValueError: If no intent is found with the given identifier.
    """
    result = await session.execute(select(PaymentIntent).where(PaymentIntent.id == intent_id))
    intent = result.scalar_one()
    intent.status = status
    await session.flush()


async def get_pending_intents_by_tenant(
    session: AsyncSession, tenant_id: UUID
) -> list[PaymentIntent]:
    """Retrieve all pending payment intents for a tenant.

    Args:
        session: Database session for querying.
        tenant_id: Unique identifier of the tenant.

    Returns:
        List of pending PaymentIntent records, newest first.
    """
    result = await session.execute(
        select(PaymentIntent)
        .where(PaymentIntent.tenant_id == tenant_id, PaymentIntent.status == "pending")
        .order_by(PaymentIntent.created_at.desc())
    )
    return list(result.scalars().all())


async def create_payment(session: AsyncSession, payment: Payment) -> Payment:
    """Create a new payment record.

    Args:
        session: Database session for the operation.
        payment: Payment instance to persist.

    Returns:
        The created Payment with generated fields.
    """
    session.add(payment)
    await session.flush()
    return payment


async def get_payments_by_sale(session: AsyncSession, sale_id: UUID) -> list[Payment]:
    """Retrieve all payments for a specific sale.

    Args:
        session: Database session for querying.
        sale_id: Unique identifier of the sale.

    Returns:
        List of Payment records ordered by creation time (newest first).
    """
    result = await session.execute(
        select(Payment).where(Payment.sale_id == sale_id).order_by(Payment.created_at.desc())
    )
    return list(result.scalars().all())


async def log_webhook(session: AsyncSession, log: WebhookLog) -> None:
    """Log a webhook event for auditing.

    Args:
        session: Database session for the operation.
        log: WebhookLog instance to persist.
    """
    session.add(log)
    await session.flush()


async def get_subaccount_by_tenant(session: AsyncSession, tenant_id: UUID) -> Subaccount | None:
    """Retrieve the active subaccount for a tenant.

    Args:
        session: Database session for querying.
        tenant_id: Unique identifier of the tenant business.

    Returns:
        The active Subaccount, or None if not found.
    """
    result = await session.execute(
        select(Subaccount).where(Subaccount.tenant_id == tenant_id, Subaccount.is_active == True)
    )
    return result.scalar_one_or_none()


async def get_subaccount_by_code(session: AsyncSession, subaccount_code: str) -> Subaccount | None:
    """Retrieve a subaccount by its Flutterwave subaccount code.

    Args:
        session: Database session for querying.
        subaccount_code: Flutterwave subaccount code (e.g., "SUB_xxx").

    Returns:
        The matching Subaccount, or None if not found.
    """
    result = await session.execute(
        select(Subaccount).where(Subaccount.subaccount_code == subaccount_code)
    )
    return result.scalar_one_or_none()


async def list_subaccounts(session: AsyncSession) -> list[Subaccount]:
    """List all active subaccounts.

    Args:
        session: Database session for querying.

    Returns:
        List of active Subaccount records ordered by creation time (newest first).
    """
    result = await session.execute(
        select(Subaccount)
        .where(Subaccount.is_active == True)
        .order_by(Subaccount.created_at.desc())
    )
    return list(result.scalars().all())


async def upsert_subaccount(session: AsyncSession, subaccount: Subaccount) -> Subaccount:
    """Insert or update a subaccount for a tenant.

    If an active subaccount already exists for the tenant, it is updated.
    Otherwise, a new subaccount is created.

    Args:
        session: Database session for the operation.
        subaccount: Subaccount instance with the data to persist.

    Returns:
        The created or updated Subaccount.
    """
    existing = await get_subaccount_by_tenant(session, subaccount.tenant_id)
    if existing:
        existing.subaccount_code = subaccount.subaccount_code
        existing.account_number = subaccount.account_number
        existing.bank_code = subaccount.bank_code
        existing.bank_name = subaccount.bank_name
        existing.business_name = subaccount.business_name
        existing.percentage_charge = subaccount.percentage_charge
        existing.raw_response = subaccount.raw_response
        await session.flush()
        return existing
    session.add(subaccount)
    await session.flush()
    return subaccount


async def get_dva_by_tenant(
    session: AsyncSession, tenant_id: UUID
) -> DedicatedVirtualAccount | None:
    """Retrieve the active dedicated virtual account for a tenant.

    Args:
        session: Database session for querying.
        tenant_id: Unique identifier of the tenant business.

    Returns:
        The active DedicatedVirtualAccount, or None if not found.
    """
    result = await session.execute(
        select(DedicatedVirtualAccount).where(
            DedicatedVirtualAccount.tenant_id == tenant_id,
            DedicatedVirtualAccount.is_active == True,
        )
    )
    return result.scalar_one_or_none()


async def upsert_dva(
    session: AsyncSession, dva: DedicatedVirtualAccount
) -> DedicatedVirtualAccount:
    """Insert or update a dedicated virtual account for a tenant.

    If an active virtual account already exists for the tenant, it is updated.
    Otherwise, a new virtual account is created.

    Args:
        session: Database session for the operation.
        dva: DedicatedVirtualAccount instance with the data to persist.

    Returns:
        The created or updated DedicatedVirtualAccount.
    """
    existing = await get_dva_by_tenant(session, dva.tenant_id)
    if existing:
        existing.account_number = dva.account_number
        existing.account_name = dva.account_name
        existing.bank_name = dva.bank_name
        existing.bank_code = dva.bank_code
        existing.customer_code = dva.customer_code
        existing.customer_email = dva.customer_email
        existing.customer_name = dva.customer_name
        existing.dva_id = dva.dva_id
        existing.raw_response = dva.raw_response
        await session.flush()
        return existing
    session.add(dva)
    await session.flush()
    return dva
