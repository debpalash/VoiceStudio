# TASK #34 — Runtime-verify shipped longform features (QA spec)

> A manual + (where automatable) verification plan for the Web-Audio / ffmpeg / SSE
> paths shipped this session: Audiobook (metadata, cover, loudness, preview, resume,
> import, lexicon, SSML-lite), Stories `/longform/render`, the full-width layouts,
> and the cohesion wins (Launchpad card + Projects library). Every step is grounded
> in the actual code (`file:line`), verified against the tree as of `main@df94af8`.
>
> **State under test:** these PRs are **already merged to `main`** — `feat/longform:`
> PRs **#411–#426** (commits `7af5143`→`df94af8`: per-chapter preview/resume #411,
> import #412, shared renderer #413, per-line speed #416, job library #417, review
> fixes #418, lexicon #419, full-width layout #420, SSML-lite #421, pronunciation/markup
> UI #422, cache eviction #423, control-token alignment #425, cohesion quick-wins #426).
> There is no live `feat/stories-shared-render` branch; verification runs against `main`
> (or a fresh branch off it). The repo HEAD at spec time is in fact `main@df94af8`
> (`git log -1` confirms commit `df94af8` "feat(longform): cohesion quick-wins … (#426)").
> Target: **v0.3.6, continuous-to-main** (all three version files —
> `frontend/src-tauri/tauri.conf.json:4`, `frontend/src-tauri/Cargo.toml:3`,
> `pyproject.toml:7` — already read `0.3.6` = latest release `v0.3.5` + 1 patch, per the
> versioning hard rule; verification work and any defect fixes land continuous-to-main
> with no RC, no soak, no re-version).
>
> *(Grounding note: the original draft cited a `feat/stories-shared-render` branch and a
> `e9481ef..df94af8` / PR #408 range — neither exists. Corrected to the actual merged
> commit/PR span above.)*

---

## TL;DR

This session shipped a **shared server-side chapterized renderer**
(`backend/services/longform_render.py` — the pure builders — driven by the impure SSE
generator `_render_longform_sse` in `backend/api/routers/audiobook.py:345-474`) behind
two front doors — the Audiobook tab (`POST /audiobook`, `audiobook.py:477-488`) and the
Stories Editor (`POST /longform/render`, `audiobook.py:516-543`) — plus import, a
pronunciation lexicon, SSML-lite markup, a job library (`GET /longform/jobs`,
`longform_jobs.py:146-158`), full-width layouts, and Launchpad/Projects cohesion.
The pure builders are already unit-tested. **What is NOT yet verified is the end-to-end
runtime: that the SSE stream, the real ffmpeg mux, the resume cache, the file actually
playing in a browser `<audio>` element, and the cross-feature wiring all work when the
app is actually run.** This spec is the runtime verification plan: a set of automatable
checks (pytest API-level + Playwright UI smoke) plus a manual checklist for the
ffmpeg/Web-Audio/playback paths that can't be fully automated without a GPU + ffmpeg.

The single most important thing to prove: **a user reaches a playable, chapter-marked
audiobook/story file** (the project's "first-run that actually works" core value), and
**none of the default behavior diverges across macOS/Windows/Linux** (loudness/cover are
opt-in by design; verify they stay opt-in).

**Completeness mandate (this revision):** the verification is only "done" when it has
exercised *every* state the feature can land in — not just the happy path. That means the
**empty-input** paths, the **partial-failure** paths, the **all-failure** paths, the
**missing-dependency** (no ffmpeg) path, the **invalid/hostile-input** paths (bad cover,
bad bitrate, oversize import, malformed EPUB, malformed SSE JSON, traversal cover_path),
the **resume/cache** states (hit / miss / corrupt-entry / evicted / key-invalidating
edit), the **abort/interrupt** states, and the **client playback-failure** states. The
edge-case enumeration below (§"States & edge cases") is the spine of the test plan; each
numbered case maps to an enumerated state.

**API-contract mandate (this revision):** the verification is contract-pinned. §"API /
data shapes" below now declares the **exact** request bodies (pydantic field-by-field),
the **exact** SSE event JSON per `type` (with the precise key set and the
producer-side `file:line` that emits it), the **exact** HTTP error bodies (`{detail}` +
status), the **DB read shape** (`jobs`/`job_events` columns + the recovery path), and the
**function signatures** of every seam a test injects into. A developer implementing
`tests/test_longform_e2e.py` or `frontend/e2e/longform.spec.ts` should not have to guess a
single field name, key, or type.

**Constraints mandate (this revision):** because this is a *verification* spec for
features that already ship in the default build, the verification is **also a constraints
audit**. Five VoiceStudio hard rules bear directly on these features — cross-platform
parity, local-first, backward-compatible data, CodeQL py/polynomial-redos, and
localization — and the test plan is required to *prove* each one holds, not assume it.
The new §"Constraints" section below states, rule-by-rule and grounded in `file:line`,
how each is satisfied and which verification case proves it. A constraint that a runtime
check *disproves* (e.g. a default that fails on WebKitGTK, or a hardcoded string that
leaks an i18n key) is, per the project rules, a **P0** — flagged, never papered over.

---

## Problem

The longform features landed as ~3700 lines across the PRs above. They are
covered by **pure unit tests** (backend: `tests/test_longform_render.py`,
`tests/test_audiobook.py`, `tests/test_audiobook_cover.py`,
`tests/test_audiobook_preview.py`, `tests/test_longform_import.py`,
`tests/test_longform_jobs.py`, `tests/test_longform_limits.py`,
`tests/test_pronunciation.py`, `tests/test_ssml_lite.py`; frontend:
`frontend/src/test/ssmlLite.test.js`, `frontend/src/test/storyToSpans.test.js`,
`frontend/src/test/sseParse.test.js`). But pure tests deliberately stub out the three
risky integration seams:

1. **ffmpeg** — `build_render_cmd` (`longform_render.py:232-285`) returns argv; nobody has
   confirmed the argv actually produces a valid, playable m4b/mp3 with embedded
   chapters/cover/loudness on a real ffmpeg binary across platforms. The
   cover-embed-for-MP3-skip (`embed_cover = validate_cover_image(cover_path) and not
   is_mp3`, `longform_render.py:260`) and `-disposition:v attached_pic`
   (`longform_render.py:271-272`) paths are the kind of thing that "looks right" but
   silently produces a corrupt file on some ffmpeg version. **This is also the
   cross-platform-parity seam:** the default render path (m4b, no loudness, no cover) must
   produce identical-shape output on mac/Win/Linux — a divergence here is a default-feature
   P0 (strict 2026-05-20 rule).
2. **SSE streaming** — the `_render_longform_sse` generator (`audiobook.py:345-474`)
   emits `started`/`chapter`/`chapter_error`/`assembling`/`done`/`error` events; the
   frontend read loops (`AudiobookTab.jsx:142-169`, `StoriesEditor.jsx:377-392`) parse
   them via the shared `splitSSEBuffer`/`parseSSELine` helpers (`sseParse.js:14,29`).
   Event-shape drift between producer and consumer is invisible to unit tests — and §"API
   / data shapes" now pins the exact key set each consumer reads (e.g. `started.chapters`,
   `chapter.index`, `done.failed_chapters`) so a missing/renamed key is a test failure.
3. **Web-Audio + browser playback** — the single-line Stories preview still stitches
   client-side (`StoriesEditor.jsx:287-338`), and every server output is played via an
   `<audio>` element fed by `/audio/<output>` (mounted at `main.py:716`). Nobody has
   confirmed in a running webview that the produced files actually play (WebKitGTK is
   notoriously picky about m4b/AAC). **This is the highest-risk cross-platform-parity
   gate** — see §"Constraints".

The risk is a feature that passes CI green but **fails the first time a real user clicks
"Create"** — exactly the failure mode the project's core value forbids. And the failure
is rarely the happy path: it's the **second** chapter that throws, the cover that's
secretly a `.gif` renamed `.jpg`, the EPUB with no spine, the network blip mid-SSE, the
abort click, the empty cast — the states pure tests never reach.

---

## Goal / Non-goals

### Goal
- Produce a **repeatable verification procedure** that exercises every shipped longform
  path end-to-end in a running app (backend + frontend webview), proving each path
  reaches a working, user-visible output **— and that every non-happy state degrades
  gracefully (visible error, no crash, no 500, no corrupt file, no silent hang).**
- **Pin and assert the exact API/SSE/DB contracts** (§"API / data shapes") so the
  producer↔consumer wire format is verified, not assumed — a renamed/missing event key,
  a wrong HTTP status, or a changed DB column shape is a hard failure.
- **Prove the five applicable hard-rule constraints hold at runtime** (parity,
  local-first, backward-compat data, no-ReDoS, localization) — see §"Constraints" for the
  case-by-case mapping. Any constraint a runtime check disproves is filed as a P0/blocker
  per the project rules.
- Add **automatable coverage** where it's cheap and high-signal: API-level integration
  tests (real ffmpeg, stub TTS) + Playwright UI smoke (renders, controls present,
  no console errors).
- Produce a **manual checklist** for the parts that genuinely need a human + GPU + audio
  output (actual voice quality, real-engine synthesis, cross-platform playback).
