"""Who decides which store a new document belongs to.

A document becomes an AR record, a payable or a journal entry, and all of those
are attributed to a store. So the store is not the client's to choose: an owner
picks, and everyone else is bound to the store on their own account. Relying on
the client to hide a picker would leave one crafted request away from booking a
cashier's documents into a store they do not work in.

Also covers the status transition that could not succeed: the app sent "voided"
and the service accepted "void" (or "cancelled" for a purchase order), so every
Void press failed, and as an uncaught ValueError it surfaced as a 500.
"""

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import (
    TenantContext,
    TokenData,
    get_tenant_context,
    get_tenant_db_context,
)

OWNER_RANK = 80
CASHIER_RANK = 40
BID = str(uuid4())


def _token() -> TokenData:
    return TokenData(
        {
            "sub": str(uuid4()),
            "bid": str(uuid4()),
            "role": "owner",
            "roles": ["owner"],
            "perms": ["documents:create", "documents:read", "documents:update"],
            "jti": str(uuid4()),
            "exp": 9999999999,
        }
    )


def _identity_session():
    """A session mock that answers the fee-ledger queries."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    session.add = MagicMock()
    empty = MagicMock()
    empty.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=empty)
    return session


@pytest.fixture
async def post_document():
    """POST a document and report what the route resolved the store to."""
    calls: list[dict] = []

    async def _fake_create(**kwargs):
        calls.append(kwargs)
        return {"id": str(uuid4()), "doc_number": "INV-1", "status": "draft"}

    from app.documents import routes as doc_routes

    app = FastAPI()
    app.include_router(doc_routes.router)

    async def override():
        yield TenantContext(user=_token(), session=MagicMock())

    app.dependency_overrides[get_tenant_context] = override
    app.dependency_overrides[get_tenant_db_context] = override

    with patch.object(doc_routes, "create_document", _fake_create):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, calls


def _payload(store_id: str | None = None) -> dict:
    body: dict = {
        "actor_id": str(uuid4()),
        "doc_type": "invoice",
        "items": [{"description": "Thing", "qty": 1, "unit_price": 100}],
    }
    if store_id:
        body["store_id"] = store_id
    return body


def _patch_user_store(session, store_id):
    return patch(
        "app.identity.repository.get_user_by_id",
        AsyncMock(return_value=SimpleNamespace(store_id=store_id)),
    )


def _patch_rank(rank: int):
    return patch(
        "app.core.dependencies.get_cached_role_rank", AsyncMock(return_value=rank)
    )


class TestStoreResolution:
    async def test_owner_may_choose_any_store(self, post_document):
        ac, calls = post_document
        store = str(uuid4())
        with _patch_rank(OWNER_RANK), _patch_user_store(MagicMock(), None):
            resp = await ac.post("/documents", json=_payload(store))

        assert resp.status_code == 201, resp.text
        assert calls[0]["store_id"] == store

    async def test_owner_may_file_business_wide(self, post_document):
        """No store is a legitimate choice for the owner."""
        ac, calls = post_document
        with _patch_rank(OWNER_RANK), _patch_user_store(MagicMock(), None):
            resp = await ac.post("/documents", json=_payload())

        assert resp.status_code == 201, resp.text
        assert calls[0]["store_id"] is None

    async def test_a_non_owner_is_filed_against_their_own_store(self, post_document):
        ac, calls = post_document
        own = uuid4()
        with _patch_rank(CASHIER_RANK), _patch_user_store(MagicMock(), own):
            resp = await ac.post("/documents", json=_payload())

        assert resp.status_code == 201, resp.text
        assert calls[0]["store_id"] == str(own)

    async def test_a_non_owner_cannot_name_someone_elses_store(self, post_document):
        ac, calls = post_document
        own, other = uuid4(), uuid4()
        with _patch_rank(CASHIER_RANK), _patch_user_store(MagicMock(), own):
            resp = await ac.post("/documents", json=_payload(str(other)))

        assert resp.status_code == 403
        assert calls == []

    async def test_naming_their_own_store_is_allowed(self, post_document):
        ac, calls = post_document
        own = uuid4()
        with _patch_rank(CASHIER_RANK), _patch_user_store(MagicMock(), own):
            resp = await ac.post("/documents", json=_payload(str(own)))

        assert resp.status_code == 201, resp.text
        assert calls[0]["store_id"] == str(own)


class TestStatusTransitions:
    def test_the_client_and_the_service_agree_on_the_void_spelling(self):
        """The client used to send "voided"; the service accepts "void" for a
        quote, invoice or receipt and "cancelled" for a purchase order."""
        from app.documents.service import VALID_TRANSITIONS

        for doc_type in ("quote", "invoice", "receipt"):
            assert "void" in VALID_TRANSITIONS[doc_type]
            assert "voided" not in VALID_TRANSITIONS[doc_type]
        assert "cancelled" in VALID_TRANSITIONS["purchase_order"]
        assert "void" not in VALID_TRANSITIONS["purchase_order"]

    async def test_an_invalid_transition_is_a_400_not_a_500(self):
        """It is the caller's mistake, and a 500 told the user nothing."""
        from app.documents import routes as doc_routes

        app = FastAPI()
        app.include_router(doc_routes.router)

        async def override():
            yield TenantContext(user=_token(), session=MagicMock())

        app.dependency_overrides[get_tenant_context] = override

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch.object(
                doc_routes,
                "update_document_status",
                AsyncMock(
                    side_effect=ValueError(
                        "Invalid status 'voided' for invoice. Allowed: ['draft']"
                    )
                ),
            ):
                resp = await ac.patch(f"/documents/{uuid4()}/status", json={"status": "voided"})

        assert resp.status_code == 400
        assert "voided" in resp.json()["detail"]

    async def test_a_missing_document_is_a_404(self):
        from app.documents import routes as doc_routes

        app = FastAPI()
        app.include_router(doc_routes.router)

        async def override():
            yield TenantContext(user=_token(), session=MagicMock())

        app.dependency_overrides[get_tenant_context] = override

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch.object(
                doc_routes,
                "update_document_status",
                AsyncMock(side_effect=ValueError("document_not_found")),
            ):
                resp = await ac.patch(f"/documents/{uuid4()}/status", json={"status": "sent"})

        assert resp.status_code == 404

