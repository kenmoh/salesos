# Accounting integrity decision record

## Context

The accounting module already uses double entry journals and a chart of accounts, but some paths can create incomplete or misleading results. The review found mismatched invoice backfill references, missing store attribution on invoice journals, inconsistent transaction dates, incomplete historical balance sheet reconciliation, cash flow limited to one account, gross trial balance movements, and write paths that do not consistently enforce ledger rules.

AR and AP payment recording also separates the payment balance update from its journal write. A timeout or failure between those actions can leave a payment without a journal. Retrying the request can create another payment because no idempotency key is stored.

This is internal management reporting in NGN. The system already uses Python, FastAPI, SQLAlchemy, PostgreSQL, Alembic, and tenant scoped accounting permissions. The work needs a migration, API and worker changes, and database backed regression tests. Existing historical information must not be guessed, silently repaired, or skipped.

> Premise note: This request contains three buildable areas, ledger controls, financial reporting, and payment posting. The umbrella keeps their shared accounting rules together, while child specifications let each area be built and tested independently.

## Options considered

### Option 1: Improve the existing accounting system

Keep the current database, APIs, and report paths. Add missing accounting data and validation, then update each existing writer and reader.

**Pros**:

* Preserves current client routes and the existing double entry model.
* Uses the database constraints and transaction support already available.
* Limits operational change while fixing the sources of incorrect results.

**Cons**:

* Existing SQL functions and worker paths need coordinated updates.
* Historical data can block the migration until it is reconciled.

### Option 2: Replace the accounting subsystem

Introduce a new ledger and migrate the current users and reports to it.

**Pros**:

* Could establish a clean schema without carrying all current implementation details.
* Could be useful if the current data model were fundamentally unusable.

**Cons**:

* Duplicates accounting data and increases reconciliation risk.
* Requires a longer migration and a client cutover without evidence that replacement is necessary.

### Option 3: Patch only the reports

Change report queries while leaving posting and payment writes unchanged.

**Pros**:

* Smaller initial code change.
* Avoids changing payment request contracts.

**Cons**:

* Reports would continue to read incomplete or wrongly dated ledgers.
* Duplicate payments and unbalanced journals would remain possible.

## Rationale

The existing double entry model is usable, and the defects arise from missing dates, incomplete constraints, and inconsistent write paths rather than a need for a separate accounting system. Improving in place keeps the current API and database operational model while making the database the authority for posting integrity.

The report only option is not sufficient because a correct report cannot be built on a ledger that can be duplicated or incomplete. A full replacement would add migration risk and parallel systems without solving a problem that the current schema cannot represent. The right boundary is one coordinated umbrella with separate ledger, reporting, and payment work.

