"""Cashier tip ledger models.

Every positive per-line ``extra`` becomes one immutable tip row tied to the
sale item that produced it. Settlement batches pending rows into a paid
settlement for one cashier and one date range.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class CashierTip(StoreFlowBase):
    """A single earned tip on one sale line.

    Attributes:
        tenant_identifier: Business/tenant identifier.
        sale_identifier: The sale that produced the tip.
        sale_item_identifier: The exact sale line (unique, so retries never
            duplicate a tip).
        cashier_identifier: The cashier credited with the tip.
        store_identifier: Store where the sale happened.
        extra: Per-unit override snapshot at sale time.
        qty: Quantity sold.
        amount: ``extra * qty``, rounded to two decimal places.
        status: ``pending``, ``settled`` or ``voided``.
        earned_at: Sale timestamp; settlement ranges filter on this.
        settlement_identifier: Settlement that paid this tip, if any.
        settled_at: When the tip was settled.
    """

    __tablename__ = "cashier_tips"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    sale_item_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, unique=True
    )
    cashier_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    extra: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    earned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    settlement_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class CashierTipSettlement(StoreFlowBase):
    """A paid batch of tips for one cashier over an inclusive date range.

    Settlements are final: once tips are attached they are not released, even
    if their sale is voided later.
    """

    __tablename__ = "cashier_tip_settlements"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    cashier_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    tip_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="paid")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    paid_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