class TestConversionIsGuardedServerSide:
    """Conversion leaves the document at "accepted", which still offers the
    action, so without a check a second press raises a duplicate sale. The
    confirmation dialog that used to stand in for this is gone."""

    async def test_a_converted_document_is_refused(self):
        from types import SimpleNamespace as NS

        from app.common import bridge

        already = uuid4()
        doc = NS(
            id=uuid4(),
            tenant_id=BID,
            doc_type="invoice",
            status="accepted",
            linked_sale_id=already,
        )
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        sdb = MagicMock()
        sdb.return_value.session.return_value = session

        with (
            patch.object(bridge, "_get_sdb", sdb),
            patch(
                "app.documents.repository.get_document_by_id",
                AsyncMock(return_value=doc),
            ),
            patch(
                "app.documents.repository.get_document_items",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                bridge, "create_sale_via_service", AsyncMock()
            ) as create_sale,
        ):
            with pytest.raises(ValueError, match="already been converted"):
                await bridge.convert_document_to_sale(
                    tenant_id=BID,
                    document_id=str(doc.id),
                    cashier_id=str(uuid4()),
                )

        # The refusal has to happen before any sale is written.
        create_sale.assert_not_awaited()

    async def test_the_route_reports_it_as_a_bad_request(self):
        from app.documents import routes as doc_routes

        app = FastAPI()
        app.include_router(doc_routes.router)

        async def override():
            yield TenantContext(user=_token(), session=MagicMock())

        app.dependency_overrides[get_tenant_context] = override

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch.object(
                doc_routes,
                "convert_document_to_sale",
                AsyncMock(
                    side_effect=ValueError(
                        "This document has already been converted to a sale"
                    )
                ),
            ):
                resp = await ac.post(f"/documents/{uuid4()}/convert-to-sale")

        assert resp.status_code == 400
        assert "already been converted" in resp.json()["detail"]


