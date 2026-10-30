"""Public waitlist signup endpoint.

``POST /waitlist/signups`` is anonymous and rate-limited: the landing page
submits it from visitors who have never signed in. Reading the list is wired
onto the platform router, where platform auth lives -- see
``app/platform/routes.py`` for ``GET /platform/waitlist/signups``.

The table intentionally carries no ``tenant_id`` and sits outside RLS: any
visitor may write, and nothing written here keys on a business.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field

from app.common.db.session import get_db
from app.core.ratelimit import auth_rate_limit
from app.core.responses import ok

router = APIRouter(prefix="/waitlist", tags=["Waitlist"])


class WaitlistSignupCreate(BaseModel):
    email: EmailStr
    name: str | None = Field(default=None, max_length=120)
    business_name: str | None = Field(default=None, max_length=120)


def _signup_as_dict(row) -> dict:
    return {
        "id": str(row.id),
        "email": row.email,
        "name": row.name,
        "business_name": row.business_name,
        "source": row.source,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
    }


@router.post("/signups", status_code=201, dependencies=[Depends(auth_rate_limit(20, 900))])
async def join_waitlist(payload: WaitlistSignupCreate, db=Depends(get_db)):
    from sqlalchemy import select

    from app.waitlist.models import WaitlistSignup

    email = payload.email.strip().lower()

    # The same person clicking twice should end up on the list once, and the
    # form should still say they are on it.
    existing = (
        await db.execute(select(WaitlistSignup).where(WaitlistSignup.email == email).limit(1))
    ).scalar_one_or_none()
    if existing:
        return ok(
            {"id": str(existing.id), "email": existing.email, "status": existing.status},
            message="You're already on the list — we'll be in touch.",
        )

    row = WaitlistSignup(
        email=email,
        name=(payload.name or "").strip() or None,
        business_name=(payload.business_name or "").strip() or None,
        source="landing_page",
        status="waiting",
    )
    db.add(row)
    await db.commit()
    return ok(
        {"id": str(row.id), "email": row.email, "status": row.status},
        message="You're on the list — we'll be in touch.",
    )


async def platform_list_signups(
    db, page: int = 1, page_size: int = 50, status_filter: str | None = None
):
    """Read the waitlist, newest first. Called from the platform router,
    which supplies the platform-authenticated session."""
    from sqlalchemy import desc, func, select

    from app.waitlist.models import WaitlistSignup

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    base = select(WaitlistSignup)
    count_base = select(func.count()).select_from(WaitlistSignup)
    if status_filter:
        base = base.where(WaitlistSignup.status == status_filter)
        count_base = count_base.where(WaitlistSignup.status == status_filter)

    total = (await db.execute(count_base)).scalar()
    items = (
        await db.execute(
            base.order_by(desc(WaitlistSignup.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return items, total


async def platform_update_signup_status(db, signup_id, status: str):
    """Mark a signup as reached out to, or put it back in the waiting pile."""
    from sqlalchemy import select

    from app.waitlist.models import WaitlistSignup

    ALLOWED = frozenset({"waiting", "invited"})
    if status not in ALLOWED:
        raise ValueError("status must be one of: waiting, invited")

    row = (
        await db.execute(select(WaitlistSignup).where(WaitlistSignup.id == signup_id))
    ).scalar_one_or_none()
    if not row:
        return None
    row.status = status
    await db.commit()
    return row
