"""Public bank-transfer details page.

The Receive-money screen shows a QR code for transfer payments. Scanning it
opens this page on the customer's own phone, so they can copy the account
number without reading it off the cashier's screen.

The page carries its data in a signed token rather than a database lookup:
the cashier's app has the DVA details at hand, and the page only needs a
snapshot for the few minutes the virtual account is valid. The HMAC stops
anyone from minting a page that shows *their* account number under our
brand.
"""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from html import escape

from app.core.config import settings


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(raw: bytes) -> bytes:
    return hmac.new(settings.secret_key.encode(), raw, hashlib.sha256).digest()


def build_transfer_token(payload: dict, *, ttl_seconds: int = 1800) -> str:
    body = {
        "account_number": str(payload.get("account_number", "")),
        "bank_name": str(payload.get("bank_name", "")),
        "amount": float(payload.get("amount", 0) or 0),
        "business_name": str(payload.get("business_name", "")),
        "store_name": str(payload.get("store_name", "")),
        "expiry_date": str(payload.get("expiry_date", "")),
        "tx_ref": str(payload.get("tx_ref", "")),
        "exp": int(datetime.now(UTC).timestamp()) + max(int(ttl_seconds), 60),
    }
    raw = json.dumps(body, separators=(",", ":")).encode()
    return f"{_b64url(raw)}.{_b64url(_sign(raw))}"


def parse_transfer_token(token: str) -> tuple[dict | None, bool]:
    """Return ``(payload, expired)``; payload is None when the token is forged."""
    try:
        body_b64, sig_b64 = token.split(".", 1)
        raw = _unb64url(body_b64)
        if not hmac.compare_digest(_sign(raw), _unb64url(sig_b64)):
            return None, False
        payload = json.loads(raw)
        expired = int(payload.get("exp", 0)) < int(datetime.now(UTC).timestamp())
        return payload, expired
    except Exception:
        return None, False


def _format_amount(value: float) -> str:
    if value == int(value):
        return f"\u20a6{int(value):,}"
    return f"\u20a6{value:,.2f}"


