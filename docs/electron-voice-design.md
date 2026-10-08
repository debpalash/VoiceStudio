# Electron voice design

> **Historical context:** this page was written while the Electron and Tauri apps
> coexisted. Mentions of Tauri helpers, pages, tests and regression results describe
> that migration period; the Tauri shell has since been removed and the shared code
> now lives in `electron/src/shared/`. Existing Tauri installs: see the
> [migration guide](electron-migration.md).

Open Design from the cloning sidebar or command search. Choose a preset or adjust
voice traits, write a script, and synthesize. The existing Tauri category, conflict
resolution and seed helpers build the request; clone references are never forwarded.
A seed stays fixed while adjusting traits, and New seed creates another identity.

Save as voice profile stores the seed and validated attribute state using the existing
backend profile endpoint. Saved design profiles can be restored from the Design sidebar.
Generation uses the shared lifecycle, cancellation, progress and Vidstack output player.
Local natural-language trait extraction calls the existing deterministic mapper;
manual choices cancel queued or running mappings. Personality and demo starting points,
the collapsed recipe summary, language and production controls, profile editing and persona
export use the same established controls as the Tauri workflow. Native verification and any
remaining release gates are recorded in `electron/PARITY.md`.
