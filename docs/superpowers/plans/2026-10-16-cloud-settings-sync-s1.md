# Cloud Settings Sync (S1) Implementation Plan

> **For agentic workers:** executed inline (superpowers:executing-plans) by the same session that wrote it. Each task is test-first; the tests named per task are the contract.

**Goal:** Sync settings, shortcuts, and gallery favorites between a user's devices through VoiceStudio Cloud (`/v1/sync`), opt-in per device, and stopped at once by the S0 switches.

**Architecture:** A backend package `backend/services/cloud_sync/` runs the engine: pull `GET /changes`, apply with a three-way merge against the last agreed version, then push the outbox with `If-Match`. It reads every token through `services.cloud_account.access_token()`, so main's switches stay the single gate. Local values come from three stores. `prefs.json` and the settings table are hooked in the backend. Renderer `localStorage` is mirrored into the backend by a renderer `syncedStorage` wrapper, and pulled values flow back to the renderer the same way. When sync is switched off, main tells the backend to stop at once.

**Tech stack:** Python 3 (`httpx` async, `sqlite3`, Alembic), FastAPI router, React renderer, Vitest and pytest.

**Spec:** `account_login/desktop-app-guide.md` §3–8 and §10, `account_login/sync-api.md`, and `account_login/sync-collections.md`. These are git-ignored copies; the canonical versions are in `vssaas` `docs/specs/`.

**Builds on:** `docs/superpowers/plans/2026-10-16-account-sign-in-and-cloud-switches.md` (S0).

## Global constraints
- Off means off. Every Cloud request asks `cloud_account.access_token()` first, and `None` means make no call. Main's `POST /api/cloud-sync/stop` cancels the loop, the event stream and timers at once. Local data is never deleted when sync stops.
- Never sync secrets (`secret.*`, `hf_token`), absolute paths, hardware or engine choices (`compute_device`, `*_backend`, model IDs), the analytics `installation_id`, or the device-local keys in `sync-collections.md`.
- IDs are 16 Base62 characters from `secrets.choice`. Idempotency keys are made once per outbox row, reused on retry, and replaced after a `409`.
- Applying a pulled change never re-queues it. The `from_sync` context suppresses the hooks.
- Never run two rounds at once. The outbox holds one row per item.
- Values are compared as canonical JSON: sorted keys, no whitespace.
- Localization: every new string goes into all 21 locales. Parity rule: the same behaviour on all OSes.
- Commits carry no AI attribution (CLAUDE.md).

## Rulings (made while planning)
- **Table names.** Tables are `cloud_sync_state`, `cloud_sync_items`, `cloud_sync_outbox` and `cloud_sync_local` (the renderer mirror), not the guide's `sync_*`. VoiceStudioPro already has a `sync_state` table (migration 0019, folder sync) with another schema, and both apps share this code. Cost if wrong: renaming four tables.
- **Device and Organization.** The token bridge answer also carries `organization_id` (`personal_organization_id`) and the `sync_api` base URL, so the backend needs no environment table of its own.
- **Renderer stores.** Renderer keys reach the backend over the renderer's existing loopback HTTP channel (`apiJson`), not through main. Renderer → backend is the path every other renderer setting already uses. Cost if wrong: none; the guide's "via main" was a suggestion.
- **Favorites.** `voicestudio.gallery.favorites.v1` is an ID array, so the mirror records `added_at` the first time it sees each ID.
- **Shortcuts.** `shortcuts.global` maps `dictation.toggle` to the renderer bridge's `capture` shortcut API, applied through the existing setter, so main's file stays the one writer.
- **`dictation` group.** This list is sent back to `vssaas` (S1-23): `aecEnabled` (localStorage), plus prefs `dictation.mode`, `dictation.enabled` and `dictation.prompt`. Excluded: `dictation.model_id` and `dictation.silent_models` (engine choices) and the input device.
- **Plan style.** This plan gives interfaces and test contracts rather than full code. The same session executes it immediately, so writing the code twice would only duplicate it.

