---
sessionId: session-261114-191205-1vjw
---

# Requirements

### Delivery Plan
### ✓ Step 1: Extend backend sync endpoints
### ✓ Step 2: Build mobile offline data layer
### ✓ Step 3: Implement mobile sync engine
### ✓ Step 4: Build web offline layer
### ✓ Step 5: Wire up mobile API client
### ✓ Step 6: End-to-end sync validation
### ✓ Step 7: Integrate into root layouts
### * Step 8: Test web offline sync

### Overview
Add full offline capabilities to the SellMoh application (Expo mobile + TanStack Start web admin) so users can create, read, update, and delete data without an internet connection. Changes sync automatically when connectivity is restored.

### Goals
- **Mobile (Expo)**: Full offline POS operations — create sales, manage inventory, view products and customers without internet.
- **Web (TanStack Start)**: Cache previously loaded data for offline viewing; queue writes for later sync.
- **Backend**: Extend existing sync endpoints to support full CRUD operations with conflict resolution.

### Scope

**In Scope:**
- Local data storage on mobile (SQLite via expo-sqlite) and web (IndexedDB via Dexie)
- Offline-first data access layer for core entities: Sales, Products, Inventory, Customers, Stores
- Automatic background sync when connectivity is restored
- Conflict resolution using "last write wins" strategy
- Backend extensions for bulk data download and sync state tracking
- Sync status UI indicators

**Out of Scope:**
- Real-time collaboration between multiple offline devices
- File/image uploads offline (images can be queued but not uploaded until online)
- Accounting journal entries offline (read-only access to existing data)
- Reporting offline (requires server-side aggregation)

### User Stories
1. As a cashier, I want to create sales and look up products when the internet is down, so I can continue serving customers.
2. As a store manager, I want to update inventory counts offline and have them sync automatically, so stock levels stay accurate.
3. As an admin, I want to see which data is synced and which is pending, so I know the state of my business data.
4. As a mobile user, I want the app to work seamlessly whether I'm online or offline without needing to press a sync button.

### Functional Requirements
- **Offline Data Access**: All core entities (sales, products, inventory, customers, stores) are available from local storage
- **Offline Writes**: Create, update, delete operations queue changes locally when offline
- **Auto-Sync**: Changes sync automatically in the background when connectivity is detected
- **Conflict Resolution**: Last write wins — the most recent timestamp prevails
- **Sync Status**: UI shows sync state (synced, syncing, pending changes, offline)
- **Initial Sync**: On first launch, download a full snapshot of business data

### Non-Functional Requirements
- Local database size should not exceed 100MB for typical usage
- Sync should complete within 30 seconds for normal data volumes (<1000 pending changes)
- No data loss during sync conflicts — all changes are preserved with server state winning on timestamp ties


# Technical Design

### Current Implementation

**Backend (FastAPI, `salesos/`):**
- Existing sync infrastructure in `app/stores/routes_sync.py`:
  - `POST /api/v1/sync/events` — batch submit client events (`SyncBatch` with `SyncEvent` list)
  - `GET /api/v1/sync/pending` — fetch server-side pending events since a timestamp
  - `POST /api/v1/sync/trigger` — trigger event reconciliation worker
- Event processing in `app/common/services.py`:
  - `process_sync_batch()` — queues events with deduplication via `client_id`
  - `get_pending_events()` — returns server-generated events not yet seen by client
- Schema: `SyncEvent { event_type: str, payload: dict }`, `SyncBatch { events: list[SyncEvent] }`
- Core API domains: sales, inventory, customers, stores, products, cart, payments, accounting

**What's missing:**
- No bulk data download endpoint for initial sync
- No sync state tracking (last sync cursor per device)
- Client apps have no offline storage or sync logic

### Key Decisions

1. **Mobile storage**: `expo-sqlite` with a custom ORM layer for structured local data
2. **Web storage**: `Dexie.js` (IndexedDB wrapper) for consistent API across entities
3. **Sync pattern**: Operational Transform-style event queue — client generates events locally, batches them on sync, server processes and returns conflicts
4. **Conflict resolution**: Last write wins based on `client_ts` timestamp in events
5. **Initial sync**: New backend endpoint returns full data snapshot; client replaces local database
6. **Incremental sync**: Client sends pending events, receives server events since last cursor

### Architecture Overview

```
graph LR
    A[Expo Mobile App] -->|SQLite| B[(Local DB)]
    A -->|Sync Events| C[Sync Engine]
    D[TanStack Web] -->|IndexedDB| E[(Local DB)]
    D -->|Sync Events| C
    C -->|POST /sync/events| F[FastAPI Backend]
    C -->|GET /sync/pending| F
    C -->|GET /sync/snapshot| F
    F -->|Process| G[(PostgreSQL)]
    G -->|Events| C
```

### Proposed Changes

#### Backend Changes (`salesos/`)

**1. New sync endpoints in `app/stores/routes_sync.py`:**
- `GET /api/v1/sync/snapshot` — full data snapshot for initial sync (products, inventory, customers, stores, sales)
- `POST /api/v1/sync/full` — comprehensive sync: client sends pending events + last cursor, server returns processed results + server events
- `PUT /api/v1/sync/cursor` — update last sync cursor for a device/session

**2. New service functions in `app/common/services.py`:**
- `get_data_snapshot()` — returns paginated full data for all syncable entities
- `update_sync_cursor()` — stores last sync position per device
- `resolve_conflicts()` — applies last-write-wins logic during event processing

