# Voice Studio Unification — spec

**Goal:** Collapse the separate **Clone** and **Design** tabs into one profile-centric **Voice** workspace, restack its layout (Prompt over Voice Source in a single column), and move generation history out of the left sidebar into a right-hand, workspace-scoped panel.

Three user-driven decisions (locked):
1. **Full UI consolidation** — one "Voice" workspace; the saved profile is the hub; "from audio" (clone) and "by design" are two ways to *define* the same profile object.
2. **History moves right** — left sidebar keeps **Projects + Downloads** only; the per-workspace generation history lives on the right.
3. **Design voice saves a rendered reference WAV** — on save we *try* to synthesize a deterministic sample (the saved seed, 42 when none) and store it as `ref_audio_path`, *and* persist the design params for re-editing. Same mechanism archetypes already use. **Saving never depends on a loaded TTS model** (issue #476): if the engine isn't ready (e.g. fresh model-less Docker image) the row is persisted with the sample *pending* and the deterministic sample is rendered lazily on first preview/use — the row's `vd_states` + `instruct` already make the voice fully usable (synthesis falls back to instruct-only conditioning).

Not in scope: changing the Dub or Stories workspaces beyond giving them the same right-side history panel; realtime/streaming synthesis; voice-mixing.

Constraints honored (from `CLAUDE.md`): existing `voice_profiles` / `generation_history` rows keep working with no manual migration (alembic `0005`, tested upgrade path); behavior is identical on macOS/Windows/Linux (pure frontend layout + backend logic, no platform-specific default); changes ship continuous-to-main; any doc that describes the Clone/Design tabs is updated in the same PR (docs-sync rule).

---

## 0 — Wireframes

**Full window — "From audio" method, a saved profile selected:**

```
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│  ◉ Voice                              ⊙ VoiceStudio                        ● Idle    ⚡ Flush ▾   │
├────────────┬──────────────────────────────────────────────┬────────────────────────────────────┤
│ 📁 Projects│  ⌘ PROMPT                                     │  ↺ HISTORY        [All][Clone][Des]│
│ ⤓ Downloads│ ┌──────────────────────────────────────────┐ │ ┌────────────────────────────────┐ │
│            │ │ hello jack, how are you?▌                 │ │ │ ⬡ CLONE              0:02  ⋯  │ │
│ ▸ Untitled │ │                                          │ │ │ The Storyteller take         │ │
│ ▸ Promo cut│ │                                          │ │ │ ▶ ▕▏▎▍▌▋▊▉▊▋▌▍▎▏  0:00/0:02   │ │
│            │ └──────────────────────────────────────────┘ │ ├────────────────────────────────┤ │
│            │ [laughter] [sigh] [question-en] [CMU]         │ │ ⬢ DESIGN             0:01  ⋯  │ │
│            │                                              │ │ studio                       │ │
│            │  🔊 VOICE SOURCE                             │ │ ▶ ▕▏▎▍▌▋▊▉▊▋▌▍▎▏  0:00/0:01   │ │
│            │ ┌──── Saved profiles ───────────  + New ──┐ │ ├────────────────────────────────┤ │
│            │ │ (◉The Storyteller) ( The Anchor )( Maya )│ │ │ ⬢ DESIGN             0:01  ⋯  │ │
│            │ └──────────────────────────────────────────┘ │ │ plain                        │ │
│            │  Define voice:  (◉ From audio) ( By design ) │ │ ▶ ▕▏▎▍▌▋▊▉▊▋▌▍▎▏  0:00/0:01   │ │
│            │ ┌──────────────────────────────────────────┐ │ ├────────────────────────────────┤ │
│            │ │  ↑ Drop audio · Choose file · 🎙 Record   │ │ │ ⬡ CLONE              0:02  ⋯  │ │
│            │ │     The_Storyteller.wav  ✓                │ │ │ Hello engine     seed 42     │ │
│            │ └──────────────────────────────────────────┘ │ │ ▶ ▕▏▎▍▌▋▊▉▊▋▌▍▎▏  0:00/0:02   │ │
│            │  Transcript (opt)        Style                │ └────────────────────────────────┘ │
│            │ [____________________]  [whisper________]     │   ⋯ row menu: Save · Lock · Export │
│            │  🌐 Language (646)        ⚙ Steps      [10]   │           · Load config · Delete   │
│            │ [ Vietnamese          ▾] ●━━━━━━━━━━━━━━━     │                                    │
│            │  ▸ Production overrides                       │                                    │
│            │ ┌──────────────────────────────────────────┐ │                                    │
│            │ │            ▷  Synthesize Audio            │ │                                    │
│            │ └──────────────────────────────────────────┘ │                                    │
├────────────┴──────────────────────────────────────────────┴────────────────────────────────────┤
│ GS   Backend 3   Frontend 5   Tauri 3   Updates                               ⚲ Local   ⬡ ♥     │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
   └ sidebar: Projects        └ definition column (Prompt ▸ Voice Source ▸ Synth)   └ right history
   + Downloads (no History)                                                          (this workspace)
```

**Voice Source — "By design" method (sliders return, selected profile drives them):**

```
 🔊 VOICE SOURCE
┌──── Saved profiles ─────────────────────────────  + New ──┐
│ ( The Storyteller ) ( The Anchor ) (◉ Maya — designed )    │
└────────────────────────────────────────────────────────────┘
 Define voice:  ( From audio )  (◉ By design )
┌────────────────────────────────────────────────────────────┐
│ ✎ Describe your voice                                      │
│ [ warm middle-aged female, calm, british…              ]   │
│   ↳ matched: gender, age, pitch, accent                    │
├────────────────────────────────────────────────────────────┤
│ ⛬ Personality:  ( Narrator )( Casual )( News )( Energetic ) │
├────────────────────────────────────────────────────────────┤
│ Gender   · female     [ female ][ male ][ Auto ]           │
│ Age      · middle     [child][teen][young][◉middle][elder] │
│ Pitch    · low        [v.low][◉low][mod][high][v.high]     │
│ Accent   · british    [ british accent              ▾]     │
└────────────────────────────────────────────────────────────┘
   Save → renders a sample WAV (saved seed, default 42) + stores these picks
   as a reusable design profile.      [ 💾 Save as profile ]
```

**History row — anatomy (reuses the shared `<WaveformPlayer>`):**

```
┌────────────────────────────────────────────────┐
│ ⬢ DESIGN                       0:01s        ⋯  │  ← mode badge · gen time · row menu
│ studio                                         │  ← prompt text (clamped)
│ seed 42                                         │  ← seed (when present)
│ ┌────────────────────────────────────────────┐ │
│ │ ▶  ▕▏▎▍▌▋▊▉▊▋▌▍▎▏▎▍▌▋▊▉   0:00 / 0:01     │ │  ← one shared player
│ └────────────────────────────────────────────┘ │
└────────────────────────────────────────────────┘
   ⋯ →  💾 Save as profile   🔒 Lock   ⤓ Export   📂 Load config   🗑 Delete
   [All][Clone][Design] chips filter by item.mode (default All)
```

---

## 1 — Layout restructure

Today `CloneDesignTab` (`frontend/src/pages/CloneDesignTab.jsx:217`) renders `.clone-split-grid` as **two side-by-side columns**:

```text
[ App sidebar: Proj | Hist(58) ] [ PROMPT        ] [ VOICE SOURCE   ]
                                 [ lang | steps  ] [ overrides/synth]
```

Implemented target: a **voice-library rail**, one **definition column** (Prompt stacked over Voice Source), and one **generation-history rail**:

```text
[ ACTIVE VOICE    ] [ PROMPT                  ] [ GENERATION HISTORY      ]
[ Saved voices    ] [ ........(textarea)..... ] [ [All][Clone][Design]    ]
[  • Maya         ] [ tags / [CMU]            ] [  ▸ plain    0:01  ◀ play ]
[  • Storyteller  ] [ VOICE SOURCE            ] [  ▸ studio   0:01        ]
[                 ] [  • from audio / design  ] [  ▸ Hello…   0:02        ]
[                 ] [ language | steps        ] [  …                      ]
[                 ] [ ▸ Production overrides   ] [                         ]
[                 ] [ [ Synthesize Audio ]     ] [                         ]
```

Concretely:
- `.studio-with-history` has three columns: voices (fixed 280px), definition (flex), and history (fixed 340px). Narrow shells stack the definition first, then voices and history.
- Move the existing PROMPT panel (`CloneDesignTab.jsx:221-284`) and VOICE SOURCE panel (`:313-532`) into the **same** `definition` column, prompt first. Language/steps and Production Overrides + Synthesize stay at the bottom of that column.
- The left library rail (`.studio-voices`) contains **`<WorkspaceVoices>`**; the right column (`.studio-right`) gives **`<WorkspaceHistory>`** the full height — §4.
- **Left sidebar dissolved** for the Voice workspace (`hideSidebar` includes `clone`/`design`). The saved-profile list that lived in the sidebar's Projects tab moved into `<WorkspaceVoices>`; downloads remain reachable via OmniDrive. Dub keeps its sidebar until §5 extends the right-panel pattern.

No backend involved in this section.

---

## 2 — Unified "Voice" workspace (navigation)

- Introduce one `AppMode` for the workspace. `clone` and `design` both currently fall through to `CloneDesignTab` (the `else` branch at `App.jsx:1125`). Replace them with a single id **`studio`** (avoids colliding with the existing `voice` profile-detail page and the `generate` mode).
- `uiSlice.ts` `AppMode`: remove `'clone' | 'design'`, add `'studio'`. Keep a back-compat shim: when restoring persisted UI state or a history item whose `mode` is `'clone'`/`'design'` (`useAppData.js:137`, `App.jsx` `restoreHistory`), map → `'studio'` and preset the **define method** (below).
- Inside the workspace, a **Define voice** segmented control picks the method:
  - **From audio** (was *clone*): drop/record/choose reference, transcript, style. (`CloneDesignTab.jsx:344-405`)
  - **By design** (was *design*): describe-box + personality chips + category sliders. (`CloneDesignTab.jsx:436-528`)
  - **Saved profile**: selecting a profile card sets the method implicitly from `profile.kind`.
- The `mode` prop branching inside `CloneDesignTab` (`mode === 'clone' ? … : …`) becomes a local `defineMethod` state (`'audio' | 'design'`), seeded from the selected profile or the last-used method. Generation request shape is unchanged — it already keys off `profile_id` / `ref_audio` / `instruct`, not the tab name.
- Rename the file `CloneDesignTab.jsx` → `StudioTab.jsx` (keep the export working; update the lazy import in `App.jsx:1128`). NavRail label → "Voice".

The **profile is the hub:** the saved-profiles strip (`CloneDesignTab.jsx:319-342`) moves to the top of Voice Source and is always visible regardless of define method; picking one fills the form and sets the method; "+ New" clears selection back to a blank definition.

---

## 3 — Profile data model unification

`voice_profiles` today (`backend/core/db.py:39-53`) has no way to represent a design voice as a first-class profile, and `POST /profiles` requires a `ref_audio` file (`backend/api/routers/profiles.py:40`). Add a discriminator and design params; keep audio for identity.

### Migration `0005_unified_profiles` (0003 consent + 0004 mcp landed upstream) (`backend/migrations/versions/`)

Follow the existing idempotent `_has_column()` pattern (`0002_voice_profile_demo_fields.py`):

```sql
ALTER TABLE voice_profiles ADD COLUMN kind TEXT DEFAULT 'clone';      -- 'clone' | 'design'
ALTER TABLE voice_profiles ADD COLUMN vd_states TEXT DEFAULT NULL;    -- JSON of design category picks
-- ref_audio_path stays TEXT/nullable (SQLite: already nullable). '' == no audio.
UPDATE voice_profiles SET kind='clone' WHERE kind IS NULL OR kind='';
```

- **Backfill is safe:** every existing profile becomes `kind='clone'` (they all have real or rendered `ref_audio_path` today, including archetype-materialized ones). No user data migration, no re-render.
- Mirror the columns in the `_BASE_SCHEMA CREATE TABLE` in `db.py` so fresh installs converge.
- `downgrade()` drops both columns (guarded by `_has_column`).

### Profile shape (after)

```ts
interface Profile {
  id: string; name: string;
  kind: 'clone' | 'design';
  ref_audio_path: string | null;   // clone: user audio. design: rendered sample (saved seed, default 42)
  ref_text: string;
  instruct: string;                 // style; for design = buildDesignInstruct(vd_states) ⊕ free text
  vd_states: Record<string,string> | null;  // design only — for re-editing the sliders
  language: string;
  seed: number | null;
  is_locked: boolean; locked_audio_path: string;
  created_at: number;
}
```

Frontend `types.ts:107-119` already declares `kind`/`ProfileKind` but never receives it — this makes it real. `vd_states` is added there too.

---

## 4 — Right-side generation history

### Component `<WorkspaceHistory>` (`frontend/src/components/WorkspaceHistory.jsx`, new)

- Reads `history` (already loaded in `useAppData.js`), filters to the active workspace. For Voice: `item.mode ∈ {clone, design}`. Renders newest-first.
- A small filter chip row **[All] [Clone] [Design]** (Voice only) toggles `item.mode`. Default **All**.
- Each row reuses the shared **`<WaveformPlayer>`** (already built) + the existing item actions, lifted out of `Sidebar.jsx:382-416`: Save-as-profile, Lock (when `profile_id`), Export, Load-config (`restoreHistory`), Delete. The CLONE/DESIGN mode badge stays.
- Live updates: nothing new — the `generation_history` WS event already triggers `loadHistory()` (`useAppData.js:115`, `useRealtimeEvents.js`).

### API: add optional filtering (`backend/api/routers/generation.py:463`)

The list endpoint returns all 50 mixed. Add **optional** query params, fully backward-compatible (no param = today's behavior):

```
GET /history?mode=clone|design&limit=50
```

- Frontend can keep client-side filtering for v1 (50-row cap makes it cheap) and adopt the query param only if/when history grows. Spec ships the param so the right panel can later page per-mode without loading everything.

### Saved voices panel (`frontend/src/components/WorkspaceVoices.jsx`, built)

- Relocates the sidebar's saved-profile list (clone → reference profiles, design → designed profiles) into the full-height `.studio-voices` left rail. Same card markup/actions (select, preview, open, try-voice, unlock, delete) + local search.
- Clicking a card runs `handleSelectProfile` (loads it into the definition form). The center Voice Source's old inline profile block is removed (single source of truth).

### Sidebar cleanup (`frontend/src/components/Sidebar.jsx`)

- `hideSidebar` (`App.jsx`) now includes `clone`/`design` → the **left sidebar is dissolved** for the Voice workspace. Its synth-history block stays for dub. Downloads → OmniDrive.

---

## 5 — Backend: profile create/resolve

### `POST /profiles` (`backend/api/routers/profiles.py:37`)

- Make `ref_audio` **optional**. Add `kind` (default `clone`) and `vd_states` (JSON string, optional) form fields.
- Validation:
  - `kind='clone'` → `ref_audio` required (today's rule). Upload saves recreate the voices storage directory if it is missing, including after startup.
  - `kind='design'` → `vd_states` required; `instruct` is **not** required (an all-Auto design has an empty instruct and is still a valid, saveable voice). The server *opportunistically* renders a sample WAV (reuse the archetype renderer in `archetypes.py`: synth `sample_script` at the submitted seed, `_PREVIEW_SEED=42` when none, store as `ref_audio_path`) and always persists `vd_states` + derived `instruct` + that `seed`. A deferred render uses the stored seed. **The render is non-fatal** (issue #476): if the engine isn't ready the row is saved with `ref_audio_path=NULL` (sample pending) and `GET /profiles/{id}/audio` renders + caches it lazily on first request (returning a precise "model not ready — finish setup / download a model" 503 if the engine is still unavailable).
- Return `kind` and `vd_states` in the profile payload (and from `GET /profiles`, `GET /profiles/{id}`).

### `POST /generate` profile resolution (`backend/api/routers/generation.py:310-335`)

Replace the brittle inference (`is_locked` + `instruct` presence) with an explicit branch on `profile.kind`:
- `clone` → `ref_audio_path` (or `locked_audio_path` if locked) + `ref_text` + `instruct`.
- `design` → use the rendered `ref_audio_path` for identity (deterministic) + `instruct`; if absent, fall back to `instruct` only. `vd_states` is *not* needed at synth time (already baked into `instruct`/sample), but is returned for the editor.
- History `mode` column logic (`:399-405`) stays (`"clone" if ref_audio_path else "design"`) but we now also have `profile.kind` as the authoritative source — write `mode = profile.kind` when a profile drives the gen.

### Frontend save/load (`hooks/useProfiles.js`)

- `handleSaveProfile` (`:37`): when define method is **design**, POST `kind='design'` + `vd_states` (from store `vdStates`) + `instruct` (`buildDesignInstruct`), no audio file. When **audio**, unchanged.
- `handleSelectProfile` (`:62`): if `profile.kind==='design'`, `setVdStates(profile.vd_states)` and set define method to `design`; else fill `refText`/`instruct` and set method to `audio`. (Fixes today's gap where selecting never restores sliders.)

---

## 6 — Phasing (continuous-to-main, each independently shippable)

1. **P1 — `<WorkspaceHistory>` + right column, no consolidation yet.** Render history on the right of the existing clone/design tabs; remove it from the sidebar. Pure frontend; reuses `<WaveformPlayer>`. Lowest risk, immediate visible win.
2. **P2 — Layout restack.** Prompt over Voice Source in one `definition` column. Frontend/CSS only.
3. **P3 — Profile data model.** Migration `0005`, `POST /profiles` optional-audio + `kind`/`vd_states`, generate resolution on `kind`, save/load design profiles. Backend + small frontend.
4. **P4 — Navigation consolidation.** `clone`+`design` → `studio`; define-method control; rename `CloneDesignTab`→`StudioTab`; legacy-mode shims. Frontend.
5. **P5 — Extend the right-history pattern** to Dub/Stories ("etc"), and adopt `GET /history?mode=` paging if needed.

---

## 7 — Risks / open items

- **Mode-id churn:** persisted `mode:'clone'|'design'` in `localStorage` and `generation_history.mode` must keep resolving. Shim in `useAppData` restore + `restoreHistory`; never rename the history `mode` *values* (still `clone`/`design`), only the *navigation* mode id.
- **Archetype path reuse:** the design-save render must share one helper with `archetypes.py` materialization, not copy it (single source for "synth a sample at the saved seed → store as profile").
- **`personality` column** (`db.py`, added in `0002`) stays unused; do not repurpose in this spec.
- **Cross-platform:** all default behavior here is platform-agnostic; no opt-in gating needed (record/mic already exists today).
- **Docs:** update any `docs/**` / README sections that name "Clone tab" / "Design tab" in the P4 PR (docs-sync hard rule).

---

## 8 — Test plan

- **Migration:** fresh install converges to `kind`+`vd_states` via base schema; upgrade from a `0002` DB backfills `kind='clone'`; downgrade drops cleanly. Existing profiles still synth.
- **Backend:** `POST /profiles` rejects design-without-`vd_states` and clone-without-audio; design create produces a playable `ref_audio_path`; `GET /history?mode=design` filters; generate on a `design` profile is deterministic across runs.
- **Frontend:** selecting a design profile restores sliders + define method; saving a design voice round-trips; right-panel history filter chips work; history removed from sidebar; `<WaveformPlayer>` plays each row; legacy `mode:'clone'` localStorage opens the Voice workspace in "audio" method.
- **Regression:** existing clone profiles, lock/unlock, and the archetype "Use voice" → profile flow (just wired in a prior change) still work.
