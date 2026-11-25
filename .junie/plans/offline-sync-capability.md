---
sessionId: session-261114-191205-1vjw
---

# Requirements

### Delivery Plan
### ✓ Step 1: Pause mobile client sync entry points
Mobile no longer starts sync or sends client sync requests, while all offline code remains in place.
- Comment out sync hook and status bar wiring in `sell_moh_app/src/app/_layout.tsx`.
- Comment out calls to sync endpoints and offline queue producers in `sell_moh_app/src/sync/engine.ts` and any other active client call sites.
- Confirm online REST workflows still work and the app makes no `/sync/*` requests after login or reconnect.

### ✓ Step 2: Keep web client sync inactive and verify no requests
Web remains in normal online mode, with no active sync calls and the reusable implementation preserved.
- Keep `sellmoh-admin/src/routes/__root.tsx` disconnected from sync and inspect all hook and engine consumers.
- Comment out sync endpoint requests and offline queue producers in `sellmoh-admin/src/lib/sync/engine.ts` and any other active call sites.
- Confirm online REST workflows still work and the app makes no `/sync/*` requests after login or reconnect.

### Overview
Temporarily pause client initiated offline sync in the Expo mobile app and TanStack Start web admin until user feedback is collected. Preserve the existing client code and backend sync capability for a later, deliberate rollout.

### Goals
- Stop the clients from calling offline sync endpoints or presenting offline sync as active.
- Keep existing online API workflows working as they do now.
- Preserve sync modules, local databases, queued data, dependencies, and backend endpoints.

### Scope

**In Scope:**
- Comment out active mobile sync startup and status UI wiring.
- Inspect both clients for any other active sync triggers or calls, and comment out those call sites without deleting their code.
- Confirm the web client remains disconnected from sync. Its current root route has no sync startup wiring.
- Validate that normal online API operations still work and neither client calls sync endpoints.

**Out of Scope:**
- Reworking offline sales, documents, commissions, stock updates, conflict handling, or other dependent writes.
- Changing backend sync endpoints, schemas, services, database tables, or migrations.
- Deleting sync code, local databases, queued operations, or installed dependencies.
- Clearing existing local data or pending queue items.
- Expanding offline support before collecting user feedback.

### Functional Requirements
- Client application flows make no requests to sync endpoints while the pause is in effect.
- On mobile, comment out the root layout's sync hook, initial sync effect, and sync status bar, plus endpoint calls and offline queue producers in `src/sync/engine.ts` and any other reachable client call sites.
- On web, retain the current inactive root route and comment out sync endpoint calls, offline queue producers, and any active hook, timer, reconnect, or manual trigger found during inspection.
- Keep the disabled code in place as comments; do not remove or rewrite the sync implementation modules.
- Do not enqueue new offline operations while sync is paused.
- Normal online reads and writes continue through their existing REST API paths.
- Do not display a sync status indicator that suggests offline sync is currently enabled.
- Leave local database contents and already queued operations untouched. Review any retained queue before reactivating sync in a future rollout.


# Technical Design

### Current Implementation

- Mobile `src/app/_layout.tsx` imports `SyncStatusBar` and `useSync`, invokes initial sync after login, and mounts the status bar.
- Mobile `src/hooks/useSyncStatus.ts` starts background sync; `src/sync/engine.ts` calls `/sync/snapshot`, `/sync/full`, and `/sync/cursor`.
- Web `src/routes/__root.tsx` currently has no sync hook or status bar wiring. The reusable `src/hooks/useSync.ts` and `src/lib/sync/engine.ts` still contain callable background and endpoint sync paths.
- The FastAPI sync endpoints and their supporting backend code remain available. This task does not change them.

### Proposed Changes

#### Mobile (`sell_moh_app/`)

- In `src/app/_layout.tsx`, comment out the `useSync` and `SyncStatusBar` integration, including the login triggered initial sync effect and status bar rendering.
- In `src/sync/engine.ts`, comment out client requests to sync endpoints and any automatic sync triggers; inspect consumers of `useSync`, `queueEvent`, and sync engine functions and comment out offline queue producers.
- Preserve normal online REST paths and keep the sync hook, engine, database, repositories, and local queue code in place. Do not clear queued operations or local data.

#### Web (`sellmoh-admin/`)

- Keep `src/routes/__root.tsx` disconnected from sync; it currently has no sync startup or status bar wiring.
- In `src/lib/sync/engine.ts`, comment out client requests to sync endpoints and any automatic triggers; inspect consumers of `useSync`, `queueEvent`, and sync engine functions and comment out offline queue producers.
- Preserve normal online REST paths and keep the sync hook, engine, database, repositories, and local queue code in place. Do not clear queued operations or local data.

#### Shared behavior

- Preserve ordinary API client calls used by online business workflows.
- Do not change backend routes, sync endpoint contracts, migrations, or dependencies.
- Do not show sync status UI while client sync is paused.
- Before sync is reactivated later, review retained queue items so old operations are not replayed without user review.

### File Structure

**Mobile (`sell_moh_app/`):**
- Comment out active wiring in `src/app/_layout.tsx` and any other sync call sites found during inspection.
- Preserve `src/sync/engine.ts`, `src/hooks/useSyncStatus.ts`, database files, and repositories.

**Web (`sellmoh-admin/`):**
- Confirm `src/routes/__root.tsx` remains disconnected; comment out any active sync call sites found in the app.
- Preserve `src/lib/sync/engine.ts`, `src/hooks/useSync.ts`, database files, and repositories.

### Risks
- **Missed call sites**: A screen or reconnect handler may still call sync directly. Check all hook, engine, queue, and endpoint references in both clients.
- **Old queued operations**: Pending operations remain stored during the pause. Review them before re-enabling sync to avoid replaying stale work.


# Testing

### Validation Approach
Confirm both clients remain in their normal online mode, make no requests to offline sync endpoints, and retain the disabled sync implementation without deleting local data or queued operations.

### Key Scenarios

**Mobile (Expo):**
- Start the app, sign in, and reconnect the network; confirm no requests are made to `/sync/snapshot`, `/sync/full`, `/sync/cursor`, or other `/sync/*` routes.
- Confirm the sync status bar is not displayed and no new offline operations are enqueued while sync is paused.
- Exercise an existing online business action and confirm its normal REST API request still works.
- Confirm sync modules and local database contents are preserved.

**Web (TanStack Start):**
- Load the app, sign in, and reconnect the network; confirm no sync endpoint requests occur.
- Confirm the root route remains disconnected, all active sync request call sites are commented out, and no new offline operations are enqueued.
- Exercise an existing online business action and confirm its normal REST API request still works.
- Confirm sync modules and IndexedDB contents are preserved.

### Edge Cases
- A less obvious screen or reconnect listener calls the sync engine directly; inspect and disable every reachable call site.
- Pending queued operations remain stored during the pause and must not be submitted until a future reactivation is explicitly planned.