**3. New schemas in `app/auth/schemas/schema.py` and `responses.py`:**
- `SyncSnapshotRequest` / `SyncSnapshotResult`
- `FullSyncRequest` / `FullSyncResult`
- `SyncCursor` with device_id, last_cursor, updated_at

#### Mobile App Changes (`sell_moh_app/`)

**1. Local Database Layer:**
- SQLite schema mirroring core entities (sales, products, inventory, customers, stores)
- Repository pattern for each entity with offline-first reads
- Event queue table for pending outbound changes

**2. Sync Engine:**
- Connectivity monitor using `expo-network`
- Background sync worker that batches and sends pending events
- Conflict resolution handler (last write wins)
- Initial sync flow on first launch

**3. API Client Layer:**
- Wrapper around REST calls that routes through local DB
- Automatic event generation on CRUD operations
- Retry logic with exponential backoff for failed syncs

**4. UI Components:**
- Sync status indicator (online/offline/pending changes)
- "Pull to sync" gesture support
- Offline banner when no connectivity

#### Web Admin Changes (`sellmoh-admin/`)

**1. Local Storage Layer:**
- Dexie.js database with tables for core entities
- Cache layer for TanStack Query that persists to IndexedDB

**2. Sync Integration:**
- TanStack Query persistence plugin for offline cache
- Background sync worker using `navigator.onLine`
- Event queue for pending mutations

**3. UI Components:**
- Sync status in header/navigation
- Offline mode banner
- Pending changes indicator

### Data Models

**Local Database Tables (Mobile SQLite / Web IndexedDB):**

```
entities:
  - id (UUID, primary key)
  - entity_type (sale | product | customer | inventory | store)
  - data (JSON)
  - version (integer, incremented on each update)
  - created_at (timestamp)
  - updated_at (timestamp)
  - deleted (boolean soft delete)

sync_queue:
  - id (auto-increment)
  - client_id (UUID, unique per operation)
  - event_type (string)
  - payload (JSON)
  - client_ts (timestamp)
  - status (pending | syncing | synced | failed)
  - retry_count (integer)
  - created_at (timestamp)

sync_metadata:
  - key (string, primary key)
  - value (string)
  - updated_at (timestamp)
```

**Sync Event Types:**
- `sale.created`, `sale.voided`, `sale.returned`
- `product.created`, `product.updated`, `product.deleted`
- `inventory.adjusted`, `inventory.transferred`
- `customer.created`, `customer.updated`
- `store.created`, `store.updated`

### File Structure

**Backend (`salesos/`):**
- Modified: `app/stores/routes_sync.py` — add new endpoints
- Modified: `app/common/services.py` — add snapshot and cursor services
- Modified: `app/auth/schemas/schema.py` — add request schemas
- Modified: `app/auth/schemas/responses.py` — add response schemas

**Mobile (`sell_moh_app/`):**
- New: `src/database/` — SQLite setup and schema
- New: `src/database/repositories/` — entity repositories
- New: `src/sync/` — sync engine, connectivity monitor, conflict resolver
- New: `src/hooks/useSyncStatus.ts` — sync state hook
- Modified: API service layer to route through local DB

**Web (`sellmoh-admin/`):**
- New: `src/lib/db/` — Dexie setup and schemas
- New: `src/lib/sync/` — sync worker and queue
- New: `src/hooks/useSync.ts` — sync status hook
- Modified: TanStack Query config for persistence

### Risks
- **Data consistency**: Offline periods can lead to stale data. Mitigated by clear UI indicators and auto-sync on reconnect.
- **Database size**: Large sales histories could bloat local storage. Mitigated by configurable sync window (e.g., last 90 days of sales).
- **Conflict frequency**: Multiple devices editing same record. Mitigated by last-write-wins with clear audit trail.
- **Initial sync time**: Full snapshot could be slow on slow connections. Mitigated by progressive loading and compression.


# Testing

### Validation Approach
Testing focuses on the sync flow: offline operations, sync triggers, conflict resolution, and data consistency.

### Key Scenarios

**Backend:**
1. **Sync batch processing**: Submit a batch of events, verify they're queued and processed correctly
2. **Deduplication**: Submit the same `client_id` twice, verify only one is processed
3. **Data snapshot**: Request full snapshot, verify all entities are returned
4. **Cursor management**: Set and retrieve sync cursor for a device
5. **Conflict resolution**: Submit conflicting events, verify last-write-wins behavior

**Mobile (Expo):**
1. **Offline sale creation**: Create a sale with no network, verify it's in local DB and sync queue
2. **Auto-sync on reconnect**: Create offline sale, restore network, verify it syncs to server
3. **Initial sync**: First launch with no local data, verify full snapshot is downloaded
4. **Conflict scenario**: Modify same product on two devices, verify resolution

**Web (TanStack Start):**
1. **Offline cache**: Load data online, go offline, verify cached data is accessible
2. **Mutation queue**: Create data offline, verify it queues and syncs on reconnect
3. **TanStack Query persistence**: Verify queries are restored from IndexedDB

### Edge Cases
- Network drops mid-sync (partial batch) — retry with deduplication
- Local database corruption — fallback to re-download from server
- Very large sync queue (1000+ events) — batch processing with progress tracking
- Server schema changes while client is offline — version checking and migration
- Concurrent edits on same entity from multiple devices — last-write-wins with audit log
