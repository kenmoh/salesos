"""Payment service data models.

This module defines the database models for the payments service,
including subaccounts, payment intents, payments, and webhook logs.

These models use SQLAlchemy ORM with async support and are mapped
to the "payments" PostgreSQL schema.

Schema: payments
Tables: subaccounts, dedicated_virtual_accounts, payment_intents, payments, webhook_logs
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base import StoreFlowBase


class Subaccount(StoreFlowBase):
    """Flutterwave subaccount for payment splitting.

    Each tenant business gets a subaccount to receive split payments.
    The platform automatically splits card/transfer payments between
    the platform's main account and tenant subaccounts.

    Attributes:
        id: Unique subaccount identifier (primary key).
        tenant_id: Identifier of the tenant business this subaccount belongs to.
        subaccount_code: Flutterwave subaccount code (e.g., "SUB_xxx").
        account_number: Bank account number for settlements.
        bank_code: Bank code (e.g., "044" for Access Bank).
        bank_name: Human-readable bank name.
        business_name: Name of the business/tenant.
        percentage_charge: Commission percentage charged by platform.
        raw_response: Full JSON response from Flutterwave API.
        is_active: Whether this subaccount is currently active.
        created_at: Timestamp when the subaccount was created.
        updated_at: Timestamp of last update.
    """

    __tablename__ = "subaccounts"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    subaccount_code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    account_number: Mapped[str] = mapped_column(String(20), nullable=False)
    bank_code: Mapped[str] = mapped_column(String(20), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=True)
    business_name: Mapped[str] = mapped_column(String(200), nullable=False)
    percentage_charge: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=1.5)
    raw_response: Mapped[dict] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class DedicatedVirtualAccount(StoreFlowBase):
    """Dedicated virtual bank account for receiving payments.

    Provides a fixed bank account number that customers can transfer to.
    Useful for recurring payments and customer convenience.

    Attributes:
        id: Unique virtual account identifier (primary key).
        tenant_id: Identifier of the tenant business.
        scope: Account scope (e.g., "business" or "individual").
        customer_email: Email of the account holder.
        customer_name: Name of the account holder.
        customer_code: Flutterwave customer code.
        account_number: Virtual bank account number.
        account_name: Account holder name at the bank.
        bank_name: Bank name for the virtual account.
        bank_code: Bank code for the virtual account.
        dva_id: Flutterwave dedicated virtual account identifier.
        is_active: Whether this virtual account is currently active.
        raw_response: Full JSON response from payment provider.
        created_at: Timestamp when the virtual account was created.
        updated_at: Timestamp of last update.
    """

    __tablename__ = "dedicated_virtual_accounts"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="business")
    customer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=True)
    customer_code: Mapped[str] = mapped_column(String(80), nullable=False)
    account_number: Mapped[str] = mapped_column(String(20), nullable=False)
    account_name: Mapped[str] = mapped_column(String(200), nullable=True)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=True)
    bank_code: Mapped[str] = mapped_column(String(20), nullable=True)
    dva_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    raw_response: Mapped[dict] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class PaymentIntent(StoreFlowBase):
    """Payment intent for tracking pending payments.

    Represents a payment request that may or may not be completed.
    Used for card and transfer payments where the customer needs
    to complete the payment via an external interface.

    Attributes:
        id: Unique intent identifier (primary key).
        tenant_id: Identifier of the tenant business.
        sale_id: Identifier of the sale this intent is for.
        method: Payment method ("card", "transfer", "cash").
        amount: Payment amount in Nigerian Naira.
        currency: Currency code (default: "NGN").
        status: Intent status ("pending", "completed", "failed", "expired").
        gateway_reference: Gateway transaction reference (unique).
        authorization_url: URL for customer to complete payment.
        dva_account_number: Virtual account number for transfer payments.
        dva_account_name: Virtual account holder name.
        bank_name: Bank name for virtual account.
        expires_at: When this intent expires.
        intent_metadata: Additional payment metadata (JSON).
        created_at: Timestamp when the intent was created.
    """

    __tablename__ = "payment_intents"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    gateway_reference: Mapped[str | None] = mapped_column(
        String(120), nullable=True, unique=True
    )
    authorization_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dva_account_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dva_account_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    intent_metadata: Mapped[dict] = mapped_column("metadata", Text, nullable=True, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Payment(StoreFlowBase):
    """Completed payment record.

    Represents a successful payment transaction. Created when a payment
    is confirmed via webhook or direct API call.

    Attributes:
        id: Unique payment identifier (primary key).
        tenant_id: Identifier of the tenant business.
        sale_id: Identifier of the sale this payment is for.
        intent_id: Identifier of the payment intent (if applicable).
        method: Payment method ("card", "transfer", "cash").
        amount: Payment amount in Nigerian Naira.
        currency: Currency code (default: "NGN").
        status: Payment status ("pending", "completed", "failed", "refunded").
        reference: Internal payment reference.
        gateway_reference: Gateway transaction reference.
        gateway_response: Full response from payment gateway (JSON).
        settled: Whether the payment has been settled to the tenant.
        settled_at: Timestamp when the payment was settled.
        created_at: Timestamp when the payment was recorded.
    """

    __tablename__ = "payments"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    intent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gateway_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gateway_response: Mapped[dict | None] = mapped_column(Text, nullable=True)
    settled: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class WebhookLog(StoreFlowBase):
    """Webhook event log for auditing and debugging.

    Records all incoming webhook events from payment providers
    for audit trail and debugging purposes.

    Attributes:
        id: Unique log entry identifier (primary key).
        event_type: Type of webhook event (e.g., "charge.completed").
        event_id: Provider's event identifier.
        signature: Webhook signature for verification.
        payload: Full webhook payload (JSON).
        processed: Whether this event has been processed.
        error: Error message if processing failed.
        created_at: Timestamp when the webhook was received.
    """

    __tablename__ = "webhook_logs"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    signature: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(Text, nullable=False)
    processed: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