- Surface and log any **defects found during verification** as concrete `file:line`
  findings (this spec's execution feeds bug fixes, not new features).

### Non-goals
- Not building new features or fixing the bugs found (this is the *verify* task; fixes
  are follow-up commits/PRs — continuous-to-main, no re-version, per the versioning rule).
- Not GPU/engine quality benchmarking (MOS scores, voice fidelity) — out of scope.
- Not load/scale testing a 10-hour book (note as a manual spot-check, not a gate).
- Not re-testing already-green pure unit tests beyond running them as a baseline.
- Not adding telemetry, cloud verification, accounts, or any required network call
  (local-first constraint — all verification runs on-device; see §"Constraints").
- Not introducing a new DB table, alembic migration, or localStorage schema bump — this
  session shipped none, and verification must **confirm** that (backward-compat-data
  constraint), not add one.

---

## States & edge cases (the completeness spine)

Every verification case below maps to one of these states. A state with no test is an
untested state; the test plan is required to cover the **Verify** column for each.

### S0 — Empty / degenerate input
| State | Where | Expected behavior | Verify |
|-------|-------|-------------------|--------|
| Empty script / no chapters parsed (`/audiobook`) | plan has 0 chapters; reaches `_render_longform_sse` | **single terminal `error` "nothing to render (no chapters)"** at `audiobook.py:385-387` — emitted **before** the ffmpeg check, so even on a no-ffmpeg box you get *this* error, not the ffmpeg one | B-empty |
| Empty chapter list after retention filter (`/longform/render`) | all spans filtered out (no text + no pause) → `chapters=[]` → `AudiobookPlan(chapters=[])` | reaches the **same** "nothing to render (no chapters)" `error` (NOT "all chapters failed") — the retention filter at `audiobook.py:530-532` produces an empty plan, distinct from per-chapter synth failure | B-empty2 |
| Whitespace-only span text but pause present | `s.text=""`, `pause_ms_after>0` | span **retained** (pause-only span carries silence); chapter renders | B11 |
| Stories `generateAll` with no usable tracks | `usable.length===0` | front-end early-returns silently (`StoriesEditor.jsx:362`); **no** request fired | D18 (Stories) |
| Stories `storyToSpans` yields 0 chapters | `!chapters.length` | toast `stories.exportFailed`, no request (`:364`) | D18 |
| Import: empty file (0 bytes) | `/audiobook/import` | **400 `{detail:"empty file"}`** (`audiobook.py:108-109`) | C16 |
| Import: file decodes to empty/whitespace script | non-epub, all-whitespace text | **400 `{detail:"no text found in the file"}`** (`audiobook.py:119-120`) | C16 |
| Preview: `chapter_index` out of range | `/audiobook/preview` | **400 `{detail:"chapter_index out of range (0..n-1)"}`** (`audiobook.py:325-326`) | B13b |
| Preview: empty script | `/audiobook/preview` | **400 `{detail:"no chapters parsed from the script"}`** (`audiobook.py:322-323`) | B13c |

### S1 — Dependency / environment failure
| State | Where | Expected behavior | Verify |
|-------|-------|-------------------|--------|
| ffmpeg not on PATH / not bundled | `find_ffmpeg()` → None | single terminal `error` "ffmpeg not available; the output needs it" (`audiobook.py:388-391`); **NOT a 500**, **NOT a partial file** | B15 |
| ffmpeg present but the mux subprocess fails (bad argv, disk full, codec missing) | `run_ffmpeg` raises inside the outer `try` | caught at `audiobook.py:466`; emits generic `error` "render failed (see backend log)" (`:474`), full stack logged server-side only; `job_store.mark_failed` best-effort | B-ffmpeg-fail |
| `job_store.create`/`append_event`/`mark_*` raises | best-effort wrappers | swallowed (`audiobook.py:374-375`, `:381-382`, `:460-461`, `:471-472`); **stream still flows**, job just doesn't appear in the library | B14b |

### S2 — Per-chapter render outcomes (partial-failure matrix)
| State | Where | Expected behavior | Verify |
|-------|-------|-------------------|--------|
| Chapter renders fresh | synth path | `chapter` event `cached:false`; WAV written to cache | B1 |
| Chapter served from cache (resume hit) | `os.path.exists(wav_path)` true + WAV header readable | `chapter` event `cached:true`; **no** synth call | B7 |
| Cache entry exists but is **corrupt** (unreadable WAV header) | `wave.open` raises at `audiobook.py:292-297` | **falls through and re-renders** (the `except: pass` at `:296-297`); ends as a `cached:false` chapter, not an error | B-corrupt-cache |
| One chapter (index 1 of N) throws in synth | `except` at `audiobook.py:420-426` | `chapter_error` for index 1, `chapter` for the rest; loop **continues**; `done.failed_chapters==[1]`; `done.chapters==N-1` (count of muxed WAVs) | B9 |
| Multiple but not all chapters throw | same | each gets `chapter_error`; survivors muxed; `failed_chapters` lists all of them | B9b |
| **All** chapters throw | `if not chapter_files:` at `audiobook.py:434-436` | single terminal `error` "all chapters failed to render"; **no `assembling`, no `done`, no file** | B10 |
| Synth returns empty/zero-length tensor | `synthesize_chapter` returns `(torch.zeros(0), 0.0)` at `services/audiobook.py:202-203` | confirm it doesn't crash the chapter (silence is valid); duration ≈ 0 + any pause | B-silent-chapter |

### S3 — Cover image states
| State | Where | Expected behavior | Verify |
|-------|-------|-------------------|--------|
| Valid jpg/png cover, m4b | `embed_cover` true | `attached_pic` video stream embedded | B4 |
| Valid cover, **mp3** | `not is_mp3` gate at `longform_render.py:260` | cover **silently dropped**; mp3 produced, audio intact, **no** attached_pic | B4-mp3 |
| Cover upload: wrong extension (`.gif`, `.webp`, none) | `/audiobook/cover` | **400 `{detail:"cover must be a .jpg or .png"}`** (`audiobook.py:133-134`) | B-cover-ext |
| Cover upload: 0 bytes or > 8 MB | `/audiobook/cover` | **400 `{detail:"cover must be between 1 byte and 8 MB"}`** (`audiobook.py:136-137`) | B-cover-size |
| Cover upload: valid extension but bytes aren't a real image | passes upload validation (ext+size only); `validate_cover_image` re-checks ext+size at render but **not** magic bytes (`longform_render.py:213-227`) | ffmpeg ingest fails → falls into S1 mux-failure (generic `error`). **Flag as a candidate gap:** a renamed non-image `.jpg` of legal size reaches ffmpeg and can fail the whole render rather than being dropped like a bad path | B-cover-fakebytes |
| `cover_path` with `..` / traversal | `_safe_cover_path` (`audiobook.py:48-71`) | returns `None` → cover dropped, **render still succeeds** without art | B5 |
| `cover_path` in a foreign dir (outside `audiobook_covers/`) | `os.path.commonpath` mismatch | `None` → dropped, render succeeds | B5 |
| `cover_path` filename not matching `^[0-9a-f]{12}\.(jpg\|jpeg\|png)$` | `_COVER_NAME_RE` (`audiobook.py:45`) | `None` → dropped, render succeeds | B5 |
| `cover_path` points at a missing file | `validate_cover_image` returns false (no such file) | dropped, render succeeds | B5b |

### S4 — Format / bitrate / loudness states
| State | Where | Expected behavior | Verify |
|-------|-------|-------------------|--------|
| `format:"m4b"` (default) | `is_mp3` false | mp4/aac, faststart; `out_name` ends `.m4b` | B1 |
| `format:"mp3"` | `is_mp3` true | mp3/libmp3lame; `out_name` ends `.mp3` | B2 |
| Unknown/garbage format string | `(fmt or "").lower()=="mp3"` else m4b (`longform_render.py:254`); `ext` decided at `audiobook.py:445` | **silently treated as m4b** (anything not `"mp3"`); `out_name` ends `.m4b` | B-fmt-garbage |
| Invalid bitrate (`"abc"`, `"-1"`, `"5k"`, `"1234k"`, empty) | `_BITRATE_RE = ^\d{2,3}k$` no-match (`longform_render.py:37,252`) | **falls back to `128k`**, render succeeds | B-bitrate-fallback |
| `loudness:null` / `"off"` / `"none"` / unknown preset | `build_loudnorm_filter` (`longform_render.py:159-168`) | **no `-af` filter** (all collapse to `None`); default path | B6-off |
| `loudness:"acx"` / `"podcast"` | known preset (`LOUDNESS_PRESETS:153-156`) | `loudnorm=I=…:TP=…:LRA=…` injected; integrated LUFS measurably shifts | B6 |

### S5 — Metadata / ffmetadata states
| State | Where | Expected behavior | Verify |
|-------|-------|-------------------|--------|
| Full metadata dict | `build_ffmetadata` (`longform_render.py:173-197`) | tags mapped per `_GLOBAL_TAG_KEYS:49-57` — **7 keys**: title→title, **album→album**, author→artist, narrator→composer, year→date, genre→genre, description→comment | B3 |
| Partial metadata (some keys blank/None) | `if val is not None and str(val).strip()` (`:184`) | blanks **omitted**, present keys written; front-end already filters blanks at `AudiobookTab.jsx:126-128` | B3b |
| Metadata value containing `=`, `;`, `#`, `\`, newline | `_escape_meta` (`longform_render.py:60-62`) | escaped; ffprobe round-trips the literal value | B3c |
| Chapter title containing the same special chars | `_escape_meta(title)` (`:194`) | escaped in `[CHAPTER]` block | B3c |
| Zero-duration chapter (`dur_ms<=0`) | `max(0, int(dur_ms))` (`:188`) | START==END, no negative span | B-zero-dur |

### S6 — Resume / cache lifecycle states
| State | Where | Expected behavior | Verify |
|-------|-------|-------------------|--------|
| Re-render identical plan | same `chapter_cache_key` | all chapters `cached:true`, `done.cached_chapters==N` | B7 |
| Edit one chapter's text | key changes for that chapter only (`chapter_cache_key`, `longform_render.py:109-135`) | that chapter `cached:false`, others `true` | B7 |
| Change voice / order / pause / speed / sample_rate / engine | each is in the key payload (`:126-131`) | re-render | B7b |
| Add/change lexicon (`/audiobook`) | folded under `"\x00lexicon"` sig key (`audiobook.py:284-287`) | re-render of affected chapters | B8 |
| Underlying voice profile edited (ref_audio/instruct/seed change) | `voice_sig` changes (`audiobook.py:282-283`) | re-render | B7c |
| Cache over `_CACHE_MAX_BYTES` (default 2 GB, `OMNIVOICE_LONGFORM_CACHE_MAX_GB`) | `prune_cache_dir` called at render exit, before releasing cache leases | oldest unused WAVs evicted by mtime; files held by this or another active render remain available, even above the cap | B-evict |
| Eviction race on a tie mtime | `entries.sort()` on `(mtime,size,p)` (`longform_render.py:93`) | deterministic enough; keep cache cases small to avoid mtime ties (see Risk) | B-evict |
| `prune_cache_dir` on missing dir / unstattable file | `try/except OSError` (`longform_render.py:74-77`, `:87-88`, `:102-103`) | returns `(0,0)` / skips file, never raises | B-evict |

### S7 — SSE wire / client-parse states
| State | Where | Expected behavior | Verify |
|-------|-------|-------------------|--------|
| Well-formed event sequence | both read loops | UI progresses through started→chapter…→assembling→done | D18 |
| `data:` line with no trailing space / with extra whitespace | `parseSSELine` slices `line.slice(5).trim()` (`sseParse.js:31`) | tolerated | unit (sseParse.test.js) + D18 |
| Malformed JSON in a `data:` line | `parseSSELine` returns `null` (`:35-37`) | line skipped (`if (!evt) continue`), stream continues | unit + D18 |
| Buffer split mid-event across two reads | `splitSSEBuffer` keeps `rest` (`sseParse.js:16`) | reassembled on next read | unit + D18 |
| Terminal `error` event mid-stream (Stories) | `StoriesEditor.jsx:390` | `throw new Error` → caught → `toast.error('stories.exportFailed')`, `exporting` reset in `finally` (`:399-401`) | D18 |
| Terminal `error` event (Audiobook) | `AudiobookTab.jsx:165-167` | `setError(evt.error)`, generating reset in `finally` (`:172-173`) | D18 |
| `done` with `failed_chapters` non-empty (partial) | `AudiobookTab.jsx:159-164` | output shown + a "failed chapters" note (`:332-340`); Stories: download still triggered if `output` present | D18 |
| `done` with empty/missing `output` (Stories) | `if (!output) throw` (`StoriesEditor.jsx:393`) | error toast, no download | D18 |
| Network drop / stream truncated before `done` (no terminal event) | Audiobook loop ends on `reader.read()` `done` (`:144`) with no `output` set; Stories `if (!output) throw` | Audiobook: **no error surfaced, no output, spinner clears** — UI just sits "done-but-nothing" (**flag: missing terminal-state handling for a truncated stream**); Stories: throws "no output produced" | manual E + flag |
| **User aborts** (Audiobook `abortRef`) | `while (!abortRef.current)` (`AudiobookTab.jsx:142`) | loop exits next iteration; `finally` clears generating. **Note:** the backend generator keeps rendering (no server-side cancel) — confirm no zombie ffmpeg/hung job; **flag if abort doesn't stop server work** | manual E + flag |

### S8 — Import / parse states
| State | Where | Expected | Verify |
|-------|-------|----------|--------|
| `.txt` with `Chapter 1`/`Prologue` lines | `chapterize_plaintext` (`longform_import.py:34-52`) | `# ` headings inserted | C16 |
| `.md` already containing `# ` | `_H1_RE` no-op (`:43`) | untouched | C16 |
| Valid `.epub` | `epub_to_chapter_script` (`:129-192`) | spine-order chapters | C16 |
| Malformed/empty EPUB (no spine, broken zip) | raises `ValueError` (`:145-146`, `:191`) | **400 `{detail:"couldn't parse EPUB: …"}`** (`audiobook.py:115-116`) | C16 |
| EPUB zip-bomb (entry > 25 MB or total > 300 MB) | `_EPUB_MAX_ENTRY_BYTES:30` / `_EPUB_MAX_TOTAL_BYTES:31` | rejected before decompress; `ValueError` → 400 | C16-bomb |
| Import > 64 MB | `_IMPORT_MAX_BYTES:91` | **400 `{detail:"file too large (max 64 MB)"}`** (`audiobook.py:110-111`) | C16 |
| `.bin` / unknown non-epub extension | non-`.epub` branch (`audiobook.py:117-118`) | **treated as UTF-8 text** (`decode(…,"ignore")`); chapterized as plaintext | C16 |
| Non-UTF-8 bytes in a `.txt` | `decode("utf-8","ignore")` (`:118`) | undecodable bytes dropped, no crash | C16 |

### S9 — Markup / lexicon states
| State | Where | Expected | Verify |
|-------|-------|----------|--------|
| `[voice:NAME]` run split | `_VOICE_RE = \[voice:([^\]\[]*)\]` precedence (`services/audiobook.py:42,99-104`) | voice switches per run | manual E |
| `[pause 1s]` / `[pause 500ms]` | `parse_pause_markers` (`omnivoice/utils/text.py:253`) | inter-span silence | manual E |
| `[slow]…[/slow]`, `[fast]`, `[emphasis]`, `[spell]…[/spell]` | `parse_ssml_lite` / `_TAGS` (`ssml_lite.py:49-54`) | rate change / spell-out | manual E |
| Unclosed / nested / unknown SSML-lite tag | `parse_ssml_lite` (`:87-139`) | confirm graceful (no crash, tag passed through or ignored) | unit (ssmlLite.test.js) + manual |
| Lexicon whole-word, longest-first | `apply_lexicon` (`pronunciation.py:92-113`) | longest match wins, single pass | unit + manual |
| Empty/whitespace lexicon keys/values | `normalize_lexicon` (`pronunciation.py:41-58`) | confirm no empty-pattern regex blowup | unit |

### S10 — Job library / Projects states
| State | Where | Expected | Verify |
|-------|-------|----------|--------|
| Finished audiobook/story job with `done` event | `build_longform_library` (`longform_jobs.py:67-143`) | appears newest-first | B14 |
| Job with **no** `done` event (failed/in-flight) | skipped (`longform_jobs.py:110-111`) | excluded from library | B14 |
| Job whose `done` event has no `output` | `if not output: continue` (`:113-114`) | excluded (nothing to re-download) | B14 |
| Title recovery | `done.get("title")` (always absent — `done` event has no `title` key) → `meta_json` (also empty; `job_store.create` passes only `type`, `audiobook.py:372`) | **no recoverable title** → `title` key **omitted** from the item → Projects card falls back to `j.title \|\| j.output` = filename (`Projects.jsx:203`) — **confirmed minor defect candidate** | B14 + PR-C(a) |
| `limit` out of range | `?limit=` `Query(50, ge=1, le=500)` (`longform_jobs.py:147`) | **422** below 1 / above 500; default 50 | B14b |

### S11 — Layout / cross-platform / localization states
| State | Where | Expected | Verify |
|-------|-------|----------|--------|
| Full-width/height layout, rail-left and rail-right | `AudiobookTab.css`, `StoriesEditor.css` | no phantom gap / no horizontal scrollbar (cf. MEMORY phantom-48px-gap) | manual E |
| Default render path (no loudness, no cover, m4b) | all three OSes | **identical-shape output** (strict 2026-05-20 rule) | F |
| WebKitGTK plays m4b/AAC | Linux desktop build | plays — **P0 if not** | F |
| i18n keys resolve (no raw `audiobook.*` / `stories.*` keys leaking) | all shipped controls; keys exist in `frontend/src/i18n/locales/en.json` (`audiobook.*` block at `:112`, `stories.exportFailed` at `:94`) | rendered labels, not keys, on a non-`en` locale too | D18 + F (locale switch) |
| No hardcoded CJK in shipped longform source | the 10 shipped files (verified clean at spec time) | CJK guard test green (`tests/test_no_hardcoded_cjk.py`) | A (Layer 0) |

---

## Design

Three verification layers, cheapest-first, each gating the next:

### Layer 0 — Baseline (must be green before any runtime check)
Run the existing pure suites so a runtime failure is unambiguously a runtime issue, not
a regression in the pure core:
- Backend (run from the repo root so the root-level `omnivoice` package — where
  `parse_pause_markers` lives at `omnivoice/utils/text.py:253` — is importable):
  `uv run pytest tests/test_longform_render.py tests/test_audiobook.py
  tests/test_audiobook_cover.py tests/test_audiobook_preview.py
  tests/test_longform_import.py tests/test_longform_jobs.py tests/test_longform_limits.py
  tests/test_pronunciation.py tests/test_ssml_lite.py -q`
- **Localization guard (constraint gate):** also run `uv run pytest
  tests/test_no_hardcoded_cjk.py -q` so the localization hard rule is enforced as part of
  the baseline — the 10 shipped longform files were verified CJK-free at spec time, and
  this keeps it true. If a future fix adds *functional* CJK (regex/model vocab/fixture),
  it must be added to the `_ALLOWED_FILES` allowlist there with a justification, not
  left to fail CI.
- Frontend: `bun run --cwd frontend test` (runs `vitest run`; covers `ssmlLite.test.js`,
  `storyToSpans.test.js`, `sseParse.test.js` under `frontend/src/test/`).
- Per MEMORY: local pytest can segfault on torch/Triton import. The longform pure tests
  are mostly import-light, **but note** that the parser path pulls
  `from omnivoice.utils.text import parse_pause_markers` (`services/audiobook.py:29`) and
  `synthesize_chapter` lazily imports `torch` (`services/audiobook.py:183`). The pure
  parser/builder tests don't reach torch; if a segfault appears, deselect the offending
  module and let CI confirm.

### Layer 1 — API integration (automatable, real ffmpeg + stubbed TTS)
New pytest module `tests/test_longform_e2e.py` that runs the **real** SSE generator and
**real** ffmpeg, but injects a **stub synth** so no GPU/model is needed. This is the
highest-value new automation: it proves the ffmpeg argv actually muxes a playable file
and the SSE event sequence is correct, without a model. **It must also exercise the
non-happy states in §S0–S10 that don't require a human** — empty input, no-ffmpeg,
partial fail, all-fail, corrupt cache, cover rejection, bad bitrate, garbage format,
out-of-range preview index, oversize/empty/malformed import — and **assert the exact
event/HTTP/DB shapes pinned in §"API / data shapes"**.

Mechanism (grounded in the code):
- `_render_longform_sse` (`audiobook.py:405`) calls `await _prepare_synth(default_voice)`
  (defined `audiobook.py:239-257`), which itself calls `_build_synth`
  (`audiobook.py:200-236`). `_prepare_synth` returns the uniform tuple `(synth,
  sample_rate, resolve, engine_id)` consumed by the job, the preview
  (`audiobook.py:331`), and `_render_chapter_cached`.
- **Recommended injection point: monkeypatch `_build_synth`** to return a "generic"-mode
  dict — `{"mode": "generic", "resolve": <trivial>, "engine_id": "stub", "synth":
  <tone>, "sample_rate": 24000}` — because `_prepare_synth`'s "generic" branch
  (`audiobook.py:257` → `return info["synth"], info["sample_rate"], resolve, engine_id`)
  returns that straight through *without awaiting a model*. Patching `_build_synth` (not
  `_prepare_synth`) means you don't have to reimplement the async await/model-load fork,
  and the patch is exercised through the real `_prepare_synth`. (Patching `_prepare_synth`
  directly also works — it's `async`, so the stub must be a coroutine returning the
  4-tuple `(synth, sample_rate, resolve, engine_id)` — but it bypasses the
  `_build_synth`→`_prepare_synth` contract.) One patch covers `/audiobook`,
  `/longform/render`, **and** `/audiobook/preview` since all three route through
  `_prepare_synth`.
- **Stub signatures (exact):**
  - `_build_synth(default_voice: str | None) -> dict` — return
    `{"mode": "generic", "resolve": resolve, "engine_id": "stub", "synth": synth,
    "sample_rate": 24000}`.
  - `synth(text: str, voice_id: str | None, speed: float | None = None) -> torch.Tensor` —
    a **1-D float32** tensor (`torch.zeros(n)` or a short sine, `n>0`). `synthesize_chapter`
    (`services/audiobook.py:163-206`) filters with `getattr(r, "numel", lambda: 0)()`
    (`:192`) and concatenates via `concatenate_audio_chunks`; `_render_chapter_cached`
    saves the result with `atomic_save_wav(wav_path, audio, sr)` (`services/audio_io.py`,
    via `audiobook.py:300`).
  - `resolve(voice_id: str | None) -> dict` — must carry the four keys
    `{"ref_audio", "ref_text", "instruct", "seed"}` so the signature build at
    `audiobook.py:282-283` (`f"{v.get('ref_audio')}|{v.get('ref_text')}|{v.get('instruct')}|{v.get('seed')}"`)
    doesn't `KeyError` (it uses `.get`, so missing keys yield `None` — but author the dict
    explicitly so a voice-signature-change cache case (B7c) can mutate it deterministically).
