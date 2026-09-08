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
                "AND status = 'completed' "
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
                "AND s.status = 'completed' "
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
                "AND status = 'completed' "
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
                "AND status = 'completed' "
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
    threshold: int | None = None,
) -> str:
    """Get products with low stock levels (reorder alerts).

    Uses each product's reorder_point from the database. If no threshold is
    provided, filters products where available stock <= their reorder_point.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        threshold: Optional override -- if set, ignores per-product reorder_point.

    Returns:
        JSON string with low-stock products.
    """
    try:
        if threshold is not None:
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
        else:
            result = await session.execute(
                text(
                    "SELECT p.name, p.sku, SUM(sb.qty) as total_stock, "
                    "SUM(sb.reserved_qty) as total_reserved, p.reorder_point "
                    "FROM stock_balances sb "
                    "JOIN products p ON p.id = sb.product_id "
                    "WHERE sb.tenant_id = :tid "
                    "GROUP BY p.name, p.sku, p.reorder_point "
                    "HAVING SUM(sb.qty) - SUM(sb.reserved_qty) <= p.reorder_point "
                    "ORDER BY (SUM(sb.qty) - SUM(sb.reserved_qty)) ASC"
                ),
                {"tid": tenant_id},
            )

        rows = result.fetchall()
        alerts = [
            {
                "product_name": r[0],
                "sku": r[1],
                "total_stock": float(r[2]),
                "reserved": float(r[3]),
                "available": float(r[2]) - float(r[3]),
                "reorder_point": float(r[4]) if len(r) > 4 and r[4] else None,
            }
            for r in rows
        ]
        return json.dumps({"count": len(alerts), "alerts": alerts})
    except Exception as e:
        logger.warning("get_inventory_alerts failed: %s", e)
        return json.dumps({"count": 0, "alerts": [], "error": str(e)})


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
                "WHERE tenant_id = :tid AND status = 'completed' "
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


# --- Store Tools ----------------------------------------------------------------------


async def list_stores(
    session: AsyncSession,
    tenant_id: UUID,
) -> str:
    """List all stores for the tenant with stock summary.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.

    Returns:
        JSON string with store listing.
    """
    try:
        result = await session.execute(
            text(
                "SELECT s.id, s.name, s.address, s.is_warehouse, s.status, s.created_at, "
                "COALESCE(sb_agg.total_products, 0) as total_products, "
                "COALESCE(sb_agg.total_stock_value, 0) as total_stock_value "
                "FROM stores s "
                "LEFT JOIN ("
                "  SELECT store_id, COUNT(DISTINCT product_id) as total_products, "
                "  SUM(qty * unit_cost) as total_stock_value "
                "  FROM stock_balances "
                "  WHERE tenant_id = :tid AND qty > 0 "
                "  GROUP BY store_id"
                ") sb_agg ON sb_agg.store_id = s.id "
                "WHERE s.tenant_id = :tid "
                "ORDER BY s.name"
            ),
            {"tid": tenant_id},
        )
        rows = result.fetchall()
        stores = [
            {
                "id": str(r[0]),
                "name": r[1],
                "address": r[2],
                "is_warehouse": r[3],
                "status": r[4],
                "created_at": r[5].isoformat() if r[5] else None,
                "total_products": int(r[6]),
                "total_stock_value": float(r[7]),
            }
            for r in rows
        ]
        return json.dumps({"count": len(stores), "stores": stores})
    except Exception as e:
        logger.warning("list_stores failed: %s", e)
        return json.dumps({"count": 0, "stores": [], "error": str(e)})


async def get_store_analytics(
    session: AsyncSession,
    tenant_id: UUID,
    store_id: str | None = None,
) -> str:
    """Get sales analytics for a store or all stores this month.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        store_id: Optional UUID of a specific store.

    Returns:
        JSON string with store sales analytics.
    """
    try:
        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        if store_id:
            store_clause = "AND s.store_id = :sid"
            params: dict[str, Any] = {"tid": tenant_id, "sid": UUID(store_id), "month_start": month_start}
        else:
            store_clause = ""
            params = {"tid": tenant_id, "month_start": month_start}

        result = await session.execute(
            text(
                f"SELECT st.id, st.name, "
                f"COUNT(s.id) as total_sales, "
                f"COALESCE(SUM(s.total), 0) as total_revenue, "
                f"COALESCE(AVG(s.total), 0) as avg_sale, "
                f"COUNT(DISTINCT DATE(s.created_at)) as active_days "
                f"FROM stores st "
                f"LEFT JOIN sales s ON s.store_id = st.id AND s.status = 'completed' AND s.created_at >= :month_start "
                f"WHERE st.tenant_id = :tid {store_clause} "
                f"GROUP BY st.id, st.name "
                f"ORDER BY total_revenue DESC"
            ),
            params,
        )
        rows = result.fetchall()
        analytics = [
            {
                "store_id": str(r[0]),
                "store_name": r[1],
                "total_sales": int(r[2]),
                "total_revenue": float(r[3]),
                "avg_sale": float(r[4]),
                "active_days": int(r[5]),
            }
            for r in rows
        ]
        return json.dumps({"period": "this_month", "stores": analytics})
    except Exception as e:
        logger.warning("get_store_analytics failed: %s", e)
        return json.dumps({"period": "this_month", "stores": [], "error": str(e)})


