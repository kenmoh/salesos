# Scope: StoreFlow

StoreFlow supports business sales and operations. This scope tracks an improvement to its accounting records and reports.

**Build approach:** Tracer Bullet (prove a complete working path through the database, API, and reports before expanding).
**Workflow:** Prototype (develop self check only). This is the project default. `/architect` is recommended when a feature still needs a decision.

These are recommendations to keep your build orderly. Skip anything that does not fit. A feature can use a stronger workflow when its risk calls for it.

## At a glance

| # | Feature | Phase | Status |
|---|---------|-------|--------|
| 1 | Accounting integrity improvements | Accounting | in-progress |
| 2 | Platform admin management depth | Platform | done |

## Accounting

### 1. Accounting integrity improvements · in-progress · GA

Improve journal validation, transaction dates, store attribution, payment retries, and financial reports so you can rely on the accounting records.
**Done when:** every posted journal and payment has a valid, tenant matched ledger trail, and dated, store filtered reports show correct net balances and classified cash flows.
- [x] Design it (spec): `/architect accounting integrity improvements`
- [x] Build it: `/develop accounting integrity improvements`
  - [x] Ledger schema, controls, and migration checks (AC-1, AC-2, AC-7)
  - [x] Transaction dates and store attribution (AC-3, AC-5)
  - [x] Atomic, idempotent AR and AP payments (AC-1, AC-2, AC-5, AC-6)
  - [x] Classified reports, audit events, and regression coverage (AC-3, AC-4, AC-8)
- [ ] Verify it: `/check verify accounting integrity improvements`
- [ ] Test it: `/test accounting integrity improvements`
- [ ] Review it (fresh model): `/check review accounting integrity improvements`
- [ ] Document it: `/document accounting integrity improvements`
Spec [0001](../specs/0001-accounting-integrity/index.md) · code in `app/accounting/`, `app/worker/handlers/documents.py`, `alembic/versions/`, and `tests/`

## Platform

### 2. Platform admin management depth · done · GA

Deepen the platform admin surface so the ops team can run the business without guessing: full tenant profiles, store→product drill-down, analytics, impersonation, bulk actions, and CSV exports.
**Done when:** an admin can open a tenant and see settlement, owner activity, subscription, wallet, invoices, fee debt, and entity counts; browse every store's products with live stock; run cohort retention and health scores; impersonate read-only with audit; tag/note/suspend in bulk; export tenants/users/invoices to CSV.
- [x] Design it (spec): approved in chat
- [x] Build it: implemented across `app/platform/` + migration `c9f1a2b3d4e5`
  - [x] Tenant detail enrichment (settlement, subscription, wallet, invoices, fees, counts)
  - [x] Admin CRUD + role/status/name updates, detail view, session revoke, password reset
  - [x] Audit log filters (date/action/search), admin names, CSV export
  - [x] Fee ledger pagination/filters/summary/CSV, fixed sale_id/document_id
  - [x] Analytics: cohort retention, tenant health (0-100), plan distribution, module adoption, device distribution, finance reconciliation
  - [x] Store → products drill-down with stock, pricing, filters
  - [x] Impersonation (audited, read-only tokens, extra JWT claims)
  - [x] Notes, tags, status history, data requests (export/deletion) with CRUD
  - [x] Bulk actions (suspend, reinstate, add/remove tag)
  - [x] CSV exports (tenants, users, invoices)
  - [x] Role guards (reads open, writes require admin/super_admin)
- [x] Verify it: platform tests pass (54/54)
- [x] Test it: existing platform tests cover core flows
Spec: migration `alembic/versions/c9f1a2b3d4e5_platform_management_depth.py` · code in `app/platform/routes.py`, `app/platform/management.py`, `app/platform/analytics_extra.py`, `app/platform/health.py`, `app/platform/access.py`, `app/platform/exporting.py` · new models in `app/platform/models.py`