class TestConversionCarriesTheStore:
    """A sale with no store makes every per-store figure that includes it wrong,
    and SaleCreateCommand requires one, so conversion used to fail on every
    document with a pydantic error about UUIDs."""

    def _doc(self, store_id=None):
        from types import SimpleNamespace as NS

        return NS(
            id=uuid4(),
            tenant_id=BID,
            doc_type="invoice",
            status="sent",
            discount=0,
            customer_name=None,
            customer_phone=None,
            store_id=store_id,
            linked_sale_id=None,
            doc_number="INV-1",
            # The conversion books the platform's fee against the document total.
            total=100,
        )

    def _session(self):
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.commit = AsyncMock()
        # Conversion now books the platform fee, which queries the fee ledger,
        # so execute has to be awaitable and report "no existing fee".
        empty = MagicMock()
        empty.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=empty)
        sdb = MagicMock()
        sdb.return_value.session.return_value = session
        return session, sdb

    async def test_the_documents_store_is_used(self):
        from types import SimpleNamespace as NS

        from app.common import bridge

        store = uuid4()
        session, sdb = self._session()
        create_sale = AsyncMock(
            return_value={
                "id": str(uuid4()),
                "sale_number": "SALE-1",
                "total": 100,
            }
        )

        with (
            patch.object(bridge, "_get_sdb", sdb),
            patch(
                "app.documents.repository.get_document_by_id",
                AsyncMock(return_value=self._doc(store)),
            ),
            patch(
                "app.documents.repository.get_document_items",
                AsyncMock(return_value=[NS(product_id=None, description="Thing",
                                           qty=1, unit_price=100, discount_pct=0,
                                           tax_breakdown=None)]),
            ),
            patch.object(bridge, "create_sale_via_service", create_sale),
            patch(
                "app.documents.repository.update_document_status", AsyncMock()
            ),
        ):
            await bridge.convert_document_to_sale(
                tenant_id=BID,
                document_id=str(uuid4()),
                cashier_id=str(uuid4()),
            )

        assert create_sale.await_args.kwargs["store_id"] == str(store)

    async def test_a_sale_with_no_store_is_refused_clearly(self):
        from app.common import bridge

        session, sdb = self._session()
        create_sale = AsyncMock()

        with (
            patch.object(bridge, "_get_sdb", sdb),
            patch(
                "app.documents.repository.get_document_by_id",
                AsyncMock(return_value=self._doc(None)),
            ),
            patch(
                "app.documents.repository.get_document_items",
                AsyncMock(return_value=[]),
            ),
            patch.object(bridge, "create_sale_via_service", create_sale),
            patch(
                "app.identity.repository.get_user_by_id",
                AsyncMock(return_value=None),
            ),
        ):
            with pytest.raises(ValueError, match="has no store"):
                await bridge.convert_document_to_sale(
                    tenant_id=BID,
                    document_id=str(uuid4()),
                    cashier_id=str(uuid4()),
                )

        create_sale.assert_not_awaited()

    async def test_the_invariant_is_legible_at_the_service_boundary(self):
        from app.common import bridge

        with pytest.raises(ValueError, match="must be attributed to a store"):
            await bridge.create_sale_via_service(
                tenant_id=BID,
                cashier_id=str(uuid4()),
                items=[
                    {
                        "product_id": str(uuid4()),
                        "product_name": "Thing",
                        "qty": 1,
                        "unit_price": 10,
                    }
                ],
                store_id=None,
            )


class TestFeeLimitBlocksDocumentCreation:
    """A document is the start of a sale, and a sale is what accrues unpaid
    platform fees. Once the tenant is at the limit, no document type may be
    written."""

    async def test_creation_is_refused_over_the_limit(self):
        from app.common import bridge

        session = _identity_session()

        with (
            patch.object(bridge, "_get_sdb") as sdb,
            patch(
                "app.platform.fee_calculator.get_pending_fee_balance",
                AsyncMock(return_value=5000.0),
            ),
            patch(
                "app.platform.fee_calculator.get_max_pending_balance",
                AsyncMock(return_value=1000.0),
            ),
            patch(
                "app.documents.service.plan_document_creation",
                AsyncMock(
                    side_effect=lambda *a, **k: (_ for _ in ()).throw(
                        AssertionError("must not plan a document past the limit")
                    )
                ),
            ),
            patch(
                "app.documents.repository.create_document", AsyncMock()
            ) as create,
        ):
            sdb.return_value.session.return_value = session
            with pytest.raises(ValueError, match="fee_balance_exceeded"):
                await bridge.create_document(
                    tenant_id=BID,
                    actor_id=str(uuid4()),
                    doc_type="invoice",
                    items=[{"description": "x", "qty": 1, "unit_price": 100}],
                )

        create.assert_not_awaited()

    @pytest.mark.parametrize(
        "doc_type", ["invoice", "quote", "receipt", "purchase_order"]
    )
    async def test_every_document_type_is_covered(self, doc_type):
        from app.common import bridge

        session = _identity_session()

        with (
            patch.object(bridge, "_get_sdb") as sdb,
            patch(
                "app.platform.fee_calculator.get_pending_fee_balance",
                AsyncMock(return_value=1000.0),
            ),
            patch(
                "app.platform.fee_calculator.get_max_pending_balance",
                AsyncMock(return_value=1000.0),
            ),
        ):
            sdb.return_value.session.return_value = session
            with pytest.raises(ValueError, match="fee_balance_exceeded"):
                await bridge.create_document(
                    tenant_id=BID,
                    actor_id=str(uuid4()),
                    doc_type=doc_type,
                    items=[{"description": "x", "qty": 1, "unit_price": 100}],
                )

    async def test_the_route_answers_402_and_says_why(self):
        from app.documents import routes as doc_routes

        app = FastAPI()
        app.include_router(doc_routes.router)

        # The route resolves the store before creating, which reads the user.
        session = _identity_session()

        async def override():
            yield TenantContext(user=_token(), session=session)

        async def override_db():
            yield TenantContext(user=_token(), session=session)

        app.dependency_overrides[get_tenant_context] = override
        app.dependency_overrides[get_tenant_db_context] = override_db

        with patch(
            "app.documents.routes.create_document",
            AsyncMock(side_effect=ValueError("fee_balance_exceeded")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
                resp = await ac.post("/documents", json=_payload())

        assert resp.status_code == 402
        assert "outstanding fees" in resp.json()["detail"]
