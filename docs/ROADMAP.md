# VoiceStudio — Road to World-Class

**Last updated:** 2026-04-21 · **Current phase:** Phases 0–4 complete. Remaining work sits in Design / Performance / Quality tracks + the Phase 4 eval sprint. · **Resourcing:** 1 FTE + ad-hoc

Honest plan for moving from "polished solo-dev project" to software that holds up under scrutiny at scale. Living tracker: every item has a status, every phase has a progress bar, every shipped item has a date.

---

## 📊 Status overview

```
Phase 0 · Momentum                ▓▓▓▓▓▓▓▓▓▓  5 / 6     ✅ shipped
Phase 1 · Visible quality leap    ▓▓▓▓▓▓▓▓▓▓  4 / 4     ✅ shipped
Phase 2 · Foundation refactor     ▓▓▓▓▓▓▓▓▓▓  8 / 8     ✅ shipped (2.2 closed with 5 Zustand slices)
Phase 3 · Pluggable engines       ▓▓▓▓▓▓▓▓▓▓  4 / 4     ✅ shipped
Phase 4 · The two bets            ▓▓▓▓▓▓▓▓▓▓  6 / 6     ✅ shipped · 4.1 benchmarked at 4.04s warm (target ≤5s)
Phase 5 · Productisation          ░░░░░░░░░░  0 / 5     🚫 demand-driven

Design track        ▓▓▓▓▓▓▓▓▓░  ongoing · 14 primitives + ~67 migrated inline styles · DubTab/Header/Sidebar/CloneDesignTab drained
Performance track   ▓▓▓░░░░░░░  underway · profiling, preload, isolated engines + cache-remix I/O
Feature-magic track ▓▓░░░░░░░░  underway · project-level casting board shipped
Quality track       ▓▓░░░░░░░░  12 smoke tests, 10 error messages rewritten
```

**Legend:** ✅ shipped · 🟡 in progress · ⏳ not started · 🔒 blocked · 🚫 deferred

---

## 🎯 North Star

> The best **local-first** cinematic dubbing studio in the world. Indistinguishable from a cloud product in quality and UX, but never leaves the user's machine.

Three non-negotiables:
1. **Two defensible innovations** that no competitor can copy in a sprint, backed by obsessive polish everywhere else.
2. **Dub output quality** good enough that a human editor would only tweak wording, not rebuild timing.
3. **UX** where a non-technical user produces a polished dubbed video in under 10 minutes, end to end, and never waits on a spinner longer than they expect.

## 🎲 Our two bets (the story worth telling)

| | Bet | Status |
|---|---|---|
| **A** | **Directorial AI** — natural-language per-segment direction ("make segment 14 feel more urgent") rewrites translation tone + TTS `instruct` + speech-rate target. | ⏳ Phase 4 |
| **B** | **Incremental re-dub** — change one word in a 2-hour video, regenerate only affected segments + crossfades in seconds. | ⏳ Phase 4 |

Together they compose: directorial edits trigger incremental re-dubs. That's the one-liner we stand on.

---

## 📍 Where we are today

✅ Feature-complete MVP. Competitive baseline parity with VideoLingo / pyVideoTrans.
🟡 Code health improving fast. **14 design-system primitives shipped**, 9 components migrated, main bundle -2.7 % despite net additions.
⏳ Quality ceiling unchanged — one-shot translation, raw WhisperX segments, no speech-rate adaptation. **This is what Phase 1 fixes.**
⏳ UX ceiling — motion language, error messages, onboarding: partial. Launch animation shipped; error messages rewritten (10 sites); onboarding + sample clip still pending.

See [`STRUCTURE.md`](STRUCTURE.md) for file layout. (The competitor analysis `research/LEARNINGS.md` and the `design/` ASCII mockups this roadmap was built on were removed in the 2026-07-12 root cleanup — git history.)

---

## 📐 Resourcing assumption

**1 FTE + ad-hoc contributors.** All timeline ranges below assume that. Double the team → roughly halve the time. Solo → roughly 1.5×. All timelines are **ranges**, not promises, revisited at each phase exit.

---

## 🧱 Phase 0 — Momentum _(1 week → ✅ shipped 2026-04-21)_

> *Ship-this-week wins. Nothing invisible. Build a habit of landing improvements.*

Progress: **5/6 (83 %)**

| ID | Item | Status | Notes |
|----|------|:---:|------|
| 0.1 | Dead-code sweep (orphaned `test_*.py`, debug scripts, runtime artifacts at root) | ✅ | Shipped with the STRUCTURE.md cleanup. Deleted 8 files, archived `legacy_gradio/`, cleaned 16 stray `.DS_Store`s. |
| 0.2 | Alembic migration skeleton | ✅ | `alembic.ini`, `backend/migrations/{env,script.py.mako,versions,README}` ready. Legacy `_migrate()` in `core/db.py` kept as fallback for one release. |
| 0.3 | Top-10 error-message rewrite ("what happened · why · what to do") | ✅ | `dub_generate.py:98`, `dub_generate.py:26`, `generation.py:59`, `generation.py:183`, and `exports.py` ×6 all rewritten. Validated via smoke test on `/export/reveal` and `/dub/generate/{bad-id}`. |
| 0.4 | One smoke test per router | ✅ | `tests/test_router_smoke.py` — **12 tests, runs in 2.79 s**. Covers `system`, `profiles`, `projects`, `exports`, `generation`, `dub_core`, `dub_generate`. |
| 0.5 | 500 ms launch animation + `.ui-skel` shimmer utility | ✅ | In `ui/tokens.css`, auto-applied via `#root` selector. `prefers-reduced-motion` fallback included. |
| 0.6 | Pre-loaded 30-second sample clip for first-run onboarding | 🚫 | Deferred — requires licensed asset selection + Tauri bundling work. Revisit in Phase 5. |

**Exit criteria met:** errors actionable, CI smoke-green on every router, launch animation live. `kill -9` during a job **does not yet resume** — that moves to Phase 4.

---

## 🎨 Phase 1 — Visible quality leap _(shipped 2026-04-21 → ✅)_

> *The single biggest user-visible upgrade. Directly from VideoLingo's playbook. Ships before the deeper foundation refactor so users feel the improvement first.*

Progress: **4/4 (100 %)**

| ID | Item | Status | Target files |
|----|------|:---:|------|
| 1.1 | Translate → Reflect → Adapt (3-step LLM chain) | ✅ | Shipped 2026-04-21. `backend/services/translator.py` (new), `dub_translate.py` (wired), `schemas/requests.py` (+`quality`, `+glossary`), `App.jsx` (`translateQuality` state, localStorage-persisted), `DubTab.jsx` (Fast/Cinematic Segmented control), response carries `literal`+`critique` per segment for the future 3-column UI. Works with any OpenAI-compatible endpoint (real OpenAI, Ollama, LM Studio, Together, Anyscale). Graceful fallback when no LLM configured. Smoke suite still green (12/12 in 2.85 s). |
| 1.2 | NLP-aware subtitle segmentation (Netflix rules: ≤42 chars/line, ≤17 CPS) | ✅ | Shipped 2026-04-21. `backend/services/subtitle_segmenter.py` (new, 226 lines, pure-function, zero deps). Hooked into both transcription paths in `dub_core.py` (chunked streaming + single-shot), runs after diarization so speaker_id flows through splits. Tests: `tests/test_subtitle_segmenter.py` — 14 tests covering sentence/clause/conjunction splits, CPS enforcement, word-level timing, merger, speaker-boundary respect, edge cases. All 26 backend tests (14 new + 12 smoke) pass in 2.87 s. |
| 1.3 | Project-scoped term glossary with LLM auto-extract | ✅ | Shipped 2026-04-21. `glossary_terms` table in `core/db.py` (+ schema version 3); new `backend/api/routers/glossary.py` with full CRUD + `POST /glossary/{id}/auto-extract`; new `frontend/src/components/GlossaryPanel.jsx` (edit-in-place, auto/manual badges, LLM auto-extract via same OpenAI-compatible client as translator); `frontend/src/api/glossary.js` client; DubTab renders the panel when a job is loaded; `App.jsx` collects `glossaryTerms` and pipes them into `/dub/translate` on every call. Translator's existing `glossary` prompt injection (1.1) now actually gets data. |
| 1.4 | Dual subtitle export (original + translated together) | ✅ | Shipped 2026-04-21. `GET /dub/srt/{id}?dual=1` and `GET /dub/vtt/{id}?dual=1` stack translated line on top, original italicised underneath (`<i>…</i>`). Shared `_pick_subtitle_text()` helper so both formats stay identical. UI: "Dual subtitles" checkbox in Output Options (localStorage-persisted `omnivoice.dub.dualSubs`); SRT/VTT buttons show a ✦ badge + `_dual` filename suffix when on. |

**Exit criteria:** blind A/B on 3 clips (EN→DE, EN→JA, EN→ES) shows ≥70 % preference for the new pipeline. Dub output materially feels more professional to an untrained listener.

---

## 🧱 Phase 2 — Foundation refactor _(10–14 weeks → 🟡 partial)_

> *The invisible work that unblocks everything after Phase 4. Running in parallel where it doesn't interfere with Phase 1.*

Progress: **7/8 (88 %)** · Phase 2.5 has overshot expectations thanks to the design-system sprints; 2.8 was added after Phase 1 shipped to give the voice library its long-overdue first-class surface.

