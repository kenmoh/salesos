"""Platform service database models.

This module defines the models for the platform administration domain:
- PlatformAdmin: Platform administrator accounts
- PlatformAuditLog: Audit trail for platform actions
- PlatformCommission: Fee rules for platform commission
- PlatformFeeLedger: Tracks cash fee deductions per sale

The platform service manages:
- Commission/fee calculation and tracking
- Audit logging for compliance
- Cash debt tracking for unpaid fees
"""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class FeeType(str, Enum):
    """Fee calculation type for platform commissions.

    Attributes:
        FLAT: Fixed amount in Nigerian Naira.
        PERCENTAGE: Percentage of sale total (0-100).
    """

    FLAT = "flat"
    PERCENTAGE = "percentage"


class PlatformAdmin(StoreFlowBase):
    """Represents a platform administrator account.

    Platform admins can manage all tenants, view reports, and
    configure platform-wide settings.

    Attributes:
        email: Unique admin email address.
        password_hash: Hashed admin password.
        full_name: Admin's full name.
        role: Admin role (e.g., "admin", "super_admin").
        status: Account status ("active", "inactive", "suspended").
        last_login_at: Timestamp of last successful login.
    """

    __tablename__ = "platform_admins"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="admin")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class PlatformAuditLog(StoreFlowBase):
    """Audit trail for platform administrative actions.

    Records all significant actions performed by platform admins,
    including commission changes, tenant management, and fee adjustments.

    Attributes:
        admin_identifier: Reference to the admin who performed the action.
        action: Action type (e.g., "created", "updated", "deleted").
        resource: Resource type affected (e.g., "commission", "tenant").
        resource_identifier: Specific resource identifier.
        details: JSON string with additional action details.
        ip_address: Admin's IP address for security tracking.
    """

    __tablename__ = "platform_audit_log"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    admin_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    resource: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    details: Mapped[dict | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class PlatformCommission(StoreFlowBase):
    """Fee rule for platform commission calculation.

    Defines how platform fees are calculated for sales. Multiple rules
    can be created with different thresholds, and the system selects
    the first matching rule based on `min_threshold`.

    Example rules:
    - Fee 1: flat, 100 NGN, min 0 NGN (applies to all sales)
    - Fee 2: percentage, 1.8%, min 5000 NGN (applies to larger sales)

    Attributes:
        label: Human-readable fee rule name.
        fee_type: Calculation type (flat or percentage).
        amount: Fee amount (flat: NGN, percentage: 0-100).
        min_threshold: Minimum sale total for this rule to apply.
        max_pending_balance: Cash fee threshold before blocking (default 1000 NGN).
    """

    __tablename__ = "platform_commissions"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    fee_type: Mapped[str] = mapped_column(String(10), nullable=False, default=FeeType.FLAT.value)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    min_threshold: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    max_pending_balance: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=1000)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class PlatformFeeLedger(StoreFlowBase):
    """Tracks platform fee deductions for cash payments.

    When a sale includes a cash payment, the platform fee portion
    is recorded here as a "pending" debt. This debt is deducted
    when the tenant receives their next settlement, or manually
    cleared by a platform admin.

    The `max_pending_balance` field in PlatformCommission determines
    when new sales are blocked (fees too high).

    Status values:
    - pending: Fee not yet deducted
    - deducted: Fee deducted from tenant settlement

    Attributes:
        tenant_identifier: Reference to the tenant business.
        sale_identifier: Reference to the sale that generated this fee.
        amount: Fee amount in Nigerian Naira.
        fee_type: Fee calculation type (flat or percentage).
        rate: The rate/amount used for calculation.
        payment_method: Payment method that generated this fee (cash/card/transfer).
        status: Deduction status (pending or deducted).
        settled_at: Timestamp when fee was deducted from settlement.
    """

    __tablename__ = "platform_fee_ledger"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    fee_type: Mapped[str] = mapped_column(String(10), nullable=False)
    rate: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
