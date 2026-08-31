"""Celery background tasks for the SalesOS worker.

This module defines background tasks that are NOT covered by the
RabbitMQ event-driven system. These are typically:
- Scheduled/cron tasks (analytics refresh, session cleanup, notification sending)
- Manual verification tasks (payment verification)
- Legacy migration tasks (event reconciliation)
- Background jobs with retry logic (QR generation)

Tasks that duplicate event handlers have been removed:
- task_post_sale_journal (replaced by accounting.handle_sale_confirmed)
- task_post_payment_journal (replaced by accounting.handle_payment_succeeded)
- task_handle_flutterwave_event (replaced by webhook handler)
- task_create_flutterwave_terminal (replaced by terminal event handlers)
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text

from app.common.settings import get_common_settings
from app.worker.celery_app import celery_app

logger = logging.getLogger("salesos.worker.tasks")

settings = get_common_settings()


@celery_app.task(name="app.worker.tasks.task_process_notifications")
def task_process_notifications() -> dict:
    """Process pending notifications and send them.

    Scheduled task that picks up pending notifications and sends them
    via the appropriate channel (email, SMS, in-app).

    Returns:
        Dict with counts of sent and failed notifications.
    """
    logger.info("Processing pending notifications")
    from app.notifications.repository import (
        get_pending_notifications,
        mark_notification_sent,
        mark_notification_failed,
    )
    from app.notifications.service import send_notification
    from app.common.db.engine import create_service_database

    sdb = create_service_database(settings.database_url)
    import asyncio

    async def _process():
        async with sdb.session() as session:
            notifications = await get_pending_notifications(session, limit=50)
            sent = 0
            failed = 0

            for notification in notifications:
                try:
                    success = send_notification(notification)
                    if success:
                        await mark_notification_sent(session, notification.id)
                        sent += 1
                    else:
                        await mark_notification_failed(
                            session, notification.id, "Send returned False"
                        )
                        failed += 1
                except Exception as exc:
                    logger.exception("Failed to send notification %s", notification.id)
                    await mark_notification_failed(session, notification.id, str(exc))
                    failed += 1

            await session.commit()
            return {"sent": sent, "failed": failed}

    result = asyncio.run(_process())
    logger.info(
        "Processed notifications: %d sent, %d failed",
        result["sent"],
        result["failed"],
    )
    return result


@celery_app.task(
    name="app.worker.tasks.task_generate_product_qr", bind=True, max_retries=3, default_retry_delay=10
)
def task_generate_product_qr(self, product_id: str, tenant_id: str, qr_payload: str) -> dict:
    """Generate QR code for a product and upload to Cloudinary.

    Args:
        self: Celery task instance for retry logic.
        product_identifier: Unique product identifier.
        tenant_identifier: Business/tenant identifier.
        qr_payload: Encoded QR code payload.

    Returns:
        Dict with product identifier and QR URL.
    """
    logger.info("Generating and uploading QR for product %s", product_id)
    from app.catalog.qr import generate_qr_png
    from app.catalog.cloudinary_upload import upload_qr_png
    from app.catalog.repository import update_product
    from app.common.db.engine import create_service_database

    sdb = create_service_database(settings.database_url)
    import asyncio

    async def _generate():
        png_bytes = generate_qr_png(qr_payload, box_size=12, border=4)
        upload = upload_qr_png(
            tenant_id=tenant_id,
            product_id=product_id,
            png_bytes=png_bytes,
        )
        async with sdb.session() as session:
            kwargs = {"qr_url": upload["url"]}
            if upload.get("public_id"):
                kwargs["qr_asset_id"] = upload["public_id"]
            await update_product(session, UUID(product_id), **kwargs)
            await session.commit()
        return upload

    try:
        upload = asyncio.run(_generate())
        return {
            "product_id": product_id,
            "qr_url": upload["url"],
            "generated": True,
        }
    except Exception as exc:
        logger.exception("Failed to generate QR for product %s", product_id)
        raise self.retry(exc=exc)


@celery_app.task(name="app.worker.tasks.task_verify_payment")
def task_verify_payment(business_id: str, payment_id: str, reference: str) -> dict:
    """Manually verify a payment with Flutterwave.

    Used for manual verification when webhook delivery fails.

    Args:
        business_identifier: Business/tenant identifier.
        payment_identifier: Payment record identifier.
        reference: Flutterwave transaction reference.

    Returns:
        Dict with verification status and details.
    """
    logger.info("Verifying payment %s ref %s", payment_id, reference)
    from app.payments.repository import get_intent_by_reference
    import app.common.flutterwave_service as flutterwave_service
    from app.common.db.engine import create_service_database

    sdb = create_service_database(settings.database_url)
    import asyncio

    async def _verify():
        async with sdb.session() as session:
            intent = await get_intent_by_reference(session, reference)
            if not intent:
                return {"status": "not_found", "reference": reference}

            result = await flutterwave_service.verify_transaction(tx_ref=reference)
            return {
                "status": result.get("status"),
                "reference": reference,
                "amount": result.get("amount"),
                "channel": result.get("channel"),
            }

    result = asyncio.run(_verify())
    return {"business_id": business_id, "payment_id": payment_id, **result}


@celery_app.task(
    name="app.worker.tasks.task_reconcile_events", bind=True, max_retries=3, default_retry_delay=30
)
def task_reconcile_events(self, business_id: str) -> dict:
    """Reconcile legacy events from the old api.events table.

    Processes pending events from the legacy system and applies them
    to the new schema-per-domain architecture.

    Args:
        self: Celery task instance for retry logic.
        business_identifier: Business/tenant identifier.

    Returns:
        Dict with reconciliation status and counts.
    """
    logger.info("Reconciling events for business %s", business_id)
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from app.common.settings import get_common_settings

    common = get_common_settings()
    conn = psycopg2.connect(common.database_url, cursor_factory=RealDictCursor)

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.business_id', %s, true), set_config('app.user_id', %s, true), set_config('app.role', %s, true)",
                (business_id, business_id, "system"),
            )
            cur.execute(
                """SELECT id, event_type, payload, client_id, client_ts
                   FROM events
                   WHERE business_id = %s AND source = 'client' AND status = 'pending'
                   ORDER BY created_at LIMIT 100 FOR UPDATE SKIP LOCKED""",
                (business_id,),
            )
            rows = cur.fetchall()

        if not rows:
            return {"business_id": business_id, "reconciled": True, "processed": 0}

        event_ids = [r["id"] for r in rows]
        processed = 0
        errors = []

        for row in rows:
            try:
                result = _reconcile_event(row, common)
                if result["ok"]:
                    processed += 1
                else:
                    errors.append({"id": str(row["id"]), "error": result.get("error")})
            except Exception as exc:
                logger.exception("Reconcile failed for event %s", row["id"])
                errors.append({"id": str(row["id"]), "error": str(exc)})

        with conn.cursor() as cur:
            for eid in event_ids:
                cur.execute(
                    "UPDATE events SET status = 'processed', processed_at = NOW() WHERE id = %s",
                    (eid,),
                )
            for err in errors:
                cur.execute(
                    "UPDATE events SET status = 'failed', error = %s WHERE id = %s",
                    (err["error"], err["id"]),
                )
        conn.commit()

        return {
            "business_id": business_id,
            "reconciled": True,
            "processed": processed,
            "failed": len(errors),
        }
    except Exception as exc:
        conn.rollback()
        logger.exception("Reconciliation task failed")
        raise self.retry(exc=exc)
    finally:
        conn.close()


def _reconcile_event(row: dict, common) -> dict:
    """Process a single legacy event during reconciliation.

    Args:
        row: Legacy event row from api.events table.
        common: Common settings with database URLs.

    Returns:
        Dict with 'ok' status and optional error message.
    """
    import asyncio
    from uuid import UUID

    etype = row["event_type"]
    payload = row["payload"]
    tenant_id = str(row.get("tenant_id") or "")

    if etype == "customer.created":
        from app.customers.repository import create_customer
        from app.customers.models import Customer
        from app.common.db.engine import create_service_database

        sdb = create_service_database(common.database_url)

        async def _apply():
            async with sdb.session() as session:
                customer = Customer(
                    tenant_id=UUID(tenant_id) if tenant_id else UUID(int=0),
                    name=payload.get("name", ""),
                    phone=payload.get("phone", ""),
                    email=payload.get("email", ""),
                    address=payload.get("address", ""),
                )
                await create_customer(session, customer)
                await session.commit()

        asyncio.run(_apply())
        return {"ok": True}

    if etype == "inventory.adjustment":
        from app.inventory.repository import adjust_stock
        from app.common.db.engine import create_service_database

        sdb = create_service_database(common.database_url)

        async def _apply():
            async with sdb.session() as session:
                await adjust_stock(
                    session,
                    tenant_id=UUID(tenant_id) if tenant_id else UUID(int=0),
                    product_id=UUID(payload["product_id"]),
                    store_id=UUID(payload["store_id"]),
                    qty_change=payload["qty_change"],
                    reason=payload.get("reason", "reconciliation"),
                    unit_cost=payload.get("unit_cost"),
                    notes=payload.get("notes"),
                )
                await session.commit()

        asyncio.run(_apply())
        return {"ok": True}

    return {"ok": False, "error": f"Unknown event type: {etype}"}


MV_NAMES = [
    "mv_daily_sales",
    "mv_product_rankings",
    "mv_payment_methods",
    "mv_cashier_performance",
    "mv_customer_summary",
    "mv_inventory_status",
    "mv_store_sales",
    "mv_store_product_rankings",
    "mv_store_inventory",
]


@celery_app.task(name="app.worker.tasks.task_refresh_analytics_mvs")
def task_refresh_analytics_mvs() -> dict:
    """Refresh analytics materialized views.

    Scheduled task that refreshes all materialized views used
    for reporting and analytics dashboards.

    Returns:
        Dict with count of refreshed views.
    """
    logger.info("Refreshing analytics materialized views")
    from app.common.db.engine import create_service_database

    sdb = create_service_database(settings.database_url)
    import asyncio

    async def _refresh():
        async with sdb.session() as session:
            for mv in MV_NAMES:
                try:
                    await session.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}"))
                    logger.info("Refreshed %s", mv)
                except Exception as exc:
                    logger.warning("Failed to refresh %s: %s", mv, exc)
            await session.commit()

    asyncio.run(_refresh())
    return {"refreshed": len(MV_NAMES)}


@celery_app.task(name="app.worker.tasks.task_cleanup_sessions")
def task_cleanup_sessions() -> bool:
    """Clean up expired cart sessions.

    Scheduled task that marks expired carts as inactive to free
    up database resources.

    Returns:
        True on completion.
    """
    logger.info("Cleaning up expired sessions")
    from app.cart.models import Cart
    from app.common.db.engine import create_service_database
    from datetime import UTC, datetime

    sdb = create_service_database(settings.database_url)
    import asyncio

    async def _cleanup():
        async with sdb.session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(Cart).where(Cart.status == "active", Cart.expires_at < datetime.now(UTC))
            )
            expired = list(result.scalars().all())
            for cart in expired:
                cart.status = "expired"
            await session.commit()
            return len(expired)

    cleaned = asyncio.run(_cleanup())
    logger.info("Cleaned up %d expired carts", cleaned)
    return True


@celery_app.task(name="app.worker.tasks.task_check_suspicious_login")
def task_check_suspicious_login(
    user_id: str,
    business_id: str,
    ip: str,
    user_agent: str,
) -> dict:
    """Check for suspicious login activity.

    Analyzes login attempt against historical patterns to detect
    potentially compromised accounts or unauthorized access.

    Detection heuristics:
    - Multiple failed login attempts (>= 5 in 15 minutes)
    - Login from new/unfamiliar IP address
    - Login at unusual hours (2-5 AM UTC)

    Args:
        user_identifier: User identifier.
        business_identifier: Business/tenant identifier.
        ip: Client IP address.
        user_agent: Client user agent string.

    Returns:
        Dict with analysis results including flagged status and reasons.
    """
    logger.info("Checking suspicious login for user %s from %s", user_id, ip)
    from app.identity.repository import (
        get_failed_login_count,
        get_unique_ips_for_user,
    )
    from app.identity.suspicious_login import analyze_login
    from app.common.db.engine import create_service_database

    sdb = create_service_database(settings.database_url)
    import asyncio

    async def _check():
        async with sdb.session() as session:
            # Get recent failed login count (last 15 minutes)
            failed_count = await get_failed_login_count(
                session, UUID(user_id), minutes=15
            )

            # Get known IPs (last 30 days)
            known_ips = await get_unique_ips_for_user(
                session, UUID(user_id), days=30
            )

            # Analyze the login
            result = analyze_login(
                ip_address=ip,
                user_agent=user_agent,
                recent_failed_count=failed_count,
                known_ips=known_ips,
            )

            return {
                "flagged": result.flagged,
                "reasons": result.reasons,
                "failed_attempts": result.failed_attempts,
                "is_new_ip": result.is_new_ip,
                "is_unusual_hour": result.is_unusual_hour,
            }

    analysis = asyncio.run(_check())

    # Log the result
    if analysis["flagged"]:
        logger.warning(
            "Suspicious login detected for user %s: %s",
            user_id,
            "; ".join(analysis["reasons"]),
        )

    return {
        "user_id": user_id,
        "business_id": business_id,
        "ip": ip,
        "user_agent": user_agent,
        **analysis,
    }
