# Ledger controls

## Summary

This specification makes every posted journal valid and tenant safe. It also makes balance sheet checks use receivables and payables from the selected date, and ensures invoice journals keep their source store.

## Requirements

* **AC-1**: Every journal write path verifies tenant ownership, account ownership, valid debit and credit lines, and balanced posted journals. The chart of accounts supplies the authoritative account code and type.
* **AC-2**: Receivable and payable amounts remain positive, paid amounts and balances remain nonnegative, and original amount equals paid amount plus remaining balance.
* **AC-5**: Store filters apply consistently to reports and automatic postings. An invoice journal uses the same store as its source document and receivable.
* **AC-7**: The migration detects ambiguous historical journals, payments, account links, or dates before changing ledger data. It stops with useful record identifiers and does not silently repair or skip them.

## Decision

**Chosen option**: Use shared database posting validation and tenant matched constraints for every writer.

API, RPC, and worker writers use the same validation path. Account code and type are loaded from the tenant matched chart account. Posted journals are balanced and immutable.

## Feature design

**Data model sketch**:

| Entity | Fields and constraints |
| --- | --- |
| Journal | Existing tenant, status, posting user and timestamp, store, plus required `accounting_date DATE`. |
| JournalEntry | Existing journal and account IDs, tenant, debit, credit, account code, type, status, and timestamp. Enforce tenant matched journal and account ownership. |
| AccountReceivable and AccountPayable | Original amount greater than zero, paid amount at least zero, balance at least zero, and original amount equals paid amount plus balance. |

**State transitions**:

* A journal moves from draft to posted. A posted journal is immutable.
* If validation fails, the journal remains unposted and the database transaction rolls back.

**API surface**:

| Endpoint or writer | Input | Result | Auth | Key errors |
| --- | --- | --- | --- | --- |
| `POST /journals` | Account IDs and debit or credit amounts | Journal ID on successful post | `accounting:write` | 403, 404, 422 |
| Expense, sale, invoice, and payment writers | Tenant, source record, journal lines, store, and source date | Posted journal with derived account metadata | Existing service or worker identity | Explicit posting failure; transaction rolls back |
| `GET /balance-sheet` | Optional `as_at` and `store_id` | General ledger balances and historical AR/AP reconciliation | `accounting:read` | 403, 422 |

**Value sourcing**:

| Action | Value produced | Source |
| --- | --- | --- |
| Validate a journal line | Tenant, account code, account type | Authenticated tenant and its ChartOfAccount row for `account_id` |
| Validate a posted journal | Debit and credit totals, line count | The JournalEntry rows in the posting transaction |
| Date a journal | Accounting date | Source transaction date; for manual journals see financial reports specification |
| Attribute an invoice | Store ID | Source Document `store_id`, copied to both receivable and invoice Journal |
| Reconcile historical AR/AP | Balance as of report date | The receivable or payable source journal `accounting_date` and payment rows with `payment_date` at or before the report date |
| Report an invalid history row | Row IDs and reason | Conflicting existing journal, entry, payment, account, or source records |

**Key invariants**:

* Every journal entry belongs to a journal and account in the same tenant.
* Each line has nonnegative debit and credit and exactly one side greater than zero.
* A posted journal has at least two lines and total debit equals total credit.
* The database posting transaction is the final authority for these rules. API and worker code cannot bypass it.
* An invoice sent event creates at most one invoice journal. Existing `invoice_sent` and other known invoice references are checked before any backfill.
* A missing or ambiguous historical match stops the migration with record identifiers.

**Security model**:

* All writes use the authenticated tenant or the worker event tenant, never a caller supplied tenant.
* User initiated writes require `accounting:write`. Reports require `accounting:read`.
* The worker validates the event tenant against the document, receivable, journal, and account rows.

**Critical test scenarios**:

* A valid balanced journal posts and derives account metadata, verifies **AC-1**.
* Cross tenant account references, negative amounts, two sided lines, one line journals, and unbalanced totals are rejected, verifies **AC-1**.
* Receivable and payable updates that violate the balance equation are rejected, verifies **AC-2**.
* An invoice event with a store writes that store on both its receivable and journal, verifies **AC-5**.
* A historical invoice with multiple candidate journals stops migration and reports the candidate IDs, verifies **AC-7**.

## Build plan

1. Add tenant matched constraints and the shared journal posting checks, satisfies **AC-1**.
2. Add receivable and payable balance constraints and update their write functions, satisfies **AC-2**.
3. Correct invoice journal store attribution and verify tenant scoped worker posting, satisfies **AC-5**.
4. Add migration preflight checks for duplicate and ambiguous journal history, satisfies **AC-7**.
5. Add database backed tests for valid and invalid write paths, satisfies **AC-1**, **AC-2**, **AC-5**, and **AC-7**.

## Consequences

**Positive**:

* Invalid journals cannot enter posted reports through a different API or worker path.
* A historical balance sheet can compare the general ledger and subledgers for the same date.

**Negative / tradeoffs**:

* Database posting functions become more important to every accounting write path.
* Migration may stop for manual reconciliation.

**Neutral**:

* The existing journal and subledger entities remain in place.

