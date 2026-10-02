# Electron voice gallery

Open VoiceStudio Gallery from the cloning sidebar or command search. Browse the
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

Verification: node electron/tests/gallery-smoke.mjs exercises categories, search,
pagination, real native playback of synthetic audio, saved-profile handoff, and
navigation during saving using mocked API responses. The running backend also
returned 1,126 catalogue entries and seven categories during development. The
live Community acceptance forces a temporary catalogue outage, retries in place,
then favorites and previews a real item before handing its attributes to Designer.
Separate real smokes cover upload, trim, persona import/export, and profile
materialization without retaining disposable profiles. The packaged Ubuntu app
also completed profile creation, native `.ovsvoice` Save As, bundle inspection
and cleanup against a reused installed runtime without downloading anything.

Inline trimming decodes the source directly with Web Audio at 22,050 Hz. It does not wait for a separate media-element duration probe; files still need to be supported by the desktop audio decoder.