def _format_expiry(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%d %b %Y, %I:%M %p UTC").replace(" 0", " ")
    except ValueError:
        return value


def _shell(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#5e33cb">
<title>{escape(title)}</title>
<style>
  * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
  body {{
    margin: 0; min-height: 100vh;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: linear-gradient(160deg, #efeafe 0%, #f7f6fb 45%, #faf9f7 100%);
    color: #16161d; display: flex; justify-content: center;
    padding: 28px 16px calc(28px + env(safe-area-inset-bottom));
  }}
  .wrap {{ width: 100%; max-width: 420px; }}
  .brand {{ display: flex; align-items: center; gap: 10px; margin-bottom: 18px; padding: 0 4px; }}
  .mark {{
    width: 34px; height: 34px; border-radius: 10px; flex: none;
    background: #5e33cb; color: #fff; font-weight: 800; font-size: 17px;
    display: flex; align-items: center; justify-content: center;
  }}
  .brand-name {{ font-size: 14px; font-weight: 700; letter-spacing: .2px; color: #3b3b46; }}
  .card {{
    background: #fff; border-radius: 24px; padding: 24px 20px;
    box-shadow: 0 18px 45px rgba(38, 25, 84, .12), 0 2px 8px rgba(38, 25, 84, .05);
  }}
  .eyebrow {{
    font-size: 11px; font-weight: 800; letter-spacing: 1.6px;
    text-transform: uppercase; color: #8a8699; margin: 0 0 6px;
  }}
  h1 {{ font-size: 21px; line-height: 1.25; margin: 0 0 4px; font-weight: 800; }}
  .store {{ font-size: 13px; color: #6f6b7d; margin: 0; }}
  .amount {{ font-size: 34px; font-weight: 800; margin: 18px 0 2px; letter-spacing: -.5px; }}
  .amount-note {{ font-size: 12.5px; color: #6f6b7d; margin: 0; }}
  .divider {{ border: 0; border-top: 1px dashed #e4e1ec; margin: 20px 0; }}
  .row {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 0; }}
  .row + .row {{ border-top: 1px solid #f2f0f7; }}
  .label {{ font-size: 12.5px; color: #77728a; margin: 0 0 3px; }}
  .value {{ font-size: 15.5px; font-weight: 650; margin: 0; word-break: break-word; }}
  .mono {{ font-variant-numeric: tabular-nums; letter-spacing: .5px; }}
  .account {{ font-size: 26px; font-weight: 800; letter-spacing: 2.5px; }}
  .copy {{
    flex: none; width: 42px; height: 42px; border-radius: 999px;
    border: 1px solid #e4e1ec; background: #faf9fd; color: #5e33cb;
    display: flex; align-items: center; justify-content: center; cursor: pointer;
    transition: transform .12s ease, background .12s ease;
  }}
  .copy:active {{ transform: scale(.92); background: #f0eafc; }}
  .copy svg {{ width: 19px; height: 19px; }}
  .copy.done {{ border-color: #b5e6cf; background: #eafaf1; color: #059669; }}
  .hint {{ font-size: 12.5px; line-height: 1.5; color: #77728a; text-align: center; margin: 16px 6px 0; }}
  .foot {{ text-align: center; font-size: 11.5px; color: #a19dae; margin-top: 20px; letter-spacing: .2px; }}
  .state {{ text-align: center; padding: 8px 4px 4px; }}
  .state-icon {{
    width: 64px; height: 64px; border-radius: 999px; margin: 0 auto 16px;
    display: flex; align-items: center; justify-content: center; font-size: 28px; font-weight: 800;
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">
    <span class="mark">S</span>
    <span class="brand-name">StoreFlow</span>
  </div>
  {body}
  <p class="foot">Secure bank transfer &middot; Powered by StoreFlow</p>
</div>
</body>
</html>"""


_COPY_JS = """
<script>
(function () {
  function flash(btn) {
    btn.classList.add("done");
    setTimeout(function () { btn.classList.remove("done"); }, 1600);
  }
  function copy(text, btn) {
    function done() { flash(btn); }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done).catch(function () { fallback(text, done); });
    } else {
      fallback(text, done);
    }
  }
  function fallback(text, done) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(area);
    done();
  }
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () { copy(btn.getAttribute("data-copy"), btn); });
  });
})();
</script>
"""

_ICON_COPY = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" '
    'height="12" rx="2.5"/><path d="M5 15V5.5A2.5 2.5 0 0 1 7.5 3H17"/></svg>'
)


def render_transfer_page(payload: dict) -> str:
    business_name = payload.get("business_name") or "Payment details"
    store_name = payload.get("store_name") or ""
    account_number = payload.get("account_number") or ""
    bank_name = payload.get("bank_name") or ""
    tx_ref = payload.get("tx_ref") or ""
    amount = _format_amount(float(payload.get("amount", 0) or 0))
    expiry = _format_expiry(payload.get("expiry_date") or "")

    def copy_btn(value: str, label: str) -> str:
        return (
            f'<button class="copy" type="button" data-copy="{escape(value)}" '
            f'aria-label="Copy {escape(label)}">{_ICON_COPY}</button>'
        )

    rows = []
    if bank_name:
        rows.append(
            '<div class="row"><div><p class="label">Bank</p>'
            f'<p class="value">{escape(bank_name)}</p></div>'
            f"{copy_btn(bank_name, 'bank name')}</div>"
        )
    rows.append(
        '<div class="row"><div><p class="label">Account number</p>'
        f'<p class="value mono account">{escape(account_number)}</p></div>'
        f"{copy_btn(account_number, 'account number')}</div>"
    )
    if tx_ref:
        rows.append(
            '<div class="row"><div><p class="label">Reference</p>'
            f'<p class="value mono" style="font-size:13px">{escape(tx_ref)}</p></div>'
            f"{copy_btn(tx_ref, 'reference')}</div>"
        )
    if expiry:
        rows.append(
            f'<div class="row"><div><p class="label">Expires</p>'
            f'<p class="value">{escape(expiry)}</p></div></div>'
        )

    subtitle = escape(store_name) if store_name else "Bank transfer"
    body = f"""
  <div class="card">
    <p class="eyebrow">Pay by bank transfer</p>
    <h1>{escape(business_name)}</h1>
    <p class="store">{subtitle}</p>
    <p class="amount">{amount}</p>
    <p class="amount-note">Transfer this exact amount to the account below.</p>
    <hr class="divider">
    {''.join(rows)}
    <p class="hint">Your payment is confirmed automatically &mdash; no need to show
    the cashier a receipt. You can close this page after the transfer.</p>
  </div>
"""
    return _shell("Bank transfer details", body + _COPY_JS)


def render_expired_page() -> str:
    body = """
  <div class="card">
    <div class="state">
      <div class="state-icon" style="background:#f2f0f7;color:#8a8699">&#9203;</div>
      <h1>This payment window has closed</h1>
      <p class="store" style="margin-top:8px">Virtual accounts only stay open for a few
      minutes. Ask the cashier to generate fresh transfer details for you.</p>
    </div>
  </div>
"""
    return _shell("Transfer link expired", body)


def render_not_found_page() -> str:
    body = """
  <div class="card">
    <div class="state">
      <div class="state-icon" style="background:#fdecec;color:#dc2626">&#10005;</div>
      <h1>Link not found</h1>
      <p class="store" style="margin-top:8px">This link is invalid or incomplete.
      Scan the QR code on the cashier's screen again.</p>
    </div>
  </div>
"""
    return _shell("Link not found", body)