# --- Employee Tools --------------------------------------------------------------------


async def get_employees(
    session: AsyncSession,
    tenant_id: UUID,
) -> str:
    """List employees (users) for the tenant with their roles and last login.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.

    Returns:
        JSON string with employee listing.
    """
    try:
        result = await session.execute(
            text(
                "SELECT u.id, u.full_name, u.email, u.phone, u.status, u.last_login_at, "
                "u.created_at, "
                "COALESCE(r.name, 'No Role') as role_name "
                "FROM users u "
                "LEFT JOIN user_roles ur ON ur.user_id = u.id "
                "LEFT JOIN roles r ON r.id = ur.role_id "
                "WHERE u.tenant_id = :tid "
                "ORDER BY u.full_name"
            ),
            {"tid": tenant_id},
        )
        rows = result.fetchall()
        employees = [
            {
                "id": str(r[0]),
                "full_name": r[1],
                "email": r[2],
                "phone": r[3],
                "status": r[4],
                "last_login_at": r[5].isoformat() if r[5] else None,
                "created_at": r[6].isoformat() if r[6] else None,
                "role": r[7],
            }
            for r in rows
        ]
        return json.dumps({"count": len(employees), "employees": employees})
    except Exception as e:
        logger.warning("get_employees failed: %s", e)
        return json.dumps({"count": 0, "employees": [], "error": str(e)})


# --- Expense Tools ---------------------------------------------------------------------


async def get_expenses_list(
    session: AsyncSession,
    tenant_id: UUID,
    limit: int = 20,
) -> str:
    """Get recent expenses with details.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        limit: Maximum expenses to return (default: 20).

    Returns:
        JSON string with recent expenses.
    """
    try:
        result = await session.execute(
            text(
                "SELECT id, expense_number, category, description, amount, "
                "vendor, expense_date, created_at "
                "FROM expenses "
                "WHERE tenant_id = :tid "
                "ORDER BY expense_date DESC "
                "LIMIT :limit"
            ),
            {"tid": tenant_id, "limit": limit},
        )
        rows = result.fetchall()
        expenses = [
            {
                "id": str(r[0]),
                "expense_number": r[1],
                "category": r[2],
                "description": r[3],
                "amount": float(r[4]) if r[4] else 0,
                "vendor": r[5],
                "expense_date": r[6].isoformat() if r[6] else None,
                "created_at": r[7].isoformat() if r[7] else None,
            }
            for r in rows
        ]
        total = sum(e["amount"] for e in expenses)
        return json.dumps({"count": len(expenses), "total": total, "expenses": expenses})
    except Exception as e:
        logger.warning("get_expenses_list failed: %s", e)
        return json.dumps({"count": 0, "total": 0, "expenses": [], "error": str(e)})


# --- Advanced Analytics Tools ----------------------------------------------------------


