from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class PaymentIntentCreateCommand(BaseModel):
    tenant_id: UUID
    sale_id: UUID
    method: str = Field(..., min_length=1, max_length=30)
    amount: Decimal = Field(..., gt=0)
    currency: str = "NGN"
    customer_email: str | None = None
    customer_name: str | None = None
    subaccount_code: str | None = None
    callback_url: str | None = None
    metadata: dict = {}
    correlation_id: str | None = None


class PaymentIntentResult(BaseModel):
    payment_id: UUID
    reference: str
    authorization_url: str | None
    access_code: str | None
    amount: Decimal
    expires_at: str | None


class WebhookPayload(BaseModel):
    event: str
    data: dict

