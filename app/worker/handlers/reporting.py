from sqlalchemy.ext.asyncio import AsyncSession

from app.common.events.envelope import EventEnvelope


async def handle_sale_receipt_created(envelope: EventEnvelope, session: AsyncSession) -> None:
    from app.reporting.repository import upsert_daily_summary
    from app.reporting.service import plan_upsert_daily_summary

    tenant_id = envelope.payload.get("tenant_id")
    total = envelope.payload.get("total")
    if not tenant_id or not total:
        return

    summary = plan_upsert_daily_summary(
        tenant_id=tenant_id,
        sales_total=float(total),
        sales_count=1,
    )
    await upsert_daily_summary(session, summary)


async def handle_sale_voided(envelope: EventEnvelope, session: AsyncSession) -> None:
    from app.reporting.repository import upsert_daily_summary
    from app.reporting.service import plan_upsert_daily_summary

    tenant_id = envelope.payload.get("tenant_id")
    total = envelope.payload.get("total")
    if not tenant_id or not total:
        return

    summary = plan_upsert_daily_summary(
        tenant_id=tenant_id,
        sales_total=-float(total),
        sales_count=-1,
    )
    await upsert_daily_summary(session, summary)


async def handle_payment_succeeded(envelope: EventEnvelope, session: AsyncSession) -> None:
    from app.reporting.repository import upsert_payment_method_summary
    from app.reporting.service import plan_upsert_payment_method_summary

    tenant_id = envelope.payload.get("tenant_id")
    amount = envelope.payload.get("amount")
    method = envelope.payload.get("method", "unknown")
    if not tenant_id or not amount:
        return

    summary = plan_upsert_payment_method_summary(
        tenant_id=tenant_id,
        payment_method=method,
        amount=float(amount),
    )
    await upsert_payment_method_summary(session, summary)


async def handle_stock_adjusted(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Update store inventory summary when stock is adjusted."""
    from sqlalchemy import text

    tenant_id = envelope.payload.get("tenant_id")
    store_id = envelope.payload.get("store_id")
    product_id = envelope.payload.get("product_id")
    if not tenant_id or not store_id or not product_id:
        return

    await session.execute(
        text("""
            INSERT INTO mv_store_inventory
                (tenant_id, store_id, product_id, qty, reserved_qty, committed_qty, updated_at)
            VALUES
                (:tenant_id, :store_id, :product_id, 0, 0, 0, now())
            ON CONFLICT (tenant_id, store_id, product_id)
            DO UPDATE SET updated_at = now()
        """),
        {"tenant_id": tenant_id, "store_id": store_id, "product_id": product_id},
    )


async def handle_stock_reserved(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Update reserved quantity in store inventory summary."""
    from sqlalchemy import text

    tenant_id = envelope.payload.get("tenant_id")
    store_id = envelope.payload.get("store_id")
    product_id = envelope.payload.get("product_id")
    qty = envelope.payload.get("qty", 0)
    if not tenant_id or not store_id or not product_id:
        return

    await session.execute(
        text("""
            UPDATE mv_store_inventory
            SET reserved_qty = reserved_qty + :qty, updated_at = now()
            WHERE tenant_id = :tenant_id AND store_id = :store_id AND product_id = :product_id
        """),
        {"tenant_id": tenant_id, "store_id": store_id, "product_id": product_id, "qty": float(qty)},
    )


async def handle_stock_committed(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Update committed quantity in store inventory summary."""
    from sqlalchemy import text

    tenant_id = envelope.payload.get("tenant_id")
    store_id = envelope.payload.get("store_id")
    product_id = envelope.payload.get("product_id")
    qty = envelope.payload.get("qty", 0)
    if not tenant_id or not store_id or not product_id:
        return

    await session.execute(
        text("""
            UPDATE mv_store_inventory
            SET committed_qty = committed_qty + :qty, updated_at = now()
            WHERE tenant_id = :tenant_id AND store_id = :store_id AND product_id = :product_id
        """),
        {"tenant_id": tenant_id, "store_id": store_id, "product_id": product_id, "qty": float(qty)},
    )


async def handle_stock_released(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Update released quantity in store inventory summary."""
    from sqlalchemy import text

    tenant_id = envelope.payload.get("tenant_id")
    store_id = envelope.payload.get("store_id")
    product_id = envelope.payload.get("product_id")
    qty = envelope.payload.get("qty", 0)
    if not tenant_id or not store_id or not product_id:
        return

    await session.execute(
        text("""
            UPDATE mv_store_inventory
            SET reserved_qty = GREATEST(reserved_qty - :qty, 0), updated_at = now()
            WHERE tenant_id = :tenant_id AND store_id = :store_id AND product_id = :product_id
        """),
        {"tenant_id": tenant_id, "store_id": store_id, "product_id": product_id, "qty": float(qty)},
    )


async def handle_journal_posted(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Update financial summaries when a journal entry is posted."""
    from sqlalchemy import text

    tenant_id = envelope.payload.get("tenant_id")
    store_id = envelope.payload.get("store_id")
    total_debit = envelope.payload.get("total_debit", 0)
    total_credit = envelope.payload.get("total_credit", 0)
    if not tenant_id:
        return

    if store_id:
        await session.execute(
            text("""
                INSERT INTO mv_store_sales
                    (tenant_id, store_id, revenue, expenses, transaction_count, updated_at)
                VALUES
                    (:tenant_id, :store_id, :revenue, :expenses, 1, now())
                ON CONFLICT (tenant_id, store_id)
                DO UPDATE SET
                    revenue = mv_store_sales.revenue + :revenue,
                    expenses = mv_store_sales.expenses + :expenses,
                    transaction_count = mv_store_sales.transaction_count + 1,
                    updated_at = now()
            """),
            {"tenant_id": tenant_id, "store_id": store_id,
             "revenue": float(total_credit), "expenses": float(total_debit)},
        )


async def handle_notification_sent(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Log notification sent event."""
    import logging
    logger = logging.getLogger("storeflow.worker.reporting")
    channel = envelope.payload.get("channel", "unknown")
    recipient = envelope.payload.get("recipient", "unknown")
    logger.info("Notification sent: channel=%s recipient=%s", channel, recipient)


async def handle_tier_changed(envelope: EventEnvelope, session: AsyncSession) -> None:
    """Update tenant tier in cache when tier changes."""
    import logging
    logger = logging.getLogger("storeflow.worker.reporting")
    tenant_id = envelope.payload.get("tenant_id")
    new_tier = envelope.payload.get("new_tier")
    logger.info("Tier changed for tenant %s: %s", tenant_id, new_tier)