async def get_product_performance(
    session: AsyncSession,
    tenant_id: UUID,
    period: str = "month",
    limit: int = 10,
) -> str:
    """Get product performance metrics: revenue, quantity sold, margin.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        period: Time period -- "week", "month", "year".
        limit: Number of products to return (default: 10).

    Returns:
        JSON string with product performance data.
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
                "SELECT p.name, p.sku, p.cost_price, p.selling_price, "
                "COALESCE(SUM(si.qty), 0) as total_qty, "
                "COALESCE(SUM(si.line_total), 0) as total_revenue, "
                "COUNT(DISTINCT s.id) as sale_count "
                "FROM products p "
                "LEFT JOIN sale_items si ON si.product_id = p.id "
                "LEFT JOIN sales s ON s.id = si.sale_id AND s.status = 'completed' AND s.created_at >= :start "
                "WHERE p.tenant_id = :tid "
                "GROUP BY p.id, p.name, p.sku, p.cost_price, p.selling_price "
                "ORDER BY total_revenue DESC "
                "LIMIT :limit"
            ),
            {"tid": tenant_id, "start": start, "limit": limit},
        )
        rows = result.fetchall()
        products = []
        for r in rows:
            cost = float(r[3]) if r[3] else 0
            revenue = float(r[5])
            qty = float(r[4])
            margin = ((revenue - cost * qty) / revenue * 100) if revenue > 0 and cost > 0 else 0
            products.append({
                "name": r[0],
                "sku": r[1],
                "total_qty": qty,
                "total_revenue": revenue,
                "sale_count": int(r[6]),
                "margin_pct": round(margin, 1),
            })
        return json.dumps({"period": period, "count": len(products), "products": products})
    except Exception as e:
        logger.warning("get_product_performance failed: %s", e)
        return json.dumps({"period": period, "count": 0, "products": [], "error": str(e)})


async def get_customer_detail(
    session: AsyncSession,
    tenant_id: UUID,
    customer_name: str,
) -> str:
    """Get detailed purchase history for a specific customer.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        customer_name: Name of the customer to look up.

    Returns:
        JSON string with customer purchase history.
    """
    try:
        result = await session.execute(
            text(
                "SELECT customer_name, customer_phone, "
                "COUNT(*) as total_orders, SUM(total) as total_spent, "
                "AVG(total) as avg_order, "
                "MIN(created_at) as first_order, MAX(created_at) as last_order "
                "FROM sales "
                "WHERE tenant_id = :tid AND status = 'completed' "
                "AND LOWER(customer_name) LIKE LOWER(:name) "
                "GROUP BY customer_name, customer_phone "
                "ORDER BY total_spent DESC "
                "LIMIT 1"
            ),
            {"tid": tenant_id, "name": f"%{customer_name}%"},
        )
        row = result.fetchone()
        if not row:
            return json.dumps({"error": f"Customer '{customer_name}' not found"})

        result2 = await session.execute(
            text(
                "SELECT s.sale_number, s.total, s.payment_methods, s.created_at "
                "FROM sales s "
                "WHERE s.tenant_id = :tid AND s.status = 'completed' "
                "AND LOWER(s.customer_name) LIKE LOWER(:name) "
                "ORDER BY s.created_at DESC "
                "LIMIT 10"
            ),
            {"tid": tenant_id, "name": f"%{customer_name}%"},
        )
        recent = result2.fetchall()
        return json.dumps({
            "customer_name": row[0],
            "phone": row[1],
            "total_orders": int(row[2]),
            "total_spent": float(row[3]) if row[3] else 0,
            "avg_order": float(row[4]) if row[4] else 0,
            "first_order": row[5].isoformat() if row[5] else None,
            "last_order": row[6].isoformat() if row[6] else None,
            "recent_orders": [
                {"sale_number": r[0], "total": float(r[1]) if r[1] else 0, "created_at": r[3].isoformat() if r[3] else None}
                for r in recent
            ],
        })
    except Exception as e:
        logger.warning("get_customer_detail failed: %s", e)
        return json.dumps({"error": str(e)})


async def get_inventory_value(
    session: AsyncSession,
    tenant_id: UUID,
) -> str:
    """Get total inventory value across all stores.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.

    Returns:
        JSON string with inventory value breakdown by store.
    """
    try:
        result = await session.execute(
            text(
                "SELECT s.name, "
                "COUNT(DISTINCT sb.product_id) as total_products, "
                "SUM(sb.qty) as total_units, "
                "SUM(sb.qty * sb.unit_cost) as total_value "
                "FROM stock_balances sb "
                "JOIN stores s ON s.id = sb.store_id "
                "WHERE sb.tenant_id = :tid AND sb.qty > 0 "
                "GROUP BY s.name "
                "ORDER BY total_value DESC"
            ),
            {"tid": tenant_id},
        )
        rows = result.fetchall()
        stores = [
            {
                "store_name": r[0],
                "total_products": int(r[1]),
                "total_units": float(r[2]),
                "total_value": float(r[3]) if r[3] else 0,
            }
            for r in rows
        ]
        grand_total = sum(s["total_value"] for s in stores)
        return json.dumps({"grand_total": grand_total, "stores": stores})
    except Exception as e:
        logger.warning("get_inventory_value failed: %s", e)
        return json.dumps({"grand_total": 0, "stores": [], "error": str(e)})


async def get_payment_methods(
    session: AsyncSession,
    tenant_id: UUID,
    period: str = "month",
) -> str:
    """Get payment method breakdown (cash, transfer, card, etc).

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        period: Time period -- "week", "month", "year".

    Returns:
        JSON string with payment method breakdown.
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
                "SELECT payment_methods, total "
                "FROM sales "
                "WHERE tenant_id = :tid AND status = 'completed' AND created_at >= :start"
            ),
            {"tid": tenant_id, "start": start},
        )
        rows = result.fetchall()
        method_totals: dict[str, float] = {}
        for row in rows:
            methods = row[0] if row[0] else {}
            total = float(row[1]) if row[1] else 0
            if isinstance(methods, dict):
                for method, amount in methods.items():
                    method_totals[method] = method_totals.get(method, 0) + float(amount)
            else:
                method_totals["cash"] = method_totals.get("cash", 0) + total
        grand_total = sum(method_totals.values())
        breakdown = [
            {"method": m, "amount": round(a, 2), "percentage": round(a / grand_total * 100, 1) if grand_total > 0 else 0}
            for m, a in sorted(method_totals.items(), key=lambda x: -x[1])
        ]
        return json.dumps({"period": period, "grand_total": grand_total, "methods": breakdown})
    except Exception as e:
        logger.warning("get_payment_methods failed: %s", e)
        return json.dumps({"period": period, "grand_total": 0, "methods": [], "error": str(e)})


