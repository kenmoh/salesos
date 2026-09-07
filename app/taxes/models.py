from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class Tax(StoreFlowBase):
    """A named tax type (e.g. VAT, Service Tax) that can be applied to products.

    Each tenant can create multiple tax types. At checkout, if the store has
    tax enabled and a product has a tax type assigned, that tax rate is used
    to calculate the tax amount.

    Attributes:
        id: Unique identifier (UUID, auto-generated).
        tenant_id: The business tenant this tax type belongs to.
        name: Human-readable name (e.g. "VAT", "Service Tax").
        rate: Tax rate as a percentage (e.g. 7.50 for 7.5%).
        is_active: Whether this tax type is available for assignment.
        created_at: When this tax type was created.
        updated_at: When this tax type was last modified.
    """

    __tablename__ = "taxes"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    rate: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
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
