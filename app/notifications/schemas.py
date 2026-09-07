from datetime import datetime
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


class PushTokenRegisterCommand(BaseModel):
    token: str = Field(..., min_length=1)
    platform: str = Field(..., pattern=r"^(ios|android)$")


class InAppNotificationResult(BaseModel):
    id: UUID
    type: str
    title: str
    body: str
    is_read: bool
    meta: dict | None = None
    created_at: datetime


class NotificationListResult(BaseModel):
    items: list[InAppNotificationResult]
    unread_count: int
    total: int


class MarkReadCommand(BaseModel):
    notification_ids: list[UUID]


class NotificationPreferencesUpdate(BaseModel):
    notification_types: dict[str, bool]
