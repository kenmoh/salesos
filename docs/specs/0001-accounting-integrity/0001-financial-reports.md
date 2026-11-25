# Financial reports

## Summary

This specification gives each journal a financial date separate from its posting timestamp. The trial balance and balance sheet use that date, and direct cash flow reporting includes configured cash and bank accounts with clear activity categories.

## Requirements

* **AC-3**: Financial reports use the transaction accounting date. The trial balance shows each account’s net closing balance on the debit or credit side. A balance sheet for an earlier date reconciles against receivables and payables as they stood on that date.
* **AC-4**: The direct cash flow report includes every account marked as a cash equivalent and separates operating, investing, financing, and explicitly excluded activity. Internal transfers between cash equivalent accounts do not change category totals. Unclassified cash movements return a clear error. Existing response fields remain available.
* **AC-5**: Store filters apply consistently to reports and automatic postings.
* **AC-8**: Cash flow category and cash equivalent changes require `accounting:write` and record the old and new values in the audit log.

## Decision

**Chosen option**: Use journal `accounting_date` and chart account cash flow metadata for transaction dated reports.

Keep `posted_at` as an audit timestamp. Use the existing direct cash movement approach, but classify all cash equivalents and preserve the current response fields while adding category sections.

## Feature design

**Data model sketch**:

| Entity | Fields and constraints |
| --- | --- |
| Journal | Required `accounting_date DATE`; retain `posted_at` unchanged. |
| ChartOfAccount | Nullable `cash_flow_class` with `operating`, `investing`, `financing`, or `excluded`; `is_cash_equivalent BOOLEAN NOT NULL DEFAULT false`. Cash equivalent accounts use `excluded`. |

**API surface**:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
| --- | --- | --- | --- | --- | --- |
| `/journals` | POST | Optional `accounting_date`, entries, store | `journal_id` | `accounting:write` | 403, 404, 422 |
| `/accounts` and `/accounts/{account_id}` | POST, PUT | Account fields and cash flow metadata | Account including the metadata | `accounting:write` | 403, 404, 422 |
| `/trial-balance` | GET | Optional `as_at`, `store_id` | Existing debit and credit fields now represent net closing balances, with only one side nonzero | `accounting:read` | 403, 422 |
| `/profit-and-loss` | GET | `from_date`, `to_date`, optional `store_id` | Revenue and expense for the period | `accounting:read` | 403, 422 |
| `/balance-sheet` | GET | Optional `as_at`, `store_id` | Balances as of the date and historical subledger totals | `accounting:read` | 403, 422 |
| `/cash-flow` | GET | Optional `from_date`, `to_date`, `store_id` | Existing fields plus operating, investing, financing, and excluded activity | `accounting:read` | 403, 422 for unclassified movement |

**Value sourcing**:

| Action | Value produced | Source |
| --- | --- | --- |
| Create manual journal | Accounting date | Optional request date; otherwise current UTC date |
| Create automatic journal | Accounting date | Expense date, payment date, invoice sent event time, sale event time, or source creation date where no separate transaction date exists |
| Trial balance | Closing account balance and debit or credit side | Posted JournalEntry amounts with Journal `accounting_date` on or before `as_at` |
| Profit and loss | Revenue and expense totals | Posted revenue and expense entries whose Journal `accounting_date` is within the requested period |
| Balance sheet | Asset, liability, and equity totals | Posted entries through `as_at`; current earnings are revenue less expenses through that date |
| Historical balance sheet | AR/AP subledger balance | The receivable or payable source journal `accounting_date`, less payments dated on or before `as_at`; store filters use the source journal and saved payment store |
| Cash flow | Inflow, outflow, category, and net | Noncash counterpart lines in a journal containing cash equivalent entries, classified by account and journal `accounting_date` |
| Cash flow | Report period and store | Request date parameters and validated tenant store filter |
| Account update | Cash flag and category | Authenticated account update payload; old and new values in the audit event |

**Key invariants**:

* Financial statement ranges compare `accounting_date`, not `posted_at`.
* A trial balance presents the net of debit and credit movements. Only one of its debit or credit closing amounts is nonzero for an account.
* Direct cash flow uses all tenant accounts where `is_cash_equivalent` is true. Seeded accounts 1000 Cash and 1010 Bank Account are true.
* Noncash credits in a journal with cash equivalent entries are inflows. Noncash debits are outflows. Each line uses its chart account category.
* Transfers between cash equivalent accounts have no operating, investing, financing, or excluded effect.
* An unclassified noncash counterpart causes a report error with account and journal identifiers. Explicitly excluded activity appears in its own section.
* The existing flat `inflows`, `outflows`, and `net_cash_flow` response fields remain and represent all classified and explicitly excluded activity. The response adds operating, investing, financing, and excluded inflow, outflow, and net totals.
* Changing either cash metadata field uses `accounting:write` and writes an audit event.

**Security model**:

* Reports use `accounting:read`, tenant filters, and an optional validated store.
* Account cash metadata changes use `accounting:write`.
* Audit events include the actor, account ID, previous values, and new values.

**Critical test scenarios**:

* A backdated transaction appears in the correct accounting period even when posted later, verifies **AC-3**.
* The trial balance nets activity and presents a single side, verifies **AC-3**.
* The historical balance sheet uses the source journal accounting date and subtracts only payments dated on or before the report date, verifies **AC-3**.
* Cash and bank accounts both contribute to direct cash flow, verifies **AC-4**.
* A cash transfer between two cash equivalent accounts contributes zero category flow, verifies **AC-4**.
* An unclassified counterpart causes an explicit report error and an excluded counterpart appears in its own section, verifies **AC-4**.
* A store filter excludes other stores, verifies **AC-5**.
* Unauthorized category changes fail and authorized changes produce an audit event, verifies **AC-8**.

## Build plan

1. Add `accounting_date` and cash metadata to the model and migration, including preflight checks for historical date sources, satisfies **AC-3**, **AC-4**, and **AC-7**.
2. Update manual and automatic journal creators to set accounting dates from their named transaction sources, satisfies **AC-3**.
3. Update report queries for net trial balance, historical subledger reconciliation, and store aware accounting dates, satisfies **AC-3** and **AC-5**.
4. Implement direct cash flow totals across all cash equivalents, including an excluded section, transfer exclusion, and explicit unclassified errors, satisfies **AC-4**.
5. Update account request and response schemas, authorization, audit events, and focused report tests, satisfies **AC-4** and **AC-8**.

## Consequences

**Positive**:

* Backdated reports reflect when a transaction belongs financially, not when it was entered.
* Cash flow covers future cash and bank accounts without hard coded account codes.
* Missing account configuration is visible and fixable.

**Negative / tradeoffs**:

* Historical entries without a trustworthy transaction date need review before migration.
* Users must classify new accounts that participate in cash movements.
* Existing clients may need to adapt to the corrected net trial balance values.

**Neutral**:

* `posted_at` remains available for audit and posting time queries.

