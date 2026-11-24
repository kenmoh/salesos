"""Cashier tip request/response schemas."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class TipRow(BaseModel):
    id: str
    sale_id: str
    sale_number: str
    sale_item_id: str
    product_name: str | None = None
    cashier_id: str
    cashier_name: str | None = None
    store_id: str
    extra: float
    qty: float
    amount: float
    status: str
    sale_status: str
    earned_at: datetime
    settlement_id: str | None = None
    settled_at: datetime | None = None


class TipSummaryRow(BaseModel):
    cashier_id: str
    cashier_name: str | None = None
    unsettled_amount: float
    unsettled_count: int
    settled_amount: float
    settled_count: int
    last_settlement_at: datetime | None = None


class SettlementRequest(BaseModel):
    cashier_id: UUID
    period_start: date
    period_end: date
    note: str | None = Field(default=None, max_length=500)

    @field_validator("period_start", "period_end", mode="before")
    @classmethod
    def normalize_datetime_dates(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str) and "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        return value


class SettlementResult(BaseModel):
    id: str
    cashier_id: str
    period_start: date
    period_end: date
    amount: float
    tip_count: int
    status: str
    note: str | None = None
    paid_by: str
    paid_at: datetime


class SettlementTipRow(BaseModel):
    id: str
    sale_id: str
    sale_number: str
    sale_item_id: str
    product_name: str | None = None
    amount: float
    earned_at: datetime


class SettlementDetail(SettlementResult):
    tips: list[SettlementTipRow] = []
