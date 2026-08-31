"""Notification service module.

This module provides functions for creating and sending notifications.
Notifications are created with status "pending" and processed by the
worker's notification sender task.

The notification flow:
1. Event handler creates notification via plan_send_notification()
2. Notification is persisted with status "pending"
3. Notification sender task picks up pending notifications
4. Task sends via appropriate channel (email, SMS, in-app)
5. Task marks notification as "sent" or "failed"
"""

import logging
import os
from uuid import uuid4

from app.notifications.models import Notification, NotificationTemplate
from app.notifications.schemas import (
    NotificationResult,
    NotificationSendCommand,
    TemplateCreateCommand,
    TemplateResult,
)

logger = logging.getLogger("storeflow.notifications")


def _get_resend_config() -> tuple[str, str]:
    api_key = os.environ.get("RESEND_API_KEY", "")
    from_email = os.environ.get("RESEND_FROM_EMAIL", "noreply@storeflow.ng")
    return api_key, from_email


def _get_termii_config() -> tuple[str, str, str]:
    api_key = os.environ.get("TERMII_API_KEY", "")
    from_name = os.environ.get("TERMII_FROM", "StoreFlow")
    base_url = os.environ.get("TERMII_BASE_URL", "https://api.termii.com/api")
    return api_key, from_name, base_url


def _send_email_resend(to: str, subject: str, html_body: str) -> bool:
    """Send email via Resend API."""
    api_key, from_email = _get_resend_config()
    if not api_key:
        logger.warning("RESEND_API_KEY not configured, skipping email")
        return False

    try:
        import httpx
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": from_email, "to": [to], "subject": subject, "html": html_body},
            timeout=30,
        )
        if resp.status_code in (200, 201):
            logger.info("Email sent to %s via Resend", to)
            return True
        logger.error("Resend email failed: %s %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("Resend email error: %s", e)
        return False


def _send_sms_termii(to: str, message: str) -> bool:
    """Send SMS via Termii API."""
    api_key, from_name, base_url = _get_termii_config()
    if not api_key:
        logger.warning("TERMII_API_KEY not configured, skipping SMS")
        return False

    try:
        import httpx
        resp = httpx.post(
            f"{base_url}/sms/send",
            headers={"Content-Type": "application/json"},
            json={
                "api_key": api_key,
                "to": to,
                "from": from_name,
                "sms": message,
                "type": "plain",
                "channel": "generic",
            },
            timeout=30,
        )
        if resp.status_code in (200, 201):
            logger.info("SMS sent to %s via Termii", to)
            return True
        logger.error("Termii SMS failed: %s %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("Termii SMS error: %s", e)
        return False


def plan_create_template(
    command: TemplateCreateCommand,
) -> tuple[TemplateResult, NotificationTemplate]:
    """Create a notification template.

    Args:
        command: Template creation command with channel, subject, body.

    Returns:
        Tuple of (result, template) for persistence.
    """
    template = NotificationTemplate(
        id=uuid4(),
        tenant_id=command.tenant_id,
        name=command.name,
        channel=command.channel,
        subject=command.subject,
        body=command.body,
        variables=str(command.variables or {}),
        is_active=True,
    )
    result = TemplateResult(
        id=template.id,
        tenant_id=str(template.tenant_id) if template.tenant_id else None,
        name=template.name,
        channel=template.channel,
        is_active=template.is_active,
    )
    return result, template


def plan_send_notification(
    command: NotificationSendCommand,
) -> tuple[NotificationResult, Notification]:
    """Plan a notification to be sent.

    Creates a notification with status "pending" that will be
    processed by the notification sender task.

    Args:
        command: Notification send command with recipient and content.

    Returns:
        Tuple of (result, notification) for persistence.
    """
    notification = Notification(
        id=uuid4(),
        tenant_id=command.tenant_id,
        channel=command.channel,
        recipient=command.recipient,
        subject=command.subject,
        body=command.body,
        status="pending",
        attempts=0,
    )
    result = NotificationResult(
        id=notification.id,
        tenant_id=notification.tenant_id,
        channel=notification.channel,
        recipient=notification.recipient,
        status=notification.status,
        attempts=notification.attempts,
    )
    return result, notification


def send_notification(notification: Notification) -> bool:
    """Send a notification via its channel.

    Uses Resend for email and Termii for SMS delivery.

    Args:
        notification: The notification to send.

    Returns:
        True if sent successfully, False otherwise.
    """
    if notification.channel == "email":
        return _send_email_resend(
            to=notification.recipient,
            subject=notification.subject or "",
            html_body=notification.body or "",
        )

    elif notification.channel == "sms":
        return _send_sms_termii(
            to=notification.recipient,
            message=notification.body or "",
        )

    elif notification.channel == "in_app":
        return True

    else:
        logger.warning("Unknown notification channel: %s", notification.channel)
        return False
