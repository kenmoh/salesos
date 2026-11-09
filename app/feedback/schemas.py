"""Pydantic schemas for feedback reports."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

FeedbackType = Literal["bug", "feature"]
FeedbackStatus = Literal["open", "in_progress", "resolved", "rejected"]


class FeedbackReportCreate(BaseModel):
    """Schema for creating a new feedback report."""

    type: FeedbackType
    title: str = Field(..., min_length=3, max_length=255)
    description: str = Field(..., min_length=10, max_length=5000)


class FeedbackReportUpdate(BaseModel):
    """Schema for updating a feedback report status."""

    status: FeedbackStatus


class FeedbackReportResponse(BaseModel):
    """Schema for a single feedback report in responses."""

    id: UUID
    type: FeedbackType
    title: str
    description: str
    status: FeedbackStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