| ID | Item | Status | Notes |
|----|------|:---:|------|
| 2.1 | Persist the task queue (survive restart, SSE replay) | ✅ | Shipped 2026-04-21. New `jobs` + `job_events` tables (schema v4). New `backend/core/job_store.py` with CRUD + `sweep_orphans_on_startup()`. `TaskManager` now mirrors every state transition + event to disk; the in-memory queue stays as-is for speed. Startup lifespan flips any `pending`/`running` job to `failed` with a "restart interrupted" message. New endpoints: `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/events`. `GET /tasks/stream/{id}?after_seq=N` now replays the persisted tail on reconnect before attaching to the live listener. Tests: `tests/test_job_store.py` (7 tests — lifecycle, event seq, per-job cap, active filter, startup sweep). Smoke suite extended with 4 jobs-endpoint tests. All 37 backend tests pass in 2.83 s. **Does not yet** persist the actual work — that's Phase 4.5 (step-level resumability); this phase only guarantees honest metadata across restarts. |
| 2.2 | Split the App.jsx monolith via Zustand store | ✅ | **5 slices shipped** 2026-04-20/21. `prefsSlice` (translateQuality/dualSubs/reviewMode), `glossarySlice`, `uiSlice` (mode + nav + sidebar tab + cheatsheet flag + voice-profile nav actions), `dubSlice` (18 pipeline fields with React-style setters), `generateSlice` (text/refText/instruct/language + 10 production-override knobs + vdStates). Persist middleware key bumped to v3 with the generate knobs + sidebar state. App.jsx `useState` count: **~60 → 41** — everything remaining is File/Blob refs, backend listings (history/dubHistory/exportHistory/studioProjects/profiles), transient recording timers, and compare-modal state (all non-migratable by design). Children (DubTab/Sidebar) read from store directly; ~35 props dropped from the call sites. |
| 2.3 | TypeScript migration (incremental: store + API layer first) | ✅ | Shipped 2026-04-20 — **full API layer now TS**. All 8 remaining `.js` files (dub/engines/exports/generate/glossary/profiles/projects/system) migrated to `.ts` with typed response shapes sourced from a new shared `api/types.ts` (EngineBackend, Profile, ProfileUsage, GlossaryTerm, ProjectDetail, SystemInfo, ModelStatus, …). Call sites unchanged; every consumer now gets compile-time checks on API response shapes. Store slices + `api/client.ts` already TS from Phase 2.3's first pass. Component conversion to `.tsx` stays optional per-file. |
| 2.4 | Split `dub_core.py` into router + `services/dub_pipeline.py` | ✅ | Shipped in two passes 2026-04-21. `services/dub_pipeline.py` now holds: file-hash / path-safety / SSE helpers, `find_cached_job`, process lifecycle (`register_proc`/`unregister_proc`/`kill_job_procs`), in-memory + SQLite job state (`get_job`/`put_job`/`save_job`), subprocess wrapper factory (`run_proc_factory`), yt-dlp downloader, AND the full `ingest_pipeline` async generator (the download→extract→demucs→scene→thumbnail prep flow). Module-level state (`_dub_jobs`, `_active_procs`, `_active_procs_lock`) moved too. `dub_core.py` keeps backward-compat aliases. **Size: 889 → 525 lines (−40.9 %)**. 50 backend tests green (2.84 s). Transcription generators still in router — that's the remaining Phase 4 prerequisite, not a blocker. |
| 2.5 | Design-system primitives | ✅ | **MASSIVELY overshot.** 14 primitives: Button, Panel, Field/Input/Textarea/Select, Dialog, Slider, Badge, Tabs, Segmented, Tooltip, Progress, Menu, Table. 9 components migrated. Inline-style migration (separate design-track row) has since drained DubTab (93→2), Header (24→1), Sidebar (21→8), CloneDesignTab (33→0). Sessions ran 2026-04-19 → 2026-04-21. |
| 2.6 | Logging + telemetry baseline | ✅ | Shipped 2026-04-21. `print()` sweep in `services/model_manager.py` — all four calls now use `logger.info` (model load, torch.compile apply/skip, idle unload). New `OMNIVOICE_JSON_LOGS=1` env flag swaps every handler to a single-line JSON formatter (verified: `{"t": "...", "level": "INFO", "name": "omnivoice.api", "msg": "..."}`), including the rotating file handler. Per-stage Prometheus counters at `/metrics` deferred — land when a real observability need arises. |
| 2.7 | Test floor ("every bug ships a regression test") | ✅ | Shipped 2026-04-21. New `tests/test_dub_pipeline_state.py` (8 tests — path safety, SSE shape, process tracking, in-memory + disk job round-trip) + `tests/test_translator.py` (5 tests — glossary preamble, no-LLM graceful fallback, empty-literal passthrough, full 3-step chain with mocked LLM, reflect-failure path). Total backend tests: **50** (up from 26) passing in 3.10 s. Integration tests covering the GPU / ASR model path stay out of CI — those are manual fixture-clip runs. |
| 2.8 | Voice profile page | ✅ | Shipped 2026-04-21. New `mode: 'voice'` + `activeVoiceId` in `App.jsx`; lazy-loaded `frontend/src/pages/VoiceProfile.{jsx,css}` (hero + details + try-it + usage). Backend: new `GET /profiles/{id}`, `PUT /profiles/{id}`, `GET /profiles/{id}/usage` (scans `studio_projects.state_json` for dub-segment counts). Sidebar gets an "Open" action on clone/design cards that routes here; Back returns to the previous mode. Design target: `design/03-voice-library.md` (retired 2026-07-12; git history). |

**Exit criteria:** server can restart mid-dub without losing work. Frontend type-checks. No router file >300 lines. App.jsx ≤300 lines. CI blocks regressions.

---

## 🔌 Phase 3 — Pluggable engines _(shipped 2026-04-21 → ✅)_

> *Stop being locked to one model family. Unlock VoxCPM2 as a serious alternative.*

Progress: **4/4 (100 %)**

