from uuid import UUID

from pydantic import BaseModel, Field


class TemplateCreateCommand(BaseModel):
    tenant_id: UUID | None = None
    name: str = Field(..., min_length=1, max_length=80)
    channel: str = Field(..., min_length=1, max_length=30)
    subject: str | None = None
    body: str = Field(..., min_length=1)
    variables: dict | None = None


class TemplateResult(BaseModel):
    id: UUID
    tenant_id: str | None
    name: str
    channel: str
    is_active: bool


class NotificationSendCommand(BaseModel):
    tenant_id: UUID
    channel: str = Field(..., min_length=1, max_length=30)
    recipient: str = Field(..., min_length=1, max_length=255)
    subject: str | None = None
    body: str = Field(..., min_length=1)
    correlation_id: str | None = None


class NotificationResult(BaseModel):
    id: UUID
    tenant_id: UUID
    channel: str
    recipient: str
    status: str
    attempts: int
