# Electron voice gallery

> **Historical context:** this page was written while the Electron and Tauri apps
> coexisted. Mentions of Tauri helpers, pages, tests and regression results describe
> that migration period; the Tauri shell has since been removed and the shared code
> now lives in `electron/src/shared/`. Existing Tauri installs: see the
> [migration guide](electron-migration.md).

Open VoiceStudio Gallery from the cloning sidebar or command search. Archetypes,
Community and My Imports switch in the page header, so each section's own filters
sit directly beneath it. Browse the
existing local archetype catalogue by category, search it, and load more results.
Age, gender, pitch, accent, language and whisper filters reuse the Tauri taxonomy.
Their labels reuse the existing translated voice-design vocabulary, so no internal
translation keys appear in the filter sidebar.
Star voices to keep local favorites; Favorites searches all matching catalogue
pages, including voices beyond the currently loaded cards.
Preview playback starts only on click and uses the shared Vidstack player.
Use voice asks the existing backend to materialize a reusable design profile,
then opens Voice Design with that profile's attributes and seed. The current
script stays intact. Navigating away cancels the frontend request and prevents
a late response from redirecting the user; the backend may still finish saving.

The catalogue and profile generation remain owned by the existing backend.
API wire types are shared with Tauri. No new required network service is added.
Community voices, local/search imports, inline trimming, and portable persona
bundles use the same backend contracts and keep network actions explicit.

Community filters by pack and by language on the backend (`/community/items`
`pack` and `lang`), and each page carries the catalogue-wide `packs` and
`languages` so the filters keep every option while one is applied. Items added
in the last 30 days (`added_at`, kept only as a real ISO date) show a New badge,
and an author credited by GitHub handle links to that profile, opened in the
system browser only when clicked. A search box filters the loaded Community
voices by name, description, author or language, and a voice that arrives from
two sources or overlapping pages is listed once. **Open in Design** hands a
preset's attributes to Voice Design.

Verification: node electron/tests/gallery-smoke.mjs exercises categories, search,
pagination, real native playback of synthetic audio, saved-profile handoff, and
navigation during saving using mocked API responses. The running backend also
returned 1,126 catalogue entries and seven categories during development. The
live Community acceptance forces a temporary catalogue outage, retries in place,
then favorites and previews a real item before handing its attributes to Voice Design.
Separate real smokes cover upload, trim, persona import/export, and profile
materialization without retaining disposable profiles. The packaged Ubuntu app
also completed profile creation, native `.ovsvoice` Save As, bundle inspection
and cleanup against a reused installed runtime without downloading anything.
