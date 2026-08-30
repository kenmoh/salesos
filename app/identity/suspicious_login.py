"""Suspicious login detection service.

This module provides functions for detecting potentially suspicious
login activity based on various heuristics:

- Multiple failed login attempts in a short time window
- Login from a new/unfamiliar IP address
- Login from a different device/user agent
- Login at unusual hours (e.g., 3 AM)

Detection results are returned as a dict with flags and reasons,
allowing the caller to decide how to handle the suspicious activity
(e.g., block login, require MFA, send alert).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass
class SuspiciousLoginResult:
    """Result of suspicious login analysis.

    Attributes:
        flagged: Whether the login is considered suspicious.
        reasons: List of reasons why the login was flagged.
        failed_attempts: Number of recent failed attempts.
        is_new_ip: Whether the IP is new for this user.
        is_new_device: Whether the user agent is new.
        is_unusual_hour: Whether the login is at an unusual hour.
    """
    flagged: bool = False
    reasons: list[str] = field(default_factory=list)
    failed_attempts: int = 0
    is_new_ip: bool = False
    is_new_device: bool = False
    is_unusual_hour: bool = False


def analyze_login(
    ip_address: str | None,
    user_agent: str | None,
    recent_failed_count: int,
    known_ips: list[str],
    known_user_agents: list[str] | None = None,
    max_failed_attempts: int = 5,
    unusual_hour_start: int = 2,
    unusual_hour_end: int = 5,
) -> SuspiciousLoginResult:
    """Analyze a login attempt for suspicious indicators.

    Args:
        ip_address: Client IP address.
        user_agent: Client user agent string.
        recent_failed_count: Number of failed attempts in last 15 minutes.
        known_ips: List of IP addresses previously used by this user.
        known_user_agents: List of user agents previously used (optional).
        max_failed_attempts: Threshold for flagging too many failures.
        unusual_hour_start: Start of unusual hour window (24h format).
        unusual_hour_end: End of unusual hour window (24h format).

    Returns:
        SuspiciousLoginResult with analysis results.
    """
    result = SuspiciousLoginResult()

    # Check for too many failed attempts
    result.failed_attempts = recent_failed_count
    if recent_failed_count >= max_failed_attempts:
        result.flagged = True
        result.reasons.append(
            f"Too many failed login attempts: {recent_failed_count} in last 15 minutes"
        )

    # Check for new IP address
    if ip_address and known_ips:
        result.is_new_ip = ip_address not in known_ips
        if result.is_new_ip:
            result.flagged = True
            result.reasons.append(f"Login from new IP address: {ip_address}")

    # Check for unusual hour
    current_hour = datetime.now(UTC).hour
    if unusual_hour_start <= current_hour <= unusual_hour_end:
        result.is_unusual_hour = True
        result.flagged = True
        result.reasons.append(f"Login at unusual hour: {current_hour}:00 UTC")

    # Check for new device (if user agents provided)
    if user_agent and known_user_agents:
        result.is_new_device = user_agent not in known_user_agents
        if result.is_new_device:
            result.flagged = True
            result.reasons.append(f"Login from new device: {user_agent[:50]}...")

    return result


def should_require_mfa(result: SuspiciousLoginResult) -> bool:
    """Determine if MFA should be required based on analysis.

    Args:
        result: Suspicious login analysis result.

    Returns:
        True if MFA should be required.
    """
    # Require MFA if flagged and has multiple failed attempts
    return result.flagged and result.failed_attempts >= 3


def should_block_login(result: SuspiciousLoginResult) -> bool:
    """Determine if login should be blocked.

    Args:
        result: Suspicious login analysis result.

    Returns:
        True if login should be blocked.
    """
    # Block if more than 10 failed attempts
    return result.failed_attempts >= 10


def should_send_alert(result: SuspiciousLoginResult) -> bool:
    """Determine if an alert should be sent.

    Args:
        result: Suspicious login analysis result.

    Returns:
        True if an alert should be sent.
    """
    # Alert on new IP or new device with failed attempts
    return (result.is_new_ip or result.is_new_device) and result.failed_attempts > 0
