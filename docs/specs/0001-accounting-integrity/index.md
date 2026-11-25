# 0001. Improve Accounting Integrity

**Date**: 2026-11-25
**Status**: In Progress

## Structure

* [Ledger controls](0001-ledger-controls.md) defines posting rules, account ownership, and historical checks.
* [Financial reports](0001-financial-reports.md) defines accounting dates, net balances, and cash flow reporting.
* [Payment posting](0001-payment-posting.md) defines retry safety and the payment to journal link.

## Summary

This decision improves the existing accounting module without replacing it. It makes ledger entries, financial dates, reports, and payment retries agree with each other. You will keep the current API and database tools, with careful checks before historical records are changed.

## Requirements

**User stories**:

* As a business owner, I want dependable financial reports so I can make decisions from accurate records.
* As an accounting user, I want each payment and journal to be traceable so I can investigate or reconcile a transaction.
* As a business owner, I want the same report to agree with the ledger for the date and store I selected.

**Acceptance criteria**:

* **AC-1**: Every journal write path verifies tenant ownership, account ownership, valid debit and credit lines, and balanced posted journals. The chart of accounts supplies the authoritative account code and type.
* **AC-2**: Receivable and payable amounts remain positive, paid amounts and balances remain nonnegative, and original amount equals paid amount plus remaining balance.
* **AC-3**: Financial reports use the transaction accounting date. The trial balance shows each account’s net closing balance on the debit or credit side. A balance sheet for an earlier date reconciles against receivables and payables as they stood on that date.
* **AC-4**: The direct cash flow report includes every account marked as a cash equivalent and separates operating, investing, financing, and explicitly excluded activity. Internal transfers between cash equivalent accounts do not change category totals. Unclassified cash movements return a clear error. Existing response fields remain available.
* **AC-5**: Store filters apply consistently to reports and automatic postings. An invoice journal uses the same store as its source document and receivable.
* **AC-6**: AR and AP payment requests require an `Idempotency-Key`. A retry with the same key and request returns the original payment and journal. Reusing the key with changed request data returns a conflict and writes nothing. Each payment has exactly one journal, created atomically with the payment.
* **AC-7**: The migration detects ambiguous historical journals, payments, account links, or dates before changing ledger data. It stops with useful record identifiers and does not silently repair or skip them.
* **AC-8**: Cash flow category and cash equivalent changes require `accounting:write` and record the old and new values in the audit log.

## Decision

**Chosen option**: Improve the existing accounting system in coordinated, bounded changes.

Keep the current FastAPI, SQLAlchemy, PostgreSQL, Alembic, and report APIs. Apply the ledger, payment, and reporting decisions in the child specifications. Preserve existing response fields where the user has asked for additive changes, and stop migration work when historical records cannot be matched safely.

## Feature design

**Data model sketch**:

| Entity | Target fields and rules |
| --- | --- |
| ChartOfAccount | Add nullable `cash_flow_class` with values `operating`, `investing`, `financing`, or `excluded`. Add `is_cash_equivalent BOOLEAN NOT NULL DEFAULT false`. Cash equivalent accounts use `excluded`. Seed account 1000 Cash and 1010 Bank Account as cash equivalents. |
| Journal | Add required `accounting_date DATE`. Keep `posted_at` as the actual posting timestamp. |
| JournalEntry | Enforce tenant matched journal and account links. Require nonnegative debit and credit, exactly one positive side, and balanced posted journals with at least two lines. Resolve account code and type from ChartOfAccount. |
| AccountReceivable and AccountPayable | Enforce positive original amount, nonnegative amount paid and balance, and `amount = amount_paid + balance`. |
| ArPayment and ApPayment | Add required idempotency key and request fingerprint, saved nullable `store_id`, and required unique `journal_id`. The key is unique within tenant, payment type, and saved store. For an unassigned store, the tenant and payment type form the scope. |
| Document worker | Copy the source document store to both the receivable and its invoice journal. |

Relationships remain one tenant to many accounts and journals, one journal to many entries, one account to many entries, and one receivable or payable to many payments. Each AR or AP payment links to exactly one journal. The journal reference points to the payment row, not only to its receivable or payable.