- **For the fault-injection cases (S2):** make the stub `synth` raise (e.g.
  `raise RuntimeError("boom")`) **conditionally on the span text/index** so you can fail
  exactly chapter 1 (B9) or every chapter (B10). Because `_render_chapter_cached` runs in
  the executor, the raise propagates back through `loop.run_in_executor` and is caught at
  `audiobook.py:420` — verify it lands as a `chapter_error` event, not an unhandled task
  exception. **For the corrupt-cache case (B-corrupt-cache):** pre-write a junk file at
  the expected cache-key path (`<OUTPUTS_DIR>/longform_cache/<key>.wav`, key from
  `chapter_cache_key`) so `wave.open` raises and the re-render fallthrough at `:296-297`
  fires.
- Note the executor: `_render_chapter_cached` runs under
  `loop.run_in_executor(_gpu_pool, ...)` (`audiobook.py:416`). `_gpu_pool` is a lazy
  module attribute on `services.model_manager` (a `ThreadPoolExecutor` singleton built in
  `model_manager.py:85-108` via `__getattr__`). A CPU tone stub runs fine on it; no GPU
  needed. Tests must let the event loop run the executor (drive the async generator under
  `asyncio`).
- Drive the async generator directly (`async for ev in _render_longform_sse(plan, …)`)
  collecting parsed events — **preferred** for deterministic event ordering. The generator
  yields **SSE-framed strings** (`f"data: {json.dumps(payload)}\n\n"` from `_emit`,
  `audiobook.py:383`); in a direct-drive test, strip the `data: ` prefix and
  `json.loads` the rest, or reuse a small Python mirror of `parseSSELine`. Build the
  `plan` for `/audiobook` via `parse_audiobook_script(text)` (`services/audiobook.py:135`);
  for `/longform/render` build it from `AudiobookPlan / Chapter / Span`
  (`services/audiobook.py:45-90`) the way the endpoint does (`audiobook.py:526-535`).
  Alternatively hit the endpoints via FastAPI `TestClient` / `httpx.AsyncClient` and read
  the streamed body (subject to the buffering caveat — see Risk).
- Gate the whole module on `find_ffmpeg()` (`services/ffmpeg_utils.py:56`) being
  non-None (`pytest.mark.skipif`) so it's a no-op on a runner without ffmpeg rather than
  a red failure — but it MUST run in CI where ffmpeg is available, and locally for the
  human verifier who has ffmpeg. The real run goes through `run_ffmpeg`
  (`services/ffmpeg_utils.py:378`, awaited at `audiobook.py:448`). **Exception:** the
  no-ffmpeg case (B15) deliberately monkeypatches `services.ffmpeg_utils.find_ffmpeg`→None
  *inside* a test that is otherwise gated on ffmpeg being present; structure it so that
  one case forces the None and asserts the early `error`, while the rest use the real
  binary. (Patch the symbol that `_render_longform_sse` imports —
  `services.ffmpeg_utils.find_ffmpeg`, imported locally at `audiobook.py:366`.)
- Assert on the produced file: `ffprobe` the output and confirm (a) container/codec
  (`mp4`/`aac` for m4b, `mp3`/`mp3` for mp3), (b) chapter count == `done.chapters` ==
  number of muxed (successful) chapters, (c) global tags present when metadata passed,
  (d) a video `attached_pic` stream present for m4b-with-cover and **absent** for
  mp3-with-cover (the deliberate skip at `longform_render.py:260`), (e) loudnorm changed
  the integrated loudness when a preset was passed vs off. **And assert the negative cases
  produce NO file** (all-fail, empty, no-ffmpeg) — i.e. ffprobe the expected `out_path`
  (`<OUTPUTS_DIR>/<job_type>_<job_id>.<ext>`) and confirm it does **not** exist.
- **Backward-compat-data assertion (constraint):** the job-library cases read the
  *existing* `jobs` + `job_events` SQLite tables (`core/db.py:_BASE_SCHEMA:114,:129`).
  Point `core.db` at a tmp DB and confirm the library populates **without any new
  migration step** — `build_longform_library` works purely off `_BASE_SCHEMA` tables that
  pre-date this session. No `_migrate()` rung (`core/db.py:188-206`), no alembic revision
  was shipped; the test proves a fresh `_BASE_SCHEMA` DB (and, ideally, a pre-existing
  v0.3.5-era DB fixture) serves longform jobs unchanged.

### Layer 2 — UI / runtime (Playwright smoke + manual)
- **Playwright smoke** (`frontend/e2e/longform.spec.ts`): drive the actual UI — open the
  Audiobook tab and Stories tab, assert the controls shipped this session are present and
  wired (format/loudness selects, metadata inputs, cover picker, lexicon rows, markup
  help, import button; Stories format select + Generate). These run against the Vite dev
  server (`baseURL` :3901 per `frontend/playwright.config.ts:17`) with **no real
  synthesis** (mock the SSE endpoints with `page.route` fulfill, OR assert pre-synthesis
  state only) so they don't need a GPU. They catch the "control silently missing /
  mislabeled / i18n key not resolving" class of bug. **Mock the non-happy SSE streams
  too** — a stream whose terminal event is `error`, and one with a `chapter_error` +
  partial `done.failed_chapters` — to verify the UI surfaces the error toast / failed-
  chapter note rather than silently swallowing it (states S7). **Mock bodies must match
  the exact event JSON in §"API / data shapes"** so the smoke proves the real consumer
  parses the real producer shape.
  - **Navigation:** use the existing `gotoMode(page, mode)` helper
    (`frontend/e2e/_helpers.ts`), which seeds the `omnivoice.app` zustand-persist key
    (`{state:{mode}, version:4}`) before boot. The Stories tab is `mode:'stories'`; the
    Audiobook tab is `mode:'audiobook'` (set by `Launchpad.jsx:151`,
    `onClick={() => setMode('audiobook')}`). **Grounding finding:** the `MODES` constant
    in `_helpers.ts` lists `'stories'` but **omits `'audiobook'`**, so the existing
    `ui-smoke.spec.ts` never mounts the Audiobook tab. `gotoMode` accepts any string, so
    the new spec can still seed `'audiobook'` directly — but consider adding `'audiobook'`
    to `MODES` so the generic mount-smoke covers it too (small docs/test gap to flag).
  - **Localization assertion (constraint):** at least one Playwright case must switch the
    locale to a non-`en` value (e.g. seed the i18n language) and assert the shipped
    controls render *translated* labels — i.e. **no raw `audiobook.*` / `stories.*` key
    strings leak into the DOM**. The keys exist in `en.json` and the other 20 locale files
    (`frontend/src/i18n/locales/*.json`); a leaked key string is a localization-rule
    defect, not cosmetic.
- **Manual runtime checklist** (human + running app via `bun run dev` or the desktop
  build): the parts automation can't cover — real engine synthesis, actual `<audio>`
  playback in the webview (esp. WebKitGTK m4b/AAC), file download, resume timing,
  abort/interrupt behavior, truncated-stream behavior, and cross-platform parity
  spot-checks.

> **Why three layers, not just manual:** the manual checklist is the only way to prove
> real-engine + real-webview playback, but it's slow and unrepeatable. Layers 0–1 catch
> 80% of the regressions cheaply and run in CI forever; Layer 2-manual is the irreducible
> human verification of the core value.

---

## Constraints

This is a verification spec for features that ship in the **default** VoiceStudio build, so
the verification doubles as a constraints audit. Each applicable hard rule is mapped to
how it is satisfied and which case **proves** it. A runtime check that *disproves* a
constraint is escalated per the rule (P0 for a default-behavior platform divergence).

### C1 — Cross-platform parity (strict, owner-set 2026-05-20)
- **Why it applies:** loudness, cover embedding, SSML-lite, and the m4b/mp3 output are
  all part of the *default* feature surface — they must behave identically on
  macOS / Windows / Linux, or be opt-in.
- **How it's met:** loudness is **opt-in / off by default** — the UI state defaults to
  `'off'` (`AudiobookTab.jsx:35`, verified) and sends `null` (`:134`), so the default
  `build_loudnorm_filter` path injects **no `-af`** (`longform_render.py:159-168`). Cover
  is opt-in (none unless the user uploads one). The default render path (m4b, no loudness,
  no cover) is a single ffmpeg argv with no per-OS branching in `build_render_cmd`
  (`longform_render.py:232-285`) — ffmpeg resolution is the only platform-specific code,
  via `find_ffmpeg` (`services/ffmpeg_utils.py:56`), which is an allowed
  OS-API/packaging-level branch, not a user-visible behavior fork.
- **What proves it (gate F):** (1) default render produces **identical-shape** output
  (same container/codec/chapter-count) on all three OSes — asserted by ffprobe in Layer 1
  on each platform's CI lane and spot-checked manually; (2) **WebKitGTK plays the
  m4b/AAC** Linux desktop output — the single highest-risk path. If the default m4b
  fails to play on any platform, that is a **P0**: per the rule, fix it on the platform
  or move the default behind opt-in — **no third option**, no "defer to v0.4".
- **Verification cases:** S11 rows, test plan §F, and the Layer-1 ffprobe shape assertions
  run per-OS.

### C2 — Local-first guarantee
- **Why it applies:** the project forbids required cloud calls, accounts, API keys, or
  third-party telemetry; the verification itself must not introduce any.
- **How it's met:** every shipped longform path is on-device — ffmpeg/ffprobe are
  resolved locally by `find_ffmpeg` (bundled/PATH, no download at render time); EPUB
  parsing is stdlib-only (`longform_import.py` uses `zipfile` / `xml.etree` /
  `html.parser`, no network); synthesis runs through the local engine; the job library
  reads the local SQLite DB. No output, error, or metadata is posted anywhere — the
  `/audio/<output>` mount (`main.py:716`) serves from the local `OUTPUTS_DIR`.
- **What proves it:** the verification adds **no telemetry and no cloud check** (explicit
  Non-goal). Layer 1 runs fully offline with a stub synth; Layer 2-Playwright mocks the
  SSE endpoints with `page.route` (network boundary only). The manual layer needs no
  account or key. A reviewer can run the entire plan air-gapped (modulo first-run model
  download, which is the engine's existing behavior, not introduced here).
- **Verification cases:** the whole plan; explicitly the "no telemetry" Non-goal and the
  offline Layer-1/Layer-2 harness.

### C3 — Backward-compatible project data (alembic for DB, lazy migration for localStorage)
- **Why it applies:** existing `omnivoice_data/` (DB, projects, settings) must keep
  working with no manual migration; any schema change goes through alembic with a tested
  upgrade path.
- **How it's met / grounding finding:** **this session shipped no schema change.** The
  job library reads the **pre-existing** `jobs` and `job_events` tables defined in
  `core/db.py:_BASE_SCHEMA` (`:114-127`, `:129-136`) — `build_longform_library` works
  purely off the SSE-recovery path (`job_store.list_jobs` / `events_since`, recovered via
  `_done_payload_from_events`, `longform_jobs.py:32-50`). There is **no dedicated
  `longform_jobs` table**, no new `_migrate()` rung (the ladder tops out at v4,
  `core/db.py:203-205`), and **no new alembic revision** (the DB already converges via
  `_BASE_SCHEMA`'s `CREATE TABLE IF NOT EXISTS` plus the alembic pass at
  `core/db.py:220-225`). On the frontend, the zustand-persist store key is `omnivoice.app`
  and is already at `version: 4` (`frontend/e2e/_helpers.ts:46`); this session did **not**
  bump it — no lazy-migration is required because no persisted shape changed.
- **What proves it:** Layer-1 job-library cases run against a tmp `core.db` built only
  from `_BASE_SCHEMA` and confirm longform jobs populate with **no migration step**; the
  manual layer points the app at an existing pre-session `omnivoice_data/` (a v0.3.5-era
  DB) and confirms Projects + Audiobook still load with no upgrade prompt. **If any case
  reveals a needed schema change, it must go through alembic with a tested upgrade path —
  not a raw `CREATE TABLE`** (per the rule); that would be a PR-C item, not in-scope here.
- **Verification cases:** Layer-1 §B14/B14b (job library off `_BASE_SCHEMA`), manual §E
  Cohesion (pre-existing data dir), the "no new table/migration" Non-goal.

### C4 — CodeQL py/polynomial-redos (any user-input-reachable regex)
- **Why it applies:** several shipped regexes are reachable from user input (bitrate,
  format, cover filename, metadata, lexicon, markup, voice-tag) and the project gates PRs
  on CodeQL's polynomial-ReDoS query (per MEMORY: exclude both delimiters in `[^x]*`, no
  overlapping `\s*`/`.+`, atomic groups OK on py≥3.11).
- **How it's met / grounding finding:** the regexes verified on user-input paths are
  all **linear / bounded — no nested or overlapping unbounded quantifiers**, so none is a
  ReDoS candidate:
  - `_BITRATE_RE = re.compile(r"^\d{2,3}k$")` (`longform_render.py:37`) — anchored,
    bounded `{2,3}` repetition.
  - `_escape_meta` → `re.sub(r"([=;#\\\n])", r"\\\1", …)` (`longform_render.py:62`) — a
    single-character class with no quantifier; linear.
  - `_COVER_NAME_RE = re.compile(r"^[0-9a-f]{12}\.(?:jpg|jpeg|png)$")`
    (`audiobook.py:45`) — fixed-length `{12}` + a bounded non-capturing alternation,
    fully anchored.
  - `_VOICE_RE = re.compile(r"\[voice:([^\]\[]*)\]")` (`services/audiobook.py:42`) — the
    capture group `[^\]\[]*` **excludes both bracket delimiters**, the exact MEMORY
    pattern for a non-overlapping (linear) `[^x]*`. (The code comment at `:40-42` calls
    out that this is the ReDoS-hardened form.)
