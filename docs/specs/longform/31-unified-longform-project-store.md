# Implementation Spec — TASK #31: Unified `LongformProject` store

> **Historical design reference.** Paths, status and instructions below describe
> the original implementation snapshot. For current behavior see the
> [projects guide](../../electron-projects.md); follow current repository rules for new changes.

## TL;DR

> **Persistence amendment (2026-08-24, #1636):** the unified in-memory model and
> Zustand actions below are unchanged, but the localStorage-only storage design
> is superseded. Schema v9 stores unbounded `storyTracks`, `cast`,
> `storyProjects`, `script`, `meta`, `lexicon`, and `voiceCast` in the local
> IndexedDB database `omnivoice.longform`; `omnivoice.app` keeps only bounded
> preferences and ids. Upgrading a v8 envelope commits its long-form payload to
> IndexedDB before trimming localStorage. If compacting localStorage hits quota,
> revision markers choose the newest full fallback without discarding a newer
> edit. Startup retries IndexedDB reads three times; a persistent failure gates
> the main UI behind localized Retry and explicit-clear actions. Explicit clear
> first persists a local tombstone, so permanently blocked IndexedDB does not
> trap startup or later resurrect inaccessible projects. Malformed records fail
> closed into that gate, and retries reopen invalid connections. Lifecycle
> commits force-flush their compact local envelope; preference-only reset keeps
> revisioned and legacy full fallbacks plus pending-clear tombstones. Content
> reset also clears bounded references to deleted files.

Today there are two long-form text-to-speech editors — **Stories** (`frontend/src/components/StoriesEditor.jsx`, multi-voice cast + per-line tracks) and **Audiobook** (`frontend/src/pages/AudiobookTab.jsx`, single raw-text script + book metadata) — and they share *nothing* at the data layer. Stories has a persisted project model in `storiesSlice` (cast/tracks/projects in localStorage via the root zustand persist); Audiobook has **zero persistence** (title/author/narrator/genre/cover/lexicon/format/loudness/text all live in component `useState` at `AudiobookTab.jsx:21-48` and evaporate on tab switch or reload).

This spec introduces **one project concept** — a `LongformProject` — that both editors bind to. It carries the shared book identity (`title/author/narrator/genre/year/description` + cover + lexicon) once, alongside the structured content (cast + tracks + raw script), plus a `mode` discriminator (`'stories' | 'audiobook'`). This (a) gives Audiobook the persistence it lacks today, (b) lets a single saved project be re-opened in either editor, turning **#24 (Story ⇄ Audiobook convert)** from a data-shuttling problem into a `mode` toggle, and (c) consolidates the two divergent persistence stories into one slice with a tested localStorage migration that is backward-compatible with existing `storyProjects`.

**Scope note (the "defer until #24 reveals shape" caveat):** #24 has *not* shipped. This spec deliberately lands the **store + migration + Audiobook persistence binding** (the parts whose shape is already fully determined by the two existing editors), and defines — but does **not** implement — the `mode`-toggle/convert UI, leaving a clean seam (`convertMode`, `setProjectMode`) for #24 to consume. See PR slices: 31a/31b ship now; 31c is the #24 hand-off.

## Problem

1. **Audiobook loses everything on reload.** `AudiobookTab.jsx:21-48` holds `text` (`:21`), `defaultVoice` (`:22`), `format` (`:34`), `loudness` (`:35`), `meta` (title/author/narrator/year/genre/description, `:36-38`), `coverFile`/`coverPreview` (`:39-40`), and `lex` (pronunciation lexicon, `:42`) entirely in `useState`. Switch to another mode and back, or reload, and it's all gone. Stories users get named, persisted projects (`storiesSlice` `saveProject` `:84-100` / `loadProject` `:101-105`); Audiobook users get a blank textarea every time. This is an inconsistency users hit immediately on a 50-chapter book.

2. **Two project concepts that should be one.** A "project" in `storiesSlice.ts:28-34` is `StoryProject = {id, name, tracks, cast, updatedAt}`. An Audiobook "project" doesn't exist as a noun — but conceptually it's `{title, author, narrator, genre, …, script, lexicon, cover}`. These overlap heavily (both are "a long-form thing I'm narrating with metadata"), yet there's no shared store, so #24 (convert between them) has nowhere to read/write a unified shape.

3. **The convergence is half-built and asymmetric.** The render pipeline already converged: both editors stream through the SAME backend generator `_render_longform_sse` (`backend/api/routers/audiobook.py:345`) — Audiobook via `POST /audiobook` (`audiobook.ts:76-82` `audiobookGenerate`), Stories via `POST /longform/render` (`audiobook.ts:116-122` `longformRender`); see `StoriesEditor.jsx:360-402` `generateAll`, `utils/storyToSpans.js`. But the *project/metadata* layer did not. Stories' full export at `StoriesEditor.jsx:368-371` calls `longformRender({ chapters, format })` and **drops** `metadata`/`loudness`/`cover_path` — even though `LongformRenderBody` (`audiobook.ts:100-108`) already accepts all three — because Stories has no metadata fields to send. Audiobook *has* those fields but can't persist or reuse them. One store fixes the input side of an already-converged output side.

4. **#24 is blocked on a shape.** "Story ⇄ Audiobook convert" needs a target representation. Without a unified store, #24 would invent ad-hoc serialization between two slices/components. With it, convert is `setProjectMode('audiobook')` over the same record.

## Goal / Non-goals

### Goals
- One persisted `LongformProject` shape both editors read/write, carrying shared metadata (`title/author/narrator/genre/year/description`), cover reference, pronunciation lexicon, output prefs (`format/loudness`), the structured content (`cast`, `tracks`, `script`), and a `mode` discriminator.
- **Audiobook gains persistence**: title/author/narrator/genre/cover/lexicon/format/loudness/script/defaultVoice survive reload and tab switches, and can be saved as named projects in the same Projects list Stories uses.
- **localStorage migration** from the current persist `version: 4` shape (`storyProjects`, `storyTracks`, `cast`, `currentProjectId`) to the unified shape, with backward-compat: existing saved Stories projects open exactly as before, no data loss, no manual user migration (per the project's "backward-compatible project data" constraint).
- Leave a **named seam** (`convertMode`/mode toggle action) for #24, without implementing #24's UI.
- **Clean working-state lifecycle**: loading or starting a project leaves *no stale working state* from the prior session — every working field (tracks, cast, script, meta, lexicon, coverRef, prefs, mode) is deterministically (re)set, not partially carried over (see States & edge cases → A).

### Non-goals (explicitly deferred)
- **#24's convert UI / mode-toggle button** — not built here. We ship the store + the `convertMode` action; the toggle button and the "open this project in the other editor" UX is #24.
- **Backend changes.** `/longform/render`, `/audiobook` + `/audiobook/{plan,preview,cover,import}`, `/stories/encode` endpoints are untouched. This is a frontend store + binding change only. The SSE event vocabulary (pinned below in API/data shapes → SSE) is **read-only contract** here — neither editor's event handling changes.
- **No alembic / `omnivoice_data/` change.** LongformProject is browser-owned state: Zustand supplies the in-memory/versioned shape, while schema v9 splits bounded fields into localStorage and unbounded documents into IndexedDB. The alembic rule applies only to the Python `omnivoice_data/` SQLite path, which is untouched. Browser upgrades are ordered and versioned in the storage adapter. Audiobook cover references remain client-side; `POST /audiobook/cover` returns a local backend path rather than storing project state.
- **No change to the rendered output** of either editor — same spans, same SSE handling (`utils/sseParse.js`).
- Merging the actual editor *components* into one — they stay two components; only the store unifies. (Component merge, if ever, is downstream of #22 shared `<VoiceSelector>`.)
- Cover-file persistence as bytes. localStorage can't hold the blob; we persist only a re-uploadable reference + filename (see API/data shapes).
- **No mid-flight render survival.** An in-progress Audiobook/Stories render that is interrupted by a tab switch or reload is *abandoned*, not resumed (see States & edge cases → F). Working *project* state survives; the streaming render job does not. (Backend-job resume is `longformJobs`/server territory, out of scope.)
- **No new user-input parser / regex.** This task reads/writes the *same* script string and reuses the *existing* parsers (`parseScript`, `importStory`, `storyToSpans`) only where they're already called. It introduces **no new regex over user-pasted text** (the only new validation is membership in two literal mode strings). The ReDoS surface is therefore unchanged in 31a/31b; #24 (which *will* run `parseScript` on the script string to populate `tracks`) inherits the ReDoS-review duty — flagged in Constraints → CodeQL so #24 doesn't import it blind.

## Design

### One slice, renamed in concept: `longformSlice` (supersedes `storiesSlice`)

`frontend/src/store/storiesSlice.ts` becomes `frontend/src/store/longformSlice.ts`. The current `StoryProject` (`storiesSlice.ts:28-34`) is generalized into `LongformProject`. The currently-loose top-level working state (`storyTracks`, `cast`, `storyProjects`, `currentProjectId` — all defined in the slice init at `storiesSlice.ts:67-70`) is kept and **augmented** with the shared-metadata working fields that Audiobook needs (`meta`, `lexicon`, `outputFormat`, `loudness`, `coverRef`, `mode`, `script`, `defaultVoice`).

Key design decision — **two content representations coexist in one project**:
- `cast` + `tracks` — the Stories structured model (line cards; `StoryTrack` at `storiesSlice.ts:12-19`, `CastMember` at `:21-26`).
- `script` (string) — the Audiobook raw markdown model (`AudiobookTab.jsx:21` `text`).

A `LongformProject.mode` says which one is *authoritative* for that project. Both editors can read a project; an editor opening a project whose `mode` doesn't match it either (a) renders read-friendly via the existing converters, or (b) — the simpler v1 behavior — only the matching editor binds working state, and the **non-matching open is what #24 will turn into an explicit "convert" action**. v1 ships path (b): opening an `audiobook`-mode project from the Projects list routes to the Audiobook tab; a `stories`-mode project routes to Stories. The store already has the directional bridge #24 needs: `utils/storyToSpans.js` does stories→spans; the reverse — `scriptToTracks` via the existing `utils/parseScript.js` / `utils/importStory.js` (both already imported by `StoriesEditor.jsx:19-20`) — is a #24 deliverable.

### The working-state reset contract (completeness foundation)

Today `loadProject` (`storiesSlice.ts:101-105`) sets **only** `storyTracks`, `cast`, `currentProjectId`, and `newProject` (`:106`) sets **only** `storyTracks`, `cast`, `currentProjectId`. That was complete when the *only* working content was tracks+cast. Once we add `script`/`meta`/`lexicon`/`coverRef`/`outputFormat`/`loudness`/`defaultVoice`/`mode` as working fields, **a naive port leaks the previous session's metadata into a freshly-loaded/new project.** Both actions must therefore (re)set the *entire* working surface, every time, with no partial carry-over:

- `loadProject(id)`: restore **all** record fields into working state — `storyTracks`, `cast`, `script`, `meta`, `lexicon`, `coverRef`, `outputFormat`, `loudness`, `defaultVoice`, `mode`, `currentProjectId`. Fields absent on an older/partial record (e.g. a v4-migrated Stories project that never had `script`) fall back to the **same defaults the slice init uses** (`SLICE_DEFAULTS` below): `script: ''`, `meta: {}`, `lexicon: {}`, `coverRef: null`, `outputFormat: 'm4b'`, `loudness: 'off'`, `defaultVoice: null`, `mode: 'stories'`. Defensive copies (`.map(c => ({...c}))`, `{ ...meta }`, `{ ...lexicon }`) so editing working state never mutates the stored record (today's code already does this for tracks/cast at `:104`).
- `newProject(mode?)`: clear **all** working fields to defaults (`tracks: []`, `cast: DEFAULT_CAST.map(c=>({...c}))`, plus `SLICE_DEFAULTS`); set `mode` to the arg (default `'stories'` to preserve current no-arg behavior). `currentProjectId: null`.
- `convertMode(mode)`: flips working `mode` only; does **not** touch content, `currentProjectId`, or the stored record (placeholder — #24 fills the content transform).

To keep these three actions, the slice init, and the migrate fn in lockstep, define a single source of truth in `longformSlice.ts`:

```ts
// The non-content working defaults. Used by slice init, newProject, the
// loadProject default-fill, and (imported) the migrate fn. One source of truth.
export const SLICE_DEFAULTS = {
  script: '' as string,
  meta: {} as LongformMeta,
  lexicon: {} as Record<string, string>,
  coverRef: null as CoverRef | null,
  outputFormat: 'm4b' as 'm4b' | 'mp3',
  loudness: 'off' as 'off' | 'acx' | 'podcast',
  defaultVoice: null as string | null,
  mode: 'stories' as LongformMode,
} as const;
```

This contract is the single most important completeness invariant in the task and is asserted explicitly in the test plan (#7, #8).

### Why one slice, not a sibling slice for Audiobook
The two editors share the Projects list. `ProjectsPage` (`frontend/src/pages/Projects.jsx`) already takes a `storyProjects` prop (default `[]` at `:79`) and an `onOpenStory` callback (`:82`), iterates `storyProjects` to build story cards (`:146-160`), and is rendered from `App.jsx:1046-1056` with `storyProjects={storyProjects}` (`:1051`) and `onOpenStory={(id) => { loadStoryProject(id); setMode('stories'); }}` (`:1054`). A unified store means the existing Projects UI lists *both* kinds of projects with no new plumbing — Audiobook projects simply appear there for free.

> **Disambiguation (verified in code):** `Projects.jsx` *also* maintains a separate `longformJobs` local state (`:102`, rendered at `:198-211`) — these are completed **backend** audiobook render jobs fetched from the server, of `type: 'audiobooks'`, NOT client-side projects. Do **not** conflate `longformJobs` with the client `storyProjects` collection. The project record type is unified as `LongformProject`, while the collection keeps its compatibility key and flows through the existing `storyProjects` prop; `longformJobs` is untouched.

A second slice would require a second projects list, two `currentProjectId`s, and bespoke routing — the opposite of unification.

### Migration strategy (zustand persist `version` 4 → 5)
The root store's persist config (`store/index.ts:55-132`) bumps `version: 4` (`:115`) → `version: 5`, and the `migrate` function (`:120-130`) gains a `version < 5` branch that:
1. Renames nothing destructively — old keys `storyProjects`, `storyTracks`, `cast`, `currentProjectId` (persisted at `index.ts:109-113`) are **read** and mapped forward.
2. For each old `StoryProject` (`{id,name,tracks,cast,updatedAt}`), produces a `LongformProject`, then validates nested fields so `null` or a wrong type cannot override usable defaults. Non-empty string ids/names and array tracks/cast survive; invalid values become a generated id, `Untitled`, and empty arrays.
3. Maps the loose working state: `storyTracks` and `cast` pass through unchanged (keep `storyTracks`/`cast` as the persisted/working key names to minimize component churn — see Working-state naming); seeds the new working metadata fields (`mode`, `meta`, `script`, `lexicon`, `coverRef`, `outputFormat`, `loudness`, `defaultVoice`) to defaults.
4. Returns the upgraded partial. The migrate fn must **never throw** — `index.ts:121` already returns `{}` for non-object input, and `:122-128` passes old shapes through (`version < 4` branch). **Upgrade > crash** is this codebase's stated migration philosophy (the comment at `index.ts:116-119`, verified verbatim: *"Drop old persisted shapes rather than crashing the app… Upgrade > crash."*); we keep it: anything missing falls through to slice init defaults.

**Migration must defend against malformed persisted data (enumerated):**
- `p.storyProjects` is **not an array** (corrupted blob, hand-edited localStorage, partial write) → `Array.isArray` guard, treat as `[]`; do not throw.
- An individual project entry is **not an object** (e.g. `null`, a string) → skip it (filter to objects) rather than spreading a non-object; never produce a `storyProjects` entry that lacks `id`/`name`.
- A project entry has missing, null, or wrong-typed `tracks`/`cast`/`id`/`name`/`updatedAt` → post-spread normalization fills them (`tracks: []`, `cast: []`, generated `id`, `name: 'Untitled'`, `updatedAt: 0`). A project with no usable `id` is still openable rather than silently dropped.
- `currentProjectId` points to a project that **no longer exists** post-migration (e.g. was the malformed one we skipped) → leave `currentProjectId` as-is; the load-time guard in `loadProject` (`if (!p) return`, `:103`) already no-ops harmlessly, and `currentProject` (`StoriesEditor.jsx:217`) resolves to `null` → blank working state, no crash. Optionally null it during migration; either is safe.
- `version` is `> 5` (user downgraded the app, then re-upgraded) → the function returns `persisted` unchanged via the final passthrough (`:129`); new fields already present, no double-migration.
- `version` is `< 4` → existing `version < 4` branch (`:122-128`) runs **first**, then falls through to the `version < 5` branch in the same call (ordering: handle `< 4` passthrough, then `< 5` upgrade). Confirm a v2/v3 blob (no `storyProjects` key at all) yields `storyProjects: []`, not a throw.

Backward-compat guarantee: a user on v4 with three saved Stories projects reloads → sees the same three projects, same names, same cast/tracks, `mode: 'stories'` — opens identically in Stories. Nothing prompts them. New Audiobook projects they save afterward coexist in the same list.

### Working-state naming (minimize component churn)
`StoriesEditor.jsx:115-127` binds **13 store selectors** (`storyTracks` `:115`, `setStoryTracks` `:116`, `cast` `:117`, `setCast` `:118`, `upsertCastMember` `:119`, `removeCastMember` `:120`, `setCharacterVoice` `:121`, `storyProjects` `:122`, `currentProjectId` `:123`, `saveProject` `:124`, `loadProject` `:125`, `newProject` `:126`, `deleteProject` `:127`), and `setStoryTracks` is re-wrapped at `:130-133` as `setTracks`. To keep these call sites stable, the **working content field names stay** (`storyTracks`, `cast`, `setStoryTracks`, `setCast`, `upsertCastMember`, etc., unchanged signatures from `storiesSlice.ts:71-83`). We **add** new working fields/actions for the shared metadata that Audiobook binds to. The *project record* type is renamed (`StoryProject` → `LongformProject`), while the projects array retains the `storyProjects` compatibility key used by `ProjectsPage`/`App.jsx`/`StoriesEditor.jsx` (see Integration points). This keeps the diff reviewable: Stories' line-editing code is unchanged; only project save/load gains metadata, and Audiobook gains store binding.

> **Note on `storyProjects` consumers:** a repo-wide grep confirms exactly 6 files reference `storyProjects`: `store/storiesSlice.ts`, `store/storiesSlice.test.ts`, `store/index.ts`, `components/StoriesEditor.jsx`, `pages/Projects.jsx`, `App.jsx`. All must be accounted for in the rename + alias bridge.

> **Note on the `_trackId` reseed effect (`StoriesEditor.jsx:135-140`):** Stories reseeds its module-level `_trackId` counter from the max persisted track id **once on mount** (`useEffect([], …)` — verified: dep array is `[]`, `:140`). After unification, a user who opens a *different* Stories project from the Projects page (`loadProject` while StoriesEditor is already mounted) will **not** re-trigger this effect, so a newly-added line could collide with an id from the just-loaded project. This is a **pre-existing latent bug** the load path now makes reachable. Mitigation (low-cost, in 31b): change the effect dep array to `[currentProjectId]`, or reseed inside the `setTracks` add path. Track explicitly; do not silently leave it.

## States & edge cases (COMPLETENESS)

This section enumerates every state the feature must handle and the exact behavior for each. Items A–J are load-bearing; each maps to a test or an explicit no-op-by-design.

### A. Working-state lifecycle / stale-carryover (highest risk)
- **A1. Load Stories project A (metadata set) → load Stories project B (no metadata).** B must show empty metadata, not A's. `loadProject` resets the full working surface (see reset contract). *Test #8.*
- **A2. Edit Audiobook metadata (unsaved) → `newProject()`.** New project starts with empty `meta`/`script`/`lexicon`/`coverRef`, default prefs, `mode: 'stories'`. No carry-over. *Test #7.*
- **A3. Load an `audiobook`-mode project, then load a `stories`-mode project.** Working `mode` flips `audiobook → stories`; Audiobook-only fields (`script`, `meta`, etc.) reset to the stories record's values (or defaults if absent). The matching tab (Stories) binds; the previous tab's transient render state is irrelevant (it unmounted).
- **A4. v4-migrated Stories project (no `script`/`meta` keys) loaded.** Missing keys resolve to `SLICE_DEFAULTS` via the reset contract — never `undefined` reaching a controlled `<input value={...}>` (React warns on `undefined → string` controlled-input flips). Every metadata input is bound to a guaranteed-string/object default. The six `meta.*` inputs at `AudiobookTab.jsx:274-286` each read `meta.title`/`meta.author`/etc. — all must be `''`, never `undefined`.

### B. Empty / missing / partial inputs
- **B1. Empty `name` on `saveProject('')`.** Preserve existing behavior: `name || 'Untitled'` (`storiesSlice.ts:90`) for Stories; Audiobook save reuses the same fallback. The `StoriesEditor.saveCurrent` already passes `projectName.trim() || t('stories.untitled')` (`:224`); Audiobook's new save affordance mirrors this with an audiobook-appropriate default (e.g. `meta.title || t('audiobook.untitled')` — if added as a key, propagate to all 21 locales). The **store-level** fallback (`name || 'Untitled'`) is the non-localized last-resort guard; the *displayed* default name passed in by each editor is localized via `t()` (see Constraints → Localization).
- **B2. Save an Audiobook project with empty `script` and empty `meta`.** Allowed — it persists an empty-but-named project (matches Stories allowing save of an empty cast/track set; existing test `:97-98` saves with no tracks). The record is still openable; reopening yields the blank editor.
- **B3. Empty `lexicon` rows.** `lexDict()` (`AudiobookTab.jsx:43-45`) already filters rows where `word.trim()`/`say.trim()` are blank; the store's `lexicon` should hold the **filtered dict** (`{word→say}`), but the editable UI still needs the **row array with blanks** for in-progress typing. Decision: store `lexicon` as the filtered dict (the wire/persist shape, matching `LongformProject.lexicon: Record<string,string>`); keep the *editable rows* (including blank in-progress rows) as **component-local** `useState` in AudiobookTab, hydrated from the dict on mount and flushed to the store on change. This avoids persisting half-typed `{word:'', say:''}` junk. *Edge:* a row with `word` filled but `say` blank is dropped from the persisted dict but kept in the local editing rows until the user fills `say` or removes the row. **Rehydration mapping (dict → rows):** on mount, `Object.entries(lexicon).map(([word, say]) => ({ word, say }))`; the flush direction (rows → dict) reuses the existing `lexDict()` shape exactly.
- **B4. `defaultVoice` references a profile id that no longer exists** (voice deleted between sessions). The `<select>` (`AudiobookTab.jsx:220-224`) will have no matching `<option>`, so it renders empty → falls back to engine default on render (`default_voice: defaultVoice || null`, `:132`). Acceptable; no crash. Same already true for Stories cast `profileId` (resolved by `effectiveProfile` in `storyCast.ts`).
- **B5. Stories `cast` member references a deleted profile** — unchanged from today; `storyToSpans`/`effectiveProfile` already tolerate stale `profileId` by falling back to default. Unification doesn't change this.

### C. Cover image round-trip (no bytes in localStorage)
- **C1. Pick cover → save project → reload.** `coverFile` blob is transient; on reload the live preview is gone. Persisted `coverRef = { filename, serverPath }` survives. UI shows the **filename** (and a "cover set" affordance) but not the image preview (we can't reconstruct a blob from a server path without a fetch; v1 shows filename text, not a thumbnail — keep it honest). *Test: save with cover → simulate reload → `coverRef.serverPath` survives.*
- **C2. Re-render after reload using only `coverRef.serverPath`.** `onCreate` (`AudiobookTab.jsx:121-124`) currently uploads `coverFile` and uses the returned `{ path }`. New logic: **if `coverFile` is present, upload it (fresh path) AND `setCoverRef({ filename: coverFile.name, serverPath: cover_path })`; else if `coverRef.serverPath` is present, reuse it** as `cover_path`. So a reloaded session re-renders with the persisted cover without re-picking. Exact resolution helper signature below in API/data shapes.
- **C3. `serverPath` is stale / cleaned up server-side** (the cover upload dir was a temp dir the backend garbage-collected; note `_safe_cover_path(cover_path)` at `audiobook.py:451` confines/validates the path server-side). Render either succeeds (backend ignores missing cover) or the backend errors. Behavior: the backend surfaces a generic `{type:'error', error:'render failed (see backend log)'}` SSE event (`audiobook.py:474`) which `onCreate` maps to the existing `error` channel (`AudiobookTab.jsx:165-167`); **do not crash the editor**; the user can re-pick a cover. Document that `coverRef` is best-effort — re-picking always works.
- **C4. `coverFile` upload fails** (`audiobookUploadCover` throws). Caught by the outer `try/catch` (`:170-172`); `error` is set, `generating` reset in `finally`. No partial render. Unchanged from today's behavior — just confirm the store-bound source doesn't change the error path.
- **C5. Clear cover (`clearCover`, `:57-61`)** must also null the persisted `coverRef`, not just the transient blob — otherwise a removed cover reappears (as a filename) on reload. Wire `clearCover` → also call `setCoverRef(null)`.

### D. Migration / persistence failure modes
- **D1. Corrupted / non-object persisted blob** → `migrate` returns `{}` (`index.ts:121` guard); store boots to all slice defaults. No crash. *Test #6 garbage case.*
- **D2. `storyProjects` non-array** → treated as `[]` (Array.isArray guard). *See Migration → malformed defenses.*
- **D3. Malformed individual project entries** → object-filter + default-fill; never drop silently except non-objects. *See Migration.*
- **D4. IndexedDB write fails (quota/policy/private mode).** The adapter writes a revisioned full fallback to localStorage and synchronously verifies the coalesced flush. If that fallback also cannot be written, the orderly flush rejects instead of falsely acknowledging durability; intentional recovery reload/exit actions log the failure and continue. Preference reset preserves even an unrevisioned legacy full envelope because it may be the last copy left after adding the revision itself exceeded quota.
- **D5. Two browser main tabs write the same revision.** Each tab can start from revision N and independently produce N+1. A full local fallback with a revision equal to IndexedDB wins on hydration (`>=`), because that local write necessarily followed the durable record it is recovering from. Desktop's widget remains read-only and cannot create this collision.
- **D6. Migration runs but `cast` working field is missing on a v4 blob that only had `storyProjects`** (user never touched the editor) → working `cast` falls through to slice init `DEFAULT_CAST` (`:68`). Confirmed safe.

### E. Routing / open-from-Projects edge cases
- **E1. Open `audiobook`-mode project from Projects list.** `onOpenStory(id)` → `loadProject(id)` → read loaded `mode` → `setMode('audiobook')` → routes to AudiobookTab (`App.jsx:1077-1082`), fields restored. *Acceptance #4.*
- **E2. Open `stories`-mode project.** → `setMode('stories')` → StoriesEditor. (Today's behavior, preserved.)
- **E3. Open a project whose `mode` is missing/unknown** (defensive). Default to `'stories'` (`rec.mode === 'audiobook' ? 'audiobook' : 'stories'`) so any non-`'audiobook'` value, including `undefined`, routes to Stories — the safe legacy default. (This two-value membership check is the *entire* new "validation" surface in the task — it is a literal-equality test, **not** a regex, so it carries no ReDoS exposure; see Constraints → CodeQL.)
- **E4. Open a project while the *other* editor is mid-render.** The other editor unmounts on `setMode`; its in-flight SSE read is abandoned (see F). The opened project's working state is set cleanly by `loadProject`. No bleed-through.
- **E5. `onOpenStory` called with an id that doesn't resolve** (deleted between list render and click). `loadProject` no-ops (`:103`); the routing logic must read the record mode **and** verify resolution *before* `setMode`. Exact `onOpenStory` body pinned in API/data shapes → Routing.

### F. In-flight render interrupted (tab switch / reload)
- **F1. Audiobook generating → user switches tab.** `AudiobookTab` unmounts; `abortRef` (component-local, `:31`) is lost, the `reader.read()` loop is abandoned when the component is GC'd, but the **fetch/SSE stream may keep running server-side** until the backend finishes or the connection drops. Working *project* state (text/meta/etc., now store-bound) survives; the render result (`output`) does **not** persist (it's transient `useState`, `:27`). On return, the user sees the restored inputs but no in-progress render — they re-click Create. **This is acceptable v1 behavior; explicitly NOT resumed.** (Resumable backend jobs = `longformJobs`, separate.)
- **F2. Stories generating (`generateAll`) → tab switch.** Same as F1; `exporting`/`exportPct` are component `useState` (`StoriesEditor.jsx:147-148`), lost on unmount. Project (tracks/cast/meta) survives via store.
- **F3. Reload during generation.** Stream dies with the page; nothing to resume. Inputs restored from persist. No "ghost generating" spinner because generation flags were never persisted (they're transient `useState`, never in `partialize`). Confirm none of the new persisted fields accidentally capture a transient (the partialize block lists *exactly* the working metadata fields, no `generating`/`output`/`progress`/`exporting`/`exportPct`). This matches the existing partialize comment at `index.ts:107-108` ("strip transient runtime fields… so a dead blob: URL / stuck spinner never rehydrates").

### G. `convertMode` seam (placeholder, #24 fills)
- **G1. `convertMode('audiobook')` on a stories project with content.** v1: flips working `mode` to `'audiobook'`, content untouched. Since AudiobookTab reads `script` (empty for a stories project), the user would see a blank script — which is exactly why the **content transform is #24's job**. v1 ships the flag flip only and routes nowhere (no UI button). *Test #2: assert `convertMode` flips `mode` and does NOT mutate `cast`/`tracks`/`script`.*
- **G2. `convertMode` to the same mode it's already in.** No-op (idempotent); does not bump `updatedAt` or touch the record. (Implementation guard: `if (get().mode === mode) return;`.)
- **G3. `convertMode` with an invalid mode value.** TypeScript prevents it at compile time; at runtime, guard to the two known modes or ignore (`if (mode !== 'stories' && mode !== 'audiobook') return;`) — do not set an unknown mode that breaks routing E3.

### H. Save/load idempotency & duplicate handling (preserve existing contract)
- **H1. Save twice with a `currentProjectId`** → in-place update, no duplicate (existing test `:73-81`, must stay green). The new metadata fields snapshot on every save, overwriting the prior record's metadata. *Test #2.*
- **H2. Save a new (no `currentProjectId`) project** → new id, appended (`:95-98`). `currentProjectId` set to the new id (`:98`).
- **H3. Delete the currently-open project** (`deleteProject`, `:107-111`) → removed; `currentProjectId` nulled if it matched. Working content is **not** cleared by delete today (only `currentProjectId`) — preserve that (the user keeps editing the now-orphaned content, can re-save as new). Note explicitly so the reset contract (A) doesn't accidentally also wipe working state on delete.
- **H4. Rename a project** (`renameProject`, `:112-113`) — unchanged; only `name` changes, never metadata.

### I. Concurrent field edits / partial patches
- **I1. `setProjectMeta({ title })` must merge, not replace.** Partial patch: `set(s => ({ meta: { ...s.meta, ...patch } }))`. Editing `title` must not clear `author`. The current `AudiobookTab.setMetaField` (`:49`) already does `{ ...m, [k]: ... }`; the store action must preserve that merge semantics, not overwrite the whole object.
- **I2. `setOutputPrefs({ loudness })`** similarly merges (`outputFormat`/`defaultVoice` untouched). Patch keys are `outputFormat | loudness | defaultVoice` (the 3 prefs), all optional; `set(s => ({ outputFormat: p.outputFormat ?? s.outputFormat, loudness: p.loudness ?? s.loudness, defaultVoice: p.defaultVoice !== undefined ? p.defaultVoice : s.defaultVoice }))` — note `defaultVoice` uses `!== undefined` because `null` is a *valid* value (engine default) that must overwrite.
- **I3. `setLexicon(dict)`** replaces the whole dict (it's the flushed editable-rows result; replacement is correct, not merge).

### J. i18n / metadata-on-the-wire edge cases
- **J1. Metadata field with only whitespace.** `onCreate` already filters `v && v.trim()` (`:127`) before sending. The store persists the *raw* (untrimmed) value the user typed; the **wire filter stays in `onCreate`**, unchanged. So persisted `meta.title = '  '` round-trips but is dropped from the request body — preserve this exactly (`AudiobookTab.jsx:126-128`).
- **J2. `loudness: 'off'` maps to `null` on the wire** (`:134`). The store holds `'off'`; the request maps it (`loudness === 'off' ? null : loudness`). Keep the mapping in `onCreate`/`generateAll`, not in the store (store holds the UI-level value, type `'off' | 'acx' | 'podcast'`; the wire type is `'off' | 'acx' | 'podcast' | null` per `AudiobookGenerateBody.loudness` `audiobook.ts:65`).
- **J3. Stories export with no metadata** (a v4-migrated project, `meta: {}`). `generateAll` filters empties identically to `onCreate`; sends `metadata: null` when empty so the wire shape is unchanged for metadata-less Stories projects. *No regression to today's `{chapters, format}` call for the empty case — `metadata`/`cover_path` are optional fields on `LongformRenderBody` (`audiobook.ts:106-107`), so omitting or sending `null` is equivalent on the wire.*
- **J4. Metadata persists raw bytes regardless of language.** `meta.title`/`author`/etc. are free-text the user types in any of the 646 supported languages (incl. CJK book titles). These are **runtime data, not hardcoded UI strings**, so they're outside the no-hardcoded-CJK rule (which governs *source literals*, not stored user values). No transform/strip on store; the value round-trips byte-for-byte. (The only normalization is the wire-side whitespace filter, J1.)

## Integration points (file:line)

| Location | Change |
|---|---|
| `frontend/src/store/storiesSlice.ts` (whole file, `:1-115`) | Rename to `longformSlice.ts`; generalize `StoryProject` (`:28-34`) → `LongformProject`; rename `StoriesSlice` (`:36-51`) → `LongformSlice`, adding `meta`, `lexicon`, `coverRef`, `outputFormat`, `loudness`, `mode`, `script`, `defaultVoice` working fields; export `SLICE_DEFAULTS` + the new types (`LongformMode`, `LongformMeta`, `CoverRef`); add `setProjectMeta` (merge I1), `setLexicon` (replace I3), `setOutputPrefs` (merge I2), `setScript`, `setCoverRef`, `convertMode` (G) actions; extend `saveProject` (`:84-100`) to snapshot all new fields, `loadProject` (`:101-105`) to **restore the full working surface with default-fill** (reset contract / A4), `newProject` (`:106`) to **clear the full surface** and accept an optional `mode`. Re-export old names (`createStoriesSlice` `:66`, `StoryProject` `:28`, `StoriesSlice` `:36`) as deprecated aliases for one PR. `DEFAULT_CAST` (`:53-55`), `genProjectId` (`:57-59`), `snapshotTracks` (`:62-64`) stay; **export `genProjectId`** so the migrate fn in `index.ts` can import it (see Migration). **No hardcoded non-English string literals introduced** (the store-level `'Untitled'` fallback is the only literal, English, last-resort — localized name comes from the caller; Constraints → Localization). |
| `frontend/src/store/index.ts:36-37` | Update import: `import type { StoriesSlice } from './storiesSlice'` / `import { createStoriesSlice } from './storiesSlice'` → `import type { LongformSlice } from './longformSlice'` / `import { createLongformSlice, genProjectId, SLICE_DEFAULTS } from './longformSlice'` (keep alias export from the slice so other importers don't break; import `genProjectId`+`SLICE_DEFAULTS` for the migrate fn). |
| `frontend/src/store/index.ts:45` | `AppStore` type: `… & StoriesSlice & …` → `… & LongformSlice & …`. |
| `frontend/src/store/index.ts:63` | `...createStoriesSlice(set, get, api)` → `...createLongformSlice(set, get, api)`. |
| `frontend/src/store/index.ts:107-113` | `partialize`: today persists `storyTracks` (stripped at `:109-110`), `cast` (`:111`), `storyProjects` (`:112`), `currentProjectId` (`:113`). Keep `storyProjects`/`storyTracks`/`cast`/`currentProjectId`; **add** loose working `meta`/`script`/`lexicon`/`coverRef`/`outputFormat`/`loudness`/`defaultVoice`/`mode` so an unsaved Audiobook session survives reload — matching how `storyTracks` is already persisted loose (`:109`). Keep the existing transient strip on `storyTracks` (`:109-110`). **Do NOT persist any transient render flag** (no `generating`/`output`/`progress`/`exporting`/`exportPct` — they live in component `useState`, not the slice; confirm none sneak in) so no ghost-spinner on reload (F3). Exact partialize block diff pinned in API/data shapes → partialize. |
| `frontend/src/store/index.ts:115` | `version: 4` → `version: 5`. |
| `frontend/src/store/index.ts:120-130` | Add a `version < 5` branch in `migrate` (after the existing `version < 4` branch at `:122-128`); see Design → Migration **and** the malformed-data defenses (D1–D6). Keep the non-object guard at `:121` and the final passthrough at `:129`. Ensure `version < 4` blobs flow through the `< 4` branch *then* the `< 5` branch in the same call. Full migrate fn body pinned in API/data shapes → Migration. **This `migrate` fn IS the localStorage analog of an alembic upgrade** — Test #3 is the data-integrity gate. |
| `frontend/src/pages/AudiobookTab.jsx:21-48` | Replace local `useState` for `text` (`:21`), `defaultVoice` (`:22`), `format` (`:34`), `loudness` (`:35`), `meta` (`:36-38`) with `useAppStore` bindings (selectors pinned in API/data shapes → component binding). **Lexicon stays partially local (B3):** the editable *rows* array (with in-progress blanks) is component-local `useState`, hydrated from store `lexicon` on mount, flushed to store via `setLexicon(lexDict())` on change. Cover stays partially local: `coverFile`/`coverPreview` blob (`:39-40`) is transient (matches the `URL.revokeObjectURL` cleanup at `:57-64`); the persisted *reference* is the new `coverRef` (filename + server path). `clearCover` (`:57-61`) must also `setCoverRef(null)` (C5). The `lexDict()` helper (`:43-45`) and `setMetaField`/`setLexRow`/`addLexRow`/`removeLexRow` (`:46-49`) adapt to read/write the store (meta via `setProjectMeta` merge, lexicon via local-rows→`setLexicon`). The `format`/`loudness` `onChange` handlers (`:231`, `:239`) route through `setOutputPrefs`. **All new/changed user-facing strings via `t()` only** — no hardcoded labels; this file is **not** on the CJK-test allowlist, so any non-English literal here would fail CI (Constraints → Localization). |
| `frontend/src/pages/AudiobookTab.jsx:113-175` (`onCreate`) | Read `meta`/`lexicon`/`format`/`loudness`/`text`/`defaultVoice` from store instead of local state. The `audiobookGenerate` body construction (`:130-138`) is **unchanged in shape** — only the *source* of each field moves to the store; keep the wire transforms in place (whitespace-filter J1, `loudness→null` J2). **Cover logic (C2):** `cover_path = coverFile ? (await audiobookUploadCover(coverFile)).path : (coverRef?.serverPath ?? null)` — and on a fresh upload, `setCoverRef({ filename: coverFile.name, serverPath: cover_path })`. Keep the upload-failure path (C4) intact via the existing `try/catch` (`:170-172`). All requests stay local (`/audiobook/*`); no new external endpoint (Constraints → Local-first). SSE event handling (`:151-167`) unchanged — same event vocabulary (API/data shapes → SSE). |
| `frontend/src/pages/AudiobookTab.jsx:80-95, :97-111` (`onImport`, `onPreviewChapter`) | These set/read `text`/`defaultVoice`/`lex` (`:87-88`, `:101-104`); update to read/write the store-bound equivalents. `onImport` sets store `script` (was `setText(r.text)`, `:88`) and clears `plan` (transient, stays local). `onPreviewChapter` reads store `text`/`defaultVoice` + computed lexicon dict; the `audiobookPreviewChapter` body shape (`audiobook.ts:40`: `{text, chapter_index, default_voice, lexicon}`) is unchanged. |
| `frontend/src/components/StoriesEditor.jsx:115-127` | Store-binding selectors. Keep `storyProjects` (`:122`) as the compatibility collection key. The other 12 selectors (`:115-121`, `:123-127`) stay by name. `currentProject` computed at `:217` and the projects panel render use the same collection. (This file IS on the CJK allowlist — line-editing code may keep its existing functional CJK; no *new* hardcoded UI CJK should be added regardless.) |
| `frontend/src/components/StoriesEditor.jsx:135-140` (`_trackId` reseed) | The mount-only reseed effect (dep array `[]` at `:140`) doesn't re-run on cross-session `loadProject` (latent collision bug now reachable). Change dep array to `[currentProjectId]` or reseed in the add path. See Working-state naming note. |
| `frontend/src/components/StoriesEditor.jsx:360-402` (`generateAll`) | **Now can attach metadata**: currently `:368-371` passes only `{ chapters, format }` to `longformRender`. Extend to pass `metadata`/`loudness`/`cover_path` from the unified project — `LongformRenderBody` (`audiobook.ts:100-108`) already accepts all three. Apply the **same empty-filter** as `onCreate` so a metadata-less Stories project still sends `metadata: null` (J3 — no wire regression). Map `loudness: 'off' → null`, reuse `coverRef.serverPath` if present. Exact extended body pinned in API/data shapes → Stories export. This is the immediate payoff of unification — Stories full export stops dropping book metadata. (Note: chapter-level `speed` is **not** a `LongformRenderBody.chapters[]` field; `speed` rides at span level only — `LongformRenderBody.chapters[].spans[].speed?` at `audiobook.ts:101`, set by `storyToSpans.js:40,47`.) SSE event handling (`:383-391`) unchanged. |
| `frontend/src/App.jsx:251-252` | Keep `const storyProjects = useAppStore(s => s.storyProjects)` (`:251`); `const loadStoryProject = useAppStore(s => s.loadProject)` (`:252`) now also sets working `mode` from the record. |
| `frontend/src/App.jsx:1046-1056` (`ProjectsPage` render) | `storyProjects={storyProjects}` (`:1051`) passes the renamed array (keep the `storyProjects` *prop name* on `Projects.jsx` OR rename it there too — pick one and apply consistently). Make `onOpenStory` (`:1054`) **mode-aware AND load-result-aware (E5)** — exact body pinned in API/data shapes → Routing. `setMode` is the store action selected at `App.jsx:110`. Routing behaves identically on macOS/Windows/Linux (no platform branch). |
| `frontend/src/pages/Projects.jsx:79, :146-160, :228` | Keep the `storyProjects = []` prop (`:79`), its iteration at `:146-160`, and its `useMemo` dependency at `:228`. Story cards (`type:'stories'`, `:150`) can optionally branch icon/label on `sp.mode` (e.g. `BookMarked` for audiobook vs `BookOpen` for stories, mirroring the existing `:157` icon). **Subtitle for audiobook-mode projects** (`:153-154` currently builds `[story_lines, story_voices].filter(Boolean).join(' · ')`) should show a sensible audiobook summary (e.g. char count from `script.length` or a chapter hint) or fall back gracefully when `tracks`/`cast` are empty — don't render an empty subtitle for an audiobook. Any new card label/subtitle text via `t()` (Constraints → Localization). Leave `longformJobs` (`:102, :198-211`) untouched. |
| `frontend/src/store/storiesSlice.test.ts` | Rename to `longformSlice.test.ts`; keep all existing assertions green (they're the backward-compat contract — `:16-114`); add new-field tests. The test harness (`:4-10`) calls `createStoriesSlice` directly (`:8`) and reads `storyTracks`/`cast`/`storyProjects`/`currentProjectId` — keep those names working via aliases. (Test files `*.test.ts` are **excluded from CodeQL** by `security.yml:102-105` and from the CJK source scan by the test-fixture exemption in `tests/test_no_hardcoded_cjk.py:90-94` — so the ported suite carries no SAST/CJK burden.) |
| `frontend/src/i18n/locales/*.json` (**21 files**: ar, de, en, es, fr, hi, id, it, ja, ko, nl, pl, pt, ru, sv, th, tr, uk, vi, zh-CN, zh-TW — count verified: `ls locales/ \| wc -l` = 21) | No *new* user-facing strings strictly required for 31a/31b core — reuse existing keys: Audiobook keys live under `audiobook.*` (en.json `:112` block); Stories keys under `stories.*` (en.json `:26` block: `untitled:54`, `projectSaved:58`); Projects card keys under `projects.*` (`untitled_story`, `story_lines`, `story_voices` — used at `Projects.jsx:152-154`). **New keys (each × 21 locales) if 31b adds the affordances:** an Audiobook "Save project" button label + "Saved" toast (`audiobook.save_project`, `audiobook.project_saved`), an `audiobook.untitled` default name (B1), and optionally a "cover set: {filename}" label for the reloaded-cover state (C1). Each must go through `t()` and into **all 21** locales in the **same PR** (Docs-sync + Localization hard rules); a key present in `en.json` but missing in the other 20 is a CI/UX failure, not a follow-up. |

## API / data shapes

> Everything below is **frontend localStorage + in-memory** state plus **existing** backend request/response shapes the editors already speak. No backend DB schema, no alembic migration, no new endpoint. The single "schema migration" is the zustand-persist `version: 4 → 5` `migrate` fn (the localStorage analog of an alembic upgrade), pinned in full below.

### `LongformProject` (persisted record)
```ts
export type LongformMode = 'stories' | 'audiobook';

export interface LongformMeta {
  title?: string;
  author?: string;
  narrator?: string;
  year?: string;
  genre?: string;
  description?: string;
}
// Mirrors api/audiobook.ts:51-58 AudiobookMetadata EXACTLY (same 6 optional
// string fields) — keep them in lockstep, or alias `LongformMeta = AudiobookMetadata`,
// so generateAll/onCreate need no mapping.
// Values are free-text in ANY of the 646 supported languages (incl. CJK book
// titles) — this is runtime user DATA, not a hardcoded source string, so it is
// outside the no-hardcoded-CJK rule (J4). Stored byte-for-byte; no transform.

// A re-uploadable cover reference. localStorage cannot hold the File/blob
// (AudiobookTab.jsx:39-40 coverFile/coverPreview), so we persist the filename +
// the server-side path returned by audiobookUploadCover() (api/audiobook.ts:85-90,
// returns { path: string }) once uploaded. The live blob preview is rebuilt at
// render time / re-picked by the user. serverPath is BEST-EFFORT (C3): the server
// may GC the temp cover, and `_safe_cover_path` (audiobook.py:451) validates it;
// on a missing/invalid path the render surfaces a generic backend error SSE event
// and the user re-picks. Re-picking (coverFile present) always wins over serverPath
// (C2). serverPath references a LOCAL backend path (POST /audiobook/cover) — no cloud.
export interface CoverRef {
  filename: string | null;   // original picked filename, for display
  serverPath: string | null; // `path` from POST /audiobook/cover, reusable as cover_path
}

export interface LongformProject {
  id: string;
  name: string;
  mode: LongformMode;          // which content model is authoritative
  // Stories content (multi-voice):
  cast: CastMember[];          // existing CastMember (storiesSlice.ts:21-26), unchanged
  tracks: StoryTrack[];        // existing StoryTrack (storiesSlice.ts:12-19), unchanged
  // Audiobook content (raw script):
  script: string;
  // Shared book identity + output prefs:
  meta: LongformMeta;
  lexicon: Record<string, string>;     // { word -> respelling } (filtered dict; AudiobookTab.jsx:43-45)
  coverRef: CoverRef | null;
  outputFormat: 'm4b' | 'mp3';
  loudness: 'off' | 'acx' | 'podcast'; // UI-level value; 'off' maps to null on the wire (J2)
  defaultVoice: string | null;         // Audiobook's default_voice (AudiobookTab.jsx:22)
  updatedAt: number;
}
```

Concrete project-record example (a saved Audiobook-mode project, as it lives in the `omnivoice.longform` IndexedDB payload → `storyProjects[0]`):
```json
{
  "id": "p_4f9ab2c1",
  "name": "The Hollow Crown",
  "mode": "audiobook",
  "cast": [],
  "tracks": [],
  "script": "# Chapter 1\n\nIt was a dark and stormy night...",
  "meta": { "title": "The Hollow Crown", "author": "J. Vex", "narrator": "Sam Reed", "year": "2026", "genre": "Fantasy", "description": "" },
  "lexicon": { "Cthylla": "THIGH-lah" },
  "coverRef": { "filename": "cover.png", "serverPath": "/abs/path/omnivoice_data/covers/abc123.png" },
  "outputFormat": "m4b",
  "loudness": "acx",
  "defaultVoice": "voice_77",
  "updatedAt": 1749800000000
}
```

### `StoryTrack` / `CastMember` (reused unchanged — verified `storiesSlice.ts:12-26`)
```ts
export interface StoryTrack {
  id: number;
  character: string;        // CastMember.id
  text: string;
  profileId: string | null; // per-line voice override (else inherits cast)
  emotion: string | null;   // per-line tone/instruct
  speed: number | null;     // per-line speed override
}
export interface CastMember {
  id: string;
  name: string;
  color: string;
  profileId: string | null;
}
```

### `LongformSlice` (working state + action signatures)
```ts
export interface LongformSlice {
  // --- working content (names preserved from storiesSlice for low churn) ---
  storyTracks: StoryTrack[];           // Stories working tracks (storiesSlice.ts:37,67) unchanged
  cast: CastMember[];                  // unchanged (storiesSlice.ts:38,68)
  // --- new shared working metadata (Audiobook binds here) ---
  script: string;
  meta: LongformMeta;
  lexicon: Record<string, string>;     // filtered dict; AudiobookTab keeps editable rows local (B3)
  coverRef: CoverRef | null;
  outputFormat: 'm4b' | 'mp3';
  loudness: 'off' | 'acx' | 'podcast';
  defaultVoice: string | null;
  mode: LongformMode;                  // mode of the working project
  // --- projects ---
  storyProjects: LongformProject[]; // compatibility key retained (storiesSlice.ts:39,69)
  currentProjectId: string | null;     // unchanged (storiesSlice.ts:40,70)

  // --- existing actions (UNCHANGED signatures, storiesSlice.ts:71-83) ---
  setStoryTracks: (tracks: StoryTrack[]) => void;
  setCast: (cast: CastMember[]) => void;
  upsertCastMember: (member: CastMember) => void;
  removeCastMember: (id: string) => void;
  setCharacterVoice: (castId: string, profileId: string | null) => void;

  // --- new working-state actions ---
  setScript: (s: string) => void;                                  // set({ script: s })
  setProjectMeta: (patch: Partial<LongformMeta>) => void;          // MERGE (I1): set(s => ({ meta: { ...s.meta, ...patch } }))
  setLexicon: (lex: Record<string, string>) => void;              // REPLACE whole dict (I3)
  setCoverRef: (ref: CoverRef | null) => void;                    // clearCover calls setCoverRef(null) (C5)
  setOutputPrefs: (
    p: { outputFormat?: 'm4b' | 'mp3'; loudness?: 'off' | 'acx' | 'podcast'; defaultVoice?: string | null }
  ) => void;                                                       // MERGE (I2); defaultVoice uses `!== undefined` (null is valid)

  // --- project lifecycle (extended to round-trip ALL new fields; existing :84-113) ---
  saveProject: (name: string) => void;        // snapshots full surface; name||'Untitled' (B1); upsert by currentProjectId (H1/H2)
  loadProject: (id: string) => void;          // RESETS full working surface + mode, default-fills missing via SLICE_DEFAULTS (A/A4); no-op if id missing (E5)
  newProject: (mode?: LongformMode) => void;  // CLEARS full surface to defaults; mode defaults to 'stories' (A2)
  deleteProject: (id: string) => void;        // unchanged (:107-111): removes + nulls currentProjectId; does NOT clear working content (H3)
  renameProject: (id: string, name: string) => void; // unchanged (:112-113); name only

  // --- seam for #24 (defined, NOT wired to UI here) ---
  convertMode: (mode: LongformMode) => void;  // flips working `mode` only; idempotent no-op if same (G2); invalid-value guard (G3); content untouched; #24 fills transform
}
```

Exact action bodies for the load-bearing three (the reset contract):
```ts
saveProject: (name) => set((s) => {
  const id = s.currentProjectId || genProjectId();
  const ts = (() => { try { return Date.now(); } catch { return 0; } })();
  const proj: LongformProject = {
    id,
    name: name || 'Untitled',
    mode: s.mode,
    cast: s.cast.map((c) => ({ ...c })),
    tracks: snapshotTracks(s.storyTracks),
    script: s.script,
    meta: { ...s.meta },
    lexicon: { ...s.lexicon },
    coverRef: s.coverRef ? { ...s.coverRef } : null,
    outputFormat: s.outputFormat,
    loudness: s.loudness,
    defaultVoice: s.defaultVoice,
    updatedAt: ts,
  };
  const exists = s.storyProjects.some((p) => p.id === id);
  return {
    storyProjects: exists
      ? s.storyProjects.map((p) => (p.id === id ? proj : p))
      : [...s.storyProjects, proj],
    currentProjectId: id,
  };
}),

loadProject: (id) => {
  const p = get().storyProjects.find((x) => x.id === id);
  if (!p) return;                         // E5: no-op when id missing
  set({
    storyTracks: (p.tracks || []).map((t) => ({ ...t })),
    cast: (p.cast || DEFAULT_CAST).map((c) => ({ ...c })),
    script: p.script ?? SLICE_DEFAULTS.script,
    meta: { ...(p.meta ?? SLICE_DEFAULTS.meta) },
    lexicon: { ...(p.lexicon ?? SLICE_DEFAULTS.lexicon) },
    coverRef: p.coverRef ? { ...p.coverRef } : SLICE_DEFAULTS.coverRef,
    outputFormat: p.outputFormat ?? SLICE_DEFAULTS.outputFormat,
    loudness: p.loudness ?? SLICE_DEFAULTS.loudness,
    defaultVoice: p.defaultVoice ?? SLICE_DEFAULTS.defaultVoice,
    mode: p.mode === 'audiobook' ? 'audiobook' : 'stories',   // E3 default-safe
    currentProjectId: id,
  });
},

newProject: (mode = 'stories') => set({
  storyTracks: [],
  cast: DEFAULT_CAST.map((c) => ({ ...c })),
  ...SLICE_DEFAULTS,
  mode: mode === 'audiobook' ? 'audiobook' : 'stories',       // override SLICE_DEFAULTS.mode
  currentProjectId: null,
}),

convertMode: (mode) => {
  if (mode !== 'stories' && mode !== 'audiobook') return;     // G3
  if (get().mode === mode) return;                            // G2 idempotent
  set({ mode });                                              // G1: flag only, content untouched
},
```

### `AudiobookTab` store binding (replaces `useState`)
```ts
// was: const [text, setText] = useState('');                    (AudiobookTab.jsx:21)
const text         = useAppStore((s) => s.script);
const setText      = useAppStore((s) => s.setScript);
// was: const [defaultVoice, setDefaultVoice] = useState('');     (:22)
const defaultVoice = useAppStore((s) => s.defaultVoice) ?? '';   // select coerces null→'' for the <select value>
// was: const [format, setFormat] = useState('m4b');             (:34)
const format       = useAppStore((s) => s.outputFormat);
// was: const [loudness, setLoudness] = useState('off');         (:35)
const loudness     = useAppStore((s) => s.loudness);
// was: const [meta, setMeta] = useState({...});                 (:36-38)
const meta         = useAppStore((s) => s.meta);
const coverRef     = useAppStore((s) => s.coverRef);
const setProjectMeta = useAppStore((s) => s.setProjectMeta);
const setLexicon   = useAppStore((s) => s.setLexicon);
const setCoverRef  = useAppStore((s) => s.setCoverRef);
const setOutputPrefs = useAppStore((s) => s.setOutputPrefs);

// onChange adapters (keep call-site shapes):
const setMetaField = (k) => (e) => setProjectMeta({ [k]: e.target.value });   // I1 merge in store
// format/loudness/defaultVoice <select> onChange:
//   onChange={(e) => setOutputPrefs({ outputFormat: e.target.value })}        (:231)
//   onChange={(e) => setOutputPrefs({ loudness: e.target.value })}            (:239)
//   onChange={(e) => setOutputPrefs({ defaultVoice: e.target.value || null })}(:221)

// lexicon: editable rows stay component-local; hydrate + flush:
const [lex, setLex] = useState(() => Object.entries(useAppStore.getState().lexicon)
  .map(([word, say]) => ({ word, say })));            // hydrate dict→rows on mount (B3)
const lexDict = () => Object.fromEntries(
  lex.filter((r) => r.word.trim() && r.say.trim()).map((r) => [r.word.trim(), r.say.trim()]));
useEffect(() => { setLexicon(lexDict()); }, [lex]);   // flush rows→dict on every change

// meta<input value> guards (A4): every value reads `meta.title ?? ''`, never bare `meta.title`,
// so a v4-migrated project (meta:{}) never feeds `undefined` to a controlled input.
```

### `audiobookGenerate` request body — UNCHANGED shape, sources moved to store (`onCreate`, `:130-138`)
```ts
// cover_path resolution (C2):
const cover_path = coverFile
  ? (await audiobookUploadCover(coverFile)).path     // returns { path: string } (audiobook.ts:85-90)
  : (coverRef?.serverPath ?? null);
if (coverFile) setCoverRef({ filename: coverFile.name, serverPath: cover_path });

const metadata = Object.fromEntries(                  // J1 whitespace filter (unchanged, :126-128)
  Object.entries(meta).filter(([, v]) => v && v.trim()));
const lexicon = lexDict();                            // filtered dict (B3)

await audiobookGenerate({                             // AudiobookGenerateBody, audiobook.ts:60-69
  text,                                               // <- store.script
  default_voice: defaultVoice || null,                // <- store.defaultVoice (J: '' → null)
  format,                                             // <- store.outputFormat ('m4b'|'mp3')
  loudness: loudness === 'off' ? null : loudness,     // <- store.loudness; J2 'off'→null
  cover_path,                                         // resolved above
  metadata: Object.keys(metadata).length ? metadata : null,
  lexicon: Object.keys(lexicon).length ? lexicon : null,
});
// `bitrate` is omitted (optional, audiobook.ts:63) — unchanged from today.
```

### `longformRender` request body — Stories export, EXTENDED (`generateAll`, `:368-371`)
```ts
// today (:368-371): longformRender({ chapters, format })  — DROPS metadata/loudness/cover_path
const meta     = useAppStore.getState().meta;
const loudness = useAppStore.getState().loudness;
const coverRef = useAppStore.getState().coverRef;
const metadata = Object.fromEntries(                   // same empty-filter as onCreate (J3)
  Object.entries(meta).filter(([, v]) => v && v.trim()));

await longformRender({                                 // LongformRenderBody, audiobook.ts:100-108
  chapters,                                            // from storyToSpans(usable, cast) (:363)
  format: exportFormat === 'mp3' ? 'mp3' : 'm4b',      // unchanged (:370)
  loudness: loudness === 'off' ? null : loudness,      // NEW (J2)
  cover_path: coverRef?.serverPath ?? null,            // NEW (C2)
  metadata: Object.keys(metadata).length ? metadata : null,  // NEW (J3: null when empty → no wire regression)
});
// NOTE: chapter-level `speed` is NOT a LongformRenderBody.chapters[] field; speed
// rides at span level only (LongformRenderBody.chapters[].spans[].speed?, audiobook.ts:101).
```

### SSE event vocabulary (read-only contract — both editors, UNCHANGED)
Both `POST /audiobook` and `POST /longform/render` stream through the **same** backend generator `_render_longform_sse` (`backend/api/routers/audiobook.py:345`), so the event types are identical. Each frame is a `data: <json>\n\n` line; parsed by `parseSSELine` (`sseParse.js:29`). Verified emit sites in `audiobook.py:386-474`:

```ts
type LongformSSEEvent =
  | { type: 'started';       job_id: string; chapters: number }                 // :412
  | { type: 'chapter';       index: number; total: number; title: string;       // :430
      duration_s: number; cached: boolean }
  | { type: 'chapter_error'; index: number; total: number; title: string;       // :424
      error: string }
  | { type: 'assembling' }                                                       // :438
  | { type: 'done';          output: string; chapters: number; duration_s: number; // :463-465
      cached_chapters: number; failed_chapters: number[] }
  | { type: 'error';         error: string };                                    // :386,:390,:435,:474
```
- `AudiobookTab.onCreate` consumes `started`→`progress.total`, `chapter`/`chapter_error`→`progress.current`, `assembling`→`progress.assembling`, `done`→`output`+`{cached_chapters, failed_chapters}`, `error`→`error` channel (`:151-167`). **Unchanged.**
- `StoriesEditor.generateAll` consumes `started`→`total`, `chapter`/`chapter_error`→`exportPct`, `done`→`output`, `error`→`throw` (`:383-391`). **Unchanged.**
- `output` is a filename under `OUTPUTS_DIR` (`audiobook.py:446-447`, e.g. `audiobooks_p_xxx.m4b`), served via `audioUrl(output)` (`AudiobookTab.jsx:342`; `generate.ts` `audioUrl`). Not a project field — transient render result, never persisted (F1).

### Deprecated aliases (one-PR bridge)
```ts
export type StoryProject = LongformProject;            // alias for storiesSlice.ts:28
export const createStoriesSlice = createLongformSlice; // alias for storiesSlice.ts:66
export type StoriesSlice = LongformSlice;              // alias for storiesSlice.ts:36
// The record type was unified, but the persisted collection key remains
// `storyProjects` for compatibility. Schema v9 moves that collection to the
// `omnivoice.longform` IndexedDB payload without renaming it.
```

### Pre-adapter Zustand projection vs durable storage

`partialize` still projects the complete in-memory long-form state so Zustand's
migration and slice API remain unchanged:

```ts
{
  storyTracks: s.storyTracks,
  cast: s.cast,
  storyProjects: s.storyProjects, // compatibility key retained
  script: s.script,
  meta: s.meta,
  lexicon: s.lexicon,
  voiceCast: s.voiceCast,
  currentProjectId: s.currentProjectId,
  coverRef: s.coverRef,
  outputFormat: s.outputFormat,
  loudness: s.loudness,
  defaultVoice: s.defaultVoice,
  projectMode: s.projectMode,
}
```

The schema-v9 storage adapter then splits that projection. IndexedDB
`omnivoice.longform/documents/workspace` owns the unbounded payload
(`storyTracks`, `cast`, `storyProjects`, `script`, `meta`, `lexicon`,
`voiceCast`). localStorage `omnivoice.app` receives only bounded fields such as
`currentProjectId`, `coverRef`, output preferences, app preferences, and ids.
Transient track fields are stripped while constructing the IndexedDB record.

### Migration (v4 → v5), FULL `migrate` fn body (replaces `index.ts:120-130`)
```ts
migrate: (persisted, version) => {
  if (!persisted || typeof persisted !== 'object') return {} as Partial<AppStore>; // existing :121 (D1)
  let p = persisted as any;
  if (version < 4) {
    // existing v1→v4 passthrough (:122-128) — all old keys have slice defaults.
    // No transform needed; fall through to the < 5 branch below in the same call.
  }
  if (version < 5) {
    const rawProjects = Array.isArray(p.storyProjects) ? p.storyProjects : [];     // (D2)
    p.storyProjects = rawProjects
      .filter((sp: any) => sp && typeof sp === 'object')                            // (D3) drop non-objects
      .map((sp: any) => ({
        ...sp,
        // Validate AFTER spread: malformed values cannot erase usable defaults.
        id: typeof sp.id === 'string' && sp.id.trim() ? sp.id : genProjectId(),
        name: typeof sp.name === 'string' && sp.name.trim() ? sp.name : 'Untitled',
        mode: sp.mode === 'audiobook' ? 'audiobook' : 'stories',
        cast: Array.isArray(sp.cast) ? sp.cast : [],
        tracks: Array.isArray(sp.tracks) ? sp.tracks : [],
        updatedAt: typeof sp.updatedAt === 'number' && Number.isFinite(sp.updatedAt)
          ? sp.updatedAt : 0,
      }));
    // Loose working fields seed to defaults; storyTracks/cast pass through (D6).
    p.mode = 'stories';
    // script/meta/lexicon/coverRef/outputFormat/loudness/defaultVoice are absent →
    // they fall through to slice-init SLICE_DEFAULTS on store creation (no need to
    // set them here; the slice supplies them). currentProjectId left as-is — a
    // dangling id is harmless: loadProject(:103) + currentProject(:217) no-op (E5).
    return p as Partial<AppStore>;
  }
  return p as Partial<AppStore>; // existing :129 — also covers version > 5 (downgrade-then-upgrade)
}
```
> `genProjectId` is imported from `longformSlice.ts` into `index.ts` (Integration → `index.ts:36-37`). **No user-input regex anywhere in this fn** — only `typeof`/`Array.isArray`/object-spread/`delete` (so the migrate carries no `js/redos` exposure; Constraints → CodeQL).

### `onOpenStory` routing (App.jsx:1054 — exact body, E1–E5)
```ts
onOpenStory={(id) => {
  const rec = useAppStore.getState().storyProjects.find((x) => x.id === id);
  if (!rec) return;                                  // E5: id no longer resolves → stay on Projects
  loadStoryProject(id);                              // = loadProject(id); sets working mode from rec
  setMode(rec.mode === 'audiobook' ? 'audiobook' : 'stories');  // E1/E2/E3 default-safe
}}
```

### Persisted storage shapes (schema v9)
```jsonc
// localStorage["omnivoice.app"] — bounded compact envelope
{
  "state": {
    "currentProjectId": "p_4f9ab2c1",
    "coverRef": null,
    "outputFormat": "m4b",
    "loudness": "off",
    "defaultVoice": null,
    "projectMode": "stories"
    // … plus bounded fields from other slices; no manuscript/project arrays
  },
  "version": 9
}

// IndexedDB omnivoice.longform / documents / workspace — durable payload
{
  "schema": 1,
  "revision": 12,
  "payload": {
    "storyTracks": [ /* sanitized StoryTrack[] working */ ],
    "cast": [ /* CastMember[] */ ],
    "storyProjects": [ /* LongformProject[]; compatibility key retained */ ],
    "script": "",
    "meta": {},
    "lexicon": {},
    "voiceCast": {}
  }
}
```

## Test plan

All frontend tests run via `bunx vitest run` (package.json script `"test": "vitest run"` at `frontend/package.json:14`; `vitest ^4.1.5` at `:75`). Per MEMORY: the local loop must include `bunx vitest run`. pytest is irrelevant here — no backend change — **except** the project-wide `tests/test_no_hardcoded_cjk.py` gate (runs in CI), which scans git-tracked `.jsx`/`.ts` source: the renamed `longformSlice.ts` and modified `AudiobookTab.jsx` are scanned (not allowlisted), so the local loop should also include a quick CJK self-check on changed files before push (see Constraints → Localization).

1. **`longformSlice.test.ts` — backward-compat (must stay green):** the entire existing `storiesSlice.test.ts` suite (`:16-114` — two `describe` blocks: `storiesSlice` `:16-60`, `storiesSlice — projects` `:62-114`) ported verbatim against the renamed slice. The harness (`:4-10`) and `track()` helper (`:12-14`) carry over (rename `createStoriesSlice` import → `createLongformSlice`, while `.storyProjects` reads remain unchanged). Proves cast/track/project behavior is unchanged. Specifically the transient-strip test (`:107-113`) and the in-place-update test (`:73-81`) are load-bearing.
2. **New project-shape tests:**
   - `saveProject` snapshots `meta`, `lexicon`, `script`, `coverRef`, `outputFormat`, `loudness`, `defaultVoice`, `mode` into the record (extends the existing snapshot test at `:63-71`). Assert the exact saved-record shape matches `LongformProject` (every field present).
   - Saving twice with `currentProjectId` updates metadata in place, no duplicate (H1) — `storyProjects.length === 1` after two saves with mutated `meta`.
   - `loadProject` restores all of the above into working state *and* sets working `mode` from the record (extends `:83-94`).
   - `newProject('audiobook')` seeds `mode==='audiobook'` + blank working surface; `newProject()` defaults to `'stories'` and clears tracks/cast (preserve the no-arg behavior of `:106`).
   - transient track-field stripping still holds (port of `:107-113`; `snapshotTracks` at `storiesSlice.ts:62-64`).
   - `convertMode('audiobook')` flips working `mode` without mutating `cast`/`tracks`/`script` (G1); `convertMode` to the current mode is a no-op (G2); `convertMode('bogus' as any)` is ignored (G3).
   - `setProjectMeta({title})` merges (does not clear `author`) (I1); `setOutputPrefs({loudness})` merges (`outputFormat`/`defaultVoice` untouched) (I2); `setOutputPrefs({defaultVoice: null})` overwrites to `null` (the `!== undefined` rule); `setLexicon(dict)` replaces (I3).
3. **Migration test (the load-bearing one — the localStorage "alembic upgrade" gate):** construct a v4 persisted blob `{ storyProjects: [{id:'x',name:'A',tracks:[...],cast:[...],updatedAt:1}], storyTracks, cast, currentProjectId:'x' }`, run the `migrate` fn with `version=4`, assert: `storyProjects[0]` has the original `id/name/cast/tracks/updatedAt` plus `mode:'stories'` + metadata defaults (`script:''`, `meta:{}`, `lexicon:{}`, `coverRef:null`, `outputFormat:'m4b'`, `loudness:'off'`, `defaultVoice:null`); the compatibility key remains; no throw. **Malformed-input cases (D1–D6, each asserts no-throw):**
   - non-object `persisted` (string / null / number) → `{}`.
   - `storyProjects` not an array (an object / string) → `storyProjects: []`.
   - `storyProjects` array containing `null` / a string / `{}` → non-objects dropped; the `{}` entry gets a synthesized `id` (truthy `p_…`) + `name:'Untitled'` + default content.
   - a v2/v3 blob with **no** `storyProjects` key → `storyProjects: []`.
   - `version: 6` blob → returned unchanged (no re-migration; `storyProjects` left as-is).
   - `currentProjectId` pointing to a now-missing project → no throw; a later `loadProject(id)` no-ops.
   - Note: `migrate` is defined inline in the persist config (`index.ts:120-130`) — extract it to a named export (e.g. `migrateAppStore`) or test via a re-created store to exercise it.
4. **AudiobookTab persistence test (component or store-level):** set `meta.title`, switch mode away and back (simulate by re-reading store) → title persists. Assert `audiobookGenerate` is still called with the **exact body shape** of `AudiobookGenerateBody` (`text`/`default_voice`/`format`/`loudness`/`cover_path`/`metadata`/`lexicon`) — only the *source* of each field changed. Assert the whitespace-only-meta field is dropped from the wire body (J1), `loudness:'off'→null` (J2), and a CJK `meta.title` round-trips byte-for-byte through the store (J4).
5. **Stories export carries metadata:** assert `generateAll` (`StoriesEditor.jsx:360-402`) now passes `metadata`/`loudness`/`cover_path` to `longformRender` (matching the extended body above) when the project has them (regression guard that unification wired the payoff; today `:368-371` passes only `chapters`+`format`). Also assert a **metadata-less** Stories project still sends `metadata: null` (J3 — no wire regression). Assert `chapters[]` carries no top-level `speed` (span-level only).
6. **Garbage/guard suite:** covered under (3). Plus: a v4 blob whose persisted store has `cast` missing → working `cast` falls to `DEFAULT_CAST` (D6).
7. **Stale-carryover test (A — the lifecycle invariant):** save Stories project A with `meta.title='A-title'`, save Stories project B with empty meta; `loadProject(A)` then `loadProject(B)` → working `meta.title` is `''` (not `'A-title'`); same for `script`/`lexicon`/`coverRef`/prefs (A1). `newProject()` after an edited session → all working fields at `SLICE_DEFAULTS` (A2). Loading a v4-migrated project (no `script`/`meta` keys) → working fields are defaults, never `undefined` (A4).
8. **Cover round-trip test:** save with `coverRef={filename,serverPath}` → simulate reload (re-create store from persisted) → `coverRef` survives; assert `clearCover` path nulls `coverRef` (C5); assert the cover-path resolution reuses `coverRef.serverPath` when `coverFile` is null and prefers `coverFile` upload when present (C2 — unit-test the resolution expression).
9. **`bunx vitest run` full suite** stays green (existing storyToSpans / storyExport / storyCast / importStory / sseParse tests untouched but must pass).
10. **CJK source-literal gate (CI, project-wide):** `tests/test_no_hardcoded_cjk.py::test_no_hardcoded_cjk_outside_locales` must stay green after the rename — confirm `frontend/src/store/longformSlice.ts` and `frontend/src/pages/AudiobookTab.jsx` contain **no** hardcoded CJK source literals (neither file is on `_ALLOWED_FILES`; `StoriesEditor.jsx` already is, for its existing functional CJK). Run it in the local loop before push (`uv run pytest tests/test_no_hardcoded_cjk.py -q`), not just rely on CI.

## Constraints (from CLAUDE.md)

Each relevant hard rule, and exactly how this task satisfies it:

- **Backward-compatible project data (no manual migration; DB→alembic / browser storage→versioned migration).** Existing v4–v8 `storyProjects` envelopes keep working with zero user action. The v5 shape migration validates nested project fields after applying defaults; schema v9 then commits the complete long-form payload to IndexedDB before compacting localStorage. Neither browser schema touches `omnivoice_data/` SQLite, so no alembic revision applies. Malformed data fails to defaults or the recovery gate, never a white screen.

- **Cross-platform parity (default behavior identical on macOS/Windows/Linux; platform-only features behind opt-in).** The split localStorage/IndexedDB adapter is standard browser behavior on every supported shell. Tauri's widget is explicitly read-only; browser main tabs use revision arbitration for collisions. The Windows-only WebView repair clears cache-only directories and preserves both storage databases.

- **Local-first guarantee (no cloud, no accounts, no API keys, no third-party telemetry; app fully functional offline).** Bounded fields live in localStorage and manuscript/project payloads live in local IndexedDB. Neither layer makes a network call. Existing cover/render requests still target the configured VoiceStudio backend.

- **CodeQL — `js/redos` / polynomial-ReDoS on user-input regex (and `py/*`).** Verified: `security.yml:94-96` runs CodeQL `security-and-quality` on **both `python` and `javascript-typescript`** (`:74`), with `*.test.{ts,tsx,js,jsx}` excluded from analysis (`:102-105`). This is a **frontend-only** task, so the relevant query is the **JS/TS ReDoS** one, not `py/*`. **This task introduces no new regex over user-pasted text** — the only new "validation" is the two-value `rec.mode === 'audiobook' ? … : …` literal-equality check (E3) and the `convertMode` membership guard (G3), and the migrate fn uses only `typeof`/`Array.isArray`/spread (no regex). Existing parsers (`parseScript.js:14-39` builds `attributionName` regexes from a `TAG_VERBS` alternation and runs them on user-pasted script via `autoCast`) are **untouched** here. ⚠️ **Hand-off flag for #24:** #24 *will* call `parseScript`/`importStory` on the `script` string to populate `tracks`. If #24 adds or modifies any regex that consumes the user `script`, it must run the ReDoS discipline from MEMORY (`codeql-redos-regex`): no overlapping `\s*`/`.+`, exclude both delimiters in `[^x]*`, atomic groups OK on py≥3.11 — and re-audit the existing `parseScript` alternations under the same lens. #31 carries the ReDoS *zero-delta*; #24 inherits the *duty*.

- **Localization (no hardcoded non-English UI text; all strings via `t()` into `i18n/locales/`; functional CJK only via the test allowlist).** Verified **21** locale files (`ls frontend/src/i18n/locales/ | wc -l = 21`: ar, de, en, es, fr, hi, id, it, ja, ko, nl, pl, pt, ru, sv, th, tr, uk, vi, zh-CN, zh-TW). 31a/31b reuse existing keys (`audiobook.*` / `stories.*` / `projects.*`); any new toast/label (`audiobook.save_project`, `audiobook.project_saved`, `audiobook.untitled`, optional cover-set label) goes through `t()` and is added to **all 21** locales **in the same PR** (a key in `en.json` but missing elsewhere is a failure, not backlog). The store's `name || 'Untitled'` fallback is an English **non-UI last-resort sentinel** (never the displayed default — each editor passes a localized `t('…untitled')`); acceptable as a code-level guard. **CJK-test note:** `tests/test_no_hardcoded_cjk.py` scans git-tracked `.jsx`/`.ts` source; the renamed `longformSlice.ts` and modified `AudiobookTab.jsx` are scanned and are **not** on `_ALLOWED_FILES` (`StoriesEditor.jsx` already is, for its existing functional CJK) — so any hardcoded CJK literal in the new/changed code fails CI. User-typed metadata in CJK (book titles, etc.) is **runtime data, exempt** (it's not a source literal; J4). Run the gate locally before push (Test #10).

- **Docs-sync (any change to README/CONTRIBUTING/SECURITY/SUPPORT/LICENSE/`docs/**` ships in the same PR).** Verified there is **no user-facing doc** describing Stories/Audiobook persistence in `docs/` (no `docs/features/audiobook.md` or `docs/features/stories.md`; `docs/features/` has only `diarization.md`; `docs/features.yaml` mentions neither). Internal specs exist (`docs/specs/2026-06-13-stories-audiobook-maturity.md`, `docs/superpowers/specs/2026-05-30-stories-editor-studio-design.md`) — update those in-PR **only if** the persistence model materially changes their description; no end-user install/feature doc requires a change. State the conclusion explicitly in the PR body ("no user-facing doc impact; updated internal spec X" or "no doc impact").

- **Versioning (continuous-to-main patch; main = latest release + 1; no RCs, no codenames, no minor/major bump, no version files touched).** This task ships **no version bump** — main already rolls at the next patch (`v0.3.6`); `tauri.conf.json` / `Cargo.toml` / `pyproject.toml` are **untouched**. No `-rc` tag, no `v0.4` deferral: 31a/31b are absorbed into the open v0.3.x line continuous-to-main; 31c is the **#24** task (a separate open issue absorbed into the same line), not a re-versioned defer. (Note: the zustand-persist `version: 4→5` bump is the *localStorage schema* version, entirely independent of the app/release version — do not confuse the two.)

- **GSD workflow (file-changing tools only inside a GSD command).** Per CLAUDE.md, the implementing change must go through a GSD entry point — `/gsd-execute-phase` for this planned phase work — not raw `Edit`/`Write` outside a GSD workflow.

## Dependencies

- **No new npm packages.** Uses existing `zustand` + `zustand/middleware` (`persist`, `createJSONStorage`) already imported in `store/index.ts:17-18`.
- **No new Python deps** (CLAUDE.md confirms Capabilities 1/2/3/5 add none; this task touches none of them).
- **Soft dep on #24:** the `convertMode` action and mode-aware routing are *defined* here but the content-transform (`scriptToTracks`, `tracksToScript`) and the toggle button are **#24's** to fill in. The existing `utils/storyToSpans.js:21-54` is the stories→spans direction; the reverse parser would reuse `utils/parseScript.js` / `utils/importStory.js` (both already imported by `StoriesEditor.jsx:19-20`, and `parseScript` already used by `autoCast` at `:176`). #24 inherits the **ReDoS-review duty** on `parseScript`'s `TAG_VERBS` alternation regexes if it runs them on the unified `script` (Constraints → CodeQL).
- **Adjacent tasks (coordinate, don't block):** #22 (shared `<VoiceSelector>`) and #27 (unify longform parsers) touch the same two editors; land #31 store first so #22/#27 build on the unified state. (#27 — unifying the 3 longform parsers — is the natural owner of the `parseScript` ReDoS audit; #31 keeps the regex surface unchanged and hands the audit forward.)

## Risk

| Risk | Severity | Mitigation |
|---|---|---|
| Migration drops/corrupts existing saved Stories projects (the localStorage data-integrity / "no manual migration" constraint) | High | Dedicated migration unit test with a realistic v4 blob **plus** malformed-input cases (non-object, non-array `storyProjects`, null/garbage entries, missing keys, version >5, dangling `currentProjectId` — D1–D6); spread `...sp` *last* so original fields always win over defaults; `migrate` returns upgraded partial, never throws (matches the existing guard at `index.ts:121` + philosophy at `:116-119`). Full fn body pinned in API/data shapes → Migration. This `migrate` is the localStorage analog of an alembic upgrade — Test #3 is the gate. |
| Stale metadata leaks across project loads (the new working fields aren't reset) | High | The working-state reset contract: `loadProject`/`newProject` (re)set the **entire** working surface with default-fill (`SLICE_DEFAULTS`) every time (exact action bodies pinned); dedicated stale-carryover test (Test #7, edge cases A1–A4). This is the subtlest regression the task introduces. |
| Changing the `storyProjects` compatibility key breaks the 6 consumers | Medium | Retain `storyProjects` across the slice, persistence adapter, `StoriesEditor.jsx`, `Projects.jsx`, and `App.jsx`; the test harness reads `.storyProjects` directly (`test:19,:67,:79,:103`), so its unchanged assertions pin the contract. |
| Hardcoded CJK / missing-locale string sneaks into the renamed/changed source and trips CI | Medium | `tests/test_no_hardcoded_cjk.py` scans the (non-allowlisted) `longformSlice.ts` + `AudiobookTab.jsx`; route every new label through `t()`, add new keys to **all 21** locales in-PR, run the gate locally (Test #10). User-typed CJK metadata is exempt runtime data (J4). |
| Conflating client `storyProjects` with `Projects.jsx` server-side `longformJobs` | Medium | These are distinct: `longformJobs` (`Projects.jsx:102,198-211`) are completed backend render jobs; `storyProjects` are client projects flowing through the same-named prop (`:79,146-160`) and stored durably in IndexedDB. Do not touch `longformJobs`. |
| Cover image can't round-trip through localStorage; stale `serverPath` | Medium | Persist only `CoverRef` (filename + `serverPath` from `audiobookUploadCover` `{path}`, `audiobook.ts:85-90`), not bytes. On reload show the filename; re-render reuses `serverPath` as `cover_path` (C2). Stale/GC'd/invalid `serverPath` (C3) surfaces a generic backend error SSE event (`audiobook.py:474`) via the existing channel, never crashes; re-picking always works. `clearCover` nulls `coverRef` (C5). Blob preview stays transient by design (matches `AudiobookTab.jsx:57-64`). serverPath is a *local* path — no cloud (Local-first). |
| `_trackId` reseed effect (`StoriesEditor.jsx:135-140`) doesn't re-run on cross-session `loadProject` → new-line id collision | Medium | Latent today (dep array `[]`, single-mount), made reachable by Projects-list load. Change dep array to `[currentProjectId]` or reseed in the add path; called out in Integration. |
| #24 imports the existing `parseScript` regexes onto the unified `script` without a ReDoS pass | Medium | #31 carries **zero** new user-input regex (Constraints → CodeQL). Explicitly hand the ReDoS-review duty to #24/#27 in Dependencies + a code comment on the `convertMode` seam so a future PR doesn't wire `parseScript` over user `script` blind to the `js/redos` gate. |
| In-flight render abandoned on tab switch / reload (no resume) | Low | Explicitly NOT resumed in v1 (F1–F3); inputs survive via store, transient render flags (component `useState`, never in `partialize`) never persist, so no ghost spinner. Backend-job resume is `longformJobs` territory, out of scope. |
| Long manuscripts or many projects exhaust localStorage quota | High | Schema v9 moves every unbounded long-form field to IndexedDB and leaves only bounded preferences/ids in `omnivoice.app`. Migration commits IndexedDB before trimming the v8 fallback, and quota-failure tests prove the durable record still restores. |
| Controlled-input `undefined → string` warning on v4-migrated projects | Low | Every metadata `<input value>` binds to a guaranteed default (A4) — read `meta.title ?? ''`, never bare; slice init + load-time default-fill ensure `meta.*` are always strings, `lexicon`/`coverRef` always object/null. |
| Scope creep into #24 | Medium | Hard line: 31c (mode toggle/convert UI + content transforms `scriptToTracks`/`tracksToScript`) is explicitly out; ship 31a/31b only. The "defer until #24" caveat is honored by shipping just the store + Audiobook binding whose shape is already determined by the two existing editors. |
| Two content models (`tracks` vs `script`) drift in a single project | Low (v1) | v1 keeps one authoritative per `mode`; only the matching editor binds working state. Cross-editing is #24's concern with explicit convert. `convertMode` flips the flag only (G1). |

## PR slices

- **PR 31a — `longformSlice` + migration (store-only, no UI behavior change).** Rename `store/storiesSlice.ts`→`longformSlice.ts`, generalize types (`StoryProject`→`LongformProject` from `:28-34`; `StoriesSlice`→`LongformSlice` from `:36-51`), export `SLICE_DEFAULTS` + `genProjectId`, add new working fields/actions (with the pinned signatures + bodies: merge semantics I1–I3, reset contract in `loadProject`/`newProject`, `convertMode` placeholder), alias bridge, bump persist `version` 4→5 (`index.ts:115`) + `version < 5` migrate branch with malformed-data defenses (`index.ts:120-130`, D1–D6, full fn body pinned), update the 4 import/type/spread sites in `index.ts` (`:36-37,45,63`) + the partialize block (`:107-113`, pinned diff), port + extend `storiesSlice.test.ts`→`longformSlice.test.ts` (backward-compat + new-shape + migration + stale-carryover tests). Stories and Audiobook behave exactly as today (Audiobook still local-state — not yet bound). Fully green `bunx vitest run` **and** `tests/test_no_hardcoded_cjk.py` (renamed slice scanned). Lowest-risk, fully reversible. *This is the "land now" core.*
- **PR 31b — Bind Audiobook to the store + Stories export metadata payoff.** Replace `AudiobookTab.jsx` local `useState` (`:21,22,34,35,36-38` → `text/defaultVoice/format/loudness/meta`) with store bindings (pinned selectors); keep lexicon **editable rows** local, hydrate dict→rows on mount + flush rows→`setLexicon` on change (B3); wire `clearCover`→`setCoverRef(null)` (C5) and `onCreate` cover-path resolution to reuse `coverRef.serverPath` (C2, pinned expression); Audiobook projects now save/load in the shared Projects list; make `App.jsx` `onOpenStory` (`:1054`) mode-aware + load-result-aware (E1–E5, pinned body) via `setMode` (`:110`); fix the `_trackId` reseed effect dep array (`StoriesEditor.jsx:135-140`); wire `StoriesEditor.generateAll` (`:360-402`, currently `:368-371`) to pass `metadata/loudness/cover_path` to `longformRender` with the empty-filter (J3, pinned body); branch Projects.jsx story-card icon/subtitle on `sp.mode` (`:146-160`). Adds an Audiobook "Save project" affordance reusing the Stories projects-panel pattern. i18n (save-project / project-saved / untitled / cover-set keys × **21** locales, in-PR) + docs-sync verdict in PR body. CJK gate green (AudiobookTab scanned). *Ships the user-visible win (Audiobook persistence).*
- **PR 31c — (#24 hand-off, NOT in this task) mode toggle + convert.** Implement `convertMode` content transforms (`scriptToTracks`/`tracksToScript`, reusing `parseScript`/`importStory`/`storyToSpans`), the in-editor "Open in the other editor / Convert" button, and the cross-mode open behavior. **Carries the ReDoS-review duty** on any regex run over the user `script` (Constraints → CodeQL). Tracked under #24.

## Acceptance criteria

1. `bunx vitest run` is green, including the ported backward-compat suite (`storiesSlice.test.ts:16-114`), the new migration/shape tests, the malformed-migration cases (D1–D6), and the stale-carryover test (A1–A4); and `tests/test_no_hardcoded_cjk.py` stays green with the renamed/modified source scanned (Test #10).
2. A user with existing saved Stories projects (localStorage at persist `version: 4`) reloads the app and sees the **same projects, same names, same cast/tracks**, openable in Stories with zero prompts (manual + automated migration test). A user with a *corrupted* persisted blob still boots to defaults (no white-screen crash).
3. In Audiobook, entering title/author/narrator/genre, a lexicon row, format, and loudness, then switching tabs and back (or reloading), **preserves all of it**. (Today: lost — all live in `AudiobookTab.jsx:21-48` `useState`.) Half-typed lexicon rows are not persisted as junk (B3). A CJK book title round-trips byte-for-byte (J4).
4. An Audiobook project can be **saved by name** and reappears in the shared Projects list (`Projects.jsx:146-160`) with an audiobook-appropriate card; reopening it routes to the Audiobook tab (`App.jsx:1077-1082`) with fields restored. Opening a `stories`-mode project still routes to Stories. Opening an id that no longer resolves does not route to a blank editor (E5).
5. A Stories full export (`StoriesEditor.generateAll`, `:360-402`) now sends book `metadata`/`loudness`/`cover_path` to `/longform/render` (matching the pinned `LongformRenderBody`) when the project has them (verified by the export test + a manual render whose output m4b carries the title tag); a metadata-less Stories project sends `metadata: null` with no wire regression (J3).
6. Loading project B after editing project A leaves **no field of A's metadata** in B's working state; starting a `newProject()` after an edited session yields all-default working state (A1, A2). v4-migrated projects bind controlled inputs to defined defaults (no React controlled/uncontrolled warning) (A4).
7. Cover round-trips as a reference: filename/serverPath survive reload, the persisted path is reused on re-render (C2), removing the cover nulls the reference (C5), and a stale/invalid server path degrades to a surfaced error, never a crash (C3).
8. The store exposes a `convertMode` action (idempotent, content-preserving) and `mode`-aware `loadProject`/`newProject`, with `StoryProject`/`StoriesSlice`/`createStoriesSlice` aliases still resolving (no broken imports across the 6 `storyProjects` consumers) — the documented seam #24 will consume.
9. An interrupted render (tab switch / reload mid-generate) abandons the stream without a persisted ghost spinner; inputs survive, the render does not (F1–F3).
10. **Constraints satisfied:** no new platform branches (cross-platform parity — identical on macOS/Windows/Linux); no cloud/account/telemetry/credential and only the existing local backend is contacted (local-first); zero new user-input regex (no `js/redos` delta; #24 inherits the parser ReDoS duty); all new UI strings via `t()` into all 21 locales and the CJK source gate green (localization); doc-impact verdict stated in the PR body (docs-sync); no app version-file change (versioning — the persist `version: 4→5` bump is the localStorage schema version, not the release version); change lands via a GSD command (GSD workflow).

---

### Key file references (verified)
- `/home/pal/Desktop/VoiceStudio/frontend/src/store/storiesSlice.ts` — slice to generalize. Types `:12-51` (`StoryTrack` `:12-19`, `CastMember` `:21-26`, `StoryProject` `:28-34`, `StoriesSlice` `:36-51`); `DEFAULT_CAST` `:53-55`; `genProjectId` `:57-59` (must be **exported** for the migrate fn); `snapshotTracks` `:62-64`; `createStoriesSlice` `:66`; init `:67-70`; actions `:71-113` (`loadProject` `:101-105` sets only tracks/cast/currentProjectId today; `newProject` `:106` clears only those — the reset-contract gap; `saveProject` upsert `:95-98`; `loadProject` no-op guard `:103`). **No hardcoded CJK present; not on the CJK allowlist** — must stay clean after rename.
- `/home/pal/Desktop/VoiceStudio/frontend/src/store/index.ts` — persist config `:55-132`; slice import `:36-37`; `AppStore` type `:45`; spread `:63`; partialize `:72-114` (Stories block `:107-113`, transient-strip comment `:107-108`, `storyTracks` strip `:109-110`, `cast` `:111`, `storyProjects` `:112`, `currentProjectId` `:113`); `version: 4` `:115`; "Upgrade > crash" comment `:116-119`; `migrate` `:120-130` (non-object guard `:121` returns `{}`, `version < 4` passthrough branch `:122-128`, final passthrough `:129`). Confirmed verbatim against source.
- `/home/pal/Desktop/VoiceStudio/frontend/src/pages/AudiobookTab.jsx` — local-state metadata to persist `:21-49` (`text` `:21`, `defaultVoice` `:22`, `format` `:34`, `loudness` `:35`, `meta` `:36-38`, `coverFile`/`coverPreview` `:39-40`, `lex` `:42`); `lexDict` filter `:43-45`; `setLexRow`/`addLexRow`/`removeLexRow`/`setMetaField` `:46-49`; cover pick/clear/cleanup `:51-64`; `onPreview` `:68-78`; `onImport` `:80-95` (sets `text` `:88`); `onPreviewChapter` `:97-111` (`audiobookPreviewChapter` body `:102-105`); `onCreate` `:113-175` (cover upload `:121-124`, metadata empty-filter `:126-128`, `audiobookGenerate` body `:130-138` with `loudness→null` `:134`, SSE handling `:142-169`, error path `:165-167,:171`, `finally` `:172-174`); `format`/`loudness`/`defaultVoice` `<select>` onChange `:221,:231,:239`; controlled metadata inputs `:274-286`. **Scanned by the CJK gate; not allowlisted** — new UI text must be `t()`-keyed.
- `/home/pal/Desktop/VoiceStudio/frontend/src/components/StoriesEditor.jsx` — store selectors `:115-127` (`setTracks` proxy `:130-133`); `_trackId` mount-only reseed `:135-140` (dep array `[]` at `:140` — cross-load collision risk); `currentProject` `:217`; `saveCurrent`/`newStory`/`openProject` `:223-228`; full export `generateAll` `:360-402` (current call `:368-371`, SSE handling `:383-391`, error toast `:396-398`); `parseScript`/`importToText` imports `:19-20`; `autoCast` uses `parseScript` `:176`. **On the CJK allowlist (`tests/test_no_hardcoded_cjk.py:56`)** for existing functional CJK.
- `/home/pal/Desktop/VoiceStudio/frontend/src/utils/parseScript.js` — `TAG_VERBS` alternation `:14-16`; `normalizeSpeaker` regexes `:19-26`; `attributionName` builds 4 `RegExp`s from `${V}` and runs them on user text `:28-39`; `parseScript` paragraph split `:42-56`. **The ReDoS-review surface #24/#27 inherits** — untouched by #31.
- `/home/pal/Desktop/VoiceStudio/frontend/src/api/audiobook.ts` — `AudiobookSpan` `:3-7`; `AudiobookChapter` `:8-12`; `AudiobookPlan` `:13-17`; `audiobookPlan` `:20-29`; `AudiobookPreview` `:31-36`; `audiobookPreviewChapter` `:39-48`; `AudiobookMetadata` (6 optional strings, mirror for `LongformMeta`) `:51-58`; `AudiobookGenerateBody` `:60-69` (`loudness?: 'off'|'acx'|'podcast'|null` `:65`); `audiobookGenerate` (returns raw SSE `Response`) `:76-82`; `audiobookUploadCover` (returns `{ path: string }`) `:85-90`; `audiobookImport` (returns `{ text, chapters }`) `:93-98`; `LongformRenderBody` (accepts `metadata`/`loudness`/`cover_path`, span-level `speed?`) `:100-108`; `longformRender` (returns raw SSE `Response`) `:116-122`.
- `/home/pal/Desktop/VoiceStudio/frontend/src/utils/sseParse.js` — `splitSSEBuffer` (lines + trailing remainder) `:14-18`; `parseSSELine` (tolerant `data:` JSON parse, returns `null` on bad frame) `:29-38`. Used unchanged by both editors.
- `/home/pal/Desktop/VoiceStudio/backend/api/routers/audiobook.py` — shared SSE generator `_render_longform_sse` `:345`; `_emit` (`data: <json>\n\n`) `:383`; event emits — `error` (no chapters) `:386`, `error` (no ffmpeg) `:390`, `started {job_id,chapters}` `:412`, `chapter_error {index,total,title,error}` `:424`, `chapter {index,total,title,duration_s,cached}` `:430`, `error` (all failed) `:435`, `assembling` `:438`, `_safe_cover_path` cover validation `:451`, `done {output,chapters,duration_s,cached_chapters,failed_chapters}` `:463-465`, `error` (generic, no stack leak) `:474`; out_name under `OUTPUTS_DIR` `:446-447`. **The SSE contract both `/audiobook` and `/longform/render` share — read-only here.**
- `/home/pal/Desktop/VoiceStudio/frontend/src/utils/storyToSpans.js` — stories→spans converter `:21-54` (span-level `speed` at `:40,47`; deps: `parseStoryText`, `storyExport`'s `isChapterLine`/`chapterTitle`, `storyCast`'s `effectiveProfile`, `ssmlLite`).
- `/home/pal/Desktop/VoiceStudio/frontend/src/store/storiesSlice.test.ts` — backward-compat contract to port; harness `:4-10` (reads `.storyProjects` at `:19,:67,:79,:103`), `track()` helper `:12-14`, `storiesSlice` block `:16-60`, projects block `:62-114` (snapshot `:63-71`, in-place update `:73-81`, load/new `:83-94`, delete/rename `:96-105`, transient strip `:107-113`). **Excluded from CodeQL (`security.yml:104`) and the CJK source scan (test exemption, `test_no_hardcoded_cjk.py:90-94`).**
- `/home/pal/Desktop/VoiceStudio/frontend/src/pages/Projects.jsx` — `storyProjects` prop `:79`, story-card builder `:146-160` (`tracks`/`chars` derive `:147-148`, subtitle `[story_lines, story_voices].filter(Boolean).join(' · ')` `:153-154`, `Icon: BookOpen` `:157`, `onClick: () => onOpenStory?.(sp.id)` `:158`), `useMemo` dep `:228`; separate server-side `longformJobs` `:102,198-211` (do not touch).
- `/home/pal/Desktop/VoiceStudio/frontend/src/App.jsx` — `storyProjects`/`loadStoryProject` selectors `:251-252`; `setMode` selector `:110`; `ProjectsPage` render `:1046-1056` (`storyProjects` prop `:1051`, `onOpenStory={(id)=>{loadStoryProject(id); setMode('stories');}}` `:1054`); stories/audiobook mode routing `:1071-1082`.
- `/home/pal/Desktop/VoiceStudio/frontend/package.json` — `test: "vitest run"` `:14`; `vitest ^4.1.5` `:75`.
- `/home/pal/Desktop/VoiceStudio/frontend/src/i18n/locales/` — **21** locale files (verified `ls | wc -l = 21`); Audiobook keys under `audiobook.*` (en.json block `:112`), Stories under `stories.*` (en.json block `:26`: `untitled:54`, `projectSaved:58`), Projects card keys under `projects.*` (`untitled_story`/`story_lines`/`story_voices`, used `Projects.jsx:152-154`).
- `/home/pal/Desktop/VoiceStudio/tests/test_no_hardcoded_cjk.py` — project-wide CJK gate; `_ALLOWED_FILES` `:43-80` (includes `StoriesEditor.jsx:56`, **not** `AudiobookTab.jsx` or `longformSlice.ts`); `_is_allowed` test-fixture exemption `:83-95`; scans git-tracked source `:98-122`; `test_no_hardcoded_cjk_outside_locales` `:125`.
- `/home/pal/Desktop/VoiceStudio/.github/workflows/security.yml` — CodeQL matrix `python` + `javascript-typescript` `:74`; `security-and-quality` query pack `:95-96`; test-file `paths-ignore` `:102-105`; analyze `:107-110`. Confirms the `js/redos` query gates this frontend task and excludes `*.test.*`.
