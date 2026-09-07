"""Push notification service using Expo Server SDK.

Sends push notifications to Expo-registered devices.
Handles token validation, batch sending, and stale token cleanup.
"""

import logging
from uuid import UUID

from exponent_server_sdk import (
    DeviceNotRegisteredError,
    PushClient,
    PushMessage,
    PushServerError,
    PushTicketError,
)
import requests
from requests.exceptions import ConnectionError, HTTPError

logger = logging.getLogger("storeflow.notifications.push")

_expo_client: PushClient | None = None


def _get_client() -> PushClient:
    global _expo_client
    if _expo_client is None:
        session = requests.Session()
        session.headers.update(
            {
                "accept": "application/json",
                "accept-encoding": "gzip, deflate",
                "content-type": "application/json",
            }
        )
        _expo_client = PushClient(session=session)
    return _expo_client


def send_push_single(
    token: str,
    title: str,
    body: str,
    data: dict | None = None,
    badge: int | None = None,
) -> dict:
    """Send a single push notification.

    Returns:
        Dict with 'ok', 'token', and optionally 'error' or 'ticket_id'.
    """
    client = _get_client()
    try:
        message = PushMessage(
            to=token,
            title=title,
            body=body,
            data=data or {},
            sound="default",
        )
        if badge is not None:
            message.badge = badge

        ticket = client.publish(message)

        try:
            ticket.validate_response()
            return {"ok": True, "token": token, "ticket_id": ticket.id}
        except DeviceNotRegisteredError:
            logger.warning("Push token %s is no longer registered", token)
            return {"ok": False, "token": token, "error": "DeviceNotRegistered"}
        except PushTicketError as exc:
            logger.error("Push ticket error for %s: %s", token, exc)
            return {"ok": False, "token": token, "error": str(exc)}

    except (ConnectionError, HTTPError) as exc:
        logger.error("Push connection error for %s: %s", token, exc)
        return {"ok": False, "token": token, "error": "ConnectionError"}
    except PushServerError as exc:
        logger.error("Push server error for %s: %s", token, exc)
        return {"ok": False, "token": token, "error": str(exc)}


def send_push_batch(
    tokens: list[str],
    title: str,
    body: str,
    data: dict | None = None,
    badge: int | None = None,
) -> dict:
    """Send push notifications to multiple tokens in a single batch.

    Expo's HTTP/2 API supports multiplexed messages, so we send all
    tokens in one request for efficiency.

    Returns:
        Dict with 'total', 'success', 'failed', 'failed_tokens' (device-not-registered).
    """
    if not tokens:
        return {"total": 0, "success": 0, "failed": 0, "failed_tokens": []}

    client = _get_client()
    messages = []
    for token in tokens:
        msg = PushMessage(
            to=token,
            title=title,
            body=body,
            data=data or {},
            sound="default",
        )
        if badge is not None:
            msg.badge = badge
        messages.append(msg)

    try:
        tickets = client.publish_multiple(messages)
    except (PushServerError, ConnectionError, HTTPError) as exc:
        logger.error("Batch push failed: %s", exc)
        return {
            "total": len(tokens),
            "success": 0,
            "failed": len(tokens),
            "failed_tokens": [],
        }

    success = 0
    failed_tokens: list[str] = []
    for ticket, token in zip(tickets, tokens):
        try:
            ticket.validate_response()
            success += 1
        except DeviceNotRegisteredError:
            failed_tokens.append(token)
        except PushTicketError:
            failed_tokens.append(token)

    return {
        "total": len(tokens),
        "success": success,
        "failed": len(tokens) - success,
        "failed_tokens": failed_tokens,
    }
