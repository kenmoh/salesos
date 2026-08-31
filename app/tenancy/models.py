"""Tenancy service database models.

This module defines the models for multi-tenant business management:
- Tenant: Represents a business/organization
- TenantTierProjection: Tier limits for business features

The tenancy system provides:
- Multi-tenant isolation
- Tier-based feature limits
- Business owner management
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class Tenant(StoreFlowBase):
    """Represents a business/organization tenant.

    A Tenant is the top-level entity in the system. Each business
    operates as an isolated tenant with its own stores, users,
    products, and sales data.

    Attributes:
        slug: URL-friendly unique identifier (e.g., "acme-retail").
        subdomain: Unique subdomain for the business.
        business_name: Display name of the business.
        business_email: Optional business contact email.
        tier: Subscription tier (starter, professional, enterprise).
        status: Account status (active, suspended, cancelled).
        owner_name: Business owner's full name.
        owner_email: Business owner's email address.
        owner_phone: Optional business owner's phone.
        settings: JSON string with tenant-specific settings.
    """

    __tablename__ = "tenants"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    subdomain: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    business_name: Mapped[str] = mapped_column(String(120), nullable=False)
    business_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tier: Mapped[str] = mapped_column(String(30), nullable=False, default="starter")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    owner_name: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_email: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    settings: Mapped[dict] = mapped_column(Text, nullable=True, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class TenantTierProjection(StoreFlowBase):
    """Local projection of tenant tier limits for other services to consume.

    This table provides quick access to tier-based limits without
    querying the main tenancy service. It's synchronized via events.

    Attributes:
        tenant_identifier: Reference to the tenant.
        tier: Current subscription tier.
        max_terminals: Maximum number of POS terminals.
        max_products: Maximum number of products.
        max_users: Maximum number of users.
    """

    __tablename__ = "tenant_tier_projections"


    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), unique=True, nullable=False, index=True
    )
    tier: Mapped[str] = mapped_column(String(30), nullable=False)
    max_terminals: Mapped[int] = mapped_column(nullable=False, default=1)
    max_products: Mapped[int] = mapped_column(nullable=False, default=100)
    max_users: Mapped[int] = mapped_column(nullable=False, default=2)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
