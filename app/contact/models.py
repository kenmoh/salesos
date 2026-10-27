"""Website contact form submissions.

A public inbox, deliberately outside the tenancy model: the form on the
marketing site has no signed-in user, so the table carries no ``tenant_id``
and no RLS. Platform admins read the inbox; anyone can drop a message.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class ContactMessage(StoreFlowBase):
    """A message sent through the public contact form.

    Attributes:
        name: The sender's full name.
        business_name: Their business, when they gave one.
        email: Reply-to address.
        phone: Phone or WhatsApp number, when given.
        message: What they are looking for.
        source: Where the message came from (e.g. "website").
        created_at: When the message landed.
    """

    __tablename__ = "contact_messages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="website")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
