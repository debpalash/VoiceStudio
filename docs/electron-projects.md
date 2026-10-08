# Electron projects

> **Historical context:** this page was written while the Electron and Tauri apps
> coexisted. Mentions of Tauri helpers, pages, tests and regression results describe
> that migration period; the Tauri shell has since been removed and the shared code
> now lives in `electron/src/shared/`. Existing Tauri installs: see the
> [migration guide](electron-migration.md).

Open Projects from the sidebar, command search, or Dubbing header. Current dubbing work can be saved with a name or saved as a new copy. Projects use the existing backend `/projects` database and Tauri state format.

Opening a project asks before replacing the current draft. Active dubbing work and unresolved task recovery prevent switching. Deletion requires confirmation, removes only the saved project record, and detaches it from the current draft. It does not delete source media or generated audio.

Segment edits, selected language, source job, and available tracks are restored. Unexposed legacy options and per-segment metadata are retained when saving. Renderer reload preserves the active project identity. This does not recreate missing source media or imply an interrupted backend job is running.

The library combines backend dubbing projects, local IndexedDB Stories and Audiobooks, saved
voice profiles, transcription history, generated takes, export history and completed longform
renders. A stable secondary sidebar filters each source and shows live counts; one search and the
list/grid switch operate without moving the workspace origin. At compact widths the global
sidebar becomes its icon rail, matching the other secondary-sidebar workspaces.

Opening a book or story restores its saved draft after confirmation; deletion detaches the draft
without removing generated media. Inline rename changes only the saved name: dubbing uses the
lightweight PATCH endpoint, and local books rename the latest durable record inside the serialized
write queue. Profile rows select the voice in cloning, transcript and take rows reuse their script,
take and completed-render rows preview through Vidstack, and export rows reveal the destination.

Verification: `electron/tests/projects-smoke.mjs` exercises the UI with a mocked project API; project-format unit tests check legacy round trips and malformed segments. A separate disposable fixture passed create/read/update/delete against the running backend.

Browser regressions verify all-source counts and profile/transcript filters alongside local book
save, unified-list rename, reload, confirmed open with original script intact, and deletion; dubbing
rename preserves unexposed options and updates the current save name. A disposable real backend
rename/read/delete round trip confirmed state preservation.

## Delete management

Every row has a delete action in both list and grid views. Select individual checkboxes or
**Select visible**, then **Delete selected** to review the names before confirming. Changing the
search or category clears selection, so hidden items are never included. Cancel is focused first;
active dubbing/longform work blocks deletion. A failed batch keeps only failed items in the dialog
for retry; successful deletions are refreshed immediately and are not repeated.

- Saved dubbing, Stories and Audiobook projects: remove the saved record, detach the current
  draft, and keep source/generated media.
- Transcripts: remove stored text and update other open transcription views.
- Voice profiles and generated takes: use their existing deletion behavior, including removal
  of associated stored audio. Exported copies are unaffected.
- Export history and completed longform renders: remove library records only; files remain on
  disk. Active or unrelated jobs cannot be removed through the render-library endpoint.

The confirmation describes these consequences. Deletion cannot be undone. Backend metadata
routes are `DELETE /export/history/{id}` and `DELETE /longform/jobs/{job_id}`; both are idempotent.
Tests: `projects-page.test.tsx`, `transcript-deletion.test.ts`, and
`tests/test_projects_delete_management.py` use disposable data, not the user's library.