## Tasks
1. **IDs and merge** (`cloud_sync/ids.py`, `cloud_sync/merge.py`). Provides `new_sync_id()`, `canonical(v)`, and `merge3(base, local, remote) -> Merge(values, conflicts)`. Item level: `merge_item(base, local, remote)` handles tombstones, where an edit beats a delete. Tests: the guide's six cases plus every row of the §7 table.
2. **Allowlist** (`cloud_sync/allowlist.py`). It holds the item definitions for `settings` (appearance, generation-defaults, review, transcription, dictation, history, llm), `shortcuts.global`, `favorites.gallery` and `device-preferences`. Provides `read_values(collection, item_id)` and `write_values(collection, item_id, values)`, sourced from prefs, the settings table and the renderer mirror. Also provides `items_for(choices)` and `RENDERER_KEYS`. Tests: every allowlisted key has a reader and a writer; denied keys are never read; projection keeps the excluded `clone.settings` fields; llm reads no secret.
3. **Local tables** (alembic `0014_cloud_sync`, `_BASE_SCHEMA`, `cloud_sync/store.py`). Covers state, items (the merge base), the outbox (unique per item) and the renderer mirror with a sequence number. Tests: upgrade from a pre-0014 snapshot; outbox upsert keeps one row and its key; the mirror `since` query.
4. **Change hooks** (`core/change_hooks.py`, plus `prefs`, `settings_store` and the mirror router call it). Provides `notify(source, key)`, `from_sync()` context, and `subscribe(cb)`. Tests: each allowlisted key emits exactly one change; a write inside `from_sync` emits none; a non-allowlisted key emits none.
5. **API client** (`cloud_sync/client.py`). An async `httpx` client covering devices, collections, usage, changes, `put`/`delete`/`batch`, and errors mapped to `SyncApiError(status, code, details, retry_after)`. Tests: request shapes and headers through `httpx.MockTransport`; error mapping including `Retry-After`; the `If-Match` encoding.
6. **Engine** (`cloud_sync/engine.py`). Provides `run_once`, `pull` (with paging and the cursor saved alongside the applied items), `apply_remote`, and `push`, which handles `409` merge-and-retry (3 rounds), `413` (pause and show "full"), `403` (`not_entitled`/`collection_disabled`/`device_revoked`/`account_suspended`), `429` and `5xx`. It also covers the first-sync merge with an empty base, full resync on `410`, batching when more than 10 rows wait, the mass-delete guard (>20 or >25% of a collection), conflict notes, and a single-round lock. Tests: the guide's §11 items 5–7 (merge, outbox, pull) against a fake server.
7. **Event stream** (`cloud_sync/events.py`). An SSE parser plus a runner with a 60 s stall timeout, backoff of 1–60 s with ±20% jitter, `bye`/`revoked` handling and a 60 s poll fallback. Tests: the parser handles `head`, comments, `bye` and `revoked`; stall triggers a reconnect; the backoff sequence.
8. **Service and router** (`cloud_sync/service.py`, `api/routers/cloud_sync.py`, `main.py` wiring, the bridge answer extended). Routes under `/api/cloud-sync`: `status`, `PUT choices` (turn on, toggles, "also remove from the cloud"), `run`, `stop`, `devices`, `devices/{id}/revoke`, `confirm-deletes`, `use-choices-of/{device_id}`, and `local` GET/PUT for the renderer mirror. Tests: no Cloud call without a token; `stop` cancels the loop and the stream; turning sync back on does a full pull before any push; the router contract.
9. **Main.** On `syncEnabled` → off, main POSTs `/api/cloud-sync/stop` to the local backend; it never does this for a remote one. The bridge carries `organization_id` and `sync_api`. Tests: switch-off calls stop; remote mode makes no call; the bridge payload.
10. **Renderer.** `syncedStorage` (wraps `setItem`/`removeItem` for `RENDERER_KEYS`, posts to the mirror, applies pulled values with a `storage` event), the shortcut bridge, and Settings → Account → Sync: "Choose what to sync" with Pro locks, paused rows, sizes and "Use the same choices as …", the Devices list with Remove, the guide §10 messages, the mass-delete confirm and the conflict notice. All strings in 21 locales. Tests: the wrapper posts once per change and never echoes a pulled value; the UI states.
11. **Docs and verification.** `HANDOFF.md` records the S1-23 dictation list; `STRUCTURE.md` service count; full suites.