async def get_category_performance(
    session: AsyncSession,
    tenant_id: UUID,
    period: str = "month",
) -> str:
    """Get sales performance by product category.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        period: Time period -- "week", "month", "year".

    Returns:
        JSON string with category performance breakdown.
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
                "SELECT COALESCE(c.name, 'Uncategorized') as category, "
                "COUNT(DISTINCT p.id) as product_count, "
                "SUM(si.qty) as total_qty, "
                "SUM(si.line_total) as total_revenue "
                "FROM sale_items si "
                "JOIN sales s ON s.id = si.sale_id AND s.status = 'completed' "
                "JOIN products p ON p.id = si.product_id "
                "LEFT JOIN categories c ON c.id = p.category_id "
                "WHERE s.tenant_id = :tid AND s.created_at >= :start "
                "GROUP BY c.name "
                "ORDER BY total_revenue DESC"
            ),
            {"tid": tenant_id, "start": start},
        )
        rows = result.fetchall()
        categories = [
            {
                "category": r[0],
                "product_count": int(r[1]),
                "total_qty": float(r[2]),
                "total_revenue": float(r[3]) if r[3] else 0,
            }
            for r in rows
        ]
        return json.dumps({"period": period, "categories": categories})
    except Exception as e:
        logger.warning("get_category_performance failed: %s", e)
        return json.dumps({"period": period, "categories": [], "error": str(e)})


async def get_daily_summary(
    session: AsyncSession,
    tenant_id: UUID,
) -> str:
    """Get today's business summary: sales, revenue, top product, average.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.

    Returns:
        JSON string with daily summary.
    """
    try:
        now = datetime.now(UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        result = await session.execute(
            text(
                "SELECT COUNT(*), COALESCE(SUM(total), 0), COALESCE(AVG(total), 0) "
                "FROM sales "
                "WHERE tenant_id = :tid AND status = 'completed' "
                "AND created_at >= :start"
            ),
            {"tid": tenant_id, "start": today_start},
        )
        row = result.fetchone()

        top = await session.execute(
            text(
                "SELECT p.name, SUM(si.qty) as qty, SUM(si.line_total) as revenue "
                "FROM sale_items si "
                "JOIN sales s ON s.id = si.sale_id "
                "JOIN products p ON p.id = si.product_id "
                "WHERE s.tenant_id = :tid AND s.status = 'completed' "
                "AND s.created_at >= :start "
                "GROUP BY p.name ORDER BY revenue DESC LIMIT 3"
            ),
            {"tid": tenant_id, "start": today_start},
        )
        top_rows = top.fetchall()
        top_products = [
            {"name": r[0], "qty": float(r[1]), "revenue": float(r[2]) if r[2] else 0}
            for r in top_rows
        ]

        return json.dumps({
            "date": today_start.date().isoformat(),
            "total_sales": int(row[0]),
            "total_revenue": float(row[1]),
            "average_sale": float(row[2]),
            "top_products": top_products,
        })
    except Exception as e:
        logger.warning("get_daily_summary failed: %s", e)
        return json.dumps({"error": str(e)})


async def get_comparison(
    session: AsyncSession,
    tenant_id: UUID,
    period: str = "month",
) -> str:
    """Compare current period vs previous period (sales, revenue, customers).

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        period: Comparison period -- "week", "month".

    Returns:
        JSON string with period comparison.
    """
    try:
        now = datetime.now(UTC)
        if period == "week":
            current_start = now - timedelta(days=7)
            prev_start = now - timedelta(days=14)
            prev_end = now - timedelta(days=7)
        else:
            current_start = now - timedelta(days=30)
            prev_start = now - timedelta(days=60)
            prev_end = now - timedelta(days=30)

        async def _get_stats(start, end):
            result = await session.execute(
                text(
                    "SELECT COUNT(*), COALESCE(SUM(total), 0), "
                    "COUNT(DISTINCT customer_name) "
                    "FROM sales "
                    "WHERE tenant_id = :tid AND status = 'completed' "
                    "AND created_at >= :start AND created_at < :end"
                ),
                {"tid": tenant_id, "start": start, "end": end},
            )
            return result.fetchone()

        current = await _get_stats(current_start, now)
        prev = await _get_stats(prev_start, prev_end)

        def _pct(curr, prv):
            if prv == 0:
                return 100.0 if curr > 0 else 0.0
            return round((curr - prv) / prv * 100, 1)

        return json.dumps({
            "period": period,
            "current": {"total_sales": int(current[0]), "total_revenue": float(current[1]), "unique_customers": int(current[2])},
            "previous": {"total_sales": int(prev[0]), "total_revenue": float(prev[1]), "unique_customers": int(prev[2])},
            "change": {
                "sales_pct": _pct(int(current[0]), int(prev[0])),
                "revenue_pct": _pct(float(current[1]), float(prev[1])),
                "customers_pct": _pct(int(current[2]), int(prev[2])),
            },
        })
    except Exception as e:
        logger.warning("get_comparison failed: %s", e)
        return json.dumps({"error": str(e)})


async def get_tax_summary(
    session: AsyncSession,
    tenant_id: UUID,
    period: str = "month",
) -> str:
    """Get VAT/tax collected this period.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        period: Time period -- "week", "month", "year".

    Returns:
        JSON string with tax summary.
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
                "SELECT COALESCE(SUM(tax), 0), COALESCE(SUM(subtotal), 0), COUNT(*) "
                "FROM sales "
                "WHERE tenant_id = :tid AND status = 'completed' AND created_at >= :start"
            ),
            {"tid": tenant_id, "start": start},
        )
        row = result.fetchone()
        return json.dumps({
            "period": period,
            "total_tax_collected": float(row[0]),
            "total_subtotal": float(row[1]),
            "total_transactions": int(row[2]),
        })
    except Exception as e:
        logger.warning("get_tax_summary failed: %s", e)
        return json.dumps({"period": period, "error": str(e)})


