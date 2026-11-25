# Payment posting

## Summary

This specification makes AR and AP payments safe to retry and traceable to one journal. Payment and journal creation happen in one database transaction, so neither can be left behind alone.

## Requirements

* **AC-1**: Every journal write path verifies tenant ownership, account ownership, valid debit and credit lines, and balanced posted journals. The chart of accounts supplies the authoritative account code and type.
* **AC-2**: Receivable and payable amounts remain positive, paid amounts and balances remain nonnegative, and original amount equals paid amount plus remaining balance.
* **AC-5**: Store filters apply consistently to reports and automatic postings.
* **AC-6**: AR and AP payment requests require an `Idempotency-Key`. A retry with the same key and request returns the original payment and journal. Reusing the key with changed request data returns a conflict and writes nothing. Each payment has exactly one journal, created atomically with the payment.
* **AC-7**: The migration detects ambiguous historical journals or payments before changing ledger data. It stops with useful record identifiers and does not silently repair or skip them.

## Decision

**Chosen option**: Require a store scoped idempotency key and save a unique journal link on every payment.

The key scope is tenant, payment type, and store captured when the payment is posted. If the record has no store, the scope is tenant and payment type. Keep the key with the payment indefinitely so an old retry cannot create a second financial transaction.

## Feature design

**Data model sketch**:

| Entity | Fields and constraints |
| --- | --- |
| ArPayment and ApPayment | Add required `idempotency_key`, request fingerprint, nullable saved `store_id`, and required unique `journal_id`. |
| Journal | Keep `reference_type` as `ar_payment` or `ap_payment`; set `reference_id` to the payment ID. |

Each payment belongs to one AR or AP parent and one journal. A parent has many payments. Payment store is copied from its parent at posting time and remains stable if the parent is later moved.

**API surface**:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
| --- | --- | --- | --- | --- | --- |
| `/receivable/{ar_id}/payment` | POST | `Idempotency-Key` header, amount, payment date, optional notes | Existing receivable result plus payment ID and journal ID | `accounting:write` | 403, 404, 409, 422 |
| `/payable/{ap_id}/payment` | POST | `Idempotency-Key` header, amount, payment date, optional notes | Existing payable result plus payment ID and journal ID | `accounting:write` | 403, 404, 409, 422 |

**Value sourcing**:

| Action | Value produced | Source |
| --- | --- | --- |
| Scope an idempotency key | Tenant and user | Authenticated request context |
| Scope an idempotency key | Payment type | AR or AP route |
| Scope an idempotency key | Store | Parent store copied to payment at posting time |
| Compare a retry | Request fingerprint | Canonical route parent ID, amount, payment date, notes, and saved store |
| Create payment | Payment ID and balance changes | Payment row and parent subledger transaction |
| Create journal | Journal ID and journal reference | Journal created with payment ID as `reference_id` |
| Replay identical request | Original payment and journal IDs | Existing payment row with matching key and fingerprint |
| Reject changed request | Conflict response | Existing key in the same tenant, type, and store scope with a different fingerprint |

**Key invariants**:

* The `Idempotency-Key` header is required, nonempty, and no longer than 255 characters.
* Same key and same request returns the original payment and journal.
* Same key and changed request returns HTTP 409 and does not update the parent, add a payment, or add a journal. The fingerprint is calculated from a canonical representation of the route parent ID, amount, payment date, notes, and saved store.
* The key is unique within tenant, payment type, and saved store. Rows without a store share one null store scope.
* Payment, parent balance update, and journal commit together or roll back together.
* Every payment has one tenant matched journal and every payment journal refers to the payment row.
* A payment journal does not refer only to the AR or AP parent.

**Security model**:

* The authenticated tenant and user are authoritative. A request cannot select another tenant or override the parent store.
* The parent receivable or payable must belong to the authenticated tenant.
* Payment writes require `accounting:write`; payment reads require `accounting:read`.

**Critical test scenarios**:

* A new AR or AP payment creates exactly one payment row and one journal, verifies **AC-1**, **AC-2**, and **AC-6**.
* Repeating the same request returns the original payment and journal IDs, verifies **AC-6**.
* Concurrent identical requests create one payment and one journal, verifies **AC-6**.
* Reusing the key with changed amount, date, notes, or parent returns 409 and changes no balances, verifies **AC-6**.
* The same key can be used for a different payment type or store, while a duplicate in the same scope conflicts, verifies **AC-5** and **AC-6**.
* Moving the parent later does not change the payment’s saved store, verifies **AC-5**.
* A historical payment with zero or multiple possible journals stops migration and reports its identifiers, verifies **AC-7**.

## Build plan

1. Add payment key, fingerprint, saved store, and unique journal relationship after migration preflight, satisfies **AC-6** and **AC-7**.
2. Change the payment database functions and route handlers to atomically create or replay the payment and journal, satisfies **AC-1**, **AC-2**, and **AC-6**.
3. Return payment and journal identifiers and map conflict responses to HTTP 409, satisfies **AC-6**.
4. Add tests for retries, concurrent requests, changed request data, store scope, and rollback on journal failure, satisfies **AC-5**, **AC-6**, and **AC-7**.

## Consequences

**Positive**:

* Retries and timeouts cannot silently create duplicate ledger activity.
* Each payment has a direct audit link to its journal.
* The payment’s original store remains stable if its parent is moved.

**Negative / tradeoffs**:

* Idempotency keys remain reserved for the lifetime of the payment record.
* Atomic posting requires the payment and journal functions to share one database transaction.
* Old payment records without a unique journal match can block migration.

**Neutral**:

* Existing payment endpoints remain, with a required header and additive response identifiers.