- **What proves it:** these are already shipped and CodeQL-green on `main@df94af8`
  (PR #426 merged). Verification adds **no new user-input regex** (it's a test/verify
  task). The lexicon/markup parsers (`pronunciation.py:_compile:72-89`,
  `ssml_lite.py:87-139`) are exercised by the pure suites (S9 "empty/whitespace lexicon
  keys → no empty-pattern blowup", `normalize_lexicon:41-58`) — confirm those stay green
  as the regression guard. **Constraint on any PR-C fix:** if a defect fix introduces or
  edits a user-input regex (e.g. a magic-byte cover sniff that uses a regex), it must
  satisfy the CodeQL polynomial-ReDoS query before merge — anchor it, avoid
  `[^x]*`/`\s*` overlap, prefer fixed bounds.
- **Verification cases:** Layer-0 pure suites (the regexes' direct tests), S4
  (bitrate/format fallback), S3 (cover-name regex rejection, B5), S9 (lexicon, voice-tag).

### C5 — Localization (no hardcoded non-English/CJK; all UI via i18n `t()` keys)
- **Why it applies:** every shipped UI control (Audiobook tab, Stories Generate, Projects
  cards, Launchpad cards, error toasts) is user-facing text and must route through i18n.
- **How it's met / grounding finding:** the shipped controls use `t('audiobook.*')` and
  `t('stories.*')` keys, and those keys **exist** in `frontend/src/i18n/locales/en.json`
  (the `"audiobook"` block at `:112` — `subtitle`/`create`/`assembling`/`loudness*`/etc;
  `stories.exportFailed` at `:94`) and across all 20 other locale files
  (`ar/de/es/fr/hi/id/it/ja/ko/nl/pl/pt/ru/sv/th/tr/uk/vi/zh-CN/zh-TW.json`). A scan of
  the 10 shipped longform source files (`longform_render.py`, `longform_import.py`,
  `pronunciation.py`, `ssml_lite.py`, `audiobook.py` router, `longform_jobs.py`,
  `AudiobookTab.jsx`, `StoriesEditor.jsx`, `storyToSpans.js`, `sseParse.js`) found **no
  hardcoded CJK** at spec time. Backend error *strings* ("nothing to render", "ffmpeg not
  available", "couldn't parse EPUB") are English diagnostic messages surfaced via toasts;
  these are functional/diagnostic, not localized UI chrome — consistent with existing
  practice (and ASCII-only, so outside the CJK rule).
- **What proves it:** (1) Layer-0 runs `tests/test_no_hardcoded_cjk.py` (the CI gate);
  (2) a Playwright case switches to a non-`en` locale and asserts no raw `audiobook.*` /
  `stories.*` key string leaks into the DOM (a leaked key = a missing-translation defect,
  filed, not ignored). **Constraint on any PR-C fix:** any new user-facing string a defect
  fix adds must be a `t('...')` key with entries in `locales/*.json`, never a literal —
  and any *functional* CJK must be allowlisted in `tests/test_no_hardcoded_cjk.py` with a
  justification.
- **Verification cases:** Layer-0 §A (CJK guard), Layer-2 §D18 (i18n-key-leak assertion),
  §F (locale-switch spot-check).

### C6 — Versioning + release cadence (continuous-to-main, no RC)
- **Why it applies:** the verification artifacts and any defect fixes are real commits and
  must follow the cadence rule.
- **How it's met:** all three version files already read `0.3.6`
  (`tauri.conf.json:4`, `Cargo.toml:3`, `pyproject.toml:7`) = latest release `v0.3.5` + 1
  patch, in lockstep (verified). Verification commits and PR-C defect fixes land
  **continuous-to-main** under v0.3.6 — **no RC tag, no soak, no `v0.4` deferral, no
  re-version**. If a defect fix is worth cutting, the owner tags `v0.3.6` from main and
  the post-release bump moves main to `0.3.7`; that's an owner action, not part of this
  task.
- **Docs-sync (hard rule):** this verification touches no documented behavior
  (README/CONTRIBUTING/SECURITY/SUPPORT/`docs/**`). **If a PR-C defect fix changes a
  documented behavior** (e.g. supported formats, install flow, Docker tags), the doc must
  be updated **in the same PR** — stale docs are bugs. The verification spec itself is not
  a doc artifact; per project instruction, no summary `.md` is written — findings are
  returned directly.

---

## Integration points (`file:line`)

These are the exact seams the verification must exercise (verified against `main@df94af8`):

### Backend
- `backend/services/longform_render.py:232-285` — `build_render_cmd`: the ffmpeg argv.
  Signature: `build_render_cmd(ffmpeg: str, concat_list_path: str, metadata_path: str,
  out_path: str, *, fmt: str = "m4b", bitrate: str = "128k", cover_path: str | None = None,
  loudness: str | None = None) -> list[str]`. Key branches to hit: m4b vs mp3 (`is_mp3`
  at `:254`), cover embed gated to m4b only (`:260`, `:267-272`, `:282-283`), loudnorm
  filter injection (`:274-276`), bitrate allowlist fallback (`:252`, regex `_BITRATE_RE`
  at `:37` — `^\d{2,3}k$`, linear/bounded, CodeQL-clean per C4).
- `backend/services/longform_render.py:159-168` — `build_loudnorm_filter(preset: str |
  None) -> str | None`: off/acx/podcast (presets `LOUDNESS_PRESETS` at `:153-156`, each a
  frozen `LoudnessPreset(key,i,tp,lra)` dataclass `:140-147`; unknown/None/`"off"`/`"none"`
  → `None`, a single collapse point for all "no-loudness" inputs — this is what keeps
  loudness opt-in and the default path platform-identical per C1). Output string shape:
  `f"loudnorm=I={p.i}:TP={p.tp}:LRA={p.lra}"`.
- `backend/services/longform_render.py:173-197` — `build_ffmetadata(chapters:
  Iterable[tuple[str,int]], global_meta: dict | None = None) -> str`: a `;FFMETADATA1`
  header, optional global tags, then one `[CHAPTER]` block per `(title, duration_ms)`.
  Tag-key mapping (`_GLOBAL_TAG_KEYS:49-57`) — **7 keys, in this order**:
  `title→title`, `author→artist`, `album→album`, `narrator→composer`, `year→date`,
  `genre→genre`, `description→comment`. **Grounding correction:** the original draft's
  6-key list **omitted `album→album`** (`:52`); assert all seven. Blank/None values
  **omitted** (`:184`); special chars escaped via `_escape_meta` (`:60-62`, single-char-
  class substitution — CodeQL-clean per C4); `max(0,…)` guards a zero/negative chapter
  duration (`:188`); START/END are cumulative ms.
- `backend/services/longform_render.py:65-104` — `prune_cache_dir(cache_dir: str,
  max_bytes: int = _CACHE_MAX_BYTES) -> tuple[int,int]`: LRU eviction by mtime, returns
  `(remaining_bytes, removed_count)` (verify it does not evict files held by active
  cache leases during pruning at render exit); never raises
  (OSError-swallowing at `:74-77`, `:87-88`, `:102-103`). Ceiling is `_CACHE_MAX_BYTES`
  (`:41`, `OMNIVOICE_LONGFORM_CACHE_MAX_GB`, default 2 GB).
- `backend/services/longform_render.py:109-135` — `chapter_cache_key(spans:
  Iterable[tuple], *, sample_rate: int, engine_id: str, voice_sig: dict | None = None) ->
  str`: resume key; payload (`:126-131`) = `{"sr", "engine", "spans":
  [[voice_id,text,int(pause),speed], …], "voices": {sorted voice_sig}}`; SHA1[:20]
  content-address (`:135`, `usedforsecurity=False`).
- `backend/services/longform_render.py:213-227` — `validate_cover_image(path: str | None)
  -> bool`: jpg/png + 8 MB cap + file-exists (`_COVER_EXTS:42`, `_COVER_MAX_BYTES:43`).
  **Note (completeness):** checks *extension + size + existence only*, **not** image magic
  bytes — a legal-size, legal-extension non-image reaches ffmpeg (see S3 `B-cover-fakebytes`).
- `backend/api/routers/audiobook.py:345-474` — `_render_longform_sse`: the SSE generator
  (`async def … yield`). Signature: `_render_longform_sse(plan, *, default_voice: str |
  None, fmt: str = "m4b", bitrate: str = "128k", loudness: str | None = None, cover_path:
  str | None = None, metadata: dict | None = None, lexicon: dict | None = None, job_type:
  str = "audiobook")`. Event types and their emitting lines: `started`(`:412`),
  `chapter`(`:430-432`), `chapter_error`(`:424-425`), `assembling`(`:438`), `done`
  (`:463-465`), `error`(`:386` no-chapters, `:390` no-ffmpeg, `:435` all-fail, `:474`
  outer-catch). **Ordering note:** the no-chapters check (`:385-387`) precedes the
  no-ffmpeg check (`:388-391`) — an empty plan errors with "nothing to render" even when
  ffmpeg is also missing. The `_emit` helper (`:377-383`) best-effort-persists every event
  to `job_store.append_event(job_id, json.dumps(payload))` (note: the **bare JSON**, not
  the `data: …` frame) and returns the SSE-framed `f"data: {json.dumps(payload)}\n\n"`.
  **Grounding finding:** the `done` event (`:463-465`) carries
  `output / chapters / duration_s / cached_chapters / failed_chapters` — but **no `title`**
  (see job-library note below). `done.chapters` = `len(chapter_files)` (muxed/successful
  count), `done.duration_s` = sum of *successful* chapter durations only
  (`total_s = sum(d for _, d in chapters_meta) / 1000.0`, `:462`).
- `backend/api/routers/audiobook.py:260-301` — `_render_chapter_cached(chapter, synth, sr,
  engine_id, resolve, cache_dir, lexicon=None) -> tuple[str, float, bool]` → `(wav_path,
  duration_s, was_cached)`. Cache-hit path (`:291-297`, reads duration from the WAV
  header; corrupt entry → `except Exception: pass` re-render fallthrough at `:296-297`) vs
  synth path (`:299-301`, calls `synthesize_chapter` then `atomic_save_wav`); lexicon
  folded into the key via `normalize_lexicon` under the reserved `"\x00lexicon"` sig key
  (`:284-287`).
- `backend/api/routers/audiobook.py:200-236` — `_build_synth(default_voice: str | None) ->
  dict` (the recommended stub target; returns either an `omnivoice`-mode dict with
  `get_model` or a `generic`-mode dict with a ready `synth` + `sample_rate`) and
  `:239-257` — `_prepare_synth(default_voice) -> (synth, sample_rate, resolve, engine_id)`
  (async; the "generic" branch at `:257` is the no-model path — the no-GPU/local-first
  hook per C2).
- `backend/api/routers/audiobook.py:97-122` — `POST /audiobook/import` (txt/md/epub; 64 MB
  cap `_IMPORT_MAX_BYTES:91`, empty→400 `:108-109`, oversize→400 `:110-111`, epub parse
  error→400 `:115-116`, no-text→400 `:119-120`; non-`.epub` → UTF-8 text with
  `decode(…,"ignore")` `:117-118`). Returns `{"text": script, "chapters":
  plan.chapter_count}` (`:122`). EPUB parse is stdlib-only (local-first, C2).
- `backend/api/routers/audiobook.py:125-143` — `POST /audiobook/cover` upload (jpg/png +
  8 MB; ext check before reading bytes `:132-134`, size/empty `:136-137`). Returns
  `{"path": path}` where `path` is the **absolute** server path
  `os.path.join(OUTPUTS_DIR, "audiobook_covers", f"{uuid4().hex[:12]}{ext}")` (`:140`) —
  this exact string is what the client passes back as `cover_path`.
- `backend/api/routers/audiobook.py:311-342` — `POST /audiobook/preview` single chapter
  (no-chapters→400 `:322-323`, index-out-of-range→400 `:325-326`). Returns
  `{"output": os.path.relpath(wav_path, OUTPUTS_DIR), "duration_s": round(dur,2),
  "cached": bool, "title": chapter.title}` (`:337-342`) — `output` is a `.wav` under
  `longform_cache/` (a path relative to `OUTPUTS_DIR`), served via `/audio`. The request
  model has **no** `bitrate`/`format`/`loudness` fields (preview is a raw WAV audition).
- `backend/api/routers/audiobook.py:48-71` — `_safe_cover_path(cover_path: str | None) ->
  str | None`: the CodeQL-hardened path confinement (regex allowlist `_COVER_NAME_RE:45` =
  `^[0-9a-f]{12}\.(?:jpg|jpeg|png)$` — fixed-length + bounded alternation, anchored,
  CodeQL-clean per C4 — plus `os.path.basename` + `os.path.realpath` +
  `os.path.commonpath`). Verify a traversal/foreign/missing path returns `None` (cover
  silently dropped, render still succeeds). Called inline at the mux: `audiobook.py:451`.
  (This whole helper exists because of the prior CodeQL py/path-injection findings —
  commits `31ee5ef`→`8bb0e73`; do not regress it.)
- `backend/api/routers/audiobook.py:146-157` — `AudiobookRequest` pydantic model (the
  `/audiobook` body; fields + defaults pinned in §"API / data shapes"); `:477-488` —
  `POST /audiobook`.
- `backend/api/routers/audiobook.py:493-513` — `LongformSpan` / `LongformChapter` /
  `LongformRenderRequest` pydantic models; `:516-543` — `POST /longform/render`
  (`_MAX_CHAPTERS` 10_000 422 guard at `:523-524`; pause-only-span retention filter at
  `:530-532`; **empty resulting plan flows to the "nothing to render" error**, not a 422).
  **Grounding correction:** `LongformRenderRequest` (`:505-513`) **does** declare a
  `lexicon` field and the endpoint **does** forward it (`:540`) — the original draft's
  "this model has NO `lexicon` field" was wrong. The real gap is frontend-only (the TS
  type `LongformRenderBody`, see below).
- `backend/api/routers/longform_jobs.py:67-143` — `build_longform_library(list_jobs:
  Callable[..., list[dict]], events_since: Callable[..., list[dict]], *, limit: int = 50)
  -> list[dict]`; `:146-158` — `GET /longform/jobs?limit=…` (`Query(50, ge=1, le=500)`;
  out-of-range → 422; over-fetches `limit*4` rows then filters, `:89`). Recovers the
  `done` payload from the persisted SSE tail (`_done_payload_from_events:32-50`, scans
  newest-first, `json.loads(ev["payload"])`); jobs with no `done` event are skipped
  (`:110-111`), jobs whose `done` has no `output` skipped (`:113-114`). **Backward-compat-
  data anchor (C3):** this reads the **existing** `jobs`/`job_events` tables (`job_store`
  over `core/db.py:_BASE_SCHEMA:114,:129`) — no new table, no migration this session.
  **Grounding finding:** `title` is recovered via `done.get("title")` (always absent — the
  `done` event has no `title` key) falling back to `row["meta_json"]` parsed for `title`
  (`:124-136`); but `job_store.create` is called with only `type=job_type`
  (`audiobook.py:372`, no `meta`), so longform jobs have **no recoverable title from
  either source** — the `title` key is therefore **omitted** from each item, and the
  Projects card falls back to `j.title || j.output` (`Projects.jsx:203`) i.e. the
  filename. Flag as a minor defect candidate, not a blocker.
- `backend/core/job_store.py:38` — `create(job_id: str, *, type: str, project_id: str |
  None = None, meta: dict | None = None) -> None` (writes a `jobs` row with
  `status='pending'`, `meta_json=json.dumps(meta or {})`); `:49-62` —
  `mark_running/mark_done/mark_failed/mark_cancelled`; `:83-111` — `append_event(job_id:
  str, payload: str) -> int` (stores `payload` **opaque** — the renderer passes
  `json.dumps(event_dict)`, NOT a `data: …` frame; per-job cap 500 rows); `:114-122` —
  `events_since(job_id, after_seq=0, limit=1000) -> list[dict]` returning
  `[{seq, created_at, payload}]`; `:134-153` — `list_jobs(*, status=None, project_id=None,
  limit=100) -> list[dict]` returning full `jobs` rows (keys: `id, type, project_id,
  status, created_at, updated_at, finished_at, error, meta_json`), newest-first.
  **Grounding correction:** the module docstring (`job_store.py:84-86`) says `payload` is
  "the raw SSE line (e.g. `data: {...}`)", but the actual longform caller (`_emit`,
  `audiobook.py:380`) stores the **bare** `json.dumps(payload)` — so
  `_done_payload_from_events` parses it directly with `json.loads`, no `data: ` strip.
  Tests/manual must mirror this: seed `job_events.payload` with bare JSON.
- `backend/core/db.py:_BASE_SCHEMA` defines `jobs` (`:114-127`: `id PK, type, project_id,
  status, created_at, updated_at, finished_at, error, meta_json DEFAULT '{}'`) + `job_events`
  (`:129-136`: `id PK AUTOINCREMENT, job_id, seq, created_at, payload`). `core/db.py` runs
  dual migrations — a `PRAGMA user_version` + `_migrate()` ladder (`:188-225`, tops out at
  v4) **and** pending alembic revisions (`_run_alembic_upgrade`, `:220-225`). The longform
  features added **no** rung to either. Tests/manual must confirm a pre-session DB upgrades
  to nothing new.
- `backend/services/longform_import.py:34-52` — `chapterize_plaintext`; `:129-192` —
  `epub_to_chapter_script` (zip-bomb caps `_EPUB_MAX_ENTRY_BYTES:30` 25 MB /
  `_EPUB_MAX_TOTAL_BYTES:31` 300 MB; spine-order walk at `:161-188`; raises `ValueError`
  on a malformed/empty EPUB at `:145-146`, `:191`; stdlib-only — local-first, C2).
- `backend/services/pronunciation.py:92-113` — `apply_lexicon` (whole-word, longest-first,
  single `re.sub` pass; `normalize_lexicon:41-58`, `_compile:72-89` — verify the compiled
  pattern can't blow up on empty/whitespace keys, the S9 ReDoS-adjacent guard for C4).
- `backend/services/ssml_lite.py:87-139` — `parse_ssml_lite`; `spell_out:142-155`
  (`_TAGS:49-54`: slow 0.85 / fast 1.15 / emphasis 0.92+flag / spell). Returns a list of
  `{"text", "speed", "spell"}` segment dicts (the shape `_parse_spans` consumes at
  `services/audiobook.py:118-121`).
- `backend/services/audiobook.py:45-90` — `Span`/`Chapter`/`AudiobookPlan` dataclasses
  (`Span(voice_id, text, pause_ms_after=0, speed=None)`; `Chapter(title, spans=[])` with a
  `char_count` property; `AudiobookPlan(chapters=[])` with `char_count` + `to_dict`).
  `:135-160` — `parse_audiobook_script(text, *, default_voice=None) -> AudiobookPlan`
  (chapters via `_HEADING_RE:36`); `:93-132` — `_parse_spans` marker precedence
  `[voice:]` (run split `:99-104`) → `[pause]` (`parse_pause_markers` at `:113`) →
  SSML-lite prosody (`:118-121`); pause-only span carry at `:124-125`.
- `backend/services/audiobook.py:163-206` — `synthesize_chapter(spans, synth,
  sample_rate, *, crossfade_ms=50, lexicon=None) -> (audio_tensor, duration_s)` (lazy
  torch import `:183`; lexicon applied per-span via `apply_lexicon` `:190`; returns
  `(torch.zeros(0), 0.0)` when no parts `:202-203`).
- `omnivoice/utils/text.py:253` — `parse_pause_markers` (repo-root `omnivoice` package,
  NOT under `backend/`; importable because the backend adds the repo root to its path).
- `backend/main.py:716` — `/audio` static mount (every output served here, from the local
  `OUTPUTS_DIR` — local-first, C2); `:769-770` — `audiobook.router` (769) +
  `longform_jobs.router` (770) included; `:754` — `stories.router`.

### Frontend
- `frontend/src/pages/AudiobookTab.jsx:35` — `loudness` state defaults to `'off'`
  (verified) — the **opt-in default** that keeps loudness platform-identical (C1).
- `frontend/src/pages/AudiobookTab.jsx:113-175` — `onCreate`: uploads cover
  (`:122-124`), builds metadata (`:126-128`) + lexicon (`:129`), maps `loudness==='off' →
  null` (`:134`), calls `audiobookGenerate` (`:130-138`), then the SSE read loop
  (`:142-169`). **Exact event-consumer key reads:** `started.chapters` (`:152`),
  `chapter.index`/`.total`/`.title` (`:154`), `assembling` (`:155-156`),
  `chapter_error.index`/`.total`/`.title` (`:158`), `done.output` (`:160`) +
  `done.cached_chapters`/`done.failed_chapters` (`:162-163`), `error.error` (`:166`).
  **Completeness notes:** (1) the loop condition is `while (!abortRef.current)` (`:142`) —
  abort just stops the *client* read, the backend keeps rendering; (2) there is **no
  terminal-event guard** — if the stream ends (`reader.read().done`) without a
  `done`/`error` event (truncation), the loop exits, `generating` clears in `finally`
  (`:172-173`), but **no output and no error** is shown (flag).
- `frontend/src/pages/AudiobookTab.jsx:97-111` — `onPreviewChapter` (per-chapter audition,
  warms cache).
- `frontend/src/pages/AudiobookTab.jsx:237-242` — loudness `<select>`: options
  `loudness_off/_acx/_podcast`, each via `t('audiobook.*')` and `aria-label` (i18n-routed
  per C5; keys exist in `en.json`).
- `frontend/src/pages/AudiobookTab.jsx:329-349` — output `<audio>` (`:342`) + download
  `<a download>` (`:344-346`) + cached/failed notes (`:332-340`); `:351-373` — plan
  preview list with per-chapter ▶ + inline `<audio>` (`:370`).
- `frontend/src/components/StoriesEditor.jsx:360-402` — `generateAll`: early-returns on no
  usable tracks (`:362`) or empty `storyToSpans` (`:364`, toast); compiles via
  `storyToSpans(usable, cast)` (`:363`), posts `longformRender({chapters, format})`
  (`:368-371`, format mapped `mp3`→`mp3` else `m4b`), SSE loop (`:377-392`). **Exact
  event-consumer key reads:** `started.chapters` → `total` (`:386`),
  `chapter`/`chapter_error` → `.index`/`total` for `exportPct` (`:387-388`), `done.output`
  → `output` (`:389`), `error.error` → `throw` (`:390`). Then guards `if (!output) throw`
  (`:393`) before `downloadUrl(audioUrl(output), output.split('/').pop())` (`:394`);
  `exporting` reset in `finally` (`:399-401`). **Note:** no abort path here (the loop is
  `while (true)`).
- `frontend/src/components/StoriesEditor.jsx:287-338` — `previewTrack`: client-side
  single-line preview (chained `new Audio()` playback with pause timing; the only
  remaining client render path). `:340-353` — `deliver` (legacy WAV/MP3 path used by
  stems). `:404+` — `exportStemsAll` (client stems export).
- `frontend/src/utils/storyToSpans.js:21-54` — `storyToSpans(tracks, cast)`: cast+lines →
  chapter/span compiler (chapter on `isChapterLine`, `[pause]` fold `:36-40`, SSML-lite
  inner layer `:45-48`, per-line speed ride-through `:34`,`:47`). Output shape matches
  `LongformRenderRequest.chapters` (`[{title, spans:[{voice_id,text,pause_ms_after,
  speed}]}]`).
- `frontend/src/api/audiobook.ts:20-122` — all client functions: `audiobookPlan`(`:20`),
  `audiobookPreviewChapter`(`:39`), `audiobookGenerate`(`:76`), `audiobookUploadCover`
  (`:85`), `audiobookImport`(`:93`), `longformRender`(`:116`); TS interfaces
  `AudiobookSpan/Chapter/Plan` (`:3-17`), `AudiobookPreview` (`:31-36`),
  `AudiobookMetadata` (`:51-58`), `AudiobookGenerateBody` (`:60-69`), `LongformRenderBody`
  (`:100-108`). **Grounding finding (corrected):** the lexicon parity gap is
  **frontend-only** — `LongformRenderBody` (`:100-108`) does **not** declare a `lexicon`
  field, whereas `AudiobookGenerateBody` (`:60-69`) does. The **backend** `/longform/render`
  accepts and forwards `lexicon` (`audiobook.py:513`, `:540`); only the Stories client
  can't send one. **Second TS-shape gap to flag:** `AudiobookSpan` (`:3-7`) and the
  `LongformRenderBody.chapters` spans differ — `AudiobookSpan` (the *plan-preview* shape)
  has **no `speed`**, while `LongformRenderBody` spans carry `speed?`. Both are correct for
  their respective endpoints (`/audiobook/plan` returns no per-span speed; `/longform/render`
  accepts it), but note the asymmetry. Minor parity gap to flag (not a blocker; fixed by
  adding `lexicon?` to `LongformRenderBody`).
- `frontend/src/utils/sseParse.js:14` — `splitSSEBuffer(buffer) -> {lines, rest}`; `:29` —
  `parseSSELine(line) -> object | null` (shared by both read loops; requires
  `line.startsWith('data:')`, slices `line.slice(5).trim()`, `JSON.parse`, returns
  `null` on non-`data:` / empty / malformed JSON — the malformed-line tolerance is a key
  S7 state).
- `frontend/src/pages/Projects.jsx:101-113` — `/longform/jobs` fetch into `longformJobs`
  (consumes `{jobs:[…]}`); `:198-211` — audiobook cards (`type:'audiobooks'`, title
  `j.title || j.output` at `:203`, opens `window.open(audioUrl(j.output), '_blank')` at
  `:209`). Filter id `'audiobooks'` registered at `:96`.
- `frontend/src/pages/Launchpad.jsx:148-153` — Stories (`:148-150`,
  `setMode('stories')`) + Audiobook (`:151-153`, `setMode('audiobook')`) action cards.
- `frontend/src/i18n/locales/en.json` — `"audiobook"` block at `:112` (subtitle/create/
  assembling/loudness*/…), `"stories".exportFailed` at `:94`; mirrored across the other
  20 locales (`ar/de/es/fr/hi/id/it/ja/ko/nl/pl/pt/ru/sv/th/tr/uk/vi/zh-CN/zh-TW`). The
  source of truth for the C5 "no leaked key" assertion.
- `frontend/src/pages/AudiobookTab.css`, `frontend/src/components/StoriesEditor.css` —
  full-width/height layout (PRs #420, #425); verify no phantom gaps / overflow (cf.
  MEMORY: rail-right phantom gap).

---

## API / data shapes (the contracts to assert)

> Every shape below is verified field-by-field against the pydantic models, the SSE
> emitter, the HTTP error sites, and the DB read path on `main@df94af8`. A test
> implementing this section should never have to guess a key, type, or status.

### Request bodies (pydantic, verified)

```jsonc
// POST /audiobook  →  AudiobookRequest (audiobook.py:146-157)
{
  "text": "# Ch 1\nHello [pause 0.5s] world",   // str (required)
  "default_voice": null,                          // str | null  (default null)
  "bitrate": "128k",                              // str         (default "128k")
  "format": "m4b",                                // str "m4b"|"mp3" (default "m4b")
  "loudness": null,                               // str | null  (null/"off"|"acx"|"podcast"; default null)
  "cover_path": null,                             // str | null  (server path from /audiobook/cover)
  "metadata": null,                               // dict | null {title,album,author,narrator,year,genre,description}
  "lexicon": null                                 // dict | null {word: respelling}
}

// POST /longform/render  →  LongformRenderRequest (audiobook.py:505-513)
// CORRECTION vs prior draft: this model DOES have `lexicon` and the endpoint forwards it
// (audiobook.py:540). The lexicon gap is FRONTEND-only (LongformRenderBody TS type,
// audiobook.ts:100-108, omits `lexicon`) — the Stories client just never sends one.
{
  "chapters": [                                   // list[LongformChapter] (default [])
    { "title": "Ch 1",                            // str (default "")
      "spans": [                                  // list[LongformSpan] (default [])
        { "voice_id": null,                       // str | null (default null)
          "text": "Hello",                        // str (required)
          "pause_ms_after": 500,                  // int (default 0)
          "speed": 1.0 } ] } ],                   // float | null (default null)
  "default_voice": null, "bitrate": "128k", "format": "m4b",
  "loudness": null, "cover_path": null, "metadata": null, "lexicon": null
}

// POST /audiobook/plan  →  AudiobookPlanRequest (audiobook.py:74-76)
{ "text": "…", "default_voice": null }            // pure parse, no synth

// POST /audiobook/preview  →  AudiobookPreviewRequest (audiobook.py:304-308)
// NOTE: no bitrate / format / loudness — preview is a raw single-chapter WAV audition.
{ "text": "…", "chapter_index": 0, "default_voice": null, "lexicon": null }
```

`loudness:null` and `cover_path:null` are the **default-build** values (opt-in per C1) —
assert the default request shape produces the platform-identical default output.

### SSE event sequence (the contract both frontends depend on)

The stream is `media_type="text/event-stream"`. Each event is one frame
`data: <json>\n\n` (`_emit`, `audiobook.py:383`). Exactly one **terminal** event
(`done` or `error`) is emitted per stream. Pinned per-`type` JSON (every key listed is
the complete key set the producer emits):

```jsonc
// started — once, after _prepare_synth succeeds (audiobook.py:412)
{ "type": "started", "job_id": "<16hex>", "chapters": <N> }   // N = total planned chapters

// chapter — one per successfully rendered chapter (audiobook.py:430-432)
{ "type": "chapter", "index": <i>, "total": <N>, "title": "…",
  "duration_s": <float, 2dp>, "cached": <bool> }

// chapter_error — one per failed chapter (audiobook.py:424-425)
{ "type": "chapter_error", "index": <i>, "total": <N>, "title": "…",
  "error": "chapter failed to render" }                       // fixed string, no stack

// assembling — once, before the mux (audiobook.py:438), only if ≥1 chapter rendered
{ "type": "assembling" }

// done — terminal success (audiobook.py:463-465). NOTE: no "title" key.
{ "type": "done",
  "output": "<job_type>_<16hex>.<ext>",   // bare filename, job_type ∈ {audiobook, story}, ext ∈ {m4b, mp3}
  "chapters": <k>,                          // len(chapter_files) = MUXED/SUCCESSFUL count (not N, not total events)
  "duration_s": <float, 2dp>,               // sum of SUCCESSFUL chapter durations only
  "cached_chapters": <c>,                   // count of chapters served from cache
  "failed_chapters": [<int>, …] }           // indices that emitted chapter_error (possibly empty)

// error — terminal failure (audiobook.py:386 / :390 / :435 / :474), INSTEAD of done
{ "type": "error", "error": "…" }           // see terminal-state matrix for the exact strings
```

Assert: `output` is a bare filename `<job_type>_<16hex>.<ext>` served at `/audio/<output>`;
`done.chapters` == the number of `chapter` events (since each successful chapter emits
exactly one `chapter`) == ffprobe's chapter count; the `done` event has **no `title`** key;
each `chapter_error.error` is the literal `"chapter failed to render"` (no stack leak).

**Terminal-state matrix (exhaustive — exactly one terminal event per stream):**
| Trigger | Terminal event JSON | `file:line` | File produced? |
|---------|---------------------|-------------|----------------|
| Empty plan (no chapters) | `{"type":"error","error":"nothing to render (no chapters)"}` | `:385-387` | No |
| ffmpeg missing | `{"type":"error","error":"ffmpeg not available; the output needs it"}` | `:388-391` | No |
| All chapters fail | `{"type":"error","error":"all chapters failed to render"}` | `:434-436` | No |
| Mux / unexpected exception | `{"type":"error","error":"render failed (see backend log)"}` | `:466-474` | No (or partial; treat as failure) |
| ≥1 chapter rendered | `{"type":"done", …failed_chapters possibly non-empty}` | `:463-465` | Yes |

### `POST /audiobook/preview`
Request `{text, chapter_index, default_voice?, lexicon?}` (`AudiobookPreviewRequest`,
`audiobook.py:304-308`) → **200** `{ "output": "<rel-path>.wav", "duration_s": <2dp>,
"cached": <bool>, "title": "<chapter title>" }` (`audiobook.py:337-342`). `output` is
`os.path.relpath(wav_path, OUTPUTS_DIR)` — a `.wav` under `longform_cache/`, served via
`/audio`.
Error paths (HTTP 400, body `{"detail": "<msg>"}`): empty script → `"no chapters parsed
from the script"` (`:322-323`); `chapter_index` out of `0..n-1` →
`"chapter_index out of range (0..n-1)"` (`:325-326`).
Assert a second call with identical inputs returns `"cached": true` (resume warm).

### `POST /audiobook/cover`
Multipart field **`cover`** (FastAPI `File(...)`). → **200** `{ "path":
"<OUTPUTS_DIR>/audiobook_covers/<12hex>.<ext>" }` — the **absolute** server path
(`audiobook.py:140`). The client passes this exact string back as `cover_path`.
Error paths (HTTP 400, `{"detail": …}`): ext not in `{.jpg,.jpeg,.png}` →
`"cover must be a .jpg or .png"` (`:133-134`, checked **before** reading bytes); 0 bytes
or > 8 MB → `"cover must be between 1 byte and 8 MB"` (`:136-137`).

### `POST /audiobook/import`
Multipart field **`file`** (FastAPI `File(...)`). → **200** `{ "text": "<script>",
"chapters": <int> }` (`audiobook.py:122`). Non-`.epub` is parsed as UTF-8 text with
`decode(…,"ignore")` (`:117-118`).
Error paths (HTTP 400, `{"detail": …}`): empty → `"empty file"` (`:108-109`); > 64 MB →
`"file too large (max 64 MB)"` (`:110-111`); malformed EPUB → `"couldn't parse EPUB: <e>"`
(`:115-116`); decoded-empty/whitespace → `"no text found in the file"` (`:119-120`).

### `GET /longform/jobs?limit=50`
→ **200** `{ "jobs": [ { "job_id": "<16hex>", "type": "audiobook"|"story", "output":
"<filename>", "duration_s": <2dp float>, "chapters": <int>, "created_at": <float epoch>
[, "title": "<str>"] } ] }`. Item shape notes:
- Keys always present: `job_id, type, output, duration_s, chapters, created_at`
  (`longform_jobs.py:116-123`). **`title` is a conditional key** — added only when
  recoverable (`:135-136`); for longform jobs it is **virtually always absent** (the
  `done` event has no `title`, and `job_store.create` writes empty `meta_json`).
- `output` is a bare filename → served at `/audio/<output>`.
- Newest-first; only finished `audiobook`/`story` jobs (`_LONGFORM_TYPES`,
  `longform_jobs.py:29`) with a recoverable `done` event carrying an `output` filename.
- `limit` is `Query(50, ge=1, le=500)` (`:147`) → out of `1..500` → **422**.
- **C3 anchor:** sourced entirely from the pre-existing `jobs`/`job_events` tables — no
  new table or migration.

### DB read shape (what `build_longform_library` consumes — C3 anchor)
- `list_jobs(status="done", limit=limit*4)` → list of full `jobs` rows; the keys read are
  `row["type"]`, `row["id"]` (→ the item's `job_id`), `row["created_at"]`,
  `row["meta_json"]` (`longform_jobs.py:99-100,122,127`). Columns defined in
  `core/db.py:_BASE_SCHEMA:114-127`.
- `events_since(job_id)` → `[{seq, created_at, payload}]` (`job_store.py:114-122`).
  **`payload` is the bare `json.dumps(event_dict)` string** (the longform `_emit` stores
  it without the `data: ` frame, `audiobook.py:380`); `_done_payload_from_events`
  `json.loads(payload)` directly and returns the first newest-first event whose
  `type == "done"` (`longform_jobs.py:40-50`). Tests/manual seeding `job_events` must use
  **bare JSON**, not an SSE-framed string.

### ffprobe assertions (Layer 1)
- m4b: `format.format_name` contains `mp4`; `streams[audio].codec_name=="aac"`;
  `chapters` length == `done.chapters` == rendered (successful) count; with cover → a
  stream with `disposition.attached_pic==1`.
- mp3: `codec_name=="mp3"`; **no** attached_pic stream even when `cover_path` sent
  (the `not is_mp3` gate at `longform_render.py:260`).
- metadata: `format.tags` carries the mapped keys for any non-blank field — assert all
  **seven** mappings (`title, album, artist←author, composer←narrator, date←year, genre,
  comment←description`); special-char values round-trip the literal (escape verified).
- loudness on: integrated LUFS measurably shifted vs off (loose tolerance; the filter
  ran, exact target not asserted since single-pass per `build_loudnorm_filter`'s docstring).
- **default-shape (C1):** the default request (m4b, `loudness:null`, `cover_path:null`)
  must ffprobe to the **same** container/codec/chapter-shape on every OS lane — assert it
  identically across mac/Win/Linux CI.
- **negative cases:** for empty / no-ffmpeg / all-fail, assert the expected `out_path`
  (`<OUTPUTS_DIR>/<job_type>_<job_id>.<ext>`) **does not exist** (no orphan/partial file).

---

## Test plan

### A. Automated — backend baseline (Layer 0)
1. Run the 9 pure modules listed in Design/Layer 0. Expect all green. Record any
   local-only segfault and defer that module to CI.
1b. **Localization gate (C5):** run `uv run pytest tests/test_no_hardcoded_cjk.py -q` —
   expect green (the 10 shipped longform files are CJK-free). A failure means a hardcoded
   non-English string slipped in: fix it to a `t()` key, or allowlist genuine *functional*
   CJK in `_ALLOWED_FILES` with a justification.

### B. Automated — new API integration `tests/test_longform_e2e.py` (Layer 1)
Module-level `pytestmark = pytest.mark.skipif(find_ffmpeg() is None, ...)`. Stub
`_build_synth` (recommended) or `_prepare_synth` via monkeypatch to a tone generator
returning a 1-D float32 `torch.Tensor` (signatures pinned in Design/Layer 1 and Notes);
point `core.config.OUTPUTS_DIR` at a tmp dir, and point `core.db` at a tmp SQLite DB
built from `_BASE_SCHEMA` (so the job-library cases run against the existing schema with
**no migration** — C3). Every case asserts the **exact event/error/file shapes** from
§"API / data shapes". Cases (each maps to a state in §"States & edge cases"):

**Happy + format/metadata/cover (S2/S3/S4/S5):**
1. **Happy path m4b** (S2 fresh): 3-chapter plan via `/audiobook` → collect SSE → assert
   the **exact event order and key set** (`started{job_id,chapters:3}` → 3×
   `chapter{index,total:3,title,duration_s,cached:false}` → `assembling{}` →
   `done{output:"audiobook_<16hex>.m4b",chapters:3,duration_s,cached_chapters:0,
   failed_chapters:[]}`); ffprobe the `/audio/<output>` file → mp4/aac + 3 chapters.
   **This is the C1 default-shape case** — record the exact ffprobe shape to compare
   across OS lanes.
2. **mp3 format** (S4): same plan, `format:"mp3"` → `done.output` ends `.mp3`; ffprobe mp3.
3. **Global metadata** (S5): pass full `metadata` (all 7 fields incl. `album`) → ffprobe
   tags: `title/album/artist(author)/composer(narrator)/date(year)/genre/comment(description)`
   present and escaped correctly (mapping at `longform_render.py:49-57`; escaping via
   `_escape_meta:60-62`).
   - **3b.** Partial metadata (some keys blank) → only non-blank keys present.
   - **3c.** Metadata + chapter title with `=`/`;`/`#`/`\`/newline → ffprobe round-trips
     the literal value (escape verified).
4. **Cover embed (m4b)** (S3): upload via `/audiobook/cover` (multipart field `cover`),
   pass the returned absolute `cover_path` → attached_pic present. **`B4-mp3`:** same
   cover, `format:"mp3"` → **no** attached_pic, audio intact.
5. **Cover rejection** (S3): pass a `cover_path` with `..` / foreign dir / non-matching
   name / missing file → render still succeeds (`done` emitted), no attached_pic
   (`_safe_cover_path` returns `None`, `audiobook.py:48-71`; the `_COVER_NAME_RE` allowlist
   is the CodeQL path-injection guard, do not regress).
6. **Loudness** (S4): `loudness:"acx"` vs `null` → integrated loudness differs.
   - **6-off.** `loudness:"off"`, `"none"`, and `"bogus"` all behave identically to `null`
     (no `-af`; `build_loudnorm_filter`→None). **C1:** confirms loudness is genuinely
     opt-in (default ≡ off ≡ no filter), so the default path is platform-identical.

**Resume / cache (S6):**
7. **Resume cache**: render plan once, then again unchanged → 2nd run emits
   `cached:true` chapters / `done.cached_chapters>0`; then change one chapter's text →
   that chapter re-renders (`cached:false`), others cached (key change via
   `chapter_cache_key`).
   - **7b.** Same with a voice/order/pause/speed/sample_rate/engine change → re-render.
   - **7c.** Change the resolved voice signature (mutate the stub `resolve` output dict's
     `ref_audio`/`instruct`/`seed`) → re-render (voice_sig in key, `audiobook.py:282-283`).
8. **Lexicon invalidates cache**: identical plan with a new `lexicon` → re-render
   (cache key includes lexicon via the `"\x00lexicon"` sig key, `audiobook.py:284-287`).
   Drive through `/audiobook` (has the field) **and** through `/longform/render`
   programmatically (the *backend* accepts `lexicon` — `audiobook.py:540` — even though
   the Stories TS client omits it; this asserts the backend contract independent of the
   frontend gap).
   - **B-corrupt-cache.** Pre-write a junk file at the cache-key path
     (`<OUTPUTS_DIR>/longform_cache/<key>.wav`) → render → that chapter re-synthesizes
     (`cached:false`) via the `wave.open` `except: pass` fallthrough (`audiobook.py:296-297`);
     no error.
   - **B-evict.** Set `OMNIVOICE_LONGFORM_CACHE_MAX_GB` tiny, pre-seed old WAVs with
     explicit `os.utime` mtimes, render a fresh plan → old entries evicted, the fresh
     chapters survive (cache leases remain held during pruning at render exit).
     Overlap another render's exit with a multi-chapter resume at `started`, engine
     preparation, and after its first chapter: later chapter WAVs and reusable
     segments still hit. Unrelated audio is evictable after plan reservation,
     and closing the stream releases every pin. Plan reservation runs in a worker
     so other requests remain responsive; cancellation releases pins even when
     that worker finishes later. Failed or cancelled muxes remove
     partial MP3/M4B files while retaining the cached audio and resume manifest.

**Partial / total failure (S2):**
9. **Per-chapter fault isolation**: stub synth raises on chapter index 1 →
   `chapter_error{index:1,…,error:"chapter failed to render"}` for index 1, `chapter` for
   the rest, `done.failed_chapters==[1]`, `done.chapters==N-1`, file has N-1 chapters.
   - **9b.** Raise on indices 1 and 3 of 4 → both get `chapter_error`,
     `failed_chapters==[1,3]`, `done.chapters==2`, file has 2 chapters.
10. **All-fail** (S2): stub raises for every chapter → single terminal
    `error{error:"all chapters failed to render"}` (`audiobook.py:435`), **no
    `assembling`, no `done`, no file** (ffprobe path → does not exist).

**Empty / dependency-missing (S0/S1):**
- **B-empty.** `/audiobook` with empty/whitespace `text` → plan has 0 chapters → single
  terminal `error{error:"nothing to render (no chapters)"}` (`audiobook.py:385-387`); no
  file.
- **B-empty2.** `/longform/render` where every span is filtered out (no text + no pause)
  → empty plan → **same** "nothing to render" `error` (NOT "all chapters failed"); no
  file.
15. **No-ffmpeg behavior** (S1): force `services.ffmpeg_utils.find_ffmpeg`→None
    (monkeypatch the symbol the generator imports locally at `audiobook.py:366`) →
    `/audiobook` emits a single `error{error:"ffmpeg not available; the output needs it"}`
    — not a 500, no file (`audiobook.py:388-391`).
    - **B-empty-noffmpeg.** Empty plan **and** no ffmpeg → asserts the "nothing to render"
      error wins (ordering at `:385` precedes `:388`).
    - **B-ffmpeg-fail.** ffmpeg present but make `run_ffmpeg` raise (e.g. monkeypatch to
      raise, or feed an unwritable `out_path`) → terminal `error{error:"render failed (see
      backend log)"}` (`:474`); stack logged, not leaked (assert the body carries no
      traceback text).
    - **B14b-jobstore-fail.** Monkeypatch `job_store.create` to raise → stream still
      completes normally (best-effort swallow at `:374-375`); job just absent from
      library.

**Stories parity + limits (S0):**
11. **`/longform/render` parity**: same happy/cover/loudness/metadata assertions via the
    Stories endpoint, including a pause-only span (`text:""`,`pause_ms_after>0`) carried
    through the retention filter (`audiobook.py:530-532`); `done.output` starts `story_`.
12. **`_MAX_CHAPTERS` guard**: 10_001 chapters to `/longform/render` → **422** with
    `{"detail":"too many chapters (max 10000)"}` (`audiobook.py:523-524`) — assert it's a
    422 *before* the stream opens, not an SSE `error`.

**Preview (S0):**
13. **`/audiobook/preview`**: single chapter → 200 `{output:"longform_cache/….wav",
    duration_s, cached:false, title}`, then re-call → `cached:true`.
    - **B13b.** `chapter_index` out of range → **400** `{"detail":"chapter_index out of
      range (0..n-1)"}`.
    - **B13c.** empty script → **400** `{"detail":"no chapters parsed from the script"}`.

**Job library (S10) — also the C3 backward-compat-data gate:**
14. **`/longform/jobs`**: after a successful render against a **`_BASE_SCHEMA`-only** tmp
    DB (no migration applied), the job appears with the **exact item shape** — keys
    `job_id`(==row `id`), `type`, `output`, `duration_s`, `chapters`, `created_at`, and
    **no `title`** key; a job with no `done` event is excluded
    (`build_longform_library` skips it, `longform_jobs.py:110-111`); a job whose `done`
    has no `output` is excluded (`:113-114`). Seed `job_events.payload` with **bare JSON**
    `done` events (matching `_emit`'s storage form). **C3 assert:** the library populated
    with **no new table and no `_migrate()`/alembic step** — i.e. a fresh base-schema DB
    serves longform jobs; record this as the backward-compat proof.
    - **B14b.** `?limit=0` and `?limit=501` → **422**; `?limit=50` default honored.

### C. Automated — import/parse edge cases (S8)
16. `/audiobook/import` (multipart field `file`):
    - `.txt` with `Chapter 1`/`Prologue` lines → headings inserted (`chapterize_plaintext`);
      assert `{text, chapters}` shape.
    - `.md` already containing `# ` → untouched (the `_H1_RE` no-op at
      `longform_import.py:43`).
    - a minimal valid `.epub` → spine-order chapters.
    - **empty file → 400** `{"detail":"empty file"}`.
    - **all-whitespace text file → 400** `{"detail":"no text found in the file"}`.
    - **> 64 MB → 400** `{"detail":"file too large (max 64 MB)"}`.
    - **malformed/empty EPUB (broken zip / no spine) → 400** `{"detail":"couldn't parse
      EPUB: …"}`.
    - **zip-bomb EPUB** (oversize entry/total) → 400 (caps at `longform_import.py:30-31`).
    - a `.bin` non-epub → treated as text (the non-`.epub` branch at `audiobook.py:117-118`).
    - a file with non-UTF-8 bytes → decodes with `ignore`, no crash.
    (All stdlib-only parsing — local-first, C2; no network reached during import.)

### D. Automated — frontend unit (Layer 0) + Playwright smoke (Layer 2)
17. Run `bun run --cwd frontend test` (existing vitest suite — includes `sseParse.test.js`
    which already covers the malformed-JSON / split-buffer / no-trailing-space S7 states;
    confirm those exist, extend if a state is missing).
18. New `frontend/e2e/longform.spec.ts` (mock SSE via `page.route`; navigate with
    `gotoMode` from `_helpers.ts`; **mock bodies must use the exact event JSON from
    §"API / data shapes"**):
    - **Controls present (S11):** Audiobook tab (`gotoMode(page, 'audiobook')`): type a
      script, click the plan button (mock `/audiobook/plan` → `{chapters,chapter_count,
      char_count}`) → chapter list renders; assert format/loudness selects, metadata
      inputs, cover picker (`<input type="file" accept=".jpg…">`), lexicon add/remove
      rows, markup-help `<details>`, import button (`accept=".txt,.md,.epub"`,
      `AudiobookTab.jsx:192`) all present and labeled (no raw i18n keys leaking).
    - **Localization (C5):** seed a non-`en` locale (e.g. set the i18n language before
      boot) and assert the same controls render **translated** labels — assert the DOM
      contains **no** raw `audiobook.*` / `stories.*` key strings. A leaked key fails the
      localization rule.
    - **Loudness opt-in (C1):** assert the loudness `<select>` defaults to the `off`
      option (`AudiobookTab.jsx:35,240`) on first mount — proving the default is opt-in.
    - **Happy SSE (S7):** click "Create" with a mocked SSE stream returning
      `started`/`chapter`/`done` (exact JSON) → progress text updates and the output
      `<audio>` (`AudiobookTab.jsx:342`) + download link (`:344`) appear, fed by
      `done.output`.
    - **Error SSE (S7):** mock a stream whose terminal event is
      `{"type":"error","error":"…"}` → assert `setError` surfaces the message in the UI
      (no silent swallow), spinner clears.
    - **Partial SSE (S7):** mock `chapter_error` + `done.failed_chapters:[1]` → assert the
      output appears **and** the failed-chapter note renders (`AudiobookTab.jsx:332-340`).
    - **Stories (S7):** `gotoMode(page, 'stories')`: add lines, pick format, mock
      `/longform/render` SSE → Generate shows `%` (`exportPct`) then triggers a download
      via `downloadUrl` (`StoriesEditor.jsx:394`; assert the synthesized `<a download>`).
      Also mock an `error`-terminal stream → assert `toast.error('stories.exportFailed')`
      and `exporting` resets.
    - **Projects (S10):** mock `GET /longform/jobs` with `{jobs:[{job_id,type:"audiobook",
      output,duration_s,chapters,created_at}]}` (no `title`) → an `audiobooks` card renders
      with the right title(=filename)/subtitle (`Projects.jsx:198-210`); also mock an empty
      `{jobs:[]}` → empty-state renders, no crash.
    - All mocks stay at the **network boundary** (`page.route` only) — no cloud call,
      local-first preserved (C2); the real React + `sseParse`/`storyToSpans` run.
    - Assert **zero fatal console errors** across the run (reuse the `collectErrors` sink
      from `_helpers.ts`; consider widening beyond the chunk-load `FATAL` list to also
      flag i18n-miss / React-key warnings for this spec).

### E. Manual runtime checklist (Layer 2 — human, running app)
Run `bun run dev` (vite on :3901 — see config note) with the backend on :3900, or the
desktop build; use a real installed engine (or the bundled demo voice) so synthesis is
real. Verify on the local platform; spot-check the other two platforms or delegate.
(All local — no cloud/account/key; local-first, C2.)

Audiobook tab — happy paths:
- [ ] Paste a 2–3 chapter `# H1` script with a `[voice:NAME]` switch and a `[pause 1s]`;
      the plan-preview lists the right chapters/spans/char counts.
- [ ] Per-chapter ▶ preview produces audio that **plays in the inline `<audio>`**
      (`AudiobookTab.jsx:370`).
- [ ] Re-preview the same chapter is instant (cache hit; `cached:true`; no second synth
      wait).
- [ ] Fill all metadata fields (incl. album) + upload a jpg cover; "Create" streams
      progress; the finished m4b **plays** and, opened in a player (or ffprobe), shows the
      cover + 7 tags + chapter marks.
- [ ] Switch format to mp3 → output is mp3, plays, no cover (expected per `:260` skip).
- [ ] Loudness OFF by default (`loudness` state default `'off'`, `AudiobookTab.jsx:35` —
      verified; C1 opt-in); turning on ACX produces audibly normalized output.
- [ ] Add a lexicon row (e.g. `VoiceStudio → Omni Voice`) → the word is pronounced
      respelled in preview/output.
- [ ] Use markup `[slow]…[/slow]`, `[fast]`, `[emphasis]`, `[spell]USA[/spell]` in the
      script → audible rate change / letter-spelling.
- [ ] Import a `.txt`, a `.md`, and a real `.epub` → script populates with chapters.
- [ ] Download button (`download={output}`, `:344`) saves a file that opens externally.

Audiobook tab — edge/error paths (the completeness pass):
- [ ] Click "Create" on an **empty script** → a visible error ("nothing to render"),
      no spinner stuck, no file.
- [ ] Upload a cover with the **wrong type** (e.g. `.gif`) → 400 surfaced as a visible
      error, not a silent failure.
- [ ] Upload a **renamed non-image** `.jpg` (legal size) → observe whether the render
      fails wholesale (S3 `B-cover-fakebytes` gap) or drops the cover; record the result.
- [ ] **Abort mid-render** (if the UI exposes a cancel / navigate away) → spinner clears;
      check the backend log/Activity for a hung/zombie ffmpeg job (S7 abort flag).
- [ ] Trigger a **partial failure** if reproducible (e.g. an engine that fails one
      chapter) → finished file still produced; the failed-chapter note shows.

Stories tab:
- [ ] Build a multi-character cast + lines (incl. a `# ` chapter line and a per-line
      speed override); single-line ▶ preview plays (client Web-Audio path,
      `StoriesEditor.jsx:287-338`).
- [ ] "Generate" (m4b and mp3) streams `%`, downloads a chapter-marked file that plays;
      per-line speed is honored in the output (rides through `storyToSpans.js:34,47` →
      `Span.speed` → `synthesize_chapter`).
- [ ] "Generate" with **no usable tracks** → silent no-op (no request, no error toast);
      with tracks that compile to **0 chapters** → `stories.exportFailed` toast.
- [ ] Force a render `error` (e.g. stop the backend mid-stream) → `stories.exportFailed`
      toast, Generate re-enabled (`exporting` reset).
- [ ] "Stems" export still works (client path, `exportStemsAll`, one file per voice).

Cohesion + layout:
- [ ] Launchpad shows Stories + Audiobook cards (`Launchpad.jsx:148-153`); clicking each
      navigates correctly (`setMode('stories')` / `setMode('audiobook')`).
- [ ] Projects "AUDIOBOOKS" filter lists finished books/stories; clicking a card opens
      the file (`window.open(audioUrl(j.output), '_blank')`, `Projects.jsx:209`). Note
      the card title is the **filename** (no `title` key recovered — grounding finding).
      With **no longform jobs**, the filter shows an empty state (no crash).
- [ ] **Backward-compat data (C3):** point the app at a **pre-session `omnivoice_data/`**
      (a v0.3.5-era DB with old jobs/projects) → Projects + Audiobook load with **no
      migration prompt, no error**; old jobs still listed. Confirms the existing
      `jobs`/`job_events` schema is reused unchanged.
- [ ] Audiobook + Stories tabs are **full-width/height** with no phantom gap or
      horizontal scrollbar (cf. MEMORY phantom-48px-gap), at rail-left and rail-right.
- [ ] No fatal console errors in the webview devtools during any of the above.

### F. Cross-platform parity (constraint gate — C1)
- [ ] Confirm loudness + cover remain **opt-in / off-by-default** in the UI on all three
      OSes (default behavior identical — the strict 2026-05-20 rule). Default render
      (no loudness, no cover, m4b) must produce an identical-shape output everywhere
      (compare the recorded ffprobe shape from case B1 across OS lanes).
- [ ] WebKitGTK (Linux desktop build) plays the m4b/AAC output — this is the highest-risk
      playback path; if it fails, that's a P0 (default feature broken on a platform).
      Per the rule: **fix-on-platform or move behind opt-in — no third option.**
- [ ] Error/empty paths behave identically across OSes (e.g. the "nothing to render"
      error and the cover-type 400 surface the same way) — a divergence here is also a
      default-behavior P0.
- [ ] **Localization (C5):** the UI renders translated labels on at least one non-`en`
      locale on each OS (no leaked `audiobook.*` / `stories.*` keys) — a per-platform i18n
      regression is still a parity issue.

---

## Constraints (operational gate summary)

The full rule-by-rule mapping lives in §"Constraints" above; this is the one-line gate
each verification layer enforces:

- **Cross-platform parity (C1, strict 2026-05-20):** loudness/cover/SSML are opt-in
  (`loudness` defaults to `'off'` at `AudiobookTab.jsx:35` — verified; no cover unless
  uploaded); the default render path (m4b, no loudness, no cover) must ffprobe to the
  **same shape** on mac/Win/Linux and play on WebKitGTK. A default that fails on a
  platform is a **P0** — flag it and fix-on-platform-or-make-opt-in; no third option, no
  v0.4 deferral.
- **Local-first (C2):** all verification runs on-device; no cloud, no telemetry, no
  account/key. ffmpeg via `find_ffmpeg` (bundled/local); EPUB parse stdlib-only
  (`longform_import.py` — `zipfile`/`xml.etree`/`html.parser`); Playwright mocks at the
  network boundary only; Layer 1 stubs synth and runs offline.
- **Backward-compat data (C3):** **no schema change shipped this session.** The job
  library reads the **existing** `jobs`/`job_events` tables (`core/db.py:_BASE_SCHEMA:114,
  :129`) via `build_longform_library` over `job_store.list_jobs`/`events_since` — no new
  table, no `_migrate()` rung (ladder tops at v4, `core/db.py:203-205`), no new alembic
  revision (`core/db.py:220-225`). The zustand-persist key `omnivoice.app` stays at
  `version:4` (no localStorage bump). Verification proves a `_BASE_SCHEMA`-only DB and a
  pre-session `omnivoice_data/` both serve longform unchanged. **Any future schema change
  must go through alembic with a tested upgrade path** (PR-C, not in-scope here).
- **CodeQL py/polynomial-redos (C4):** the four user-input-reachable regexes are
  linear/bounded and CodeQL-clean — `_BITRATE_RE` `^\d{2,3}k$` (`longform_render.py:37`),
  `_escape_meta` `[=;#\\\n]` substitution (`:62`), `_COVER_NAME_RE`
  `^[0-9a-f]{12}\.(?:jpg|jpeg|png)$` (`audiobook.py:45`), `_VOICE_RE`
  `\[voice:([^\]\[]*)\]` (excludes both bracket delimiters — `services/audiobook.py:42`).
  Verification adds no new user-input regex; any PR-C fix that adds/edits one (e.g. a cover
  magic-byte sniff) must pass the polynomial-ReDoS query (anchor, no `[^x]*`/`\s*` overlap,
  fixed bounds).
- **Localization (C5):** every shipped control routes through `t('audiobook.*')` /
  `t('stories.*')`; keys exist in `en.json` (`:112`, `:94`) and all 20 other locales; no
  hardcoded CJK in the 10 shipped files (verified). Gates: Layer-0
  `tests/test_no_hardcoded_cjk.py` + a Playwright non-`en`-locale "no leaked key" check.
  Any PR-C string must be a `t()` key; any functional CJK must be allowlisted with a
  justification.
- **Versioning + cadence (C6):** version triplet already at `0.3.6`
  (`tauri.conf.json:4` / `Cargo.toml:3` / `pyproject.toml:7`) = `v0.3.5` + 1 patch.
  Verification commits and PR-C fixes land continuous-to-main under v0.3.6 — no RC, no
  soak, no re-version. **Docs-sync:** any PR-C fix that alters documented behavior updates
  the doc in the **same** PR.
- **No GPU required for CI:** Layer 1 stubs the synth (`_build_synth`/`_prepare_synth`);
  Layer 2-Playwright mocks SSE.
- **uv for Python** (MEMORY): all backend tests via `uv run pytest`, never
  `.venv/bin/python`. Run from the repo root so the root `omnivoice` package resolves.
- **CI gates (MEMORY):** never merge before PR checks green; local loop must include
  `bun run --cwd frontend test` (vitest).
- **Playwright port (corrected grounding):** there is **no** :3901/:5173 mismatch. Vite
  serves :3901 (`vite.config.js:23`, `port: OMNIVOICE_UI_PORT || 3901`), `bun run dev` is
  `"vite"` (`frontend/package.json:8`), and `playwright.config.ts` `baseURL` is :3901
  (`:17`, `E2E_PORT || 3901`) with a `webServer` that runs `bun run dev`
  (`playwright.config.ts:25-30`, `reuseExistingServer:true`). The two env knobs differ
  (`OMNIVOICE_UI_PORT` for vite, `E2E_PORT` for playwright) — keep them equal if you
  override the default. (The original draft's "vite serves :5173" claim was incorrect.)

## Dependencies

- **No new runtime deps.** Verification only.
- **Test/dev tooling (already present):** `pytest`, `httpx` (dev dep, for SSE/TestClient),
  `ffprobe` (ships with ffmpeg — same binary `find_ffmpeg` resolves), Playwright
  (`@playwright/test@^1.60`, system chromium at `/usr/bin/chromium` per
  `playwright.config.ts:21`), `vitest@^4`, and `tests/test_no_hardcoded_cjk.py` (the
  existing CJK/localization gate).
- **For the manual layer:** a real installed TTS engine OR the bundled demo voice; a
  desktop build for the WebKitGTK playback check (C1); a **pre-session `omnivoice_data/`**
  (v0.3.5-era DB) for the C3 backward-compat check.
- **Test fixtures to author:** a minimal valid `.epub` (spine + 2 chapters), a malformed
  `.epub` (broken zip / no spine), a 1×1 valid jpg + png cover, a renamed non-image
  `.jpg`, and an oversize stub file generator (for the 64 MB import cap — generate, don't
  commit the bytes). For the job-library cases, seed `job_events.payload` with **bare-JSON**
  `done` events (not SSE-framed strings) to match `_emit`'s storage form. Keep fixtures
  tiny and under `tests/fixtures/`. Fixtures must be ASCII/binary only — no hardcoded CJK
  in fixture *filenames or committed text* unless allowlisted (C5).

## Risk

- **ffmpeg version variance** — the cover/mp3/loudnorm argv may behave differently across
  ffmpeg builds; the Layer-1 ffprobe assertions are the guard. Risk that CI's ffmpeg
  differs from a user's bundled one — note the bundled-ffmpeg version in the report. This
  is also the **C1 parity risk**: if the bundled ffmpeg differs per OS, the "identical
  default shape" claim can break silently — assert ffprobe shape per OS lane.
- **Resume cache flakiness in tests** — cache lives in `OUTPUTS_DIR/longform_cache`
  (`audiobook.py:399`, shared with preview at `:329`); tests must use a tmp `OUTPUTS_DIR`
  (monkeypatch `core.config.OUTPUTS_DIR`) and clean between cases, or cases 7/8/B-corrupt/
  B-evict cross-contaminate. `prune_cache_dir` LRU is mtime-based; on fast filesystems
  mtimes can tie — keep cache cases small and, for `B-evict`, set explicit `os.utime`
  mtimes on pre-seeded files so eviction order is deterministic.
- **DB isolation (C3)** — the job-library cases must point `core.db` at a **tmp** SQLite
  DB (built from `_BASE_SCHEMA`) so they don't write into the dev `omnivoice_data/` and so
  the "no migration needed" assertion is clean. Don't mutate the real DB during verify.
  Seed `job_events` with **bare JSON** payloads (the `_emit` storage form), not `data: …`
  frames — `_done_payload_from_events` `json.loads` the payload directly.
- **Fault-injection through the executor** — the stub raises inside
  `loop.run_in_executor`; verify the exception surfaces as a caught `chapter_error`
  (`audiobook.py:420`) and not an un-awaited-task warning. Make the stub raise
  *deterministically by chapter index/text*, not randomly.
- **SSE buffering** — a FastAPI `TestClient` may buffer the whole stream; if event
  *ordering* must be asserted live, use `httpx.AsyncClient` streaming or drive the async
  generator directly. **Prefer driving the generator** (`async for ev in
  _render_longform_sse(...)`) for ordering assertions — remember each yielded item is the
  `data: …\n\n` frame, so strip+`json.loads`. This is also the only clean way to assert
  the *terminal-state matrix* (exactly one of the five terminal events).
- **Truncated-stream / abort states are partly automation-blind** — the client
  truncation behavior (Audiobook loop exits with no output, no error) and the
  abort-doesn't-cancel-server behavior are manual-only; budget the manual time and flag
  any defect rather than skipping.
- **WebKitGTK AAC/m4b playback** is the single biggest unknown and can't be caught by
  automation — it is the explicit manual gate **and the highest-risk C1 default-parity
  path**; if it fails, the default m4b feature is broken on a platform = P0. Budget time.
- **Stub-synth fidelity** — a tone stub won't catch real-engine-only bugs (e.g. sample
  rate from `model.sampling_rate`, `audiobook.py:247`); the manual layer is the backstop.
  Document that Layer 1 proves *plumbing*, not *audio quality*.
- **False green from over-mocking** — if the Playwright smoke mocks too much, it proves
  nothing. Keep mocks at the network boundary only (`page.route`); let the real React +
  `sseParse`/`storyToSpans` parsers run. **Mock bodies must match the pinned event JSON**
  (§"API / data shapes") or the smoke verifies a fiction. (Mocking at the network boundary
  also keeps the smoke local-first — no real outbound call, C2.)
- **`validate_cover_image` doesn't check magic bytes** — a renamed non-image of legal
  size/extension reaches ffmpeg and can fail the whole render instead of being dropped
  (S3 `B-cover-fakebytes`). Confirm the actual behavior in B and manual E; if it fails the
  render wholesale, that's a defect candidate for PR-C — **and any magic-byte sniff added
  to fix it must not introduce a ReDoS-prone regex (C4).**
- **Localization regressions are easy to miss** — a control that hardcodes a string or
  references a missing key renders the raw key (e.g. `audiobook.create`) and CI may stay
  green if no test asserts it. The Playwright non-`en`-locale "no leaked key" check (C5)
  is the guard; add it, don't assume.
- **`done.chapters` semantics trap** — `done.chapters` is the **successful/muxed** count
  (`len(chapter_files)`), not the planned total (which is `started.chapters`) and not the
  number of `chapter_error`s. A test asserting `done.chapters == planned N` will wrongly
  fail on a partial render. Assert `done.chapters == N - len(failed_chapters) ==
  ffprobe chapter count`.

## PR slices

Each independently shippable; verification artifacts + any defect fixes are separate.
All land **continuous-to-main under v0.3.6** (no RC, no re-version — C6).

1. **PR-A — API integration suite** (`tests/test_longform_e2e.py`): the stub-synth +
   real-ffmpeg cases B/C above, **including the empty / no-ffmpeg / partial-fail / all-
   fail / corrupt-cache / evict / cover-rejection / bad-bitrate / garbage-format / out-of-
   range-preview / oversize-import edge cases** (not just the happy path), asserting the
   **exact event/error/file/DB shapes** pinned in §"API / data shapes", **plus the C3
   backward-compat-data assertion** (job library off a `_BASE_SCHEMA`-only tmp DB, no
   migration) and the C1 default-shape recording. Highest value, runs in CI. Includes the
   monkeypatch `_build_synth` (or `_prepare_synth`) + tmp `OUTPUTS_DIR` + tmp `core.db`
   harness + the tiny fixtures. Docs-sync: none (test-only).
2. **PR-B — Playwright longform smoke** (`frontend/e2e/longform.spec.ts`): asserts
   controls + mocked-SSE flows (happy **and** error **and** partial **and** empty-Projects,
   mock bodies matching the pinned event JSON) + the **C5 non-`en`-locale "no leaked key"
   check** + the **C1 loudness-defaults-off check** + zero fatal console errors. **Also add
   `'audiobook'` to the `MODES` array in `frontend/e2e/_helpers.ts`** (currently missing)
   so the generic mount-smoke covers it. No port-mismatch fix is needed (the ports already
   match). Docs-sync: none (test-only).
3. **PR-C — defect fixes** (only if Layers 1–2 surface bugs): one focused PR per defect,
   each with a regression test, **continuous-to-main, no re-version (C6)**, and
   docs-synced in the same PR if it touches documented behavior. Known grounding-stage
   candidates to confirm-or-fix:
   (a) longform Projects cards never show a real title (`done` event + `job_store.create`
   carry none; the item omits `title`) — if fixed by adding `meta={"title":…}` to
   `job_store.create` (`audiobook.py:372`), **no schema change needed** (`meta_json` column
   already exists, `db.py:123` / `job_store.py:45`), so C3 is satisfied automatically;
   (b) frontend `LongformRenderBody` (`audiobook.ts:100-108`) omits `lexicon` so the
   Stories client can't pass one (backend already accepts it) — TS-type-only fix, no
   localized string;
   (c) `validate_cover_image` accepts a renamed non-image (no magic-byte check) so a bad
   cover can fail the whole render rather than being dropped — **if fixed with a sniff,
   keep any regex CodeQL-clean (C4)** and surface any new user-facing message via i18n (C5);
   (d) Audiobook `onCreate` surfaces no error on a truncated stream (no terminal-event
   guard) — any new error message must be a `t()` key (C5) and behave identically on all
   OSes (C1);
   (e) client abort doesn't cancel server-side rendering — if a server cancel path is
   added it must stay local-only (C2) and behave identically cross-platform (C1).
   **If a default-behavior platform divergence is found → P0**, fix-on-platform-or-move-
   behind-opt-in (no third option per C1).
4. **Manual checklist report** — not a PR; the execution output of Layer 2-manual / F,
   returned as findings (defects → feed PR-C). Per project instruction, do not write a
   summary `.md`; report findings directly.

> Run order: Layer 0 → PR-A → PR-B → manual (E/F). Stop and file a P0 immediately if the
> default render path fails on any platform or WebKitGTK can't play the m4b.

## Acceptance criteria

- [ ] Layer 0 pure suites green (backend 9 modules + frontend vitest) **and the C5
      localization gate green** (`tests/test_no_hardcoded_cjk.py`).
- [ ] `tests/test_longform_e2e.py` exists, runs in CI with ffmpeg, and **all** Layer-1
      cases (the B/C set, happy **and** every enumerated edge state in §S0–S10 that's
      automatable) pass; the module skips cleanly (not red) when ffmpeg is absent.
- [ ] **Exact API/SSE/DB contracts asserted:** each event's full key set
      (`started{job_id,chapters}`, `chapter{index,total,title,duration_s,cached}`,
      `chapter_error{index,total,title,error}`, `assembling{}`,
      `done{output,chapters,duration_s,cached_chapters,failed_chapters}` with **no
      `title`**, `error{error}`); each HTTP error's `(status, {detail})`; the
      `/longform/jobs` item shape (keys `job_id,type,output,duration_s,chapters,created_at`,
      `title` conditional); `done.chapters` == successful/muxed count == ffprobe chapters.
- [ ] **Terminal-state matrix proven:** each of the five terminal outcomes (empty→error,
      no-ffmpeg→error, all-fail→error, mux-fail→error, ≥1-chapter→done) is asserted with
      its exact string, and the negative cases leave **no file** in `OUTPUTS_DIR`.
- [ ] ffprobe confirms: m4b = mp4/aac + N chapters; mp3 = mp3, no attached_pic; cover
      embedded for m4b only; all **7** metadata tags mapped + escaped correctly; loudness
      preset measurably applied; `off`/`none`/unknown loudness ≡ null.
- [ ] **Input-validation paths verified:** empty/oversize/malformed import → 400 (exact
      detail); wrong-type/oversize cover → 400; out-of-range/empty preview → 400; >10k
      chapters → 422; `limit` out of range → 422; bad bitrate → silent 128k fallback;
      garbage format → silent m4b.
- [ ] Resume verified: unchanged re-render reports cached chapters; a text/voice/order/
      pause/speed/sample_rate/engine/lexicon/voice-signature change invalidates exactly
      the changed chapter; a corrupt cache entry re-renders; eviction drops old entries
      but never the fresh job's chapters.
- [ ] Fault isolation verified: one (and several-but-not-all) bad chapters →
      `chapter_error` + `failed_chapters`, file still produced from the rest with
      `done.chapters==N-len(failed_chapters)`; all-fail → terminal `error`, no file, no 500.
- [ ] `_safe_cover_path` traversal/foreign/non-matching/missing path rejected without
      failing the render (CodeQL path-injection guard not regressed).
- [ ] **C3 backward-compat proven:** the job library populates from a `_BASE_SCHEMA`-only
      tmp DB with **no new table and no migration step** (events seeded as bare-JSON `done`
      payloads), and a pre-session `omnivoice_data/` loads in the running app with no
      upgrade prompt.
- [ ] **C4 confirmed:** no new user-input regex introduced; the four existing regexes
      stay CodeQL-clean (any PR-C regex passes the polynomial-ReDoS query).
- [ ] Playwright smoke passes: every shipped control present + labeled (no raw i18n
      keys), **on a non-`en` locale too (C5)**; loudness defaults off (C1);
      happy/error/partial mocked SSE flows each handled correctly (progress, error toast,
      failed-chapter note); Projects shows a longform card and an empty state; **zero
      fatal console errors**; `'audiobook'` mode is reachable.
- [ ] Manual checklist (E) fully executed on the local platform with a real engine —
      including the **edge/error rows** (empty script, wrong-type cover, renamed-non-image
      cover, abort, partial failure, no-usable-tracks, mid-stream error) **and the C3
      pre-session-data-dir row**; every box checked or a defect filed; Stories single-line
      preview (Web-Audio) and full `/longform/render` both produce playable output.
- [ ] **Cross-platform gate (F, C1):** default render path produces identical-shape output
      (same ffprobe shape across OS lanes) and plays on mac/Win/Linux incl. WebKitGTK;
      loudness/cover confirmed opt-in everywhere; error/empty paths surface identically;
      i18n labels resolve on a non-`en` locale on each OS. Any divergence filed as P0
      (fix-on-platform-or-opt-in, no third option).
- [ ] All defects found are filed as concrete `file:line` findings (and, if fixed, each
      has a regression test in PR-C, continuous-to-main under v0.3.6, docs-synced if it
      touches documented behavior). The five named candidates (a–e in PR-C) are each
      explicitly confirmed-or-refuted.
- [ ] Verification ran entirely **local-first** (C2): no cloud call, no telemetry, no
      account/key introduced; Layer 1 offline, Layer 2 mocks at the network boundary only.
- [ ] Verification is repeatable: a second person can run Layers 0–2 from this spec
      without tribal knowledge.

---

### Notes for the executor
- Existing pure-test patterns to mirror live in `tests/test_longform_render.py`,
  `tests/test_audiobook_preview.py`, `tests/test_longform_jobs.py` (backend) and
  `frontend/src/test/storyToSpans.test.js`, `frontend/e2e/ui-smoke.spec.ts` +
  `frontend/e2e/_helpers.ts` (frontend — note `collectErrors`/`gotoMode` helpers). The
  localization gate to keep green is `tests/test_no_hardcoded_cjk.py` (allowlist
  `_ALLOWED_FILES`).
- The SSE generator is an `async def ... yield` (`audiobook.py:345`) — drive it with
  `async for` for deterministic ordering rather than fighting `TestClient` stream
  buffering. Each yielded item is a `data: {json}\n\n` frame; strip `data: ` and
  `json.loads` the rest. This is also the only clean way to assert the terminal-state
  matrix.
- **Monkeypatch target for the synth stub:** `backend/api/routers/audiobook.py:_build_synth`
  → return `{"mode":"generic", "resolve":<fn>, "engine_id":"stub", "synth":<fn>,
  "sample_rate":24000}` (`_prepare_synth`'s generic branch passes it straight through with
  no model load). Patching the higher-level `_prepare_synth` (async, returns the 4-tuple
  `(synth, sample_rate, resolve, engine_id)`) also works. Either patch covers `/audiobook`,
  `/longform/render`, **and** `/audiobook/preview`.
- **Exact stub signatures:** `synth(text: str, voice_id: str | None, speed: float | None =
  None) -> torch.Tensor` (1-D float32, `numel()>0`); `resolve(voice_id: str | None) ->
  {"ref_audio","ref_text","instruct","seed"}`. For fault cases, make `synth` raise
  deterministically by chapter text/index.
- Use a tmp dir for `core.config.OUTPUTS_DIR` so the cache (`longform_cache/`), covers
  (`audiobook_covers/`), per-job work dirs (`<job_type>_<id>/`), and outputs don't pollute
  `omnivoice_data/` and resume cases stay isolated. For the eviction case set explicit
  `os.utime` mtimes on pre-seeded WAVs so LRU order is deterministic. **Also point
  `core.db` at a tmp SQLite DB built from `_BASE_SCHEMA`** so the job-library / C3 cases
  don't touch the real DB and the "no migration" assertion is clean; seed `job_events`
  with **bare-JSON** payloads (the `_emit` storage form).
- Run backend tests from the **repo root** (not `backend/`) so the root-level `omnivoice`
  package — source of `parse_pause_markers` (`omnivoice/utils/text.py:253`) — imports, via
  `uv run pytest` (MEMORY: never `.venv/bin/python`).
- **Constraint quick-refs (grounded this revision):** loudness default `'off'`
  (`AudiobookTab.jsx:35`); i18n keys exist in `en.json` (`audiobook` block `:112`,
  `stories.exportFailed` `:94`) + 20 other locales; no hardcoded CJK in the 10 shipped
  files; `jobs`/`job_events` tables pre-exist in `core/db.py:_BASE_SCHEMA:114,:129` (no new
  table/migration shipped); the four user-input regexes are linear/bounded (`_BITRATE_RE`
  `longform_render.py:37`, `_escape_meta` `:62`, `_COVER_NAME_RE` `audiobook.py:45`,
  `_VOICE_RE` `services/audiobook.py:42`); version triplet at `0.3.6` (`tauri.conf.json:4`/
  `Cargo.toml:3`/`pyproject.toml:7`).
- **Corrected grounding vs. the prior draft:** (1) `/longform/render`'s pydantic model
  **does** have `lexicon` and the endpoint forwards it — the gap is the *frontend* TS
  type only (`LongformRenderBody`, `audiobook.ts:100-108`); (2) the empty-plan error
  ("nothing to render") is emitted **before** the no-ffmpeg check, so order your no-ffmpeg
  + empty combined case accordingly; (3) `validate_cover_image` checks
  extension/size/existence but **not** magic bytes; (4) the repo HEAD is `main@df94af8`
  (not the `feat/stories-shared-render` branch named in the original draft); (5) the job
  library reads **existing** `jobs`/`job_events` SQLite tables — there is no new
  `longform_jobs` table and no migration this session; (6) **NEW — `_GLOBAL_TAG_KEYS` has
  7 entries** (the prior draft's metadata list omitted `album→album`,
  `longform_render.py:52`); (7) **NEW — `done.chapters` is the successful/muxed count**
  (`len(chapter_files)`), not the planned total nor the count of `chapter` events naively
  — `started.chapters` is the planned total; (8) **NEW — `job_events.payload` stores the
  bare `json.dumps(event)` string**, NOT a `data: …` frame (the `job_store` docstring is
  stale; `_emit` at `audiobook.py:380` passes the bare JSON); seed/parse accordingly;
  (9) **NEW — `/audiobook/cover` returns the ABSOLUTE server path** in `{path}`, and the
  multipart field is `cover` (vs `file` for `/audiobook/import`); (10) **NEW —
  `/audiobook/preview` has no `bitrate`/`format`/`loudness`** — it returns a raw `.wav`
  under `longform_cache/`; (11) **NEW — `/longform/jobs` item `title` is a conditional
  key** (omitted when unrecoverable), and `job_id` mirrors the `jobs.id` column.