**API surface**:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
| --- | --- | --- | --- | --- | --- |
| `/journals` | POST | `description`, `entries`, optional `accounting_date`, reference, and store | `journal_id` | `accounting:write` | 403, 404, 422 |
| `/accounts` and `/accounts/{account_id}` | POST, PUT | Account details, `cash_flow_class`, `is_cash_equivalent` | Account with both fields | `accounting:write` | 403, 404, 422 |
| `/trial-balance` | GET | Optional `as_at` and `store_id` | Net debit or credit balance per account | `accounting:read` | 403, 422 |
| `/profit-and-loss` | GET | `from_date`, `to_date`, optional `store_id` | Report totals for the period | `accounting:read` | 403, 422 |
| `/balance-sheet` | GET | Optional `as_at` and `store_id` | Balances and as of date subledger reconciliation | `accounting:read` | 403, 422 |
| `/cash-flow` | GET | Optional `from_date`, `to_date`, and `store_id` | Existing fields plus operating, investing, financing, and excluded totals | `accounting:read` | 403, 422 for unclassified cash movement |
| `/receivable/{ar_id}/payment` | POST | Required `Idempotency-Key` header, amount, payment date, optional notes | Existing receivable result plus payment and journal identifiers | `accounting:write` | 403, 404, 409, 422 |
| `/payable/{ap_id}/payment` | POST | Required `Idempotency-Key` header, amount, payment date, optional notes | Existing payable result plus payment and journal identifiers | `accounting:write` | 403, 404, 409, 422 |

**Value sourcing**:

| Action | Value produced or displayed | Source |
| --- | --- | --- |
| Create a manual journal | Accounting date | Optional request `accounting_date`; otherwise the current UTC date |
| Post an automatic journal | Accounting date | Expense `expense_date`, payment `payment_date`, invoice sent event time, sale event time, or the source record creation date where no separate transaction date exists |
| Post a journal | Account code and type | The tenant matched ChartOfAccount row for each `account_id` |
| Read a financial report | Period and as of date | Request date parameters, with existing endpoint defaults where optional |
| Read a financial report | Journal amounts, account names, codes, and store | Posted journal entries, ChartOfAccount rows, and journal `store_id` |
| Read a historical balance sheet | Receivable and payable balances as of the date | Each receivable or payable source journal `accounting_date`, less payments whose `payment_date` is on or before that date |
| Read cash flow | Cash accounts and category totals | ChartOfAccount flags, journal entry amounts, counterpart account categories, accounting dates, and store filters |
| Record a payment | Tenant, user, parent record, store, amount, date, and notes | Authenticated tenant and user, route parent ID, saved parent store, request body, and `Idempotency-Key` header |
| Replay a payment | Original payment and journal | Existing payment row with matching key scope and request fingerprint |
| Diagnose a migration exception | Conflicting record identifiers and reason | Existing journal, entry, payment, account, and source records |

**Key invariants**:

* A posted journal is balanced, tenant matched, and immutable.
* Each payment and its journal commit or roll back together.
* A saved payment store does not change if its parent receivable or payable is later moved.
* Cash flow account classifications are explicit. An unclassified movement is not omitted, and explicitly excluded movements remain visible in their own section.
* Migration checks run before historical changes. Ambiguous history is reported for reconciliation.

**Security model**:

* All reads are scoped to the authenticated tenant and use `accounting:read`.
* Journal writes, payment writes, and account category changes use `accounting:write`.
* Store filters are validated against the tenant. Payment store scope is copied from its parent when the payment is posted.
* Category and cash equivalent changes are audited with actor, account, old values, and new values.
* No external financial reporting or statutory compliance claim is in scope. This is internal management reporting.

**Critical test scenarios**:

