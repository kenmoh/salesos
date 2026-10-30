"""Email addresses collected from the marketing site's waitlist form.

Like the contact inbox, this table sits deliberately outside the tenancy
model: the form on the landing page has no signed-in user, so the row carries
no ``tenant_id`` and no RLS. Platform admins read the list; anyone may add
themselves to it.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.base import StoreFlowBase


class WaitlistSignup(StoreFlowBase):
    """One email waiting for a place on the waitlist.

    Attributes:
        email: The address to contact. Unique, so one person cannot pile up
            entries by submitting the form repeatedly.
        name: Their name, when they gave one.
        business_name: Their business, when they gave one.
        source: Where the signup came from (e.g. "landing_page").
        status: "waiting" while they wait, "invited" once reached out to.
        created_at: When they signed up.
    """

    __tablename__ = "waitlist_signups"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    business_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="landing_page")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="waiting")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
