import json
from datetime import UTC, datetime, timedelta

import pytest

from app.payments import transfer_page
from app.payments.transfer_page import (
    build_transfer_token,
    parse_transfer_token,
    render_expired_page,
    render_not_found_page,
    render_transfer_page,
)

PAYLOAD = {
    "account_number": "1234567890",
    "bank_name": "Wema Bank",
    "amount": 12500.0,
    "business_name": "Mama's Store",
    "store_name": "Ikeja Branch",
    "expiry_date": "2026-10-23T12:30:00Z",
    "tx_ref": "SF-TRA-ABCDEF123456",
}


class TestToken:
    def test_roundtrip(self):
        token = build_transfer_token(PAYLOAD)
        payload, expired = parse_transfer_token(token)
        assert expired is False
        assert payload["account_number"] == "1234567890"
        assert payload["bank_name"] == "Wema Bank"
        assert payload["amount"] == 12500.0
        assert payload["business_name"] == "Mama's Store"
        assert payload["tx_ref"] == "SF-TRA-ABCDEF123456"
        assert payload["exp"] > int(datetime.now(UTC).timestamp())

    def test_tampered_payload_rejected(self):
        token = build_transfer_token(PAYLOAD)
        body, sig = token.split(".", 1)
        forged_body = transfer_page._b64url(
            json.dumps({**PAYLOAD, "account_number": "9999999999"}).encode()
        )
        payload, expired = parse_transfer_token(f"{forged_body}.{sig}")
        assert payload is None
        assert expired is False

    def test_garbage_rejected(self):
        assert parse_transfer_token("not-a-token") == (None, False)

    def test_expired_token_reports_expired(self):
        body = {**PAYLOAD, "exp": int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())}
        raw = json.dumps(body).encode()
        token = f"{transfer_page._b64url(raw)}.{transfer_page._b64url(transfer_page._sign(raw))}"
        payload, expired = parse_transfer_token(token)
        assert payload is not None
        assert expired is True

    def test_ttl_floor(self):
        token = build_transfer_token(PAYLOAD, ttl_seconds=1)
        payload, expired = parse_transfer_token(token)
        assert expired is False
        assert payload["exp"] > int(datetime.now(UTC).timestamp()) + 30


class TestRender:
    def test_details_page_shows_copyable_fields(self):
        html = render_transfer_page(PAYLOAD)
        assert "Mama&#x27;s Store" in html or "Mama's Store" in html
        assert "Ikeja Branch" in html
        assert "Wema Bank" in html
        assert "1234567890" in html
        assert 'data-copy="1234567890"' in html
        assert 'data-copy="SF-TRA-ABCDEF123456"' in html
        assert "₦12,500" in html
        assert 'id="copy-account"' in html

    def test_amount_with_kobo(self):
        html = render_transfer_page({**PAYLOAD, "amount": 12500.5})
        assert "₦12,500.50" in html

    def test_html_escaped(self):
        html = render_transfer_page(
            {**PAYLOAD, "business_name": "<script>alert(1)</script>"}
        )
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_expired_page_has_no_account(self):
        html = render_expired_page()
        assert "1234567890" not in html
        assert "closed" in html

    def test_not_found_page(self):
        html = render_not_found_page()
        assert "Link not found" in html


class TestRoute:
    @pytest.mark.asyncio
    async def test_valid_token_renders_page(self):
        from app.payments.routes import transfer_details_page

        response = await transfer_details_page(build_transfer_token(PAYLOAD))
        assert response.status_code == 200
        assert b"1234567890" in response.body

    @pytest.mark.asyncio
    async def test_forged_token_404(self):
        from app.payments.routes import transfer_details_page

        response = await transfer_details_page("forged")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_expired_token_410(self):
        from app.payments.routes import transfer_details_page

        body = {**PAYLOAD, "exp": 1}
        raw = json.dumps(body).encode()
        token = f"{transfer_page._b64url(raw)}.{transfer_page._b64url(transfer_page._sign(raw))}"
        response = await transfer_details_page(token)
        assert response.status_code == 410


class TestIntentKeyedPage:
    """New QRs carry the intent id; the details come from that row."""

    @staticmethod
    def _sdb_with(intent):
        from unittest.mock import AsyncMock, MagicMock

        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = AsyncMock(return_value=intent)
        sdb = MagicMock()
        sdb.session.return_value = session
        return sdb

    @pytest.mark.asyncio
    async def test_intent_id_renders_details(self):
        from unittest.mock import MagicMock, patch
        from uuid import uuid4

        import app.payments.routes as routes

        intent = MagicMock()
        intent.dva_account_number = PAYLOAD["account_number"]
        intent.bank_name = PAYLOAD["bank_name"]
        intent.amount = PAYLOAD["amount"]
        intent.expires_at = datetime.now(UTC) + timedelta(minutes=20)
        intent.intent_metadata = json.dumps(
            {
                "business_name": PAYLOAD["business_name"],
                "store_name": PAYLOAD["store_name"],
                "expiry_date": PAYLOAD["expiry_date"],
                "tx_ref": PAYLOAD["tx_ref"],
            }
        )
        intent.gateway_reference = PAYLOAD["tx_ref"]

        with patch.object(routes, "_get_sdb", return_value=self._sdb_with(intent)):
            response = await routes.transfer_details_page(str(uuid4()))

        assert response.status_code == 200
        assert b"1234567890" in response.body
        assert b"Mama&#x27;s Store" in response.body

    @pytest.mark.asyncio
    async def test_unknown_intent_404(self):
        from unittest.mock import patch
        from uuid import uuid4

        import app.payments.routes as routes

        with patch.object(routes, "_get_sdb", return_value=self._sdb_with(None)):
            response = await routes.transfer_details_page(str(uuid4()))

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_expired_intent_410(self):
        from unittest.mock import MagicMock, patch
        from uuid import uuid4

        import app.payments.routes as routes

        intent = MagicMock()
        intent.dva_account_number = PAYLOAD["account_number"]
        intent.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        intent.intent_metadata = "{}"

        with patch.object(routes, "_get_sdb", return_value=self._sdb_with(intent)):
            response = await routes.transfer_details_page(str(uuid4()))

        assert response.status_code == 410