* A valid journal posts with account code and type taken from its chart accounts, verifies **AC-1**.
* A cross tenant account, negative line, two sided line, or unbalanced journal is rejected without a posted ledger change, verifies **AC-1**.
* A report before and after a backdated payment shows the correct historical receivable or payable balance, verifies **AC-2** and **AC-3**.
* A trial balance nets debit and credit activity and puts the result on one side, verifies **AC-3**.
* Cash flow includes cash and bank, separates all four classes, excludes internal transfers from category totals, and reports an unclassified movement, verifies **AC-4**.
* An invoice with a store creates an AR row and invoice journal with the same store, verifies **AC-5**.
* A simultaneous payment retry creates one payment and one journal. A changed request using the same scoped key returns 409 and makes no ledger change, verifies **AC-6**.
* A migration with ambiguous existing records stops and names them without writing corrections, verifies **AC-7**.
* A user without `accounting:write` cannot change a classification, and an authorized change is audited, verifies **AC-8**.

## Build plan

The project has no recorded build approach. This plan uses a tracer bullet, starting with the database and one complete payment path, then completing report behavior.

1. Repair the existing migration graph blocker in the platform migration before creating or running an accounting migration. Confirm the graph can be traversed without changing unrelated platform behavior, supports **AC-7**.
2. Add a preflight validation and one accounting migration for the target columns, constraints, indexes, classifications, and payment links. Validate all historical candidates before writing; stop with diagnostics on ambiguity, supports **AC-1**, **AC-2**, **AC-6**, **AC-7**, and **AC-8**.
3. Route every API, RPC, and worker journal write through the same tenant and ledger validation. Correct invoice store attribution and set accounting dates from source transaction dates, supports **AC-1**, **AC-3**, and **AC-5**.
4. Make AR and AP payment plus journal posting atomic and idempotent, including store scoped keys, request fingerprints, conflict responses, and returned identifiers, supports **AC-1**, **AC-2**, **AC-5**, and **AC-6**.
5. Update report queries and response schemas for accounting dates, net trial balances, historical subledger balances, cash equivalent flags, classified cash flow, internal transfers, and unclassified movement errors, supports **AC-3**, **AC-4**, and **AC-5**.
6. Add focused database and API regression tests for posting, migration diagnostics, dates, reports, store isolation, retries, and account audit events, supports **AC-1** through **AC-8**.

## Consequences

**Positive**:

* Posted journals, payment subledgers, and reports use the same accounting date and account ownership.
* A retry cannot create a second payment or journal.
* Historical report totals can be recalculated from dated transactions.
* Cash flow includes all configured cash and bank accounts and makes missing classification visible.

**Negative / tradeoffs**:

* A migration may stop until a person resolves historical ambiguity.
* Every new cash flow relevant account needs an explicit category.
* Payment posting has more database work and stricter transaction handling.
* Some consumers may need to adapt to the corrected net trial balance values.

**Neutral**:

* The existing accounting endpoints, permission model, and database stack remain in place.
* The cash flow response grows without removing its current top level fields.

## Follow-up

* [ ] Resolve the existing duplicate Alembic revision `a1b2c3d4e5f6` and migration cycle before validating or running an accounting migration. The conflicting platform migration is already present in the worktree and is outside this accounting change.
* [ ] Reconcile any records named by the migration preflight before applying constraints. The migration must not repair or skip them.

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Migration plan

**Strategy**: In place, using an expand, validate, and constrain sequence.

**Phases**:

1. Resolve the preexisting migration graph issue. Run a read only diagnostic to list ambiguous accounting history before applying schema changes.
2. Add the new columns and indexes with safe defaults or nullable state. Backfill only from named transaction sources. Abort before ledger changes if a payment, journal, account, or date cannot be matched uniquely.
3. Deploy application code that writes the new fields and uses shared journal validation, atomic payment posting, and accounting dates.
4. Apply required constraints after verified backfill. Run database backed accounting tests and compare existing report behavior against known ledger examples.

**Rollback**: Roll back the application release if needed, but preserve additive columns and posted data. Do not remove accounting dates, payment keys, or journal links after they have been used. Correct forward with a new migration.

**Risks**: The current Alembic graph cannot be traversed because a duplicate revision and cycle already exist. Historical journals may not contain enough information to determine a transaction date or unique payment journal. Both conditions must be resolved before the new schema can be applied.
