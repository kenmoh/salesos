# Accounting integrity verification

1. Reconcile unmatched AR/AP payment records named by migration preflight, then run `uv run alembic upgrade head`. Confirm the migration reaches revision `f623d73e3125` and the schema live, including journal accounting dates, chart cash fields, payment idempotency fields, and journal links.
2. Post a valid manual journal with an explicit accounting date and confirm the account code and type come from the tenant chart. Verify AC-1.
3. Attempt a cross tenant account, negative line, two sided line, one line journal, and unbalanced journal. Confirm each is rejected without a posted ledger change. Verify AC-1.
4. Change `payment_date` on a receivable or payable payment and confirm historical receivable and payable balances are recalculated for the report date. Verify AC-2 and AC-3.
5. Create backdated journal activity and run trial balance with `as_at` before and after it. Confirm net balances use accounting date and show only one nonzero debit or credit side. Verify AC-3.
6. Run balance sheet at an earlier date with and without a store filter. Confirm asset, liability, receivable, and payable totals reconcile for that date. Verify AC-3 and AC-5.
7. Run cash flow with cash and bank accounts marked as cash equivalents. Confirm operating, investing, financing, and excluded totals, internal transfer exclusion, and an explicit error for unclassified movements. Verify AC-4.
8. POST an AR or AP payment twice with the same scoped `Idempotency-Key` and identical request. Confirm one payment and one journal and identical identifiers. Verify AC-6.
9. Reuse a payment idempotency key with changed amount, date, notes, or parent. Confirm HTTP 409 and no payment or balance or journal change. Verify AC-6.
10. Change a chart account cash flow class or cash equivalent flag without `accounting:write` and confirm denial. Repeat with permission and confirm old and new values are in the audit event. Verify AC-8.