async def get_void_summary(
    session: AsyncSession,
    tenant_id: UUID,
    period: str = "month",
) -> str:
    """Get void/refund summary.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        period: Time period -- "week", "month", "year".

    Returns:
        JSON string with void summary.
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
                "SELECT COUNT(*), COALESCE(SUM(total), 0) "
                "FROM sales "
                "WHERE tenant_id = :tid AND status = 'voided' "
                "AND created_at >= :start"
            ),
            {"tid": tenant_id, "start": start},
        )
        row = result.fetchone()
        return json.dumps({
            "period": period,
            "total_voids": int(row[0]),
            "total_voided_amount": float(row[1]) if row[1] else 0,
        })
    except Exception as e:
        logger.warning("get_void_summary failed: %s", e)
        return json.dumps({"period": period, "error": str(e)})


async def get_business_health(
    session: AsyncSession,
    tenant_id: UUID,
) -> str:
    """Get overall business health score combining sales, stock, receivables.

    Returns a health score (0-100) and breakdown of each factor.

    Args:
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.

    Returns:
        JSON string with business health assessment.
    """
    try:
        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)

        curr = await session.execute(
            text("SELECT COUNT(*), COALESCE(SUM(total), 0) FROM sales "
                 "WHERE tenant_id = :tid AND status = 'completed' AND created_at >= :start"),
            {"tid": tenant_id, "start": month_start},
        )
        curr_row = curr.fetchone()
        prev = await session.execute(
            text("SELECT COUNT(*), COALESCE(SUM(total), 0) FROM sales "
                 "WHERE tenant_id = :tid AND status = 'completed' AND created_at >= :start AND created_at < :end"),
            {"tid": tenant_id, "start": prev_month_start, "end": month_start},
        )
        prev_row = prev.fetchone()
        sales_growth = 0.0
        if prev_row[1] > 0:
            sales_growth = round((float(curr_row[1]) - float(prev_row[1])) / float(prev_row[1]) * 100, 1)

        low_stock = await session.execute(
            text("SELECT COUNT(*) FROM ("
                 "  SELECT p.id FROM stock_balances sb "
                 "  JOIN products p ON p.id = sb.product_id "
                 "  WHERE sb.tenant_id = :tid "
                 "  GROUP BY p.id, p.reorder_point "
                 "  HAVING SUM(sb.qty) - SUM(sb.reserved_qty) <= p.reorder_point"
                 ") sub"),
            {"tid": tenant_id},
        )
        low_stock_count = low_stock.scalar() or 0

        ar = await session.execute(
            text("SELECT COALESCE(SUM(balance), 0) FROM accounts_receivable "
                 "WHERE tenant_id = :tid AND status != 'paid'"),
            {"tid": tenant_id},
        )
        outstanding_ar = float(ar.scalar() or 0)

        sales_score = min(100, max(0, 50 + sales_growth))
        stock_score = max(0, 100 - low_stock_count * 10)
        ar_score = 100 if outstanding_ar == 0 else max(0, 100 - outstanding_ar / 1000 * 10)
        overall = round((sales_score * 0.4 + stock_score * 0.3 + ar_score * 0.3), 1)

        return json.dumps({
            "health_score": overall,
            "factors": {
                "sales_trend": {"score": round(sales_score, 1), "sales_growth_pct": sales_growth},
                "inventory_health": {"score": round(stock_score, 1), "low_stock_products": low_stock_count},
                "receivables": {"score": round(ar_score, 1), "outstanding_amount": outstanding_ar},
            },
        })
    except Exception as e:
        logger.warning("get_business_health failed: %s", e)
        return json.dumps({"error": str(e)})

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
    "list_stores": list_stores,
    "get_store_analytics": get_store_analytics,
    "get_employees": get_employees,
    "get_expenses_list": get_expenses_list,
    "get_product_performance": get_product_performance,
    "get_customer_detail": get_customer_detail,
    "get_inventory_value": get_inventory_value,
    "get_payment_methods": get_payment_methods,
    "get_category_performance": get_category_performance,
    "get_daily_summary": get_daily_summary,
    "get_comparison": get_comparison,
    "get_tax_summary": get_tax_summary,
    "get_void_summary": get_void_summary,
    "get_business_health": get_business_health,
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
    "get_inventory_alerts": "Get low-stock products using each product's reorder_point. Args: threshold (int, optional override)",
    "get_profit_loss": "Get P&L summary. Args: period (str: week/month/year)",
    "get_expenses_by_category": "Get expense breakdown. Args: period (str: week/month/year)",
    "get_accounts_receivable": "Get unpaid invoices. Args: status_filter (str, optional)",
    "list_stores": "List all stores with stock summary. Args: none",
    "get_store_analytics": "Get sales analytics by store this month. Args: store_id (str UUID, optional)",
    "get_employees": "List employees with roles and last login. Args: none",
    "get_expenses_list": "Get recent expenses. Args: limit (int, default 20)",
    "get_product_performance": "Product performance with revenue and margins. Args: period (str: week/month/year), limit (int)",
    "get_customer_detail": "Detailed customer purchase history. Args: customer_name (str)",
    "get_inventory_value": "Total inventory value across stores. Args: none",
    "get_payment_methods": "Payment method breakdown (cash/transfer/card). Args: period (str: week/month/year)",
    "get_category_performance": "Sales by product category. Args: period (str: week/month/year)",
    "get_daily_summary": "Today's business overview. Args: none",
    "get_comparison": "Current vs previous period comparison. Args: period (str: week/month)",
    "get_tax_summary": "VAT/tax collected. Args: period (str: week/month/year)",
    "get_void_summary": "Voids and refunds tracking. Args: period (str: week/month/year)",
    "get_business_health": "Overall business health score (0-100). Args: none",
    "compare_product_prices": "Web search for product prices. Args: product_name (str)",
    "search_product_info": "Wikipedia product info. Args: query (str)",
}
