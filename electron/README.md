# VoiceStudio — Electron desktop app

Electron is the only maintained desktop app for voice cloning, stories, dubbing,
transcription, voice design, and workflows. Tauri is archived at v0.5.3. For
existing installations, see [migration notes](../docs/electron-migration.md).

The runtime supervisor manages the local FastAPI backend. Network integrations
and remote workers require configuration; local generation stays on your machine.

## Stack

| Layer     | Choice                                                                                       |
| --------- | -------------------------------------------------------------------------------------------- |
| Shell     | Electron 44, electron-vite 6 (Vite 8 / Rolldown), electron-builder                           |
| Toolchain | Vite+ (`vp` — vitest, oxlint, oxfmt), TypeScript 7 (tsgo), bun                               |
| UI        | React 19, Tailwind v4, shadcn v4 on **Base UI** (`base-nova`), lucide, sonner, wavesurfer.js |
| Data      | TanStack Query, Router (hash history), Form, Store, Virtual, Pacer                           |
| i18n      | i18next (`src/renderer/src/i18n/locales/*.json`) — no hardcoded UI text                      |

## Run it

```sh
# From the repository root
bun install
bun run dev        # electron-vite: main + preload + renderer with HMR
```

On launch the shell probes `http://127.0.0.1:3900`. If a backend is already
running (for example `bun run dev:api` from the repo root) it **attaches**;
otherwise it **spawns** one with `uv run uvicorn …` from the repo checkout and
supervises it (restart on crash, exit code 78 = port already in use). The
child's stdin is the liveness signal — closing it makes the backend exit.

Environment knobs: `OMNIVOICE_PORT` (backend port), `VOICESTUDIO_UI_PORT`
(renderer dev server, default 3902), `VOICESTUDIO_SKIP_BACKEND=1` (never
spawn, only attach), `OMNIVOICE_BACKEND_CMD` (argv override, JSON array or
whitespace-separated), `OMNIVOICE_STARTUP_BUDGET_S` (default 300).

## Same-origin API

The renderer never fetches `127.0.0.1:3900` directly (no CORS games). It calls
app-relative `/api/...`:

- dev — the renderer dev server proxies `/api` to the backend;
- prod — the renderer is served from the privileged `app://voicestudio/`
  scheme and the main process proxies `/api/*` with `net.fetch`.

## Quality gates

```sh
bun run typecheck   # tsgo, both projects
bun run check:electron # types, tests, build, packaging contract
bun run smoke-test  # packaged Electron launch + first-run/native bridge checks
bun run smoke-test -- --install  # also install and start an isolated managed backend
bun run test        # vitest (jsdom)
bun run build       # electron-vite build → out/
bun run dist        # + electron-builder → release/
```

The app version is **not** stored here: `electron.vite.config.ts` and
`electron-builder.config.mjs` read it from `frontend/package.json`, the single
source of truth.

## Layout

```
src/main/       backend supervisor, app:// protocol + /api proxy, IPC, window
src/preload/    contextBridge → window.voicestudio (typed in index.d.ts)
src/renderer/   React app: routes/, features/clone/, components/, lib/, hooks/
CONTRACT.md     module contracts shared by main, preload and renderer
```

## Cloning workspace

The desktop workspace follows T3 Code's pane composition: a collapsible left
sidebar for saved voices and recent takes, an independently scrolling central
script canvas with a bottom composer, and an inline right inspector for
reference audio and production settings. The inspector resizes by dragging its
left divider or using the focused divider's arrow keys; width is remembered
locally and clamped to preserve editor space. Opening a task pane leaves the
editor usable. Confirmation dialogs remain only for destructive actions.
Playback and export appear below the composer after generation.

Layout references: T3 Code's `AppSidebarLayout`, `PreviewPanelShell`,
`RightPanelTabs`, and the repository's desktop product screenshot.

The sidebar extends through the native title-bar row. Pane headers provide window
drag regions and reserve space for native caption controls; engine status and
theme switching live at the foot of the sidebar. The Electron shell reuses the
canonical VoiceStudio artwork and platform icons retained with the archived shell;
its packaged uv tool is staged under `electron/build/uv`.

Typography uses locally bundled Inter Variable with system UI fallbacks with shared roles: 14px/20px interface text,
13px/20px labels, 12px/16px metadata, and 16px/28px script text. Controls use
readable desktop sizing rather than Mira's smallest defaults. Sizes are rem-based
and retain native display scaling; the app does not force a zoom factor.

Button and tab contents share centered icon/text alignment and symmetric padding.
Standard icons are 16px, compact icons 14px; composer controls align on the same
row, with the keyboard shortcut below the primary action.
Lucide uses a shared 1.75 stroke weight. Font files ship with the app; typography
requires no external font service.

The sidebar settings button opens `/settings`, a dedicated view with Appearance
and General navigation. Theme, Inter/system font, UI scale, and automatic output
playback persist locally. Settings remain accessible without a ready backend.

The sidebar footer places engine status and model above a separate Settings and
Theme control row, with a labeled Settings link on the left and Theme on the right.

The composer groups language and generation options beside a single primary action.
Its shortcut appears inline when the composer has space, and controls wrap in narrow panes.

