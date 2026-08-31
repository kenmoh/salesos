from fastapi import APIRouter, Depends, Query

from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, ok
from app.auth.schemas.responses import (
    CashierPerformanceItem,
    CustomerInsightsResult,
    DashboardSummary,
    DocumentSummaryResult,
    InventoryAlertsResult,
    PaymentBreakdown,
    ProfitLossResult,
    SalesSummary,
    TopProduct,
)
from app.common.analytics import (
    cashier_performance,
    customer_insights,
    dashboard_summary,
    document_summary,
    inventory_alerts,
    payment_breakdown,
    profit_loss,
    sales_summary,
    top_products,
)

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get(
    "/dashboard",
    response_model=DataResponse[DashboardSummary],
    dependencies=[Depends(require_permission("reports:read"))],
)
async def dashboard(ctx: TenantDep, days: int = Query(30, ge=1, le=365)):
    return ok(
        await dashboard_summary(session=ctx.session, tenant_id=ctx.user.business_id, days=days)
    )


@router.get(
    "/sales-summary",
    response_model=DataResponse[SalesSummary],
    dependencies=[Depends(require_permission("reports:read"))],
)
async def sales_summary_endpoint(
    ctx: TenantDep,
    from_date: str = Query(..., description="ISO 8601 date"),
    to_date: str = Query(..., description="ISO 8601 date"),
    group_by: str = Query("day", pattern="^(day|week|month)$"),
):
    return ok(
        await sales_summary(
            session=ctx.session,
            tenant_id=ctx.user.business_id,
            from_date=from_date,
            to_date=to_date,
            group_by=group_by,
        )
    )


@router.get(
    "/top-products",
    response_model=DataResponse[list[TopProduct]],
    dependencies=[Depends(require_permission("reports:read"))],
)
async def top_products_endpoint(
    ctx: TenantDep,
    from_date: str = Query(..., description="ISO 8601 date"),
    to_date: str = Query(..., description="ISO 8601 date"),
    limit: int = Query(10, ge=1, le=100),
):
    return ok(
        await top_products(
            session=ctx.session,
            tenant_id=ctx.user.business_id,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
        )
    )


@router.get(
    "/payment-methods",
    response_model=DataResponse[PaymentBreakdown],
    dependencies=[Depends(require_permission("reports:read"))],
)
async def payment_methods_endpoint(
    ctx: TenantDep,
    from_date: str = Query(..., description="ISO 8601 date"),
    to_date: str = Query(..., description="ISO 8601 date"),
):
    return ok(
        await payment_breakdown(
            session=ctx.session,
            tenant_id=ctx.user.business_id,
            from_date=from_date,
            to_date=to_date,
        )
    )


@router.get(
    "/cashier-performance",
    response_model=DataResponse[list[CashierPerformanceItem]],
    dependencies=[Depends(require_permission("reports:read"))],
)
async def cashier_performance_endpoint(
    ctx: TenantDep,
    from_date: str = Query(..., description="ISO 8601 date"),
    to_date: str = Query(..., description="ISO 8601 date"),
    limit: int = Query(20, ge=1, le=100),
):
    return ok(
        await cashier_performance(
            session=ctx.session,
            tenant_id=ctx.user.business_id,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
        )
    )


@router.get(
    "/inventory-alerts",
    response_model=DataResponse[InventoryAlertsResult],
    dependencies=[Depends(require_permission("reports:read"))],
)
async def inventory_alerts_endpoint(
    ctx: TenantDep,
    alert_type: str | None = Query(None, description="low_stock|out_of_stock|overstocked|all"),
):
    return ok(
        await inventory_alerts(
            session=ctx.session,
            tenant_id=ctx.user.business_id,
            alert_type=alert_type,
        )
    )


@router.get(
    "/profit-loss",
    response_model=DataResponse[ProfitLossResult],
    dependencies=[Depends(require_permission("reports:read"))],
)
async def profit_loss_endpoint(
    ctx: TenantDep,
    from_date: str = Query(..., description="ISO 8601 date"),
    to_date: str = Query(..., description="ISO 8601 date"),
    group_by: str = Query("day", pattern="^(day|week|month)$"),
):
    return ok(
        await profit_loss(
            session=ctx.session,
            tenant_id=ctx.user.business_id,
            from_date=from_date,
            to_date=to_date,
            group_by=group_by,
        )
    )


@router.get(
    "/customer-insights",
    response_model=DataResponse[CustomerInsightsResult],
    dependencies=[Depends(require_permission("reports:read"))],
)
async def customer_insights_endpoint(
    ctx: TenantDep,
    from_date: str = Query(..., description="ISO 8601 date"),
    to_date: str = Query(..., description="ISO 8601 date"),
    limit: int = Query(20, ge=1, le=100),
):
    return ok(
        await customer_insights(
            session=ctx.session,
            tenant_id=ctx.user.business_id,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
        )
    )


@router.get(
    "/document-summary",
    response_model=DataResponse[DocumentSummaryResult],
    dependencies=[Depends(require_permission("reports:read"))],
)
async def document_summary_endpoint(
    ctx: TenantDep,
    from_date: str = Query(..., description="ISO 8601 date"),
    to_date: str = Query(..., description="ISO 8601 date"),
    doc_type: str | None = Query(None, description="invoice|receipt|credit_note|all"),
):
    return ok(
        await document_summary(
            session=ctx.session,
            tenant_id=ctx.user.business_id,
            from_date=from_date,
            to_date=to_date,
            doc_type=doc_type,
        )
    )
