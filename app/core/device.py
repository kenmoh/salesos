"""Client device identification from request headers.

Every sign-in — tenant registration, password login, social login, platform
admin login — stamps the caller's device onto the account row it already
owns. The row is updated in place, so one account always holds exactly one
"last seen device" and the table never grows a row per login.
"""

from dataclasses import dataclass

from fastapi import Request

IOS = "ios"
ANDROID = "android"
BROWSER = "browser"
UNKNOWN = "unknown"

_BROWSER_TOKENS: tuple[tuple[str, str], ...] = (
    ("edg", "Edge"),
    ("samsungbrowser", "Samsung Internet"),
    ("opr/", "Opera"),
    ("opera", "Opera"),
    ("fxios", "Firefox"),
    ("firefox", "Firefox"),
    ("crios", "Chrome"),
    ("chrome", "Chrome"),
    ("safari", "Safari"),
)

_IOS_DEVICES: tuple[tuple[str, str], ...] = (
    ("iphone", "iPhone"),
    ("ipad", "iPad"),
    ("ipod", "iPod"),
)


@dataclass(frozen=True)
class ClientDevice:
    """What we know about the client that just signed in.

    Attributes:
        device_type: One of "ios", "android", "browser", "unknown".
        browser: Browser name ("Chrome", "Safari", ...) when the client is a
            browser; None for native apps and unknown clients.
        label: Human label for the device ("iPhone", "Android", "Chrome").
        user_agent: The raw User-Agent header, when the client sent one.
        ip_address: The client IP the request came from.
    """

    device_type: str
    browser: str | None
    label: str
    user_agent: str | None
    ip_address: str | None


def client_ip(req: Request) -> str:
    """The client IP, honouring the first entry of X-Forwarded-For."""
    forwarded = req.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return str(req.client.host) if req.client else "0.0.0.0"


def detect_browser(user_agent: str | None) -> str | None:
    """The browser name in a User-Agent string, or None when there is none."""
    if not user_agent:
        return None
    ua = user_agent.lower()
    for token, name in _BROWSER_TOKENS:
        if token in ua:
            return name
    return None


def describe_client(
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
    device_name: str | None = None,
) -> ClientDevice:
    """Work out the device a request came from.

    A client-supplied ``device_name`` wins as the label only: the type and
    browser are always read from the User-Agent, so a client cannot claim to
    be on a different platform.
    """
    ua = (user_agent or "").strip()
    lowered = ua.lower()
    browser = detect_browser(ua)

    device_type = UNKNOWN
    label = "Unknown"

    for token, name in _IOS_DEVICES:
        if token in lowered:
            device_type = IOS
            label = name
            break
    else:
        if "android" in lowered:
            device_type = ANDROID
            label = "Android"
        elif "macintosh" in lowered and "mobile/" in lowered:
            device_type = IOS
            label = "iPad"
        elif browser:
            device_type = BROWSER
            label = browser

    if device_name:
        label = device_name.strip() or label

    return ClientDevice(
        device_type=device_type,
        browser=browser,
        label=label,
        user_agent=ua[:500] or None,
        ip_address=(ip_address or "").strip()[:45] or None,
    )


def describe_request(req: Request, device_name: str | None = None) -> ClientDevice:
    """Describe the device behind an incoming request."""
    return describe_client(
        user_agent=req.headers.get("user-agent"),
        ip_address=client_ip(req),
        device_name=device_name,
    )
