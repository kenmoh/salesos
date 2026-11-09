"""Feedback report endpoints for bug reports and feature requests.

Any authenticated tenant user can submit a report. Listing is scoped to the
current tenant so each business only sees its own feedback. Status updates
are allowed by any tenant user.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import desc, func, select

from app.core.dependencies import DbTenantDep
from app.core.responses import ok, paginated

from .models import FeedbackReport
from .schemas import FeedbackReportCreate, FeedbackReportResponse, FeedbackReportUpdate

router = APIRouter(prefix="/feedback", tags=["Feedback"])


@router.post("/reports", status_code=201)
async def create_report(
    payload: FeedbackReportCreate,
    ctx: DbTenantDep,
):
    """Submit a new bug report or feature request."""
    report = FeedbackReport(
        tenant_id=UUID(ctx.user.business_id),
        user_id=UUID(ctx.user.user_id),
        type=payload.type,
        title=payload.title.strip(),
        description=payload.description.strip(),
    )
    ctx.session.add(report)
    await ctx.session.commit()
    return ok(FeedbackReportResponse.model_validate(report), message="Report submitted")


@router.get("/reports")
async def list_reports(
    ctx: DbTenantDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    type_filter: str | None = Query(None, alias="type"),
    status_filter: str | None = None,
):
    """List feedback reports for the current tenant, newest first."""
    base = select(FeedbackReport).where(FeedbackReport.tenant_id == UUID(ctx.user.business_id))
    count_base = select(func.count()).select_from(FeedbackReport).where(
        FeedbackReport.tenant_id == UUID(ctx.user.business_id)
    )

    if type_filter:
        base = base.where(FeedbackReport.type == type_filter)
        count_base = count_base.where(FeedbackReport.type == type_filter)

    if status_filter:
        base = base.where(FeedbackReport.status == status_filter)
        count_base = count_base.where(FeedbackReport.status == status_filter)

    total = (await ctx.session.execute(count_base)).scalar() or 0
    items = (
        await ctx.session.execute(
            base.order_by(desc(FeedbackReport.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return paginated(
        [FeedbackReportResponse.model_validate(r).model_dump() for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str,
    ctx: DbTenantDep,
):
    """Get a single feedback report by ID."""
    try:
        rid = UUID(report_id)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(400, "Invalid report ID") from exc

    report = (
        await ctx.session.execute(
            select(FeedbackReport).where(
                FeedbackReport.id == rid,
                FeedbackReport.tenant_id == UUID(ctx.user.business_id),
            )
        )
    ).scalar_one_or_none()

    if not report:
        from fastapi import HTTPException

        raise HTTPException(404, "Report not found")

    return ok(FeedbackReportResponse.model_validate(report).model_dump())


@router.patch("/reports/{report_id}")
async def update_report(
    report_id: str,
    payload: FeedbackReportUpdate,
    ctx: DbTenantDep,
):
    """Update the status of a feedback report."""
    try:
        rid = UUID(report_id)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(400, "Invalid report ID") from exc

    report = (
        await ctx.session.execute(
            select(FeedbackReport).where(
                FeedbackReport.id == rid,
                FeedbackReport.tenant_id == UUID(ctx.user.business_id),
            )
        )
    ).scalar_one_or_none()

    if not report:
        from fastapi import HTTPException

        raise HTTPException(404, "Report not found")

    report.status = payload.status
    await ctx.session.commit()

    return ok(FeedbackReportResponse.model_validate(report).model_dump(), message="Status updated")
