"""Pydantic schemas for the billing API."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# ── Plans ───────────────────────────────────────────────────────────────────

class PlanBase(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    monthly_price: Decimal
    currency: str = "NGN"
    is_active: bool = True
    sort_order: int = 0


class PlanOut(PlanBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
    features: list[PlanFeatureOut] = []
    fee_tiers: list[FeeTierOut] = []


class PlanCreate(PlanBase):
    pass


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    monthly_price: Optional[Decimal] = None
    currency: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


# ── Plan features ───────────────────────────────────────────────────────────

class PlanFeatureBase(BaseModel):
    plan_id: UUID
    feature_key: str
    feature_label: str
    feature_value: Optional[str] = None
    is_enabled: bool = True
    sort_order: int = 0


class PlanFeatureOut(PlanFeatureBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class PlanFeatureCreate(PlanFeatureBase):
    pass


class PlanFeatureUpdate(BaseModel):
    feature_label: Optional[str] = None
    feature_value: Optional[str] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None


# ── Pricing versions ────────────────────────────────────────────────────────

class PricingVersionBase(BaseModel):
    name: str
    version_number: int
    effective_from: datetime
    effective_until: Optional[datetime] = None
    status: str = "scheduled"


class PricingVersionOut(PricingVersionBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_by: Optional[UUID] = None
    created_at: datetime


class PricingVersionCreate(PricingVersionBase):
    pass


# ── Fee tiers ───────────────────────────────────────────────────────────────

class FeeTierBase(BaseModel):
    plan_id: UUID
    label: str
    minimum_amount: Decimal
    maximum_amount: Optional[Decimal] = None
    fee_amount: Decimal
    currency: str = "NGN"
    is_active: bool = True


class FeeTierOut(FeeTierBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pricing_version_id: UUID
    created_at: datetime
    updated_at: datetime


class FeeTierCreate(FeeTierBase):
    pass


# ── Subscription ────────────────────────────────────────────────────────────

class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    plan_id: UUID
    pending_plan_id: Optional[UUID]
    status: str
    billing_interval: str
    current_period_start: datetime
    current_period_end: datetime
    next_billing_date: datetime
    auto_debit_enabled: bool
    created_at: datetime
    updated_at: datetime


class PlanChangeRequest(BaseModel):
    plan_slug: str


# ── Invoice ─────────────────────────────────────────────────────────────────

class InvoiceBase(BaseModel):
    invoice_number: str
    status: str
    total_amount: Decimal
    amount_paid: Decimal
    amount_due: Decimal
    issued_at: datetime
    due_at: datetime
    paid_at: Optional[datetime] = None
    grace_until: datetime


class InvoiceOut(InvoiceBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    billing_period_id: Optional[UUID]
    subscription_amount: Decimal
    transaction_fee_total: Decimal
    credit_total: Decimal
    auto_debit_attempted: bool
    created_at: datetime


# ── Wallet ──────────────────────────────────────────────────────────────────

class WalletOut(BaseModel):
    tenant_id: UUID
    balance: Decimal
    currency: str
    status: str
    updated_at: datetime


class WalletTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entry_type: str
    amount: Decimal
    currency: str
    reference: Optional[str]
    invoice_id: Optional[UUID]
    description: Optional[str]
    created_at: datetime


class WalletFundRequest(BaseModel):
    amount: Decimal
    gateway_reference: Optional[str] = None
    description: Optional[str] = "Wallet funding"


# ── Billable transaction ────────────────────────────────────────────────────

class BillableTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sale_id: UUID
    transaction_amount: Decimal
    transaction_fee: Decimal
    fee_label_snapshot: Optional[str]
    currency: str
    transaction_status: str
    completed_at: datetime
    created_at: datetime