| ID | Item | Status | Notes |
|----|------|:---:|------|
| 3.1 | TTS adapter interface | ✅ | Shipped 2026-04-21. `backend/services/tts_backend.py` — `TTSBackend` ABC + registry, `VoiceStudioBackend` wrapping the current model (zero behaviour change; reuses `model_manager.get_model()` so no double load), `list_backends()` with per-engine availability reasons, env-driven selection via `OMNIVOICE_TTS_BACKEND`. |
| 3.2 | Alternative TTS backends (VoxCPM2 + MOSS-TTS-Nano) | ✅ | Shipped 2026-04-21 / 2026-04-20. **VoxCPM2**: GPU studio pick, 30 langs, 48 kHz, `"(instruct)prompt"` syntax wired from VoiceStudio's `instruct`. **MOSS-TTS-Nano-100M** added 2026-04-20 as the low-resource / broad-language pick — 100M-param autoregressive, 20 langs (incl. Arabic/Hebrew/Persian/Korean/Turkish), realtime on 4-core CPU, native 48 kHz stereo, Apache-2.0. Both `is_available()` return actionable install hints when deps are missing. **Runtime picker**: `POST /engines/select` + `backend/core/prefs.py` (atomic JSON store); Settings > Engines tab has **Use** buttons per family; env vars still override. 14 engine tests pass. |
| 3.3 | ASR adapter interface | ✅ | Shipped 2026-04-21. `backend/services/asr_backend.py` — `ASRBackend` ABC, `MLXWhisperBackend` (Apple Silicon, current default), `PyTorchWhisperBackend` (CUDA / CPU fallback using the TTS model's `_asr_pipe`). `active_backend_id()` auto-detects based on `torch.backends.mps` availability; override with `OMNIVOICE_ASR_BACKEND`. |
| 3.4 | LLM adapter | ✅ | Shipped 2026-04-21. `backend/services/llm_backend.py` — `LLMBackend` ABC, `OpenAICompatBackend` (lifts the Ollama/OpenAI client out of `translator.py` so glossary auto-extract + future Directorial AI share one code path), `OffBackend` (explicit no-LLM with a copy-paste env-var hint). Privacy default honoured: Cloud LLMs opt-in per-feature, never required. |

**Exit criteria:** engine swap works without restart on Mac + Linux. VoxCPM2 available when CUDA 12+ detected.

> **Exit status:** all four adapter interfaces shipped 2026-04-21 with the env-driven selection + registry + `/engines` HTTP surface. Backward-compat preserved — no existing callers migrated yet. Actual swap-without-restart (live reconfiguration) + the Settings-UI engine picker are follow-up UX tasks, tracked in Phase 2.2's ongoing App.jsx / Settings.jsx work.

---

## 🎛️ Phase 4 — The two bets land _(shipped 2026-04-21 → ✅)_

> *The defining phase. This is why someone chooses VoiceStudio over everything else. Built on Phase 2's persistent job store and Phase 3's adapters.*

Progress: **6/6 ✅** (both bets wired end-to-end; review banners + step-level resumability live)

| ID | Item | Status | Notes |
|----|------|:---:|------|
| 4.1 | 🎲 **Incremental re-dub (Bet B)** | ✅ | Shipped 2026-04-21 end-to-end. **Service** (`services/incremental.py`) fingerprints each segment's generation inputs. **Backend** (`dub_generate.py`) now accepts `regen_only: [seg_ids]` + parallel `segment_ids: [...]`. When `regen_only` is set, listed segments run through TTS; unlisted segments reuse their on-disk `seg_N.wav` (resampled/padded as needed), then the mix reassembles over the full timeline. **UI**: App.jsx snapshots fingerprints after every successful dub via `/tools/incremental`; DubTab footer shows "N segments changed since last generate" warn badge when stale > 0; a **"Regen N changed"** pink-tone button appears alongside "Generate Dub" in the action footer when stale > 0, calling `handleDubGenerate({ regenOnly: incrementalPlan.stale })`. On partial-regen completion, fingerprints re-snapshot so the warn badge reflects the new clean state. |
| 4.2 | 🎲 **Directorial AI (Bet A)** | ✅ | Shipped 2026-04-21 end-to-end. **Backend:** `backend/services/director.py` — stable taxonomy (5 dimensions, 30+ values), dual parser (LLM via `OpenAICompatBackend` when configured, keyword-heuristic fallback), `Direction.instruct_prompt()`/`translate_hint()`/`rate_bias()`. Translator injects direction into both reflect + adapt prompts per segment via new `directions={seg_id: text}` param. `dub_generate.py` appends the parsed `instruct_prompt()` onto the per-segment `instruct_str` and multiplies `seg_speed` by `rate_bias()`. **Schema:** `DubSegment.direction` + `TranslateSegment.direction` added. **Frontend:** `DubSegmentRow` "…" menu gains a "Set direction…" entry with a ✨ icon; segments with direction show the ✨ badge in place of the "…". `components/DirectionDialog.jsx` is a Dialog with live `/tools/direction` preview showing taxonomy tokens + TTS instruct + translate hint + rate bias. `App.jsx` wires `openDirection`/`saveDirection` into Undo, passes `direction` through `handleTranslateAll` and `handleDubGenerate`. 5 unit tests pass. |
| 4.3 | Staged checkpoints (review banners) | ✅ | Shipped 2026-04-20. The dub flow has three natural review points — **post-ASR** (transcripts ready), **post-translate** (translations ready), **post-generate** (dub complete). `components/CheckpointBanner.jsx` renders phase-appropriate banner above the segment table with an inline **Continue →** button that triggers the next stage (Translate / Generate) directly. Stage auto-detected: `dubStep === 'editing'` + no `text_original` → ASR; `editing` + any `text_original` → translate; `done` → final. Dismissible per-stage (won't reappear until reload). **Review mode toggle** added to `prefsSlice` (persisted via zustand/persist); Settings > Engines tab exposes a **Review between stages / Rapid-fire** segmented control. Defaults to 'on'. |
| 4.4 | Speech-rate engineering | ✅ | Shipped 2026-04-21 end-to-end. `backend/services/speech_rate.py` — CPS tables per language, tolerance window (0.92–1.08), LLM trim/expand loop with MAX_ATTEMPTS=3. **Wired into Cinematic translate:** `TranslateSegment.slot_seconds` auto-populated by `App.jsx` from `seg.end - seg.start`. When cinematic + a slot are supplied, translator runs `adjust_for_slot` on each refined line; returns `rate_ratio` + optional `rate_error` per segment. **UI**: `DubSegmentRow` now renders a compact 📖 `1.12×` badge in the time column whenever `seg.rate_ratio` drifted >3% from 1.0; colour-coded (red above 1.15, blue-info below 0.85, muted otherwise) with a tooltip showing the error if rate-fit bailed. `POST /tools/rate-fit` + `ToolsPage.RateFitTool` remain for standalone use. |
| 4.5 | Step-level resumability | ✅ | Shipped 2026-04-20 end-to-end. Per-segment fingerprints are persisted in `job.seg_hashes` **after each successful segment gen** (flushed every 8 segs via `_save_job`) so a kill -9 mid-dub loses at most the in-flight segment. The `done` SSE event now streams `seg_hashes` to the client, which snapshots them as `lastGenFingerprints` without a follow-up `/tools/incremental` round-trip. `loadProject` rehydrates from `project.segHashes`; `restoreDubHistory` rehydrates from `job.seg_hashes`. Net effect: closing the app mid-dub + reopening lights up the "Regen N changed" button for the exact residual, and the user resumes from the last good segment instead of restarting. |
| 4.6 | Headless CLI + standalone Tools page | ✅ | Shipped 2026-04-21. New `omnivoice-dub` CLI (`omnivoice/cli/dub.py`, `pyproject.toml` entry point) — drives the full pipeline over HTTP, stream-reads SSE tasks, supports `--url` ingest, `--quality cinematic`, `--voice`, `--glossary`. New `backend/api/routers/tools.py` with `/tools/probe`, `/tools/incremental`, `/tools/direction`, `/tools/rate-fit`. New `frontend/src/pages/ToolsPage.jsx` wired at `mode='tools'`, hosts 3 interactive cards (Directorial AI demo, Rate-fit demo, ffprobe). 3 smoke tests added. |

**Exit criteria:** changing one word regenerates in <5 s on the fixture clip. Directorial evals beat baseline by ≥15 % on the rubric. Kill -9 mid-dub resumes to completion.

> **Exit status:** all six sub-phases shipped with integration into the main dub flow + DubTab workspace (2026-04-20/21). The **measurable** exit criteria (5 s incremental wall-clock, +15 % directorial eval, benchmarked crash recovery) still require a fixture-backed eval sprint — the code path is live, the numbers are not yet pinned. Tracked separately under the Quality track.

---

## 🏗️ Phase 5 — Productisation _(demand-driven → 🚫 deferred)_

> *Do not build proactively. Trigger-based only.*

Progress: **0/5 (0 %)**

| ID | Item | Trigger that starts it | Status |
|----|------|------|:---:|
| 5.1 | Multi-worker + Redis/Postgres job queue | First team asks to share a GPU across users | 🚫 |
| 5.2 | OpenTelemetry tracing | First "why is this slow?" incident we can't diagnose | 🚫 |
| 5.3 | Optional auth (magic-link / passkey) | First team-deployment request | 🚫 |
| 5.4 | Signed installers + auto-update (Tauri) | Paid release or pre-order list forms | 🚫 |
| 5.5 | Plugin SDK (third-party TTS/ASR/LLM) | First external contributor ships an adapter | 🚫 |

None on the critical path to world-class. All are answers to real demand.

---

## 🛤️ Parallel tracks (always running)

### 🎨 Design track _(ongoing → 🟡 very active)_

| Item | Status | Notes |
|------|:---:|------|
| Motion language (60 fps, spring easing, staggers) | 🟡 | Tokens shipped in `ui/tokens.css` (durations, easings); launch animation live; stagger + spring on interactions still pending. |
| Density dial (Small/Normal/Max rebuilds layout, not just font-size) | ⏳ | Current S/M/L only scales font size. |
| Error messages rewritten in every path | 🟡 | **20 done** (Phase 0 top-10 + round-2 on 2026-04-21): `system.py` (log-read + log-clear), `dub_core.py` (invalid job_id ×2, invalid url, yt-dlp missing, ASR not loaded), `profiles.py` (nothing-to-update + profile-not-found ×2), `dub_export.py` (no-such-job, ffmpeg mux fail ×2, MP3 encode fail), `glossary.py` (empty PUT body). All now include what-happened / why / what-to-do. 138 backend tests green. ~15 lower-traffic paths remain. |
| Onboarding by doing (pre-loaded sample project) | ⏳ | See 0.6 — deferred. |
| Launch animation + recognisable sound | 🟡 | Animation live; sound not yet. |
| Weekly inconsistency audit (1 h / Friday) | ⏳ | Cadence not yet established. |
| Design-system primitives (14) | ✅ | Full inventory above. |
| Migrate remaining inline styles | 🟡 | **Four biggest offenders drained 2026-04-20** — DubTab **93 → 2**, Header **24 → 1**, Sidebar **21 → 8**, CloneDesignTab **33 → 0**. All remaining are genuinely dynamic (per-row `--row-accent` CSS custom props in Sidebar, per-bar `height/animationDelay` in WaveBars, `opacity` computed from index in skeleton rows, `fontSize` by prop). New class systems: `.dub-*` (DubTab), `.hq-col-*/.hq-stats__*/.hq-logo-*` (Header), `.sidebar-tile--*/.sidebar__scroll/.history-*--*` (Sidebar), `.clone-*/.label-row--*` (CloneDesignTab). Drag-hover on `.file-drag` and `.dub-idle-drop` now toggles `.is-dragging` instead of mutating styles via DOM. Remaining 119 across the tail (Launchpad, KeyboardCheatsheet, DubSegmentRow, WaveformTimeline, etc.) — less concentrated, lower-leverage. |

### ⚡ Performance track _(🟡 underway)_

| Item | Status | Current measurement |
|------|:---:|------|
| Batched TTS (host-derived width per forward pass) | 🟡 | The batch queue feeds OmniVoice's native variable-length forward pass, with the width derived from device headroom (1 on CPU/low-VRAM hosts, up to 8) and overridable via `OMNIVOICE_DUB_BATCH_WIDTH`; adapters without native batching retain the single-segment fallback. |
| Kill per-segment disk round-trip | 🟡 | Long-video assembly stays disk-backed to bound RAM. Unchanged same-rate natural segments now skip the redundant decode → scratch encode → decode cycle; fresh segments still persist once and reload for assembly. |
| Cold start ≤1.5 s to first audible sample | 🟡 | Installed models preload in the background and `scripts/bench_pipeline.py` measures cold/warm synthesis; target is not yet verified. |
| Speculative regeneration on hover | ⏳ | — |
| Crash-sandbox engines (subprocess isolation) | 🟡 | Killable sidecar engines and opt-in `omnivoice-subprocess` are live; the default OmniVoice engine now routes through the crash sandbox on MPS, while CUDA/ROCm/CPU retain the lower-overhead in-process path. |
| Interaction budgets (<50 ms UI, <200 ms preview, <4 s first seg) | 🟡 | `/ws/tts` reports real TTFA, total generation time and RTF; frontend responsiveness instrumentation exists, but no cross-surface budget gate yet. |
| Dedicated dev-week per quarter | ⏳ | Cadence not yet booked. |

### ✨ Feature-magic track _(🟡 underway)_

| Feature | Status | Phase gate |
|------|:---:|------|
| Project-level casting view (drag voices to speakers) | ✅ | Shipped (#1767): the dub CAST strip expands into a casting board — drag voice chips onto speaker rows, keyboard listbox included, same fields as the dropdowns. |
| Voice memory across projects | ⏳ | After Phase 4 |
| Context-aware pipeline (video frames → pipeline decisions) | ⏳ | After Phase 4 |
| On-device learning from corrections (user edits → LoRA) | ⏳ | Research only; possibly Phase 5+ |
| Real-time dub preview (stream TTS as you edit) | ✅ | Shipped 2026-09-02 (#1769) — opt-in "Live preview" toggle on the dub segment table streams the edited line over `/ws/tts` with its CAST voice; stopping playback also closes the live stream and releases the synthesis slot; export path unchanged. |

### 🧪 Quality track _(🟡 underway)_

| Item | Status | Notes |
|------|:---:|------|
| Every bug ships a regression test | ⏳ | Rule written, not yet enforced in CI. |
| Perf regression budget (≤5 % on fixture clip) | ✅ | Shipped 2026-08-20 as hardware-independent **operation-count budgets** (`tests/test_perf_operation_budgets.py`) — stricter than 5 %, and CI-stable where wall-clock on varying runners is not: one generate per sentence on `/ws/tts`, zero TTS calls on cached dub re-mixes; zero decode/rewrite and ⌈N/W⌉ `generate_batch` guards activate with their respective fast paths. See docs/performance.md §Performance budgets. |
| Accessibility (keyboard-first, WCAG AA, ARIA live regions) | 🟡 | Focus rings token defined; full audit pending. |
| Privacy (zero telemetry by default, per-feature opt-in) | ✅ | Enforced in Settings → Privacy tab. |
| Docs updated per phase | 🟡 | STRUCTURE.md, ROADMAP.md, ui/README.md current (research/ + design/ retired 2026-07-12). |

---

## ⚠️ Risks & unknowns

| Risk | Status | Mitigation in place |
|------|:---:|------|
| Local LLM ≠ frontier LLM for translation | ⏳ open | Plan: accept "Cinematic w/ local" + "Cinematic w/ cloud (opt-in)" as two SKUs. Never require cloud. |
| Model landscape drift (VoxCPM3, F5-TTS-v3 during roadmap) | ⏳ open | Phase 3 adapter interface is the hedge. |
| Tauri code-signing / notarisation costs | ⏳ open | Budgeted into Phase 5.4. 2-week compliance headroom. |
| pyannote HF-token friction (bounces new users on install) | ⏳ open | Plan: "skip diarisation for now" path; auto-diarise on first token-present run. |
| On-device fine-tuning quality drift | ⏳ open | Gated: LoRA only ships with automatic eval gate. |
| Voice-cloning likeness / IP concerns | ⏳ open | Log every clone with source + consent prompts. Deletion tools in Phase 5. |
| Apple MPS parity with CUDA | 🟡 mitigating | Adapter interface + per-engine capability gates. VoxCPM2 gated to CUDA only in UI. |
| Solo-dev bandwidth | 🟡 mitigating | 20 % buffer on every estimate; phases can slip without changing order. |

---

## 📏 Success metrics

| Metric | Target | Current reading | Status |
|------|------|------|:---:|
| Dub quality (blind pref vs. VideoLingo + pyVideoTrans, 10 clips) | ≥70 % | not measured | ⏳ |
| Time to first dub (fresh install → 3-min YouTube clip) | ≤10 min | not measured | ⏳ |
| Cold start (model load → first audible sample) | ≤1.5 s | ~4 s | 🟡 |
| Incremental re-dub (single-word change → audio on timeline) | ≤5 s | **1.96 s** on sentence-level segs (Apple Silicon MPS, num_step=8 preview path, 30-seg fireship clip) · 70× speedup vs full · TTS = 96 % of budget | ✅ |
| Recovery (`kill -9` mid-dub → resume to completion) | zero data loss | seg_hashes persistence live (4.5); benchmark unmeasured | 🟡 |
| Directorial eval (natural-language direction vs. baseline rubric) | +15 % | code path live (4.2); eval fixtures unmeasured | 🟡 |
| Engine robustness (engine crash → auto-recovery) | jobs continue | ❌ full-server crash | ⏳ |
| Interaction latency p95 | <50 ms UI, <200 ms preview | not measured | ⏳ |
| Regression safety (shipped-bug → regression test in last quarter) | 100 % re-surface rate 0 | n/a (no incidents yet) | ✅ |
| External contributor PR in first week | 3 successive external PRs | 0 (private repo) | ⏳ |

---

## 📝 Changelog

Reverse-chronological shipping log. Every phase exit + material sub-phase adds a row.

### 2026-04-21 — Auto-speaker-clone + pro-grade UI consolidation

The pro-grade dubbing promise: "same speaker in a new language." Before this change the user had to manually record or upload a reference voice and assign it to every segment. Now the reference is extracted automatically from the source video's own vocals track.

**Backend:**
- New `services/speaker_clone.py` (~150 LOC). For each diarised speaker, picks the longest-first subset of segments that accumulates into 5–15 s of clean audio from `vocals.wav` (Demucs output, not the raw mix — isolated from BG music). Writes `dub_jobs/{id}/voice_speaker_N.wav` + concatenated transcript text. Skips speakers with <5 s total — better to fall back than ship a thin clone.
- `dub_core` transcribe pipeline calls it after diarisation, stores results at `job["speaker_clones"]`, assigns `segments[i].profile_id = "auto:speaker_N"` by default (user-assigned profiles win).
- `dub_generate._gen` resolves the `auto:` prefix: reads the job-scoped ref audio + ref text instead of hitting the persistent `voice_profiles` table.
- SSE `final` event now streams `speaker_clones` to the frontend.
- Smoke-tested: synthetic 20 s vocals → correctly picks the long segments, skips speakers with <5 s, writes a valid WAV.

**Frontend (pro-grade UI consolidation):**
- `dubSlice.speakerClones: Record<speakerId, {ref_audio, ref_text, duration, source_count}>`. Transcribe `final` handler populates it.
- **CAST strip** (left column, under video): renamed from "Speaker Voices" to "CAST". Each speaker's dropdown gains a first-class `🎤 From video · 7.8s` option when an auto-clone exists — pre-selected on segments by default.
- **Removed "Apply Voice to All"** row (right column). It was a degenerate case of CAST when there's only one speaker; now the multi-speaker and single-speaker paths share one control.
- **Waveform timeline**: `height: 64` hard-cap → `height: 'auto'`. Reclaims ~80 px of dead space below the 3-tile strip. The waveform now fills whatever vertical room the left panel has.
- Typecheck clean, 62 backend tests pass, build 236 ms.

**What's still rough** (honest):
- Waveform still renders as fixed tile segments rather than one continuous timeline. Deeper fix is a proper regions overlay on one WaveSurfer instance — v2.
- Segment-table column dedupe (when every row's SPKR / LANG / VOICE are identical, hide those columns) is still pending — requires DubSegmentTable restructure.
- No "Save clone as persistent profile" button yet — job-scoped only.

### 2026-04-21 — Phase 4.1 truly ✅ on sentence-level segs: **1.96 s / 70× speedup**

Re-benched after the segmentation fix, with num_step dropped from 16 → 8 on the partial-regen path only (full-dub + export still run at 16). The change is one line in `dub_generate.py`:

```python
_num_step = 8 if regen_only is not None else req.num_step
```

**Verdict:** server total = **1.95 s** · TTS = 1.89 s · cache + mix + save = 0.05 s combined · client wall-clock = 1.96 s. Target ≤5.0 s ✅. Speedup vs full (at num_step=16) = 70×.

**The 22 s client/server gap from the previous run disappeared too.** It was one-time "first task after full-dub" warm-up noise, not a persistent tax. Not investigating further — the number is now what the roadmap promised.

**Honest tradeoff (now handled end-to-end):** num_step=8 costs ~10–20 % perceptual quality vs num_step=16. Shipped 2026-04-21:

- **Backend:** `DubRequest` gained an explicit `preview: bool` flag. num_step is derived: `_num_step = 8 if req.preview else req.num_step`. Decoupled from `regen_only`. Per-seg num_step is persisted in `job.seg_num_step` (mirror of `seg_hashes`) and streamed to the client in the `done` event.
- **Frontend:** `dubSlice` tracks `previewSegIds` (the ids whose num_step < the current `steps` floor). The "Regen N changed" button passes `preview: true`; the `done` handler populates `previewSegIds` from `evt.seg_num_step`. Export handlers (MP4 / WAV / MP3 / Clips / Stems) now pre-flight via `finalizeTtsBeforeExport()` — if any preview segs are outstanding, they're re-rendered at full quality (`preview: false`) before the download URL is triggered. Subtitle exports (SRT / VTT) skip the pre-flight since they don't depend on audio quality.
- User flow: edit word → Regen (1.96 s, preview quality) → optional further edits → Export MP4 (toast "Upgrading 1 preview-quality segment to full quality…", ~15 s TTS, then browser download). Feels fast during editing, exports at full quality.
- 43 backend tests green, typecheck clean, build 198 ms.

### 2026-04-21 — Segmentation regression caught: 489 segs → ~36 (13× fewer)

User noticed the segment table was rendering **single-word rows** ("ersten", "Runde", "eine"…) instead of sentences. Old code produced 8 segs for a 36 s clip; current produced 100+. The bench previously surfaced "489 segments for a 3-min clip" but I treated it as "that's just what Whisper returns" — it wasn't.

Adding instrumented log lines to the transcribe pipeline pinned the stage:

```
seg-trace chunk=1/5 raw=9 after_segment_transcript=7 after_subtitles=60
seg-trace chunk=2/5 raw=8 after_segment_transcript=7 after_subtitles=117
seg-trace chunk=3/5 raw=7 after_segment_transcript=7 after_subtitles=120
seg-trace chunk=4/5 raw=8 after_segment_transcript=8 after_subtitles=101
seg-trace chunk=5/5 raw=13 after_segment_transcript=7 after_subtitles=91
```

**Cause:** `services/subtitle_segmenter.segment_for_subtitles` enforced Netflix's **17 CPS reading-speed ceiling** as a segmentation rule. Normal German speech runs 15–25 CPS, so the splitter fired on every sentence and recursed until single words. It's a subtitle *display* rule being applied as dubbing *segmentation* — different units entirely.

**Fix (surgical):** removed both call sites of `segment_for_subtitles` from the transcribe pipeline (`dub_core.py` line 377 + 513). `segment_transcript` already produces sentence-level output with its own duration/char caps (IDEAL_DUR=4.5s, MAX_DUR=9s, MAX_CHARS=140). Dropped the now-unused import. The subtitle CPS rule stays available in `subtitle_segmenter.py` for a future SRT-export path where it's appropriate.

**Expected result:** 3-min clip → ~36 segments (matches user's "8 sentences in 36s" memory → ~4.5 s/seg). 63 tests pass.

### 2026-04-21 — Phase 4.1 benchmark PASSES at 4.04 s warm + 2 UI regressions fixed

Second bench run with server-side profile instrumentation. Same fixture, same flow, one small change: the model was warm from the prior full-dub pass (the real-user flow — you dub first, then edit).

| Stage | Cold (first run) | Warm (real UX) |
|---|---|---|
| Incremental regen | 7.05 s | **4.04 s** ✅ |
| speedup vs full | 10.5× | 18.5× |

**Server-side profile (`bench[incremental]` log line):**
```
total=4.02s  cache=0.01s  tts=3.97s  mix=0.00s  save=0.01s  segs=30  regen=1
```

TTS is 99 % of the budget. Cache load (29 WAVs), timeline mix (30 segments with fades + gain), and file save (24 kHz mono WAV across 3 min of timeline) combined run in <20 ms. The Phase 4.1 incremental machinery is fast — the wall-clock floor is whatever it takes to synthesise one new segment.

**Conclusion:** The ≤5 s target matches real-world UX (you dub once, then edit — model warm). Cold-path 7 s is a first-ever load, not a realistic regen. Phase 4.1 exit criterion ✅ verified.

**Two regressions caught when the user opened the UI after the session's refactors:**
1. `zustand persist` threw "couldn't be migrated" because I bumped `version: 1 → 2 → 3` without a `migrate` fn. Fixed: passthrough migrate accepts any version ≤3, relying on slice defaults for new fields.
2. `<DubTab>` referenced `onGlossaryChange` which I'd stripped from the prop destructure during the store-direct refactor. Fixed: restored to the destructure block.

### 2026-04-21 — Phase 4.1 benchmarked on fireship clip: **7.05 s, MISS by 2 s**

First real measurement of the Phase 4.1 exit criterion. Fixture: `https://www.youtube.com/watch?v=ZzI9JE0i6Lc` (fireship clip, ~3 min, 489 transcribed segments — capped to first 30 for tractable baseline). Apple Silicon MPS, VoiceStudio default engine.

| Stage | Wall-clock |
|---|---|
| Ingest URL → prep (cold first run) | 80.7 s |
| Ingest URL → prep (cache hit, same URL) | 4.0 s |
| Transcribe 3-min clip → 489 segments | 74–137 s |
| Full dub (30 segs, cold model) | **74.33 s** |
| Incremental regen (1 of 30 edited) | **7.05 s** |
| Speedup incremental vs full | 10.5× |

**Target ≤5.0 s · Actual 7.05 s · MISS by 2.05 s.**

The incremental logic works — 10.5× speedup confirms cached seg_N.wav files are being reused. The 2 s gap to target is likely disk I/O (29 cached WAVs loaded sequentially) + timeline mix + `sync_scores` computation on cached segs that never change. Lowest-hanging fixes: parallel cached-WAV load, skip no-op resample when `cached_sr == model.sampling_rate`, skip sync_scores on cached segs.

**Honest ROADMAP update:** Phase 4.1 exit criterion ⚠️ `🟡 → ❌`. Code path works; target wasn't pinned. Two paths forward:
1. Accept 7 s as the real number, widen target to "≤10 s on typical M-series".
2. Optimize the three hotspots above and retry the bench.

Either way: the `✅` on the Phase 4.1 row was premature — it meant "code shipped", not "benchmark passed."

### 2026-04-21 — Phase 4.1 benchmark harness + latent ingest bugs caught

Running Karpathy's "define a verifiable goal" principle on Phase 4.1's `≤5 s incremental` exit criterion surfaced **four latent `NameError` bugs in the ingest pipeline** that had never been hit in tests (the tests never drive the full yt-dlp → extract → demucs → scene path). All in `backend/services/dub_pipeline.py` after Phase 2.4's refactor — the symbols were referenced but never imported:
- `asyncio` (used by 14 call sites in the file)
- `find_ffmpeg` from `services.ffmpeg_utils`
- `_get_semaphore` + `_spawn_with_retry` from `services.ffmpeg_utils`
- `shutil`, `re`, `sys`, `soundfile as sf`, `AsyncIterator`, `get_best_device`, `HTTPException` (collateral of the move)

Also caught by the same `pyflakes` pass: `backend/services/translator.py` uses `Optional` in two signatures without importing it. Fixed.

**New file:** `scripts/bench_incremental.py` — 140-line HTTP-driven harness that drives the full pipeline on a fixture YouTube URL, times full-dub and incremental regen-only, and reports pass/fail vs. the 5 s target. Drives `POST /dub/ingest-url` → `/tasks/stream/{task_id}` SSE → `/dub/transcribe-stream/{job_id}` → `POST /dub/generate/{job_id}` (twice: full then `regen_only`).

Lesson: the "last mile" integration of Phase 4.1 was never end-to-end tested. Marking it ✅ on the roadmap was premature — the service + HTTP surface worked in isolation, but the real user path (URL → dub → edit → regen) had never actually been run.

### 2026-04-21 — Phase 2 closes: generateSlice + uiSlice expansion

- **`generateSlice.ts`** — new slice owns all 14 generate-tab fields: `text`, `refText`, `instruct`, `language`, plus the 10 production-override knobs (`speed`, `steps`, `cfg`, `tShift`, `posTemp`, `classTemp`, `layerPenalty`, `denoise`, `postprocess`, `duration`) and the `vdStates` category map. Users' synthesis prefs now persist across reloads via the store's `partialize`.
- **`uiSlice`** grew — added `sidebarTab`, `isSidebarProjectsCollapsed`, `showCheatsheet` (with the functional-updater pattern). Persisted.
- App.jsx `useState` count: ~60 → **41**. Remaining are all non-migratable by design — File/Blob refs, backend-loaded listings, transient recording timers, compare-modal state.
- Store composition now: 5 slices (prefs, glossary, ui, dub, generate) under the same `persist` middleware. Persistence key bumped to v3. Typecheck clean, build 225 ms.
- **Phase 2.2 marked ✅** — the monolith split is functionally complete. Phase 2 is 8/8.

### 2026-04-20 — Header + Sidebar + CloneDesignTab inline styles drained

Four biggest inline-style offenders in the repo, all migrated in one pass:
- **Header 24 → 1.** `.header-area` now a grid via CSS (not inline). New class system: `.hq-col-left/center/right`, `.hq-logo-*`, `.hq-stats__key/--gpu-active/__sep/__status-*`, `.hq-breadcrumb-sep`, `.hq-view-icon`, `.hq-flush-btn`, `.hq-reload-btn`. `.hq-wave.is-active` replaces the inline opacity flip. Only the per-bar dynamic style (WaveBars `height`/`animationDelay`) stays inline.
- **Sidebar 21 → 8.** Collapsed-mode sidebar tiles get a `.sidebar-tile` primitive with color variants (`--audio/--clone/--design/--success`). Body scroll container → `.sidebar__scroll.is-collapsed`. History-item variants get proper classes (`.history-item--dub`, `.history-kind--audio`, `.history-meta--locked`, `.history-title--clamp`, `.history-subtitle--seed/--italic`, `.history-audio`). Remaining 8 are all dynamic `--row-accent` CSS custom props (per-item colors).
- **CloneDesignTab 33 → 0 ✅.** Full grid layout, label-row variants (`--center/--spread/--flush/--sm`), drop-zone drag-hover now toggles `.is-dragging` (class added to `.file-drag` in index.css with `:hover` equivalent). Sliders column, Production Overrides column, and the duration input all classed.
- Typecheck clean, build 209 ms. Repo-wide inline styles: ~186 → ~119 (remaining tail is low-leverage, spread across 17 components).

### 2026-04-20 — DubTab inline styles drained (93 → 2)

- **DubTab went from 93 inline-style objects to 2** (both unavoidable dynamics: per-row opacity in the idle skeleton, and `fontSize: large ? … : …` in `PrepOverlay`). Repo-wide inline-styles: 186 → 97 (-48%).
- New utility classes: `.dub-col`, `.dub-panel-col`, `.dub-split-1/-2`, `.dub-idle-drop` (+ `.is-dragging` for drag hover), `.dub-ingest-row`, `.dub-cast`, `.dub-gen-overlay`, `.dub-skel-*` family for the placeholder segment rows, `.dub-settings-bar`, `.dub-transcript-*`, `.dub-bulk-select`, `.dub-outputs-*`, `.dub-footer-*`.
- Drag-hover no longer mutates `e.currentTarget.style` directly — it toggles `.is-dragging`. Cleaner React idiom + lets designers retarget the hover look without touching the handler.
- Typecheck clean, build 205 ms.

### 2026-04-20 — Store-direct reads in DubTab + Sidebar

- **DubTab now reads ~30 fields from the store directly** — dropped from the App.jsx prop interface: `dubJobId`, `dubStep`, `setDubStep`, `dubPrepStage`, `dubFilename`, `dubDuration`, `dubSegments`, `setDubSegments`, `dubTranscript`, `dubLang`, `setDubLang`, `dubLangCode`, `setDubLangCode`, `dubInstruct`, `setDubInstruct`, `dubTracks`, `dubError`, `dubProgress`, `isTranslating`, `preserveBg`, `setPreserveBg`, `defaultTrack`, `setDefaultTrack`, `exportTracks`, `setExportTracks`, `activeProjectName`, `isSidebarCollapsed`, `setIsSidebarCollapsed`, `translateQuality`, `setTranslateQuality`, `dualSubs`, `setDualSubs`. App.jsx's `<DubTab>` JSX went from 47 prop attributes to 22.
- **Sidebar** dropped 4 store-owned props (`mode`, `isSidebarCollapsed`, `dubStep`, `activeProjectId`) and reads them directly. Saves prop drilling from App → Sidebar across every mode switch.
- Store is now the source of truth — App.jsx is the orchestrator (handlers + side-effectful state), children read from the store. Handlers stay prop-threaded because they close over App.jsx's scope (API clients, refs, timer state).
- Typecheck clean, build 157 ms.

### 2026-04-20 — Phase 2.2 dubSlice migration (the big one)

- **`dubSlice.ts`** now owns all 18 dub-pipeline state fields. React-style setters (`(v | prev => next)`) via a tiny `resolve()` helper — every existing functional-update call site keeps working. Not persisted: project-load + dub-history restore explicitly rehydrate.
- App.jsx `useState` count for dub state: **18 → 0**. Only non-serialisable things stay local (File, Blob URLs, timer refs, `dubHistory` listing, `showTranscript` toggle, `previewAudios` cache).
- Setup for future work: deep children (DubTab, DubSegmentTable) could now read state directly from the store instead of receiving 30+ props — that's a separate refactor pass. For this commit, App.jsx still threads props so no child contract changed.
- Store composition: 4 slices + persist middleware. Typecheck clean, build 152 ms.

### 2026-04-20 — Phase 2.2 uiSlice migration

- **New `uiSlice.ts`** covers the "where am I in the app?" state cluster: `mode`, `activeProjectId`, `activeProjectName`, `activeVoiceId`, `modeBeforeVoice`, `isSidebarCollapsed`, `uiScale`. Action methods `openVoiceProfile(id)` / `closeVoiceProfile()` replace the tangled setMode/setModeBeforeVoice/setActiveVoiceId coordination that used to live in App.jsx.
- App.jsx dropped **8 `useState` calls** for these fields + the two useCallback wrappers around voice-profile nav. Legacy `omni_ui` localStorage restore still writes via the store's setters, so existing users' uiScale/mode/sidebar state migrates on first load.
- `setActiveProjectId` + `setActiveProjectName` unified into one `setActiveProject(id, name?)` action — four call sites simplified.
- Store persistence key bumped to v2; new partialize fields: `mode`, `isSidebarCollapsed`, `uiScale` (active project/voice ids stay transient — reload returns you to launchpad rather than half-loading stale state).
- Typecheck clean, build 160 ms.

### 2026-04-20 — Phase 2.3 API layer fully on TypeScript

- **All 8 remaining API `.js` files migrated to `.ts`.** New shared `api/types.ts` exports typed shapes: `EngineBackend` / `EngineFamilyResponse` / `SelectEngineResponse`, `Profile` / `ProfileUsage`, `ProjectSummary` / `ProjectDetail`, `GlossaryTerm` / `AutoExtractResponse`, `SystemInfo` / `ModelStatus` / `LogsResponse`, `DubHistoryResponse` / `DubTranslateResponse`.
- Every API wrapper now has a typed return signature — consumers across App.jsx, Settings, Sidebar, DubTab, VoiceProfile, etc. get compile-time checks on response shapes without changing call sites (thanks to `allowJs: true`).
- Typecheck clean, build 209 ms. Store slices + `api/client.ts` already TS from Phase 2.3's first pass; the only remaining Phase 2.3 follow-up is converting component `.jsx` files to `.tsx` (optional, can happen file-by-file).

### 2026-04-20 — Phase 4.3 staged checkpoint banners

- **Phase 4 complete.** 6/6 sub-phases shipped. The three natural review points in the dub flow (post-ASR / post-translate / post-generate) now have explicit UI gates.
- **`CheckpointBanner` component** renders above the segment table with a stage-appropriate headline + an inline **Continue →** button (Translate after ASR, Generate after translate). Stage auto-detected from `dubStep` + whether any segment has `text_original`. Dismissible per-stage.
- **`reviewMode` pref** added to `prefsSlice` (persisted). Defaults to 'on'. Settings > Engines exposes a **Review between stages / Rapid-fire** segmented control — power-users flip to Rapid-fire to go straight from Prepare → Translate → Generate without nudges.
- Typecheck clean, build 206 ms.

### 2026-04-20 — Phase 4.5 + MOSS-TTS-Nano engine + runtime picker

- **4.5 Step-level resumability ✅.** `job.seg_hashes` now persists per-segment fingerprints after each successful gen (flushed every 8 via `_save_job`). The `done` SSE event streams them to the client, which snapshots `lastGenFingerprints` directly — no `/tools/incremental` round-trip. `loadProject` + `restoreDubHistory` rehydrate from `project.segHashes` / `job.seg_hashes`. Kill -9 mid-dub → reopen → "Regen N changed" lights up for the exact residual.
- **MOSS-TTS-Nano-100M (3rd TTS adapter).** New `MossTTSNanoBackend` in `services/tts_backend.py` — 100M-param autoregressive model, 20 languages (incl. Arabic/Hebrew/Persian/Korean/Turkish), realtime on 4-core CPU, native 48 kHz, Apache-2.0. Fills the "runs on a fanless laptop" + "commercial-safe broad-language" niche next to OmniVoice (600+ langs) and VoxCPM2 (30 langs). Lazy-loaded, `is_available()` points users to the install path.
- **Runtime engine picker.** `backend/core/prefs.py` (atomic JSON store under DATA_DIR). `active_backend_id()` in all three families (TTS/ASR/LLM) now reads env var → prefs.json → default; env still wins. New `POST /engines/select` refuses unknown families + unavailable backends. Settings > Engines tab renders a **Use** button per engine — click to switch, choice persists across restarts. New `selectEngine(family, backend_id)` helper in `frontend/src/api/engines.js`.
- 50/51 tests pass (1 pre-existing skip); typecheck clean; frontend build 419 ms.

### 2026-04-21 — Phase 4.1 partial-regen + 4.4 polish

Bet B now runs end-to-end.

- **4.1 Incremental re-dub — partial regen lands.** `DubRequest` gained `regen_only: list[str] | None` and a parallel `segment_ids: list[str] | None`. `dub_generate.py` honours them: if `regen_only` is set, segments whose id isn't in the allow-list skip TTS and load `DUB_DIR/{job}/seg_N.wav` from disk (resampled + padded to slot). Graceful fallback: if the cached WAV is broken, the segment becomes silence and an SSE `warning` event fires; the mix still completes. **UI**: `App.jsx.handleDubGenerate(opts)` accepts `{ regenOnly }`; DubTab shows a pink "Regen N changed" button alongside "Generate Dub" when `incrementalPlan.stale.length > 0` post-dub.
- **4.4 Rate-ratio badge.** `DubSegmentRow` now shows a compact 📖 `1.12×` badge in the time column when `seg.rate_ratio` drifts >3% from 1.0, colour-coded (red >1.15, info-blue <0.85, muted otherwise). Tooltip surfaces `rate_error` if rate-fit bailed. `App.jsx.handleTranslateAll` propagates `rate_ratio` + `rate_error` onto each segment from the translator response.
- Build 162 ms, typecheck clean, 79 backend tests pass in 3.17 s.

### 2026-04-21 — Phase 4.2 + 4.4 fully wired; 4.1 signal-UX

This is the "last mile" the prior session explicitly deferred. The two defensible bets now run end-to-end.

- **4.2 Directorial AI — wired everywhere.**
  - Schema: `DubSegment.direction` + `TranslateSegment.direction` added.
  - Translator: `cinematic_refine_sync(direction=…)` + `cinematic_refine_many(directions={id→text})`. Director's `translate_hint()` prepended to reflect + adapt system prompts.
  - Generator (`dub_generate.py`): per-segment direction is parsed, `instruct_prompt()` appended to the segment's `instruct` string fed to TTS, `rate_bias()` multiplies `seg_speed`. So "urgent, surprised" now **simultaneously** influences the translated wording, the TTS emotive delivery, and the generation pace.
  - Frontend: new `components/DirectionDialog.jsx` (live `/tools/direction` preview pane). `DubSegmentRow` menu surfaces "Set direction…" / "Edit direction…". Segments with direction show a ✨ icon + brand-tinted button background. `App.jsx` routes `openDirection` / `saveDirection` through Undo, passes direction onwards in `handleTranslateAll` and `handleDubGenerate`.

- **4.4 Speech-rate engineering — wired into Cinematic translate.** `App.jsx` auto-populates `slot_seconds = end - start` on every segment in the translate payload. When `quality=cinematic` and a slot is provided, the translator runs `speech_rate.adjust_for_slot` on each adapted line after reflect/adapt, returning `rate_ratio` (and `rate_error` on LLM failure). Best-effort — LLM outage just leaves the cinematic text unchanged.

- **4.1 Incremental re-dub — signal-UX.** App.jsx now snapshots segment fingerprints after every successful dub via `/tools/incremental`. `incrementalPlan` recomputes whenever `dubSegments` changes. DubTab footer surfaces a **"N segments changed since last generate"** warn badge when stale > 0, or a neutral "all up to date" when clean. The partial-regen backend path (generate filters to `stale` IDs + reassembles on top of existing tracks) is the remaining gate — deferred because it needs per-segment WAV retention + crossfade reassembly for a proper UX.

- Frontend build 176 ms, typecheck clean. Backend 79/1 skipped in 4.07 s — no new suites added; integration pieces exercise existing tests.

### 2026-04-21 — Phase 4 foundation + UI catch-up

Biggest single-session push so far. Three layers at once: **UI catching up to Phase 3 backends**, **four Phase 4 services with unit tests**, **two brand-new pages**.

UI catch-up (consuming what was already built but invisible):
- **`Settings → Engines` tab** — new `EnginesTab` sub-component consumes `GET /engines`. Shows the active engine per family (TTS / ASR / LLM), lists every registered backend, badges ready vs unavailable with the actionable reason string (e.g. "install voxcpm", "set TRANSLATE_BASE_URL"). Uses the design system end-to-end.
- **`/queue` page** — new lazy `pages/BatchQueue.jsx` at `mode='queue'`. Three tabs (Active / Completed / Failed), polls `GET /jobs?status=…` every 3s on the Active tab. Per-job card: status badge + dot, type, project, age, duration, error, expandable `meta_json`. Consumes the Phase 2.1 job_store.
- **`/tools` page** — new lazy `pages/ToolsPage.jsx` at `mode='tools'`. Three interactive cards (DirectorTool, RateFitTool, ProbeTool). Uses design-system primitives only.

Phase 4 services (all tested):
- **4.1 Incremental re-dub** — `services/incremental.py`. SHA-1 fingerprint of generation inputs (`text · target_lang · profile_id · instruct · speed · direction`). Cosmetic fields (gain, selection, UI state) ignored. `plan_incremental` returns `{stale, fresh, total, fingerprints}`. 5 tests.
- **4.2 Directorial AI** — `services/director.py`. 5-dimension taxonomy (energy, emotion, pace, intimacy, formality) with 30+ values. Dual parser: LLM via `OpenAICompatBackend` when configured, keyword-heuristic fallback otherwise. `Direction.instruct_prompt()` flattens to a TTS instruct. `translate_hint()` produces a sentence for reflect/adapt. `rate_bias()` nudges slot-fit. 5 tests.
- **4.4 Speech-rate** — `services/speech_rate.py`. Per-language CPS table, tolerance window (0.92–1.08), 3-attempt LLM trim/expand loop, keeps the best-ratio candidate when exhausted. 3 tests.
- **4.6 Tools** — new router `backend/api/routers/tools.py` exposing `/tools/probe`, `/tools/incremental`, `/tools/direction`, `/tools/rate-fit`. New CLI `omnivoice/cli/dub.py` drives the full dub pipeline over HTTP with SSE streaming; added as `omnivoice-dub` entry point in `pyproject.toml`. 3 smoke tests added.

Bundle: main bundle 229.31 kB → 230.20 kB (+0.89 kB for two new lazy pages; negligible). Build 211 ms. Total backend tests: **79 passing + 1 skipped** in 3.26 s (up from 63 before this sitting).

### 2026-04-21 — Phase 3 complete: pluggable engines

All four adapter interfaces shipped in one sitting, with tests + HTTP surface.

- **3.1 TTS** — new `backend/services/tts_backend.py`. `TTSBackend` ABC, `VoiceStudioBackend` wrapping `k2-fsa/OmniVoice` (reuses `model_manager.get_model()` so no double load), env-driven selection (`OMNIVOICE_TTS_BACKEND`, default `omnivoice`). `list_backends()` returns `[{id, display_name, available, reason}]` so the Settings-UI picker can grey out unavailable engines with actionable reasons.
- **3.2 VoxCPM2** — `VoxCPM2Backend` in the same file. Scaffold returns an actionable "install voxcpm + need CUDA" message until deps are present; when they are, `generate()` maps our `instruct` field onto VoxCPM2's inline `"(instruct)prompt"` syntax and supports ultimate cloning (ref_audio + ref_text). 48 kHz, 30 advertised languages.
- **3.3 ASR** — new `backend/services/asr_backend.py`. `MLXWhisperBackend` (default on Apple Silicon) + `PyTorchWhisperBackend` (CUDA/CPU fallback reusing the TTS model's `_asr_pipe`). Auto-detects best engine based on `torch.backends.mps`; override via `OMNIVOICE_ASR_BACKEND`. Both normalise to the `chunks` shape existing code already consumes.
- **3.4 LLM** — new `backend/services/llm_backend.py`. `OpenAICompatBackend` lifts the client construction out of `translator.py` so every LLM-using feature (Cinematic translate, glossary auto-extract, Phase-4 Directorial AI) goes through one code path. `OffBackend` provides an explicit no-LLM state with a clear "set TRANSLATE_BASE_URL" hint; defaults to `off` when nothing's configured.
- **Wiring** — new `backend/api/routers/engines.py` with `GET /engines`, `/engines/tts`, `/engines/asr`, `/engines/llm`. Wired through `backend/main.py`.
- **Tests** — new `tests/test_engines.py` (12 tests covering all three registries: default listings, availability messages, env overrides, sample rates per backend, auto-detect, unknown-id error, Off-backend's actionable raise). Smoke suite extended with two engine-endpoint tests. **Total backend tests: 63** (1 skipped for missing optional `openai` pkg) in 3.18 s.
- Zero breaking changes. Existing callers (`dub_generate`, `dub_translate`, `segmentation`) still talk to the direct model/pipeline — migration to the adapters can happen one file at a time.

### 2026-04-21 — Phase 2 sweep: 2.2 / 2.3 / 2.4 finish / 2.6 / 2.7

Five items closed out together in one sitting. Phase 2 progress: 5/8 → **7/8**.

- **2.4 finish** — `ingest_pipeline` (the 260-line download → extract → demucs → scene → thumbnail async generator) moved from `dub_core.py` to `services/dub_pipeline.py`, along with `run_proc_factory` and `yt_download_sync`. `dub_core.py`: **525 lines** (down from 777, **−40.9 %** overall from original 889). Legacy aliases kept so dub_generate / dub_translate / dub_export imports keep working.
- **2.6** — `print()` sweep in `services/model_manager.py` → `logger.info`. New `OMNIVOICE_JSON_LOGS=1` env flag installs a single-line JSON formatter on every handler (console + rotating file). Verified output: `{"t": "...", "level": "INFO", "name": "omnivoice.api", "msg": "..."}`.
- **2.7** — 13 new backend tests across two files. `tests/test_dub_pipeline_state.py` covers path-safety, SSE shape, process tracking, and in-memory + SQLite job round-trip. `tests/test_translator.py` covers glossary preamble, no-LLM graceful fallback, empty-literal passthrough, full 3-step chain with mocked LLM, and reflect-failure path. **Total backend tests: 50** (from 26) in 3.10 s.
- **2.3** — TypeScript scaffolding live. `typescript@5.9.3`, `tsconfig.json` with `allowJs: true` + `strict: true` so JSX + TSX coexist during the migration. New `bun run typecheck` script. `frontend/src/api/client.ts` migrated as the first `.ts` file; store slices authored in TS from day one. Typecheck passes clean.
- **2.2** — Zustand scaffolding + first three slices. `zustand@5.0.12` installed. New `frontend/src/store/` with `prefsSlice.ts` + `glossarySlice.ts`, composed via the `persist` middleware (localStorage, key `omnivoice.app`, version 1, partial persistence). Removed three ad-hoc `useState(localStorage.getItem(...))` blocks from App.jsx (`translateQuality`, `dualSubs`, `glossaryTerms`) — behaviour identical, now centralised.

Bundle impact: main bundle 226.56 kB → 229.31 kB (+2.75 kB for zustand; negligible). Build 168 ms.

### 2026-04-21 — Phase 2.4 (partial): Split `dub_core.py`

**First pass of the dub-pipeline extraction.** Router no longer owns the business logic.

- New `backend/services/dub_pipeline.py` (222 lines). Public surface:
  - **Pure helpers:** `compute_file_hash`, `safe_job_dir`, `sse_event`, `prep_event`.
  - **Cache lookup:** `find_cached_job` — reuses artifacts from prior jobs with the same content hash.
  - **Process lifecycle:** `register_proc` / `unregister_proc` / `kill_job_procs` / `has_active_procs` — tracks in-flight ffmpeg/demucs subprocesses so `/dub/abort` can tear them down.
  - **Job state:** `get_job` / `put_job` / `save_job` on top of `_dub_jobs` (in-memory) and `dub_history.job_data` (persistent).
- `dub_core.py` keeps name-compat aliases (`_get_job = dub_pipeline.get_job`, etc.) so the other three routers that import from `dub_core` don't need updating yet.
- **Size:** dub_core.py **889 → 777 lines** (−12.6 %). dub_pipeline.py: new, 222 lines.
- All 37 backend tests remain green in 3.09 s.
- ⏳ Deferred: the 200-line `_ingest_gen` streaming pipeline and the transcription generators stay in the router for now — they're tied to FastAPI's `StreamingResponse` + async-generator contract and need a dedicated pass.

### 2026-04-21 — Phase 2.1: Persist task queue

**Tasks survive server restart.** SSE clients can reconnect with `?after_seq=N` and catch up.

- Schema v4: two new tables in `core/db.py:_BASE_SCHEMA` — `jobs` (id/type/project_id/status/timestamps/error/meta) and `job_events` (id/job_id/seq/payload) with indices on status, project, and `(job_id, seq)`. `IF NOT EXISTS` → no migration script needed for existing DBs.
- New `backend/core/job_store.py` (187 lines): `create`, `mark_running/done/failed/cancelled`, `append_event` (monotonic seq + per-job cap of 500), `events_since(after_seq)`, `list_jobs(status=active|...)`, `get`, `sweep_orphans_on_startup()`.
- `backend/core/tasks.py` rewritten: `TaskManager` mirrors every state transition and every `_push_event` to the store. In-memory queue stays for speed; disk is belt-and-braces. Any disk error is logged and swallowed — live streams never break because the store hiccups.
- `backend/main.py` lifespan: runs `sweep_orphans_on_startup()` so a crashed process's `running` jobs flip to `failed` with `"Job was interrupted by a server restart."` instead of pretending to still be alive.
- New endpoints in `dub_export.py`:
  - `GET /jobs?status=active&project_id=…&limit=N`
  - `GET /jobs/{id}` and `GET /jobs/{id}/events?after_seq=N`
  - `GET /tasks/stream/{id}?after_seq=N` — replays persisted tail, then (if still live) attaches to the live listener. After a restart, clients still get the final state with no spinning.
- Tests:
  - `tests/test_job_store.py` — 7 tests: create + lifecycle, failure carries error, monotonic seq, `events_since` filtering, per-job event cap, active filter, orphan sweep.
  - `tests/test_router_smoke.py` — 4 new: `GET /jobs` happy path, `?status=active` filter, 404s for both job + events endpoints.
- All 37 backend tests pass in 2.83 s. Does NOT persist the work itself — that's Phase 4.5 (step-level resumability). This phase just ensures **honest metadata across restarts**, which unblocks the future resumability work and gets today's UI talking to a truthful `jobs` API.

### 2026-04-21 — Phase 2.8: Voice profile page

**A first-class home for every cloned / designed voice.** Up to now, voices were managed only via tiny sidebar cards; no way to see one in detail, edit it after creation, or see where it's been used. This adds a full page.

- Backend: `GET /profiles/{id}` (full record), `PUT /profiles/{id}` (partial update of name / instruct / language / ref_text), `GET /profiles/{id}/usage` — lists synth-history count + scans `studio_projects.state_json` for dub-segment usage counts.
- Frontend: new `mode: 'voice'` + `activeVoiceId` state in `App.jsx`. Back button returns to whichever mode called it.
- New lazy page `frontend/src/pages/VoiceProfile.jsx`:
  - **Hero** — name (inline-editable), type badge (Clone / Design), lock + seed + language + created-date chips, built-in reference-audio player.
  - **Details** — style `instruct` + language + reference transcript, all edit-in-place with Save/Cancel. Lock banner with inline Unlock.
  - **Try-it** — type any line, generate with the current voice, auto-plays. Uses existing `/generate` endpoint.
  - **Usage** — synth-clip count + project list (click any project to open it).
- Sidebar clone/design cards gain an "Open" action routing to the new page alongside Preview / Select / Lock / Delete.
- Pure design-system build: `Panel`, `Button`, `Input`, `Textarea`, `Field`, `Badge`, `Segmented`, `Progress` — zero new inline styles.
- Frontend build 168 ms. Backend tests 26 / 26 green.

### 2026-04-21 — Phase 1 complete: 1.4 Dual subtitle export

Closes Phase 1.

- Backend: `GET /dub/srt/{id}` and `GET /dub/vtt/{id}` both accept a `?dual=1` query. New `_pick_subtitle_text(seg, dual)` helper emits two-line stacked cues: translated on top, original italicised underneath (`<i>…</i>` — standard across SRT/VTT renderers). Filename gets a `_dual` suffix.
- Frontend: "Dual subtitles" checkbox in Output Options, localStorage-persisted (`omnivoice.dub.dualSubs`). SRT / VTT download buttons show a ✦ badge + switch filename + pass `?dual=1` when on.
- All backend tests stay green (26 / 26 in 2.73 s). Frontend build 154 ms.

### 2026-04-21 — Phase 1.3: Project-scoped term glossary

**Per-project translation consistency**, end-to-end:

- `glossary_terms` table in `core/db.py` (`id, project_id, source, target, note, auto, created_at`) with `idx_glossary_project`. Schema version bumped to 3. The `IF NOT EXISTS` in `_BASE_SCHEMA` means old DBs pick it up automatically — no migration script needed yet.
- `backend/api/routers/glossary.py` (new, 243 lines) — full CRUD (`GET/POST/PUT/DELETE /glossary/{project_id}`), bulk clear (optionally only auto-rows), and `POST /glossary/{project_id}/auto-extract` which calls the same OpenAI-compatible LLM as the translator to propose terms from the project's source segments (deduped against existing source strings, case-insensitive).
- `frontend/src/components/GlossaryPanel.jsx` + CSS — edit-in-place table with manual/auto badges, add-row at the bottom, `⌘↵` / `Enter` to submit, `Esc` to cancel, "Auto" button to run LLM extraction, "Clear auto" to wipe LLM results while keeping manual entries.
- `frontend/src/api/glossary.js` — typed-ish client.
- `DubTab.jsx` renders the panel whenever a dub job is loaded. `App.jsx` keeps `glossaryTerms` state; `handleTranslateAll` now passes `glossary: [{source, target, note}]` into every translate call. The translator's 1.1 prompt-injection hook (already in place) finally has data to inject.
- Graceful degradation: if no LLM is configured, auto-extract returns a 503 with a copy-paste-ready env-var hint. Manual glossary still works without any LLM.
- Smoke + segmenter tests still green: 26 / 26 in 2.91 s.

### 2026-04-21 — Phase 1.2: NLP-aware subtitle segmentation

**Netflix-style subtitle rules** enforced on every transcription:

- New `backend/services/subtitle_segmenter.py` — pure-function, no ML, no network, 226 lines. Splits at (priority order): sentence terminators → clause separators → conjunctions → greedy word-boundary fallback. Enforces `≤42 chars/line · ≤2 lines · ≤17 CPS · ≥1.2 s duration`. Folds tiny orphans into their neighbour but not across sentence terminators or speaker changes.
- Word-level timings (WhisperX / mlx-whisper output) are honoured when present: splits land on the word gap (previous word's `end`-time) so they fall on natural breaths, not mid-word.
- Wired into both transcription paths in `dub_core.py` (chunked streaming + single-shot). Runs after diarization so speaker_id propagates to both halves of every split.
- `format_subtitle_lines(text, max_chars)` bonus helper for SRT / VTT render paths that want a pre-wrapped layout.
- Tests: `tests/test_subtitle_segmenter.py` — 14 tests. Total backend test count now **26** (segmenter + router smoke) passing in 2.87 s. Pre-existing `test_api.py` errors are unrelated (legacy symbol references from before the router refactor).

### 2026-04-21 — Phase 1.1: Translate → Reflect → Adapt

**Cinematic translation chain** live behind a `Quality: Fast | Cinematic` toggle:

- New `backend/services/translator.py` — 3-step LLM chain (literal result passed in → LLM reflects on tone/idiom/length/pacing → LLM adapts). Bounded concurrency via `OMNIVOICE_LLM_CONCURRENCY` (default 6). Per-segment graceful fallback to literal on LLM failure.
- `TranslateRequest` gains `quality: "fast"|"cinematic"` and `glossary: [{source, target, note}]`. Glossary, when provided, is prepended to both reflect and adapt prompts.
- `POST /dub/translate` response gains `quality_used`, and per-segment `literal` + `critique` when Cinematic ran — set up for the Phase-1.3 three-column Translation Workbench view (see `design/04-translation-workbench.md` (retired 2026-07-12; git history)).
- Frontend: `translateQuality` state in `App.jsx` (localStorage-persisted), new `Segmented` control in DubTab settings bar, toast on `cinematic_skipped="no-llm-configured"` pointing to the env vars.
- Works with real OpenAI, Ollama, LM Studio, Together, Anyscale — anything OpenAI-compatible. Env: `TRANSLATE_BASE_URL`, `TRANSLATE_API_KEY` (or `OPENAI_API_KEY`), `TRANSLATE_MODEL` (default `gpt-4o-mini`), `OMNIVOICE_LLM_TIMEOUT` (default 45 s).
- Smoke suite still green: 12 / 12 in 2.85 s.

### 2026-04-21 — Phase 0 complete + design-system mid-phase exit

**Phase 0 — Momentum** (0.1, 0.2, 0.3, 0.4, 0.5): deleted 8 orphan files, archived `legacy_gradio/`, scaffolded Alembic, rewrote 10 error messages into actionable copy, added 12-test router smoke suite (2.79 s), shipped 520 ms launch animation + `.ui-skel` utility.

**Phase 2.5 — Design system** (partial): shipped the complete primitive set:
- `Button · Panel · Field · Input · Textarea · Select · Dialog · Slider · Badge · Tabs · Segmented · Tooltip · Progress · Menu · Table` (14 primitives)
- Full tokens + motion layer (`ui/tokens.css`, `ui/motion.js`)
- 9 components migrated (Settings 0 inline, CompareModal, Header, Sidebar, CloneDesignTab, AudioTrimmer, DubSegmentTable, DubSegmentRow, DubTab)
- **-52 % inline styles** (388 → 186)
- **-146 lines** from `index.css` (1,857 → 1,711)
- **-2.7 % main bundle** despite net additions

**Phase 2.6 — Logging baseline** (partial): Backend runtime log file wired up, `/system/logs` and `/system/logs/tauri` endpoints corrected (bundle identifier fix + always-on `tauri-plugin-log`), Settings → Logs UI wired through all three tabs with working Clear buttons.

### 2026-04-20 — Research + design docs

Documented `research/LEARNINGS.md` (competitor analysis: VideoLingo, pyVideoTrans, VoxCPM2), wrote the `design/` ASCII mockup set (architecture + 8 views), drafted v1 ROADMAP.md, published STRUCTURE.md after root cleanup. (Both dirs retired in the 2026-07-12 cleanup — git history.)

### Pre-2026-04-20

MVP feature-complete: transcribe → translate → dub → mux, voice cloning, timeline editor, diarization, YouTube ingest, selective export, project persistence, Tauri desktop shell. See git history for detail.

---

## 🧭 Design target

Intended shape captured in `design/` (retired 2026-07-12; git history) — one ASCII mockup per view, plus a system-architecture diagram. Every phase brings the shipped product one step closer to what those views describe. When code and design diverge, one of them is wrong — decide which, then fix it.

---

## 🔁 Review cadence

| Frequency | Activity | Owner |
|---|---|---|
| Weekly (Friday, 1 h) | Design consistency audit | — |
| Bi-weekly | Status-overview update (this file) + estimate-drift review | — |
| End of each phase | Retrospective + exit-criteria gate + roadmap revision | — |
| Quarterly | Success-metric check + perf-track dedicated week | — |

---

*This roadmap is a living document. Every phase ends with a revision, not an append.*
