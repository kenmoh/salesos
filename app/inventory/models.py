from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class TransferRequest(StoreFlowBase):
    __tablename__ = "transfer_requests"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    requesting_store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    supplying_store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    requested_qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    approved_qty: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class StockBalance(StoreFlowBase):
    __tablename__ = "stock_balances"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    reserved_qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    min_stock_level: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    unit_cost: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class StockAdjustment(StoreFlowBase):
    __tablename__ = "stock_adjustments"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    qty_change: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit_cost: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class StockReservation(StoreFlowBase):
    __tablename__ = "stock_reservations"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StockMovement(StoreFlowBase):
    __tablename__ = "stock_movements"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    store_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    movement_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    qty_change: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    balance_before: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    balance_after: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reference_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit_cost: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
