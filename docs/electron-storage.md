# Electron storage settings

> **Historical context:** this page was written while the Electron and Tauri apps
> coexisted. Mentions of Tauri helpers, pages, tests and regression results describe
> that migration period; the Tauri shell has since been removed and the shared code
> now lives in `electron/src/shared/`. Existing Tauri installs: see the
> [migration guide](electron-migration.md).

Settings > Storage reads the existing cached disk report, shows volume use/free space, model cache, application data, engine environments and temporary files, and marks incomplete scans explicitly. Largest models and data subtotals expand inline. Warning formatting and byte formatting are shared with Tauri.

Storage scan budgets are checked between files in large flat directories and between loose application-data entries. A scan that exhausts its budget reports partial bytes and a timeout warning, including an incomplete Other subtotal. A single operating-system filesystem call can still take longer than the budget.

Open folder uses Electron's native reveal bridge, with the existing backend reveal route for browser development. Model and log links open their existing management views. Temporary-file cleanup requires explicit confirmation with the running-job warning. A partial deletion reports failure instead of claiming all files were cleared, and refreshes usage. Opening the page never deletes anything.

Database backup status displays the latest pre-migration snapshot and date. This is database backup information, not a claim that source media and generated files are backed up. History retention reuses the confirmed cap editor from Privacy.

Locking a profile to another take stores a new reference filename, so longform caches recognize the changed voice even when the take text and seed are unchanged. The replacement commits before the response returns; prior versions are retained as described below, and a failed update keeps the previous take usable.

The model cache location uses Electron's native directory picker. Main verifies the directory is writable, stores a one-shot `models_dir` capability in the backend data directory, and returns only its token to the renderer. The backend consumes that token when persisting `OMNIVOICE_CACHE_DIR`; raw host paths never cross the HTTP boundary. Reset uses the same capability flow with an empty path, and either change takes effect after restart.

Model folder names containing a hash after a space, apostrophes or backslashes are quoted and escaped in the durable environment so the startup loader retains the chosen directory. Existing plain values remain supported.

Reset & remove provides four common presets and an advanced per-scope list with measured disk sizes. The renderer owns UI preferences, history, drafts and IndexedDB projects. Main owns settings, generated content, engines, tools, models, caches and logs; it accepts only known scopes from the trusted main frame, rejects remote-backend resets and unsafe roots, stops the local backend before deletion and restarts it afterward. Removing voices/projects/audio requires typing the localized confirmation word. Shared Hugging Face caches carry a separate warning. Pending draft writes are suspended before reload so deleted work cannot be recreated by pagehide persistence.

The application data location uses Electron's native folder picker and a main-process one-shot authorization. Relocation stops the managed local backend, copies and verifies every file into an empty destination (including an existing empty folder selected in the picker), atomically persists `OMNIVOICE_DATA_DIR` in the shared durable environment, restarts the backend and confirms `/system/info` advertises the new path before removing the old copy. A destination that gains files during copying is refused without deleting those files. A failed copy or activation restores the previous setting, deletes only the verified destination and restarts from the original folder. If final old-folder cleanup fails, the new location stays active and the UI tells the user that the old copy can be removed manually. Remote and separately started backends are rejected because Electron cannot freeze their writes safely.

Remove all data scans the backend data root, Electron runtime/configuration, logs, durable environment and model cache with real sizes. Shared Hugging Face caches remain an explicit opt-in. After typed confirmation, main rescans and validates every root, stops the backend and hands the exact plan to the signed desktop helper. The helper canonicalizes every path, waits for Electron to exit, then removes the locked Chromium/runtime tree without following a path alias outside VoiceStudio-owned data. A failed helper launch restores the backend and keeps the app open; a successful handoff quits immediately. Removing the installed application binary remains the operating system's normal uninstall step.

`electron/tests/storage-settings-smoke.mjs` verifies warning/partial-scan display, folder reveal, cancel/confirm cleanup, partial cleanup failure and backup status with mocked mutations. Live read-only reports returned all four categories, one volume and an existing backup. No actual files were deleted. Tauri storage regression tests pass after shared-helper extraction. Connection, performance and privacy browser tests also pass after the settings-navigation refactor.

Interrupted sidecar installs without an environment remain included in application data. Completed sidecar environments, checkouts and weights are counted once in the engine category.

An unreadable engine directory or entry produces an incomplete report and a warning; unavailable bytes are not presented as a complete empty footprint.

Re-locking keeps prior immutable locked-reference clips for renders that already captured their filenames. These clips remain in the voices folder for the lifetime of the profile; explicit profile deletion reclaims its generated versions after committing the record deletion, while preserving versions still referenced by another profile. Deletion returns a conflict while a longform render still holds a cached reference that would be removed; the profile record and files remain intact, and deletion can be retried once all those render workers finish. Unlocking still succeeds while a render holds the current locked clip, but retains that immutable clip until explicit profile deletion can safely reclaim it. Unlocking also preserves clips referenced by any profile, including the unlocked profile’s other audio fields; an unused, unshared current clip is removed after the unlock commits. Replacing a clone sample likewise retains any previous reference still captured by a render; profile deletion reclaims these retained uploads too. Unreachable traceback cycles from finished failed renders do not keep a profile busy. Repeated locks or replacements during renders can therefore use additional local storage until the profile is deleted.
