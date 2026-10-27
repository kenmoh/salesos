"""Public contact-form inbox.

``POST /contact/messages`` is anonymous and rate-limited: the marketing site
submits it from people who have never signed in. Reading the inbox is wired
onto the platform router, where platform auth lives — see
``app/platform/routes.py`` for ``GET /contact/messages``.

The table intentionally carries no ``tenant_id`` and sits outside RLS: any
visitor may write, and nothing written here keys on a business.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field

from app.common.db.session import get_db
from app.core.ratelimit import auth_rate_limit
from app.core.responses import ok, paginated

router = APIRouter(prefix="/contact", tags=["Contact"])


class ContactMessageCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    business_name: str | None = Field(default=None, max_length=120)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    message: str = Field(..., min_length=5, max_length=5000)


def _message_as_dict(msg) -> dict:
    return {
        "id": str(msg.id),
        "name": msg.name,
        "business_name": msg.business_name,
        "email": msg.email,
        "phone": msg.phone,
        "message": msg.message,
        "source": msg.source,
        "created_at": msg.created_at.isoformat(),
    }


@router.post("/messages", status_code=201, dependencies=[Depends(auth_rate_limit(5, 900))])
async def submit_message(payload: ContactMessageCreate, db=Depends(get_db)):
    from sqlalchemy import select

    from app.contact.models import ContactMessage

    phone = (payload.phone or "").strip() or None
    message = payload.message.strip()
    email = payload.email

    # A double-clicked form shouldn't double-store the message.
    duplicate = (
        await db.execute(
            select(ContactMessage.id)
            .where(
                ContactMessage.email == email,
                ContactMessage.message == message,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if duplicate:
        return ok(
            {"id": str(duplicate)},
            message="We already have your message. We'll get back to you shortly.",
        )

    row = ContactMessage(
        name=payload.name.strip(),
        business_name=(payload.business_name or "").strip() or None,
        email=email,
        phone=phone,
        message=message,
    )
    db.add(row)
    await db.commit()
    return ok(
        {"id": str(row.id)},
        message="Thanks — we'll get back to you within one business hour.",
    )


async def platform_list_messages(db, page: int = 1, page_size: int = 50):
    """Read the inbox, newest first. Called from the platform router, which
    supplies the platform-authenticated session."""
    from sqlalchemy import desc, func, select

    from app.contact.models import ContactMessage

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    items = (
        (await db.execute(select(ContactMessage).order_by(desc(ContactMessage.created_at))))
        .scalars()
        .all()
    )
    total = (await db.execute(select(func.count()).select_from(ContactMessage))).scalar()
    return paginated([_message_as_dict(m) for m in items], total=total, page=page, page_size=page_size)
