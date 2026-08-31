"""Read-only tools for the AI assistant.

This module defines 14 read-only tools that the AI agent can invoke to query
business data. All tools are STRICTLY READ-ONLY -- they only execute SELECT
queries and never modify data.

Tool Categories:
    1. Product Tools (3): search_products, get_product_details, check_stock
    2. Sales Tools (4): get_sales_summary, get_top_products, get_revenue_trend, get_recent_transactions
    3. Customer Tools (1): get_customer_insights
    4. Inventory Tools (1): get_inventory_alerts
    5. Financial Tools (3): get_profit_loss, get_expenses_by_category, get_accounts_receivable
    6. Web Tools (2): compare_product_prices, search_product_info (Phase 5)

Multi-Tenant Security:
    Every tool receives a tenant_id parameter and ALL queries are filtered
    by tenant_id to ensure complete data isolation between businesses.

Abbreviations Used in This Module
----------------------------------
- AR: Accounts Receivable -- money owed TO the business by customers.
- COA: Chart of Accounts -- the complete list of all financial accounts.
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- NGN: Nigerian Naira -- the base currency for all financial amounts.
- SQL: Structured Query Language -- the language used to query databases.
- ASC: Ascending order -- smallest to largest (A-Z, 0-9).
- DESC: Descending order -- largest to smallest (Z-A, 9-0).
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("app.ai.tools")


# --- Product Tools --------------------------------------------------------------------


async def search_products(
    session: AsyncSession,
    tenant_id: UUID,
    query: str,
    limit: int = 10,
) -> str:
    """Search products by name, SKU, or description.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        query: Search term (matches name, SKU, or description).
        limit: Maximum results to return (default: 10).

    Returns:
        JSON string with matching products.
    """
    try:
        result = await session.execute(
            text(
                "SELECT id, name, sku, selling_price, cost_price, status "
                "FROM products "
                "WHERE tenant_id = :tid "
                "AND (LOWER(name) LIKE LOWER(:q) OR LOWER(sku) LIKE LOWER(:q)) "
                "ORDER BY name LIMIT :limit"
            ),
            {"tid": tenant_id, "q": f"%{query}%", "limit": limit},
        )
        rows = result.fetchall()
        products = [
            {
                "id": str(r[0]),
                "name": r[1],
                "sku": r[2],
                "selling_price": float(r[3]) if r[3] else 0,
                "cost_price": float(r[4]) if r[4] else 0,
                "status": r[5],
            }
            for r in rows
        ]
        return json.dumps({"count": len(products), "products": products})
    except Exception as e:
        logger.warning("search_products failed: %s", e)
        return json.dumps({"count": 0, "products": [], "error": str(e)})


async def get_product_details(
    session: AsyncSession,
    tenant_id: UUID,
    product_id: str,
) -> str:
    """Get detailed information about a specific product.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        product_id: UUID of the product to look up.

    Returns:
        JSON string with product details.
    """
    try:
        result = await session.execute(
            text(
                "SELECT id, name, sku, selling_price, cost_price, description, status "
                "FROM products "
                "WHERE tenant_id = :tid AND id = :pid"
            ),
            {"tid": tenant_id, "pid": UUID(product_id)},
        )
        row = result.fetchone()
        if not row:
            return json.dumps({"error": "Product not found"})

        product = {
            "id": str(row[0]),
            "name": row[1],
            "sku": row[2],
            "selling_price": float(row[3]) if row[3] else 0,
            "cost_price": float(row[4]) if row[4] else 0,
            "description": row[5],
            "status": row[6],
        }
        return json.dumps(product)
    except Exception as e:
        logger.warning("get_product_details failed: %s", e)
        return json.dumps({"error": str(e)})


async def check_stock(
    session: AsyncSession,
    tenant_id: UUID,
    product_id: str | None = None,
    product_name: str | None = None,
) -> str:
    """Check stock levels for a product or all products.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        product_id: Optional UUID of a specific product.
        product_name: Optional name to search (partial match).

    Returns:
        JSON string with stock levels.
    """
    try:
        if product_id:
            result = await session.execute(
                text(
                    "SELECT sb.product_id, p.name, sb.qty, sb.reserved_qty, "
                    "(sb.qty - sb.reserved_qty) as available, s.name as store_name "
                    "FROM stock_balances sb "
                    "JOIN products p ON p.id = sb.product_id "
                    "JOIN stores s ON s.id = sb.store_id "
                    "WHERE sb.tenant_id = :tid AND sb.product_id = :pid"
                ),
                {"tid": tenant_id, "pid": UUID(product_id)},
            )
        elif product_name:
            result = await session.execute(
                text(
                    "SELECT sb.product_id, p.name, sb.qty, sb.reserved_qty, "
                    "(sb.qty - sb.reserved_qty) as available, s.name as store_name "
                    "FROM stock_balances sb "
                    "JOIN products p ON p.id = sb.product_id "
                    "JOIN stores s ON s.id = sb.store_id "
                    "WHERE sb.tenant_id = :tid "
                    "AND LOWER(p.name) LIKE LOWER(:name) "
                    "ORDER BY p.name"
                ),
                {"tid": tenant_id, "name": f"%{product_name}%"},
            )
        else:
            result = await session.execute(
                text(
                    "SELECT sb.product_id, p.name, sb.qty, sb.reserved_qty, "
                    "(sb.qty - sb.reserved_qty) as available, s.name as store_name "
                    "FROM stock_balances sb "
                    "JOIN products p ON p.id = sb.product_id "
                    "JOIN stores s ON s.id = sb.store_id "
                    "WHERE sb.tenant_id = :tid "
                    "ORDER BY p.name LIMIT 50"
                ),
                {"tid": tenant_id},
            )

        rows = result.fetchall()
        stock = [
            {
                "product_id": str(r[0]),
                "product_name": r[1],
                "quantity": float(r[2]),
                "reserved": float(r[3]),
                "available": float(r[4]),
                "store": r[5],
            }
            for r in rows
        ]
        return json.dumps({"count": len(stock), "stock": stock})
    except Exception as e:
        logger.warning("check_stock failed: %s", e)
        return json.dumps({"count": 0, "stock": [], "error": str(e)})


# --- Sales Tools ----------------------------------------------------------------------


async def get_sales_summary(
    session: AsyncSession,
    tenant_id: UUID,
    period: str = "today",
) -> str:
    """Get sales summary for a time period.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        period: Time period -- "today", "yesterday", "week", "month", "year".

    Returns:
        JSON string with sales totals and counts.
    """
    try:
        now = datetime.now(UTC)
        if period == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "yesterday":
            start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            now = start + timedelta(days=1)
        elif period == "week":
            start = now - timedelta(days=7)
        elif period == "month":
            start = now - timedelta(days=30)
        elif period == "year":
            start = now - timedelta(days=365)
        else:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        result = await session.execute(
            text(
                "SELECT COUNT(*), COALESCE(SUM(total), 0), COALESCE(AVG(total), 0) "
                "FROM sales "
                "WHERE tenant_id = :tid "
                "AND status = 'confirmed' "
                "AND created_at >= :start AND created_at < :end"
            ),
            {"tid": tenant_id, "start": start, "end": now},
        )
        row = result.fetchone()

        return json.dumps({
            "period": period,
            "total_sales": int(row[0]),
            "total_revenue": float(row[1]),
            "average_sale": float(row[2]),
        })
    except Exception as e:
        logger.warning("get_sales_summary failed: %s", e)
        return json.dumps({"period": period, "error": str(e)})


async def get_top_products(
    session: AsyncSession,
    tenant_id: UUID,
    period: str = "month",
    limit: int = 5,
) -> str:
    """Get top-selling products by revenue or quantity.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        period: Time period -- "week", "month", "year".
        limit: Number of top products to return (default: 5).

    Returns:
        JSON string with top products ranked by revenue.
    """
    try:
        now = datetime.now(UTC)
        if period == "week":
            start = now - timedelta(days=7)
        elif period == "year":
            start = now - timedelta(days=365)
        else:
            start = now - timedelta(days=30)

        result = await session.execute(
            text(
                "SELECT p.name, SUM(si.qty) as total_qty, SUM(si.line_total) as total_revenue "
                "FROM sale_items si "
                "JOIN sales s ON s.id = si.sale_id "
                "JOIN products p ON p.id = si.product_id "
                "WHERE s.tenant_id = :tid "
                "AND s.status = 'confirmed' "
                "AND s.created_at >= :start "
                "GROUP BY p.name "
                "ORDER BY total_revenue DESC "
                "LIMIT :limit"
            ),
            {"tid": tenant_id, "start": start, "limit": limit},
        )
        rows = result.fetchall()
        products = [
            {
                "rank": i + 1,
                "product_name": r[0],
                "total_qty": float(r[1]),
                "total_revenue": float(r[2]),
            }
            for i, r in enumerate(rows)
        ]
        return json.dumps({"period": period, "top_products": products})
    except Exception as e:
        logger.warning("get_top_products failed: %s", e)
        return json.dumps({"period": period, "top_products": [], "error": str(e)})


async def get_revenue_trend(
    session: AsyncSession,
    tenant_id: UUID,
    days: int = 30,
) -> str:
    """Get daily revenue trend over time.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        days: Number of days to look back (default: 30).

    Returns:
        JSON string with daily revenue figures.
    """
    try:
        start = datetime.now(UTC) - timedelta(days=days)
        result = await session.execute(
            text(
                "SELECT DATE(created_at) as sale_date, SUM(total) as daily_revenue, COUNT(*) as sale_count "
                "FROM sales "
                "WHERE tenant_id = :tid "
                "AND status = 'confirmed' "
                "AND created_at >= :start "
                "GROUP BY DATE(created_at) "
                "ORDER BY sale_date"
            ),
            {"tid": tenant_id, "start": start},
        )
        rows = result.fetchall()
        trend = [
            {
                "date": r[0].isoformat() if r[0] else None,
                "revenue": float(r[1]),
                "sales_count": int(r[2]),
            }
            for r in rows
        ]
        return json.dumps({"days": days, "trend": trend})
    except Exception as e:
        logger.warning("get_revenue_trend failed: %s", e)
        return json.dumps({"days": days, "trend": [], "error": str(e)})


async def get_recent_transactions(
    session: AsyncSession,
    tenant_id: UUID,
    limit: int = 10,
) -> str:
    """Get recent sales transactions.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        limit: Maximum transactions to return (default: 10).

    Returns:
        JSON string with recent transactions.
    """
    try:
        result = await session.execute(
            text(
                "SELECT id, sale_number, customer_name, total, payment_method, status, created_at "
                "FROM sales "
                "WHERE tenant_id = :tid "
                "ORDER BY created_at DESC "
                "LIMIT :limit"
            ),
            {"tid": tenant_id, "limit": limit},
        )
        rows = result.fetchall()
        transactions = [
            {
                "id": str(r[0]),
                "sale_number": r[1],
                "customer_name": r[2],
                "total": float(r[3]) if r[3] else 0,
                "payment_method": r[4],
                "status": r[5],
                "created_at": r[6].isoformat() if r[6] else None,
            }
            for r in rows
        ]
        return json.dumps({"count": len(transactions), "transactions": transactions})
    except Exception as e:
        logger.warning("get_recent_transactions failed: %s", e)
        return json.dumps({"count": 0, "transactions": [], "error": str(e)})


# --- Customer Tools -------------------------------------------------------------------


async def get_customer_insights(
    session: AsyncSession,
    tenant_id: UUID,
    limit: int = 10,
) -> str:
    """Get customer insights -- top customers by spending.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        limit: Number of top customers to return (default: 10).

    Returns:
        JSON string with top customers and their spending.
    """
    try:
        result = await session.execute(
            text(
                "SELECT customer_name, COUNT(*) as order_count, SUM(total) as total_spent "
                "FROM sales "
                "WHERE tenant_id = :tid "
                "AND status = 'confirmed' "
                "AND customer_name IS NOT NULL "
                "GROUP BY customer_name "
                "ORDER BY total_spent DESC "
                "LIMIT :limit"
            ),
            {"tid": tenant_id, "limit": limit},
        )
        rows = result.fetchall()
        customers = [
            {
                "rank": i + 1,
                "customer_name": r[0],
                "order_count": int(r[1]),
                "total_spent": float(r[2]),
            }
            for i, r in enumerate(rows)
        ]
        return json.dumps({"count": len(customers), "top_customers": customers})
    except Exception as e:
        logger.warning("get_customer_insights failed: %s", e)
        return json.dumps({"count": 0, "top_customers": [], "error": str(e)})


# --- Inventory Tools ------------------------------------------------------------------


async def get_inventory_alerts(
    session: AsyncSession,
    tenant_id: UUID,
    threshold: int = 10,
) -> str:
    """Get products with low stock levels (reorder alerts).

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        threshold: Stock level threshold for alerts (default: 10).

    Returns:
        JSON string with low-stock products.
    """
    try:
        result = await session.execute(
            text(
                "SELECT p.name, p.sku, SUM(sb.qty) as total_stock, "
                "SUM(sb.reserved_qty) as total_reserved "
                "FROM stock_balances sb "
                "JOIN products p ON p.id = sb.product_id "
                "WHERE sb.tenant_id = :tid "
                "GROUP BY p.name, p.sku "
                "HAVING SUM(sb.qty) - SUM(sb.reserved_qty) <= :threshold "
                "ORDER BY (SUM(sb.qty) - SUM(sb.reserved_qty)) ASC"
            ),
            {"tid": tenant_id, "threshold": threshold},
        )
        rows = result.fetchall()
        alerts = [
            {
                "product_name": r[0],
                "sku": r[1],
                "total_stock": float(r[2]),
                "reserved": float(r[3]),
                "available": float(r[2]) - float(r[3]),
            }
            for r in rows
        ]
        return json.dumps({"threshold": threshold, "count": len(alerts), "alerts": alerts})
    except Exception as e:
        logger.warning("get_inventory_alerts failed: %s", e)
        return json.dumps({"threshold": threshold, "count": 0, "alerts": [], "error": str(e)})


# --- Financial Tools ------------------------------------------------------------------


async def get_profit_loss(
    session: AsyncSession,
    tenant_id: UUID,
    period: str = "month",
) -> str:
    """Get profit and loss summary.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        period: Time period -- "week", "month", "year".

    Returns:
        JSON string with P&L data (revenue, expenses, net profit).
    """
    try:
        now = datetime.now(UTC)
        if period == "week":
            start = now - timedelta(days=7)
        elif period == "year":
            start = now - timedelta(days=365)
        else:
            start = now - timedelta(days=30)

        rev_result = await session.execute(
            text(
                "SELECT COALESCE(SUM(total), 0) "
                "FROM sales "
                "WHERE tenant_id = :tid AND status = 'confirmed' "
                "AND created_at >= :start"
            ),
            {"tid": tenant_id, "start": start},
        )
        revenue = float(rev_result.scalar() or 0)

        exp_result = await session.execute(
            text(
                "SELECT COALESCE(SUM(amount), 0) "
                "FROM expenses "
                "WHERE tenant_id = :tid "
                "AND created_at >= :start"
            ),
            {"tid": tenant_id, "start": start},
        )
        expenses = float(exp_result.scalar() or 0)

        net_profit = revenue - expenses
        margin = (net_profit / revenue * 100) if revenue > 0 else 0

        return json.dumps({
            "period": period,
            "revenue": revenue,
            "expenses": expenses,
            "net_profit": net_profit,
            "profit_margin_pct": round(margin, 2),
        })
    except Exception as e:
        logger.warning("get_profit_loss failed: %s", e)
        return json.dumps({"period": period, "error": str(e)})


async def get_expenses_by_category(
    session: AsyncSession,
    tenant_id: UUID,
    period: str = "month",
) -> str:
    """Get expense breakdown by category.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        period: Time period -- "week", "month", "year".

    Returns:
        JSON string with expenses grouped by category.
    """
    try:
        now = datetime.now(UTC)
        if period == "week":
            start = now - timedelta(days=7)
        elif period == "year":
            start = now - timedelta(days=365)
        else:
            start = now - timedelta(days=30)

        result = await session.execute(
            text(
                "SELECT category, SUM(amount) as total, COUNT(*) as count "
                "FROM expenses "
                "WHERE tenant_id = :tid "
                "AND created_at >= :start "
                "GROUP BY category "
                "ORDER BY total DESC"
            ),
            {"tid": tenant_id, "start": start},
        )
        rows = result.fetchall()
        categories = [
            {
                "category": r[0],
                "total": float(r[1]),
                "count": int(r[2]),
            }
            for r in rows
        ]
        total_expenses = sum(c["total"] for c in categories)
        return json.dumps({
            "period": period,
            "total_expenses": total_expenses,
            "categories": categories,
        })
    except Exception as e:
        logger.warning("get_expenses_by_category failed: %s", e)
        return json.dumps({"period": period, "categories": [], "error": str(e)})


async def get_accounts_receivable(
    session: AsyncSession,
    tenant_id: UUID,
    status_filter: str | None = None,
) -> str:
    """Get outstanding accounts receivable (unpaid invoices).

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        status_filter: Optional status filter (pending, overdue, partial).

    Returns:
        JSON string with AR records and totals.
    """
    try:
        query = (
            "SELECT id, customer_name, invoice_number, amount, amount_paid, "
            "balance, due_date, status "
            "FROM accounts_receivable "
            "WHERE tenant_id = :tid "
        )
        params: dict[str, Any] = {"tid": tenant_id}

        if status_filter:
            query += " AND status = :status"
            params["status"] = status_filter

        query += " ORDER BY due_date ASC"

        result = await session.execute(text(query), params)
        rows = result.fetchall()

        ar_records = [
            {
                "id": str(r[0]),
                "customer_name": r[1],
                "invoice_number": r[2],
                "amount": float(r[3]) if r[3] else 0,
                "amount_paid": float(r[4]) if r[4] else 0,
                "balance": float(r[5]) if r[5] else 0,
                "due_date": r[6].isoformat() if r[6] else None,
                "status": r[7],
            }
            for r in rows
        ]
        total_outstanding = sum(r["balance"] for r in ar_records)

        return json.dumps({
            "count": len(ar_records),
            "total_outstanding": total_outstanding,
            "records": ar_records,
        })
    except Exception as e:
        logger.warning("get_accounts_receivable failed: %s", e)
        return json.dumps({"count": 0, "records": [], "error": str(e)})


# --- Tool Registry --------------------------------------------------------------------

TOOL_REGISTRY: dict[str, Any] = {
    "search_products": search_products,
    "get_product_details": get_product_details,
    "check_stock": check_stock,
    "get_sales_summary": get_sales_summary,
    "get_top_products": get_top_products,
    "get_revenue_trend": get_revenue_trend,
    "get_recent_transactions": get_recent_transactions,
    "get_customer_insights": get_customer_insights,
    "get_inventory_alerts": get_inventory_alerts,
    "get_profit_loss": get_profit_loss,
    "get_expenses_by_category": get_expenses_by_category,
    "get_accounts_receivable": get_accounts_receivable,
}

TOOL_DESCRIPTIONS = {
    "search_products": "Search products by name or SKU. Args: query (str), limit (int, default 10)",
    "get_product_details": "Get detailed info for a specific product. Args: product_id (str UUID)",
    "check_stock": "Check stock levels. Args: product_id (str, optional), product_name (str, optional)",
    "get_sales_summary": "Get sales totals. Args: period (str: today/yesterday/week/month/year)",
    "get_top_products": "Get top-selling products. Args: period (str: week/month/year), limit (int)",
    "get_revenue_trend": "Get daily revenue trend. Args: days (int, default 30)",
    "get_recent_transactions": "Get recent sales. Args: limit (int, default 10)",
    "get_customer_insights": "Get top customers. Args: limit (int, default 10)",
    "get_inventory_alerts": "Get low-stock products. Args: threshold (int, default 10)",
    "get_profit_loss": "Get P&L summary. Args: period (str: week/month/year)",
    "get_expenses_by_category": "Get expense breakdown. Args: period (str: week/month/year)",
    "get_accounts_receivable": "Get unpaid invoices. Args: status_filter (str, optional)",
    "compare_product_prices": "Web search for product prices. Args: product_name (str)",
    "search_product_info": "Wikipedia product info. Args: query (str)",
}
