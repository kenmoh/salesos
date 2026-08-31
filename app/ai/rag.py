"""RAG context builder -- legacy module, kept for backward compatibility.

The new AI service uses the tools module for data access instead of
pre-fetching context. This module is retained to avoid breaking imports
from existing code that references build_context().
"""

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("app.ai.rag")


async def build_context(
    session: AsyncSession,
    tenant_id: UUID,
    include_products: bool = True,
    include_inventory: bool = True,
    include_warehouses: bool = True,
    include_customers: bool = True,
) -> dict:
    """Fetch tenant data for LLM context.

    NOTE: This is a legacy function. The new AI service uses the tools module
    for on-demand data access instead of pre-fetching context.
    """
    context: dict = {}

    if include_products:
        try:
            rows = await session.execute(
                text(
                    "SELECT id, name, sku, selling_price, status "
                    "FROM products WHERE tenant_id = :tid LIMIT 100"
                ),
                {"tid": tenant_id},
            )
            context["products"] = [
                {
                    "id": str(r[0]),
                    "name": r[1],
                    "sku": r[2],
                    "selling_price": float(r[3]) if r[3] else 0,
                    "status": r[4],
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("Product context fetch failed: %s", e)

    if include_warehouses:
        try:
            rows = await session.execute(
                text(
                    "SELECT id, name FROM stores "
                    "WHERE tenant_id = :tid AND status = 'active' ORDER BY name"
                ),
                {"tid": tenant_id},
            )
            context["warehouses"] = [{"id": str(r[0]), "name": r[1]} for r in rows]
        except Exception as e:
            logger.warning("Store context fetch failed: %s", e)

    if include_inventory:
        try:
            rows = await session.execute(
                text(
                    """SELECT sb.product_id, sb.qty, sb.reserved_qty, s.name
                       FROM stock_balances sb
                       JOIN stores s ON s.id = sb.store_id
                       WHERE sb.tenant_id = :tid
                       ORDER BY sb.qty DESC
                       LIMIT 100"""
                ),
                {"tid": tenant_id},
            )
            name_map = {}
            if context.get("products"):
                name_map = {p["id"]: p["name"] for p in context["products"]}
            context["inventory"] = [
                {
                    "product_name": name_map.get(str(r[0]), "Unknown"),
                    "product_id": str(r[0]),
                    "quantity": float(r[1]),
                    "reserved": float(r[2]),
                    "store": r[3],
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("Inventory context fetch failed: %s", e)

    if include_customers:
        try:
            rows = await session.execute(
                text(
                    "SELECT name, phone, email FROM customers "
                    "WHERE tenant_id = :tid ORDER BY created_at DESC LIMIT 20"
                ),
                {"tid": tenant_id},
            )
            context["customers"] = [{"name": r[0], "phone": r[1], "email": r[2]} for r in rows]
        except Exception as e:
            logger.debug("Customer context unavailable: %s", e)

    return context