Settings layout primitives are adapted from T3 Code `settingsLayout.tsx`,
`SettingsSidebarNav.tsx`, and `WorkspacePageContainer.tsx` (MIT; see T3CODE-LICENSE.txt).
Appearance and General have direct routes and share a breadcrumb header, searchable
sidebar, max-w-4xl scroll frame, grouped sections, and consistent setting rows.
Sidebar active/hover surfaces use the shared T3 theme tokens.

The local palette library includes VoiceStudio Original, Canopy, Current, Hearth, and Orchid, with
upstream light/dark color definitions with VoiceStudio display names from T3 Code (MIT). Each appearance keeps
its own selected palette. System mode follows live OS appearance changes; the
sidebar toggle explicitly switches to light or dark. Choices persist under
`voicestudio.theme.v2`, migrating the older light/dark setting. Studio restores
the neutral palette. Semantic roles drive chrome, sidebar, overlays, controls,
text, and waveform colors. No network request is needed to select a theme.

Glass mode is an opt-in, persisted appearance setting for translucent in-app
surfaces with palette-tinted background depth. It does not make the native window
transparent to the desktop. Reduced-transparency preferences or missing backdrop
filter support retain opaque surfaces. voicestudio-classic maps the original
Tauri Gruvbox dark palette, with a complementary light variant.

Glass materials use static palette lighting, fine edge highlights, and separate
light/dark translucency. Major panes provide blur; nested pane headers reuse it
instead of adding another backdrop filter. Classic keeps its original charcoal
and rose anchors with refined ivory text, warmer raised surfaces, and softer borders.

Library tabs sit directly on the sidebar, with a highlight on the selected tab
and no enclosing segmented-control background.

Classic uses plum-charcoal surfaces, lavender text, and violet glass reflections
with the original rose action color.

## Workspace interactions

Both library and inspector panes share the same resize behavior: pointer drag,
arrow keys on the focused divider, and double-click to reset. Widths, library
visibility, selected library tab, and inspector choice persist locally. Escape
closes the inspector. Take selection opens details without changing the draft;
Reuse explicitly restores the known script, voice, language, and generation
parameters. New take parameters are saved locally for the latest 200 results;
older history exposes only fields returned by the backend. Uploaded reference
files must be attached again when reusing takes without a saved voice profile.

Generation has one provider above routing, so progress/cancellation remain shared
and navigating to Settings does not abort a running request. Failures remain
visible in the composer; synthesis can be retried. Library queries expose loading
and retry states. Ctrl/Cmd+K opens quick search for views, voices, takes, and
generation actions; Ctrl/Cmd+, opens Settings. The quick picker supports arrow
keys, Enter, and Escape. Settings search jumps to specific controls; Reset restores
the current settings section, and font choices preview their own typeface.

Clone readiness is shared by the composer, keyboard shortcut, and command picker.
Synthesis stays disabled until the script is nonempty and either an accepted
reference file or a loaded saved clone voice with reference audio is selected.
Recording/cleanup and file preparation also block submission. Missing inputs
show inline guidance with actions to open the reference pane or saved voices;
they do not produce error toasts. Server-side validation remains authoritative
for files that have been removed or cannot be decoded.

Cloning starts with an inline voice chooser when no usable reference is selected.
Saved voices, upload, and recording are available directly in the main content
area. A ready voice reveals and focuses the script editor; the composer appears
only at that stage. Change voice preserves the draft and can be canceled.
An in-flight generation retains its cancellation controls.

Batch dubbing can watch a selected local folder. Existing files are skipped;
new videos are queued only after their size and modification time match across
two five-second scans. The current language, voice and background settings apply
to each arrival. Pause holds future arrivals; Stop or leaving the batch view
releases access. Uploads stream through the shared native folder implementation,
without loading whole videos into the renderer or sending filesystem paths to
the backend. Folder replacement or loss of access stops watching with a message.
This requires the native desktop helper; it is not exposed in the web preview.

Gallery > My Imports accepts local audio/video clips and portable voice bundles.
URL downloads and video searches run only when submitted. Imported clips can be
previewed, removed with confirmation, or saved as a voice and opened in cloning.
The more-actions menu adds a voice to Stories or sets the Audiobook default,
keeping the current draft. Trim opens an inline waveform editor with a movable
selection, exact time fields, zoom and looping preview. Saving creates a separate
clip of up to 15 seconds and keeps the original.

Gallery > Community loads the backend-managed community catalogue when opened.
Presets can open in Designer; shared voices can be previewed and used in cloning,
Stories or Audiobooks. Favorites retain their source identity. Submission buttons
open a form in the browser; they do not publish a voice automatically.

Saved voice editor > Export persona downloads a portable `.ovsvoice` bundle.
Include voice clip controls whether the original reference accompanies the
watermarked preview. Gallery > My Imports accepts the exported bundle again.

Workspace navigation groups Clone, Design, Profiles, and Gallery under Voice;
Stories and Audiobook under Stories; and single/batch dubbing under Dubbing.
The current workflow opens automatically. Group buttons can expand or collapse
without navigating; the compact rail opens the same destinations in a flyout.
Transcribe, Projects, Tools, and Integrations remain directly accessible.
