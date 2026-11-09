# TASK #28 — Two-pass ACX loudness mastering

> **Historical design reference.** Paths, status and instructions below describe
> the original implementation snapshot. For current behavior see the
> [longform guide](../../electron-longform.md); follow current repository rules for new changes.

## TL;DR

Today the longform renderer (Audiobook + Stories) applies a **single-pass** `loudnorm` filter built by `build_loudnorm_filter()` (`backend/services/longform_render.py:159`). Single-pass `loudnorm` is a *dynamic* normalizer that does **not** reliably hit a target integrated LUFS or a hard true-peak ceiling — it's documented by FFmpeg as "the result will not be as accurate" as two-pass. ACX submission requires integrated loudness inside −23…−18 LUFS and a peak ≤ −3 dBTP; single-pass routinely lands outside that window.

Upgrade to **two-pass** `loudnorm`: a first **measure** pass (`print_format=json`, output to `-f null -`) parses the clip's `input_i / input_tp / input_lra / input_thresh / target_offset`, then a second **apply** pass feeds those measured values back as `measured_*` + `offset` + `linear=true`. This lands the output accurately on the preset target. The change is a **runner enhancement** layered over the existing pure builders — the pure `build_loudnorm_filter()` and `LOUDNESS_PRESETS` stay; we add a measure-filter builder, a measured-apply-filter builder, a JSON parser, a measure-cmd argv builder, and an async two-pass orchestrator that runs in `_render_longform_sse` (`backend/api/routers/audiobook.py:345`) between the chapter renders and the final mux. Loudness stays **opt-in** (`loudness: None` default on both `AudiobookRequest` `:151` and `LongformRenderRequest` `:510`), so default cross-platform behavior is unchanged.

> **Naming note (grounded):** "mastering" already exists in this codebase as `services.audio_dsp.apply_mastering()` (`backend/services/audio_dsp.py:101`) — a per-clip pedalboard highpass/Compressor chain used by `/generate`, `/dub`, batch, and stream paths. That is a **different** operation and **is not called** in the longform path (`_render_longform_sse` muxes chapter WAVs straight from `synthesize_chapter`, no `apply_mastering`). The two-pass loudnorm here is the *only* loudness operation in the longform renderer. To avoid conflating the two, the new SSE event is named `"mastering"` deliberately as the user-facing loudness step for longform; this is harmless because the longform stream never emits anything else by that name, but reviewers should know the term is overloaded across the repo.

## Problem

- `build_render_cmd(..., loudness="acx")` appends `-af loudnorm=I=-19.0:TP=-3.0:LRA=11.0` (`longform_render.py:274-276`, filter string built by `build_loudnorm_filter` at `:168`). This is single-pass. FFmpeg's single-pass `loudnorm` operates in dynamic mode without knowing the program's measured loudness ahead of time, so the integrated result drifts (often 1–4 LU off target) and the true-peak ceiling is approximate. ACX rejects files outside −23…−18 LUFS / −3 dBTP, so a "Normalize (ACX)" button (`frontend/src/pages/AudiobookTab.jsx:241`, i18n key `loudness_acx` — a **top-level flat key**, not nested under `audiobook.*`; see Localization in Constraints) that doesn't actually hit ACX is a correctness bug, not a cosmetic one.
- The docstrings already flag the gap as planned: `longform_render.py:160-161` ("single-pass; two-pass measure→apply is a runner enhancement") and the module header `longform_render.py:12-14`. The router-level docstring `backend/api/routers/audiobook.py:14` lists "ACX mastering" explicitly as a follow-up ("epub/pdf ingest, ACX mastering, crash-resume and the UI remain follow-ups.").
- The mux runs at `-loglevel error` (hardcoded in `build_render_cmd`, `longform_render.py:263`), which would suppress the measure pass's stats if we naively reused `build_render_cmd` — the two-pass measure command needs its own argv at `info` level. (Empirically, on system ffmpeg n8.1.1 the loudnorm JSON block prints to stderr regardless of `-loglevel error`, but we must not rely on `error`-only capture, and the measure cmd discards audio via `-f null -` so it can't reuse the mux argv anyway.)
- **Grounded fact about the current mux call (load-bearing for the fallback design):** the existing mux call site at `audiobook.py:448-455` is `await run_ffmpeg(build_render_cmd(...), job_id=job_id)` and **discards the return value entirely** — the call statement is a bare `await run_ffmpeg(...)` with no `rc, out, err =` binding. `run_ffmpeg` returns the 3-tuple `(returncode, stdout_bytes, stderr_bytes)` (`ffmpeg_utils.py:419`, full signature pinned in API/data shapes below), but the call site never binds or checks `rc`. So today a non-zero mux exit is silent: the `done` event still fires, and the client downloads whatever (possibly truncated/absent) file landed at `out_path`. The two-pass change must not regress this (it stays best-effort), but it also must not introduce a *new* hard failure: a measure-pass error must never become a render-blocking exception. See "Failure & edge-case matrix" below for the exact handling of every branch.

## Goal / Non-goals

**Goals**

1. When `loudness` is `acx` or `podcast` (the only two keys in `LOUDNESS_PRESETS`, `longform_render.py:153-156`), hit the preset's integrated LUFS and true-peak ceiling **accurately** via FFmpeg two-pass `loudnorm` (measure → apply).
2. Keep all changes additive and backward-compatible: existing pure builders (`build_loudnorm_filter`, `LOUDNESS_PRESETS`, `build_render_cmd`), tests (`tests/test_longform_render.py:25-48`), and the `loudness=None` default all behave exactly as before.
3. Run identically on macOS / Windows / Linux (constraint: default features cross-platform; loudness is opt-in so even stricter than required).
4. **Degrade gracefully on every failure path** — if the measure pass fails for *any* reason (non-zero rc, timeout, empty/garbage stderr, unparseable or non-finite JSON, abort, ffmpeg crash), fall back to the current single-pass behavior rather than failing the whole render. The orchestrator **never raises**; the only effect of a failure is `measured is None` → single-pass mux → `done.loudness.two_pass == false`. (Enumerated exhaustively in "Failure & edge-case matrix.")
5. Surface the achieved loudness back to the client in the `done` SSE event so the UI can show "mastered to −19.0 LUFS".

**Non-goals**

- No new loudness presets, no per-chapter loudness, no UI redesign (the existing `off/acx/podcast` dropdown in `AudiobookTab.jsx:238-242` stays).
- No loudness for the `/dub` pipeline (out of scope; this is the longform renderer only — note the `/dub` path uses the separate `audio_dsp.apply_mastering` per-clip chain, untouched here).
- No standalone "analyze loudness" endpoint.
- No change to the chapter cache key (`chapter_cache_key`, `longform_render.py:109-135`) — loudness is a post-concat master step applied to the final mux, not per-chapter, so it must not invalidate the chapter cache.
- No new Python dependencies (FFmpeg is already resolved via `find_ffmpeg`, `ffmpeg_utils.py:56`).
- **Not** in scope to start checking the *mux* return code (today it's discarded — see Problem). That's a separate latent bug; this task deliberately does not change mux error semantics, only adds the measure step in front of it.
- **No new regex over user-controlled input.** The measure-output parser uses a balanced-brace scan + `json.loads` + `float()`, *not* a regex (see Design builder #3 and the CodeQL note in Constraints). This is a deliberate design choice to keep CodeQL's `py/polynomial-redos` lens trivially satisfied.

## Design

### Where the master step lives

Loudness is a **whole-program** master applied to the **concatenated** audio, not per chapter. The cleanest insertion point is inside `_render_longform_sse` (`backend/api/routers/audiobook.py:345-475`), **between** the existing concat-list write (`audiobook.py:442-444`) and the final `build_render_cmd` mux (`audiobook.py:448-455`):

Current flow (`audiobook.py:438-465`):
```
assembling (:438)
  → write ffmeta (:439-441) + concat list (:442-444)
  → build out_path (:445-447)
  → run_ffmpeg(build_render_cmd(..., loudness=loudness)) (:448-455)
  → mark_done (:457-461) + done event (:463-465)
```

New flow when `loudness in {acx, podcast}`:
```
assembling (:438)
  → write ffmeta + concat list (:439-444)
  → [NEW] measure pass:  ffmpeg -f concat -i concat.txt -af loudnorm(...:print_format=json) -f null -
                          parse stderr → MeasuredLoudness (or None on ANY failure)
  → mux pass:            build_render_cmd(..., loudness=loudness, measured=<MeasuredLoudness or None>)
                          → run_ffmpeg (:448-455)  (measured=None ⇒ single-pass fallback)
  → done (:463-465)  (+ "loudness" block describing target & measured, two_pass flag)
```

When `loudness` is `None`/`off`/unknown, the measure pass is skipped entirely and the mux is byte-for-byte identical to today.

### Why measure on the concat, not on the final encoded file

We measure the **lossless concatenated source** (the chapter WAVs via the concat demuxer — the same `concat_path` written at `audiobook.py:442-444`) and then apply during the **single encode** to m4b/mp3. This is the standard two-pass pattern: measure the input, apply on the way to the output codec, one encode total. Measuring the AAC/MP3 output would require a third pass and re-encode; measuring the raw WAVs is exact and free of codec coloration. Because the measure cmd reuses the identical input args (`-f concat -safe 0 -i <concat_path>`) as `build_render_cmd` (`longform_render.py:264`), the measured signal == the muxed signal.

**Edge case — the measured set is a *subset* of the muxed set if a chapter fails between measure and mux.** It is not: the concat list (`concat_path`) is written once at `:442-444` from `chapter_files` and is **not** rewritten between the measure pass and the mux. Both passes read the same file. There is no chapter render in between (all chapter renders complete at `:414-432`, before `assembling`). So the measured signal and the muxed signal are guaranteed identical for a given job run. (If a future refactor ever interleaved a chapter render between measure and mux, the measured values would be stale — the apply pass would still be safe because `loudnorm` clamps, but the target accuracy would degrade; called out so that invariant is preserved.)

### Pure builders (new, unit-testable, in `longform_render.py`)

Add three pure functions + one dataclass + one argv builder alongside the existing loudness section (`longform_render.py:138-168`, which spans the `LoudnessPreset` dataclass `:140-147`, `LOUDNESS_PRESETS` `:153-156`, and `build_loudnorm_filter` `:159-168`). All pure: strings in, strings/argv out — no ffmpeg, no I/O — so they unit-test without a binary, matching the module's stated contract (`longform_render.py:22-24`).

**Exact function signatures to add** (Python type annotations — a developer implements against these verbatim; `Optional` and the dataclass are already imported style at `:33,35`):

```python
@dataclass(frozen=True)
class MeasuredLoudness:
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float

def build_loudnorm_measure_filter(preset: Optional[str]) -> Optional[str]: ...
def parse_loudnorm_measure(stderr_text: Optional[str]) -> Optional[MeasuredLoudness]: ...
def build_loudnorm_apply_filter(preset: Optional[str], measured: Optional["MeasuredLoudness"]) -> Optional[str]: ...
def build_loudnorm_measure_cmd(ffmpeg: str, concat_list_path: str, filt: str) -> list[str]: ...
```

1. **`build_loudnorm_measure_filter(preset: Optional[str]) -> Optional[str]`** — same target params as `build_loudnorm_filter` (re-uses the `LOUDNESS_PRESETS.get(preset.lower())` lookup at `:165`), plus `:print_format=json`. **Exact output string format** (`p` = `LOUDNESS_PRESETS[preset.lower()]`):
   ```
   loudnorm=I={p.i}:TP={p.tp}:LRA={p.lra}:print_format=json
   ```
   Golden values: `build_loudnorm_measure_filter("acx") == "loudnorm=I=-19.0:TP=-3.0:LRA=11.0:print_format=json"`; `build_loudnorm_measure_filter("podcast") == "loudnorm=I=-16.0:TP=-1.5:LRA=11.0:print_format=json"`. Returns `None` for off/unknown (so callers branch the same way as `build_loudnorm_filter`). Input edge cases it must handle: `None` → `None`; `""` → `None`; `"off"` / `"none"` → `None`; mixed case `"ACX"` / `"Acx"` → matched (lower-cased); leading/trailing whitespace such as `" acx "` — **decision: do NOT strip.** `build_loudnorm_filter` does not strip today (`:165` calls `.lower()` only), so `" acx "` already returns `None` in the existing single-pass path. The measure filter must mirror that exactly, so a value that the existing path treats as "unknown → no filter" also yields no measure pass. (If callers ever want to strip, they strip before calling, identically for both builders.)

2. **`MeasuredLoudness` dataclass (`@dataclass(frozen=True)`, matching the existing `LoudnessPreset` style at `:140`)** — fields mirror the FFmpeg JSON keys, **in this declaration order**: `input_i: float, input_tp: float, input_lra: float, input_thresh: float, target_offset: float`. All five `float`, all required (no defaults — every field must be supplied at construction), frozen so it can't be mutated after parse. Field-by-field contract: each maps 1:1 to the FFmpeg measure-JSON key of the same name (FFmpeg emits them as JSON *strings*; the parser coerces each to `float` and asserts `math.isfinite`).

3. **`parse_loudnorm_measure(stderr_text: Optional[str]) -> Optional[MeasuredLoudness]`** — FFmpeg writes the measure JSON as a pretty-printed object after a `[Parsed_loudnorm_0 @ 0x...]` line, and (verified on n8.1.1) **emits additional non-JSON lines after it** (`[out#0/null @ ...]`, `size=N/A ...`). **Parse strategy (no regex — CodeQL-safe by construction):** scan the string character-by-character tracking brace depth to locate the **last** balanced `{`…`}` block, `json.loads` it, then coerce the five required keys (`input_i`, `input_tp`, `input_lra`, `input_thresh`, `target_offset`) to float and validate each with `math.isfinite`. The scan is a single linear pass over the input (O(n), no backtracking), so it is **not reachable by `py/polynomial-redos`** — there is no regular expression evaluated against the (effectively user-influenced) ffmpeg-stderr bytes at all. **Returns `None` (caller falls back to single-pass) on every one of these failure inputs — each must be a parser unit test:**
   - `stderr_text is None` (defensive — orchestrator decodes bytes, but a `None` slip-through must not raise) → `None`.
   - empty string `""` → `None`.
   - whitespace-only → `None`.
   - no `{` or no `}` at all → `None`.
   - a `{` with no matching `}` (truncated / process killed mid-print) → `None`.
   - a syntactically malformed block (e.g. trailing comma, unquoted key) that `json.loads` rejects → `None` (catch `json.JSONDecodeError`).
   - a valid JSON object that is **missing** any one of the five required keys → `None` (don't fabricate a default).
   - a key present but **non-numeric** (`"input_i" : "n/a"`, `"input_i" : ""`) → `None` (`float()` raises `ValueError`/`TypeError`, caught → `None`).
   - a key present but **non-finite**: FFmpeg emits the literal `"-inf"` for a fully silent program and can emit `"inf"`/`"nan"`. `float("-inf")` *succeeds* in Python, so we must additionally reject with `math.isfinite(...)` false → `None`. (This is the silent-clip path; a silent program can't be normalized to a loudness target, so single-pass fallback — which also no-ops on silence — is the right behavior.)
   - JSON that is an **array or scalar** rather than an object (`json.loads` of `[1,2]` or `"x"`) → `None` (guard `isinstance(obj, dict)` before key access).
   - the block contains the keys nested under another object → only top-level keys are read; nested-only → treated as missing → `None`.
   - FFmpeg prints the *config dump* (an earlier `{...}` for filter graph debug) before the loudnorm block → the **last** balanced block wins; a dedicated unit test asserts the correct (later) block is chosen when two `{...}` blocks exist.
   - extra/unknown keys in the block (`output_i`, `output_tp`, `output_lra`, `output_thresh`, `normalization_type`, etc.) → ignored, parse still succeeds.
   - **Success return:** a frozen `MeasuredLoudness` with the five floats. On the verified fixture (below): `MeasuredLoudness(input_i=-21.75, input_tp=-18.06, input_lra=0.0, input_thresh=-31.75, target_offset=0.05)`.

4. **`build_loudnorm_apply_filter(preset: Optional[str], measured: Optional[MeasuredLoudness]) -> Optional[str]`** — builds the second-pass string. **Exact output string format** (`p` = `LOUDNESS_PRESETS[preset.lower()]`, `m` = the `measured` arg — note FFmpeg uses upper-case `measured_I`/`measured_TP`/`measured_LRA` but lower-case `measured_thresh`):
   ```
   loudnorm=I={p.i}:TP={p.tp}:LRA={p.lra}:measured_I={m.input_i}:measured_TP={m.input_tp}:measured_LRA={m.input_lra}:measured_thresh={m.input_thresh}:offset={m.target_offset}:linear=true:print_format=summary
   ```
   Field-mapping table (apply-filter param ⇐ source):

   | filter param | source | preset golden (`acx`) | measured golden (fixture) |
   |---|---|---|---|
   | `I` | `p.i` | `-19.0` | — |
   | `TP` | `p.tp` | `-3.0` | — |
   | `LRA` | `p.lra` | `11.0` | — |
   | `measured_I` | `m.input_i` | — | `-21.75` |
   | `measured_TP` | `m.input_tp` | — | `-18.06` |
   | `measured_LRA` | `m.input_lra` | — | `0.0` |
   | `measured_thresh` | `m.input_thresh` | — | `-31.75` |
   | `offset` | `m.target_offset` | — | `0.05` |
   | `linear` | constant | `true` | — |
   | `print_format` | constant | `summary` | — |

   `linear=true` requests linear (single-gain) normalization when the measured values allow it — accurate and transparent; FFmpeg auto-falls-back to dynamic internally if linear can't hit the target. Returns `None` for off/unknown preset (same lookup-miss behavior as the other builders) **and** must defensively return `None` if `measured is None` (so a caller that forgot to branch doesn't emit a filter string with `measured_I=None`). Float formatting note: use the dataclass float repr directly (Python's `str(-21.75)` → `"-21.75"`, `str(0.0)` → `"0.0"`); FFmpeg accepts both `0.00` and `0.0`. The golden fixture below uses the values as re-serialized by Python, not as FFmpeg printed them (FFmpeg printed `"0.00"`; Python stores `0.0`). Tests assert against the Python-serialized form.

5. **`build_loudnorm_measure_cmd(ffmpeg: str, concat_list_path: str, filt: str) -> list[str]`** (pure argv) — **exact argv, element-for-element, in this order**:
   ```python
   [ffmpeg, "-y", "-hide_banner", "-loglevel", "info",
    "-f", "concat", "-safe", "0", "-i", str(concat_list_path),
    "-af", filt, "-f", "null", "-"]
   ```
   That is exactly 16 elements: `argv[0]==ffmpeg`, then the `-loglevel info` pair, the `-f concat -safe 0 -i <path>` input segment (5 tokens), the `-af <filt>` pair, and the `-f null -` sink (3 tokens). Note `-loglevel info` (not the mux's hardcoded `error` at `:263`) and `-f null -` to discard audio output (measure only). The input segment (`-f concat -safe 0 -i …`) is copied verbatim from `build_render_cmd` (`longform_render.py:264`) so the measured signal == the muxed signal. Edge: this builder takes the *already-built* filter string `filt` (caller computed it via `build_loudnorm_measure_filter` and already verified it's not `None`); it does not re-derive from `preset`, so it has no preset-lookup branch and no `None` return — its contract is "given a non-empty filter, produce the argv." The orchestrator is responsible for not calling it with an empty `filt` (it won't, because it short-circuits on `build_loudnorm_measure_filter(...) is None`). The cross-platform `null` sink is the literal `-f null -` (works on every OS — it is FFmpeg's portable null muxer, **not** a shell device like `/dev/null` or `NUL`, so there is no platform-specific path string anywhere in the argv; see Constraints → cross-platform parity).

### Extend the existing render-cmd builder (not a new function)

6. Extend **`build_render_cmd`** (`longform_render.py:232-285`) with an optional `measured: Optional[MeasuredLoudness] = None` kwarg. **Exact new signature** (the new param is appended to the keyword-only block after `loudness` at `:241`):
   ```python
   def build_render_cmd(
       ffmpeg: str,
       concat_list_path: str,
       metadata_path: str,
       out_path: str,
       *,
       fmt: str = "m4b",
       bitrate: str = "128k",
       cover_path: Optional[str] = None,
       loudness: Optional[str] = None,
       measured: Optional[MeasuredLoudness] = None,
   ) -> list[str]: ...
   ```
   Branch the `-af` segment (`:274-276`) per this exact truth table so every combination is defined:

   | `loudness` resolves to | `measured` | `-af` emitted |
   |---|---|---|
   | known preset (`acx`/`podcast`) | a `MeasuredLoudness` | `build_loudnorm_apply_filter(loudness, measured)` (two-pass apply) |
   | known preset | `None` | `build_loudnorm_filter(loudness)` (single-pass fallback) |
   | off / `None` / `""` / unknown | a `MeasuredLoudness` | **no `-af`** — `loudness` not being a known preset wins; a stray `measured` is ignored (defensive; this shouldn't happen because the orchestrator only measures for known presets, but a future caller mistake must not inject a filter onto an off-render) |
   | off / `None` / `""` / unknown | `None` | **no `-af`** (today's behavior, byte-identical) |

   Implementation: replace the single line `filt = build_loudnorm_filter(loudness)` at `:274` with
   ```python
   filt = build_loudnorm_apply_filter(loudness, measured) if measured is not None else build_loudnorm_filter(loudness)
   ```
   Both branches return `None` for a non-preset `loudness`, so the existing `if filt:` guard at `:275` already gives the off-render the "no `-af`" result with no extra branch. This keeps one render-cmd builder and one `-af` insertion site, and the `measured=None` default makes every existing caller and test produce identical argv. (All other argv segments — input maps, cover, codec, faststart — are unchanged.)

### Orchestrator (new, impure)

7. **`async measure_loudness(...)`** — the impure orchestrator. Recommend a **new file** `backend/services/loudness.py` (keeps the router thin and lets the orchestrator be tested independently with a stubbed `run_ffmpeg`; there is no existing `backend/services/loudness.py`). **Exact signature:**
   ```python
   async def measure_loudness(
       ffmpeg: str,
       concat_list_path: str,
       preset: str,
       *,
       job_id: str,
   ) -> Optional[MeasuredLoudness]: ...
   ```
   Imports it needs: `from services.longform_render import build_loudnorm_measure_filter, build_loudnorm_measure_cmd, parse_loudnorm_measure, MeasuredLoudness`; `from services.ffmpeg_utils import run_ffmpeg`; `import logging`. Module logger: `logging.getLogger("omnivoice.loudness")` (the audiobook router's own logger is `logging.getLogger("omnivoice.audiobook")`, `audiobook.py:39`).

   Contract: **it never raises** — every internal error is caught, logged at WARNING via the module logger, and converted to a `None` return. All log lines are English-only (Localization constraint) and must not contain a user's HOME-path or any `*TOKEN*/*KEY*/*SECRET*` value — the only data logged is the ffmpeg rc and a short static message; the raw stderr (which is local-only ffmpeg diagnostic text and may contain the `concat_path` under `OUTPUTS_DIR`) is **not** logged verbatim (see Constraints → local-first). Exhaustive internal flow with every short-circuit:

   1. `filt = build_loudnorm_measure_filter(preset)`. If `None` (off/unknown/whitespace) → **return `None`** (no log; this is a normal skip, not an error). In practice the wiring only calls `measure_loudness` when `preset in LOUDNESS_PRESETS`, so this branch is a defensive guard, not the common path.
   2. Build argv via `build_loudnorm_measure_cmd(ffmpeg, concat_list_path, filt)`.
   3. `try:` call `rc, _out, err = await run_ffmpeg(cmd, capture=True, job_id=job_id)`. **`run_ffmpeg` exact signature (pinned, `ffmpeg_utils.py:378-379`):** `async def run_ffmpeg(cmd, timeout: float = 1800.0, capture: bool = True, job_id: "str | None" = None) -> tuple[int | None, bytes, bytes]` — returns `(returncode, stdout_bytes, stderr_bytes)`; `returncode` is `int | None` (`None` only if the proc didn't finish, which can't co-occur with a normal return). **Note the orchestrator does NOT pass `timeout=` → it inherits the default `1800.0`** (see step rationale).
      - **`run_ffmpeg` raises `asyncio.TimeoutError`** (hard timeout after 1800s; `ffmpeg_utils.py:408-418` — `run_ffmpeg` `raise`s after kill+reap) → caught, WARNING ("measure pass timed out"), **return `None`**. Critical: `asyncio.TimeoutError` is `TimeoutError`, a subclass of `Exception` (Py ≥3.11). The orchestrator's `except Exception` must catch it; otherwise it propagates to `_render_longform_sse`'s outer `except Exception` at `audiobook.py:466` and kills the *entire render* instead of falling back. **This is the single most important catch in the design** — a slow measure must degrade to single-pass, not abort the audiobook.
      - **`run_ffmpeg` raises anything else** (spawn failure, OSError, asyncio cancellation surfacing as an exception) → caught by `except Exception`, WARNING, **return `None`**. (Note: a genuine `asyncio.CancelledError` from the request being torn down is `BaseException`, not `Exception`, so it is *not* swallowed — cancellation should propagate so the SSE generator stops. Use `except Exception`, never bare `except:`.)
   4. `if rc != 0:` → WARNING ("measure pass exited rc=%s", rc), **return `None`**. (`rc` may be `None` per the signature; `None != 0` is `True`, so a `None` rc also falls back — correct.) (This covers an aborted measure: `/dub/abort`-style `kill_job_procs(job_id)` from `services.proc_registry:40` kills the registered measure proc → non-zero rc → `None`. But see the abort note below — when the job is aborted, the mux that follows is itself the bigger concern.)
   5. `if not err:` (empty stderr bytes) → WARNING ("measure pass produced no stderr"), **return `None`**. (Defensive: shouldn't happen at `-loglevel info`, but a redirected/locked stderr would yield empty.)
   6. Decode bytes: `text = err.decode("utf-8", "replace")` (never raises on bad bytes — `errors="replace"` handles non-UTF-8 ffmpeg output, which is the platform-independent decode path; see Constraints → cross-platform parity for why this matters on Windows where ffmpeg may emit cp-encoded bytes).
   7. `m = parse_loudnorm_measure(text)`. If `None` → WARNING ("measure pass output not parseable"), **return `None`**.
   8. Return `m` (a `MeasuredLoudness`).

   The measure pass decodes-only (no encode) so it's fast; **reuse the default `run_ffmpeg` timeout (`1800.0`s, `ffmpeg_utils.py:378`)** — do not shorten it: a genuinely multi-hour audiobook decode at ~133× realtime (n8.1.1 measurement) is well under 1800s even for ~60h of audio, and a separate short timeout would risk false-failing a huge legitimate book.

### Wiring into `_render_longform_sse`

**`_render_longform_sse` exact signature (unchanged by this task — pinned, `audiobook.py:345-356`):**
```python
async def _render_longform_sse(
    plan,
    *,
    default_voice: str | None,
    fmt: str = "m4b",
    bitrate: str = "128k",
    loudness: str | None = None,
    cover_path: str | None = None,
    metadata: dict | None = None,
    lexicon: dict | None = None,
    job_type: str = "audiobook",
): ...
```
No parameter is added; the measure step is internal. The `loudness` param already arrives from both `audiobook_synthesize` (`:484`, passes `req.loudness`) and `longform_render` (`:539`, passes `req.loudness`).

In `backend/api/routers/audiobook.py`, extend the import block from `services.longform_render` (`audiobook.py:32-37`, today imports `build_concat_list, build_ffmetadata, build_render_cmd, prune_cache_dir`) to also import `LOUDNESS_PRESETS`, and import `measure_loudness` from `services.loudness` (lazy/local import inside `_render_longform_sse` is fine and matches the module's pattern of local imports for `find_ffmpeg`/`run_ffmpeg` at `:366`).

After the concat list is written (`audiobook.py:442-444`) and before the mux (`audiobook.py:448`):

```python
measured = None
norm = (loudness or "").lower()
if norm in LOUDNESS_PRESETS:           # acx / podcast only; off/None/unknown skip
    yield _emit({"type": "mastering", "preset": norm})
    from services.loudness import measure_loudness  # lazy, matches :366 pattern
    measured = await measure_loudness(ffmpeg, concat_path, norm, job_id=job_id)
    # measured is None on ANY failure → build_render_cmd falls back to single-pass
```
(`_emit` is the local SSE helper defined at `audiobook.py:377-383` — it `json.dumps` the payload, best-effort-appends to `job_store`, and returns the `data: …\n\n` SSE frame; `concat_path` is the variable at `audiobook.py:442`; `ffmpeg` is resolved at `audiobook.py:388` and already guaranteed non-empty by the `:389-391` guard; `job_id` at `audiobook.py:369`.)

**Normalization consistency (grounded subtlety):** the wiring computes `norm = (loudness or "").lower()` and gates on `norm in LOUDNESS_PRESETS`. This is the **same** lookup the pure builders do (`.get(preset.lower())`). It does **not** strip whitespace — consistent with the "do not strip" decision in builder #1. So a request with `loudness="acx"` triggers two-pass; `loudness="ACX"` triggers two-pass (lower-cased); `loudness=" acx "` does **not** (mirrors single-pass, which also wouldn't apply a filter) → no measure pass, no `-af`, no `done.loudness` block. `loudness=None` → `norm == ""` → not in presets → skip. `loudness="off"` → `"off"` not in presets → skip. This is intentional: the gate and the builders agree on exactly which strings are "a preset."

Then pass `measured=measured` into the existing `build_render_cmd(...)` call (`audiobook.py:449-453`) — i.e. the call becomes:
```python
await run_ffmpeg(
    build_render_cmd(
        ffmpeg, concat_path, meta_path, out_path,
        fmt=ext, bitrate=bitrate, cover_path=_safe_cover_path(cover_path),
        loudness=loudness, measured=measured,
    ),
    job_id=job_id,
)
```
When `measured is None` (failure or skip), `build_render_cmd` falls back per the truth table above — single-pass for a known preset, no `-af` for off/None. (The mux call still discards `run_ffmpeg`'s return value, unchanged — see Problem.)

Augment the `done` event (`audiobook.py:463-465`) with a `loudness` block **only when a preset was requested** (`norm in LOUDNESS_PRESETS`):
```python
done = {"type": "done", "output": out_name,
        "chapters": len(chapter_files), "duration_s": round(total_s, 2),
        "cached_chapters": cached_n, "failed_chapters": failed}
if norm in LOUDNESS_PRESETS:
    p = LOUDNESS_PRESETS[norm]
    done["loudness"] = {
        "preset": norm,
        "target_i": p.i,
        "target_tp": p.tp,
        "two_pass": measured is not None,   # False ⇒ single-pass fallback was used
        "measured_i": measured.input_i if measured else None,
    }
yield _emit(done)
```
(`LoudnessPreset.i` / `.tp` are the fields at `longform_render.py:144-147`; `LoudnessPreset` also carries `.key` and `.lra`, not emitted.) When no preset was requested (`norm not in LOUDNESS_PRESETS`), **omit the `loudness` key entirely** from the `done` event — old clients and the off-path see exactly today's `done` shape: `{type, output, chapters, duration_s, cached_chapters, failed_chapters}`.

This event flows through both front doors unchanged — Audiobook (`/audiobook`, handler `audiobook_synthesize` at `:477-488`) and Stories (`/longform/render`, handler `longform_render` at `:516-543`) both call `_render_longform_sse`, so both get accurate mastering with one change.

### Failure & edge-case matrix (every "and then…")

This is the heart of the completeness contract. Each row is a state the feature must handle; the right column is the **observable outcome** (SSE events + final file). Every row maps to a named test in the Test plan (the "Test that covers it" column ties each row to its owning test, so coverage is auditable).

| State / input | Where caught | Outcome | Test that covers it |
|---|---|---|---|
| `loudness=None` (default) | wiring gate: `norm=="" not in LOUDNESS_PRESETS` | No measure pass, no `mastering` event, mux argv byte-identical to today, `done` has **no** `loudness` key. | `test_render_cmd_default_equivalence_no_measured` (argv); `test_off_path_emits_no_loudness_block` (integration) |
| `loudness="off"` | wiring gate | Same as above — no master applied, no `loudness` block. | `test_measure_loudness_skips_off_without_spawning`; `test_off_path_emits_no_loudness_block` |
| `loudness="podcast"` / `"acx"` (happy path) | — | `mastering` event → measure → parse → two-pass apply mux → `done.loudness.two_pass==true`, `measured_i` populated. | `test_acx_lands_in_window` / `test_podcast_lands_in_window` (integration); `test_measure_loudness_happy_parses_fixture` |
| `loudness="ACX"` (mixed case) | `.lower()` in gate + builders | Treated as `acx`; happy path. | `test_measure_filter_case_insensitive` |
| `loudness=" acx "` (whitespace) | not stripped (by design) | Not a known preset → skipped like `off`; no measure, no `loudness` block. Mirrors today's single-pass behavior exactly. | `test_measure_filter_whitespace_not_stripped`; `test_measure_loudness_skips_off_without_spawning` (param `" acx "`) |
| `loudness="bogus"` (unknown string) | wiring gate / builder lookup-miss | Skipped like `off`; no measure, no `loudness` block. | `test_measure_filter_off_or_unknown_is_none` (param `"bogus"`) |
| **No chapters at all** (`plan.chapters` empty) | existing guard `audiobook.py:385-387` | `error` event (`{"type":"error","error":"nothing to render (no chapters)"}`), `return` — never reaches the measure step. Unchanged. | covered by existing guard; `test_no_chapters_never_measures` (integration, asserts no `mastering`) |
| **All chapters failed** (`chapter_files` empty) | existing guard `audiobook.py:434-436` | `error` event (`{"type":"error","error":"all chapters failed to render"}`), `return` — never reaches `assembling`/measure. Unchanged. | `test_all_chapters_failed_never_measures` (integration, stub synth raises) |
| **Some chapters failed**, ≥1 succeeded | reaches `assembling` normally | Measure runs over the *surviving* chapters' concat (the same set the mux uses — concat written once at `:442`). Two-pass normalizes the partial program; `done.failed_chapters` still lists the failures. Correct: we master what we ship. | `test_partial_failure_masters_survivors` (integration) |
| **Single chapter** (`len(chapter_files)==1`) | normal path | Concat list has one `file '…'` line; measure + apply both operate on the one WAV. No special-casing needed (concat demuxer accepts a single entry). | `test_single_chapter_acx_in_window` (integration) |
| **ffmpeg not found** | existing guard `audiobook.py:389-391` | `error` event (`{"type":"error","error":"ffmpeg not available; the output needs it"}`) before any chapter work; measure step never reached. Unchanged. | existing behavior; not re-tested (guard predates this task) |
| **Measure pass: non-zero rc** | orchestrator step 4 | WARNING, `measure_loudness` returns `None` → single-pass fallback mux → `done.loudness.two_pass==false`, `measured_i==null`. Render still completes. | `test_measure_loudness_nonzero_rc_returns_none` |
| **Measure pass: rc is None** | orchestrator step 4 (`None != 0`) | Same as non-zero rc → fallback. | `test_measure_loudness_rc_none_returns_none` |
| **Measure pass: timeout (>1800s)** | orchestrator step 3 (catches `asyncio.TimeoutError`) | WARNING, returns `None`, single-pass fallback. Render completes. `run_ffmpeg` already killed+reaped the proc (`ffmpeg_utils.py:409-418`). | `test_measure_loudness_timeout_does_not_propagate` |
| **Measure pass: spawn/OSError** | orchestrator step 3 (`except Exception`) | WARNING, `None`, single-pass fallback. | `test_measure_loudness_oserror_returns_none` |
| **Measure stderr empty** | orchestrator step 5 | WARNING, `None`, single-pass fallback. | `test_measure_loudness_empty_stderr_returns_none` |
| **Measure stderr present but unparseable** (truncated/garbage/missing key) | `parse_loudnorm_measure` → `None`, orchestrator step 7 | WARNING, `None`, single-pass fallback. | `test_measure_loudness_unparseable_returns_none`; parser unit tests below |
| **Silent program** (FFmpeg emits `"input_i":"-inf"`) | `parse_loudnorm_measure` `math.isfinite` reject → `None` | WARNING, `None`, single-pass fallback (single-pass also no-ops on true silence; output is silence regardless, which is correct). | `test_parse_rejects_neg_inf_silent_clip` |
| **Non-UTF-8 bytes in stderr** (e.g. Windows cp-encoded ffmpeg output) | orchestrator step 6 (`decode(..., "replace")`) | Never raises; decoded with replacement chars; parser then finds the (ASCII) JSON block normally. Identical behavior on all three OSes — see Constraints → cross-platform parity. | `test_measure_loudness_non_utf8_stderr_still_parses` (stub returns cp-bytes + ASCII JSON) |
| **Two `{...}` blocks** (config dump + measure block) | parser picks the **last** balanced block | Correct block parsed; happy path. | `test_parse_picks_last_balanced_block` |
| **Job aborted mid-measure** (`/dub/abort`-style `kill_job_procs(job_id)`) | measure proc registered under `job_id` (`run_ffmpeg` `:398-400`) is killed → non-zero rc | `measure_loudness` returns `None`. **But abort intent is bigger than the measure**: the abort kills the in-flight ffmpeg; the orchestrator then returns `None` and the *next* line would start the mux. If the abort happened during measure, the SSE consumer (client) has typically disconnected, and the subsequent `await run_ffmpeg(mux...)` either runs to completion or is itself killed by a follow-up abort. **No new abort handling is added by this task** (the longform path has no abort endpoint wired today — `audiobook.py` has zero `register_proc`/`abort` references; the kill path is reachable only because `run_ffmpeg` registers under `job_id`). The measure pass simply participates in the same best-effort kill surface; it never leaks a process (registered → reaped). This is called out so a reviewer doesn't expect graceful abort-after-measure semantics that don't exist for longform yet. | covered by `test_measure_loudness_nonzero_rc_returns_none` (abort == non-zero rc); + `test_measure_loudness_forwards_job_id` (proves the proc is registered so the kill surface covers it) |
| **Mux pass non-zero rc** (pre-existing latent issue) | **not handled by this task** | Today's behavior preserved: rc discarded, `done` still emitted. Two-pass doesn't change this; the apply filter only changes the `-af` arg, not the rc-checking. | not tested (out of scope; flagged in Risk) |
| **`measured` populated but mux apply filter rejected by ffmpeg** (extremely unlikely — valid filter syntax) | mux rc would be non-zero (not checked, per above) | Same latent behavior as any mux failure. The apply filter is built from validated floats, so this is theoretical. | not tested (theoretical; filter built from validated floats) |
| **`linear=true` can't hit target** (measured LRA too wide) | FFmpeg internal | FFmpeg silently falls back to *dynamic* mode for that file — still more accurate than today's blind single-pass, still within ACX window. No app-level handling needed; documented in the apply-filter docstring. | covered implicitly by `test_acx_lands_in_window` (re-measure asserts the window regardless of internal mode) |
| **`done.loudness` consumed by an old frontend** | additive field | Old `if/else if` handlers (`AudiobookTab.jsx:159-164`, `StoriesEditor.jsx:389`) read only `evt.output`/`evt.cached_chapters`/`evt.failed_chapters`; an extra `loudness` key is ignored. No regression. | manual/visual (no JS test asserts this today); PR-3 TS types pin the shape |
| **`mastering` event consumed by an old frontend** | additive event type | Both SSE loops `if/else if` on `evt.type` and **silently drop unknown types** (verified: `AudiobookTab.jsx:151-167`, `StoriesEditor.jsx:386-390` have no `else`/default branch). No error, no progress glitch. | verified by code-read of the `if/else if` chains; no JS test |

### Cache interaction (must NOT regress resume)

`chapter_cache_key` (`longform_render.py:109-135`) deliberately excludes loudness — it hashes `sr` (`int(sample_rate)`), `engine` (`engine_id`), the per-span `[voice_id, text, int(pause_ms), speed]` lists, and `voices` (the sorted `voice_sig` map) only (`:126-131`), via `hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:20]` (`:135` — already CodeQL/bandit-clean because the digest is content-addressing, not security; the truncation to 20 hex chars is the on-disk filename stem). **Exact key signature (pinned, `:109-115`):** `chapter_cache_key(spans: Iterable[tuple], *, sample_rate: int, engine_id: str, voice_sig: Optional[dict] = None) -> str`. The cache key is consumed by `_render_chapter_cached` (`audiobook.py:260-301`, key computed at `:288`). **Do not add `loudness` (or `measured`) to the cache key or to `chapter_cache_key`'s signature** — otherwise changing the loudness preset would needlessly re-synthesize every chapter, *and* it would be a backward-incompatible change to the on-disk cache layout (old cached WAVs at `OUTPUTS_DIR/longform_cache/<key>.wav` would stop matching, forcing every existing user to re-render — a violation of the backward-compatible-project-data rule; see Constraints). The chapter WAVs are loudness-agnostic; only the final mux differs. A render with `acx` and a render with `off` reuse the exact same cached chapter WAVs (`OUTPUTS_DIR/longform_cache/<key>.wav`, `audiobook.py:289,399`).

The existing cache-key test suite (`test_longform_render.py:198-229`) — `test_cache_key_deterministic`, `test_cache_key_changes_on_any_input` (8-way parametrize at `:204-218`), `test_cache_key_voice_sig_order_irrelevant` — must remain **untouched and green**; their continued passing is the proof that the cache key signature didn't change. This task adds **no** new parametrize case to that test (loudness is not a key input). The cross-preset-reuse invariant is asserted at the integration layer instead (`test_off_path_reuses_acx_cache`, below).

Edge to verify in tests: a **corrupt cached chapter WAV** is handled upstream of the master step — `_render_chapter_cached` (`audiobook.py:291-297`) catches a `wave.open` failure on a cache hit and re-renders, so the WAV the measure pass eventually reads is always a freshly-validated one. The master step never sees a half-written cache entry (`atomic_save_wav` at `:300` is atomic).

## Integration points (file:line)

- `backend/services/longform_render.py:138-168` — loudness section. Add `MeasuredLoudness` (frozen dataclass, 5 float fields), `build_loudnorm_measure_filter`, `parse_loudnorm_measure`, `build_loudnorm_apply_filter`, `build_loudnorm_measure_cmd` (signatures pinned in Design). Keep `LoudnessPreset` (`:140-147`), `LOUDNESS_PRESETS` (`:153-156`), and `build_loudnorm_filter` (`:159-168`) unchanged. Add `import math` to the module header (`:29-35`) for the `isfinite` non-finite guard (only `hashlib`, `json`, `os`, `re` imported today; `dataclass`, `Path`, `Iterable`, `Optional` already imported at `:33-35`). Note: do **not** add any new `re.compile`/`re.match` for parsing the measure output — the only existing regex in this module is the anchored, bounded `_BITRATE_RE = re.compile(r"^\d{2,3}k$")` at `:37`, which this task does not touch (see CodeQL note in Constraints).
- `backend/services/longform_render.py:232-285` — `build_render_cmd`. Add `measured: Optional[MeasuredLoudness] = None` kwarg to the keyword-only block (after `loudness` at `:241`); replace the `filt = build_loudnorm_filter(loudness)` line at `:274` with `filt = build_loudnorm_apply_filter(loudness, measured) if measured is not None else build_loudnorm_filter(loudness)`, keeping the existing `if filt:` guard at `:275`.
- `backend/services/loudness.py` — **new file** — `async measure_loudness(ffmpeg, concat_list_path, preset, *, job_id) -> Optional[MeasuredLoudness]` orchestrator (never-raises contract, exhaustive `None` returns per the step list above). (No such file exists today.)
- `backend/api/routers/audiobook.py:32-37` — extend the `services.longform_render` import block (today: `build_concat_list, build_ffmetadata, build_render_cmd, prune_cache_dir`) to add `LOUDNESS_PRESETS`.
- `backend/api/routers/audiobook.py:345-475` — `_render_longform_sse` (signature unchanged). Import `measure_loudness` (lazy, alongside the `:366` ffmpeg imports); insert the measure step after the concat write (`:444`), gate on `norm in LOUDNESS_PRESETS`, pass `measured=measured` into the mux at `:449-453`, emit a `mastering` event and conditionally extend the `done` event at `:463-465`.
- `frontend/src/api/audiobook.ts:65,105` — `loudness?: 'off' | 'acx' | 'podcast' | null` type already correct on both `AudiobookGenerateBody` (`:60-69`, field at `:65`) and `LongformRenderBody` (`:100-108`, field at `:105`); no change needed for slices 1–2. **Grounded shape note (optional cleanup, not required):** `LongformRenderBody` (`:100-108`) is **missing the `lexicon` field** that the backend `LongformRenderRequest` accepts (`audiobook.py:513`); `AudiobookGenerateBody` has `lexicon` (`:68`) but `LongformRenderBody` does not. This is a pre-existing TS/Pydantic asymmetry unrelated to loudness — do not "fix" it as part of this task unless surfacing `done.loudness` types (PR 3) makes it convenient. If PR 3 surfaces the new `done.loudness` block, add a TS type for it (shape pinned in API/data shapes below) to the SSE-event union, not to the request bodies.
- `frontend/src/pages/AudiobookTab.jsx:35,134,237-242` — the loudness state (`:35`), request wiring (`:134`, sends `loudness === 'off' ? null : loudness` to `/audiobook`), and the dropdown (`:237-242`). **No required change** for slices 1–2 — Audiobook already flows `acx`/`podcast` through. The SSE `done` handler is at `:159-164` (reads `evt.output`, `evt.cached_chapters`, `evt.failed_chapters`); the `if/else if` chain (`:151-167`) has no default branch, so it already drops the new `mastering` event silently. The dropdown labels are already i18n'd via the flat keys `loudness`, `loudness_off`, `loudness_acx`, `loudness_podcast` (`en.json:130-133`) — any PR-3 UI copy must reuse/extend these through `t(...)`, never hardcode (Localization constraint).
- `frontend/src/components/StoriesEditor.jsx:360-402` — `generateAll` compiles `storyToSpans(usable, cast)` and calls `longformRender({ chapters, format })` (`:368-371`). **CORRECTION (grounded):** Stories currently passes **only** `chapters` + `format` (`:369-370`) — it does **not** send any `loudness` value, and there is no loudness control in the Stories UI (`ExportModal.jsx` has zero `loudness` references). So the original claim that "the value `acx`/`podcast` already flows through" both doors is true for Audiobook but **false for Stories**. Two-pass mastering will work for Stories *only if* a loudness value is added to the `longformRender` call (and, for user control, a dropdown). The shared-renderer change still benefits Stories the moment a `loudness` arg is supplied; surfacing a control is PR slice 3 scope. The Stories SSE loop (`:386-390`) reads only `evt.chapters`/`evt.index`/`evt.total`/`evt.output`/`evt.error` and likewise has no default branch → drops `mastering`/the new `loudness` block harmlessly.
- `frontend/src/components/ExportModal.jsx` — 20.9 KB component; **no loudness references today**. If a Stories loudness control is added it would live here or in `StoriesEditor`. Not required for slices 1–2.
- `tests/test_longform_render.py:23-48` — loudness unit tests live here; extend in the same file. The import block at `:11-20` (today: `LOUDNESS_PRESETS, build_concat_list, build_ffmetadata, build_loudnorm_filter, build_render_cmd, chapter_cache_key, prune_cache_dir, validate_cover_image`) must add the new symbols (`MeasuredLoudness`, `build_loudnorm_measure_filter`, `parse_loudnorm_measure`, `build_loudnorm_apply_filter`, `build_loudnorm_measure_cmd`).
- `tests/test_loudness.py` — **new file** — mocked-`run_ffmpeg` orchestrator tests (no ffmpeg, no torch). (No such file exists today.)
- `tests/test_loudness_integration.py` — **new file** — real-ffmpeg, skip-if-missing render tests via `TestClient` + monkeypatched `_prepare_synth`. (No such file exists today. Deliberately *not* `tests/test_longform_jobs.py` — see the Integration subsection's CORRECTION.)

## API / data shapes

**No request-shape change.** `loudness: 'off' | 'acx' | 'podcast' | null` stays as-is on both `AudiobookRequest` (`audiobook.py:151`, Pydantic `loudness: str | None = None`) and `LongformRenderRequest` (`audiobook.py:510`, Pydantic `loudness: str | None = None`), and on the TS `AudiobookGenerateBody` (`audiobook.ts:65`) / `LongformRenderBody` (`audiobook.ts:105`). The backend accepts any string for `loudness`; only `"acx"`/`"podcast"` (case-insensitively, no whitespace strip) trigger a filter — every other value is treated as "off." Because there is no request-shape change and the new `done` field is additive-optional, there is **no API/data contract migration** — old and new clients interoperate (see Constraints → backward-compatible data).

**Full request bodies (pinned, for reference — unchanged by this task):**
```jsonc
// POST /audiobook  → AudiobookRequest (audiobook.py:146-157)
{
  "text": "…",                       // required
  "default_voice": null,             // str | null
  "bitrate": "128k",                 // str (validated against /^\d{2,3}k$/, else 128k)
  "format": "m4b",                   // "m4b" | "mp3"
  "loudness": null,                  // null | "off" | "acx" | "podcast"  (opt-in)
  "cover_path": null,                // str | null (server-side path)
  "metadata": null,                  // {title,author,album,narrator,year,genre,description} | null
  "lexicon": null                    // {word: respelling} | null
}

// POST /longform/render  → LongformRenderRequest (audiobook.py:505-513)
{
  "chapters": [                      // list, max _MAX_CHAPTERS (422 if exceeded)
    { "title": "Chapter 1",
      "spans": [ { "voice_id": null, "text": "…", "pause_ms_after": 0, "speed": null } ] }
  ],
  "default_voice": null,
  "bitrate": "128k",
  "format": "m4b",
  "loudness": null,                  // SAME field; Stories UI doesn't send it yet (PR 3)
  "cover_path": null,
  "metadata": null,
  "lexicon": null
}
```
Both endpoints return `StreamingResponse(media_type="text/event-stream")` — an SSE stream, **not** JSON. (`media_type="text/event-stream"`, `audiobook.py:487,542`.)

**SSE wire format (pinned, `audiobook.py:383`):** every event is one frame `data: <json>\n\n` where `<json>` is `json.dumps(payload)`. The frontend splits on `\n\n` (`splitSSEBuffer`) and `JSON.parse`s the `data:` payload (`parseSSELine`). All events carry a `"type"` discriminator string.

**Complete SSE event catalog on this stream** (pinned from `_render_longform_sse`; the two events with ★ are added/extended by this task — everything else is unchanged):

```jsonc
// audiobook.py:412 — first event
{ "type": "started", "job_id": "<16-hex>", "chapters": 12 }

// audiobook.py:430-432 — per successful chapter
{ "type": "chapter", "index": 0, "total": 12, "title": "Chapter 1",
  "duration_s": 31.42, "cached": false }

// audiobook.py:424-425 — per failed chapter (render continues)
{ "type": "chapter_error", "index": 3, "total": 12, "title": "Chapter 4",
  "error": "chapter failed to render" }

// audiobook.py:438 — before the mux
{ "type": "assembling" }

// ★ NEW — emitted ONLY when loudness ∈ {acx, podcast}, just before the measure pass
{ "type": "mastering", "preset": "acx" }   // preset ∈ {"acx","podcast"} — machine id, not display text

// audiobook.py:463-465 — terminal success; loudness block is ★ NEW + OPTIONAL
{
  "type": "done",
  "output": "audiobook_<job_id>.m4b",   // or story_<job_id>.{m4b,mp3}; basename, fetched via audioUrl()
  "chapters": 12,                        // count of SUCCESSFUL chapters (len(chapter_files))
  "duration_s": 4210.5,
  "cached_chapters": 3,
  "failed_chapters": [3, 7],             // indices of failed chapters (may be [])
  "loudness": {                          // ★ OPTIONAL — present ONLY for acx/podcast; ABSENT for off/None/unknown
    "preset": "acx",                     // "acx" | "podcast"  (== the gated norm)
    "target_i": -19.0,                   // LOUDNESS_PRESETS[norm].i  (number)
    "target_tp": -3.0,                   // LOUDNESS_PRESETS[norm].tp (number)
    "two_pass": true,                    // bool — false ⇒ measure failed/aborted, single-pass fallback
    "measured_i": -21.75                 // number when two_pass==true; null when two_pass==false
  }
}

// audiobook.py:386,390,435,474 — terminal error (mutually exclusive with done)
{ "type": "error", "error": "<message>" }
//   "nothing to render (no chapters)" | "ffmpeg not available; the output needs it"
//   | "all chapters failed to render" | "render failed (see backend log)"
```

> **Localization note:** the `preset` value (`"acx"`/`"podcast"`) is a stable machine identifier, **not** display text — the frontend must render it through an i18n key (reuse `loudness_acx`/`loudness_podcast` at `en.json:132-133`), never echo the raw string into the UI. No user-facing English (or any language) string is emitted by the backend in these events. The numeric `target_i`/`target_tp`/`measured_i` are likewise not localized strings. See Constraints → Localization.

**`done.loudness` field-presence contract (so clients can be defensive):**
- `loudness` key **absent** ⇔ no preset requested (`off`/`None`/unknown/whitespace). Client shows nothing.
- `loudness` key **present** ⇔ `acx`/`podcast` requested. Always has `preset` (string), `target_i` (number), `target_tp` (number), `two_pass` (bool).
- `measured_i` is `number` when `two_pass==true`, `null` when `two_pass==false`. A client must not assume `measured_i` is non-null whenever `loudness` is present.
- `mastering` is emitted **iff** a `loudness` block will be present in `done` (same `norm in LOUDNESS_PRESETS` gate) — but it is *not* a guarantee `done.two_pass` will be true (measure can still fail after `mastering` is emitted).

**Suggested TS additions (PR 3 only — not required for slices 1–2):**
```ts
// add to the SSE-event union in frontend/src/api/audiobook.ts (or wherever the events are typed)
interface DoneLoudness {
  preset: 'acx' | 'podcast';
  target_i: number;
  target_tp: number;
  two_pass: boolean;
  measured_i: number | null;   // null when two_pass === false
}
interface MasteringEvent { type: 'mastering'; preset: 'acx' | 'podcast'; }
// extend the existing `done` event type with `loudness?: DoneLoudness`  (OPTIONAL)
```

**`MeasuredLoudness` (internal, `backend/services/longform_render.py`):** `@dataclass(frozen=True)` with fields **in order** `input_i: float, input_tp: float, input_lra: float, input_thresh: float, target_offset: float` — all five required, all finite, frozen. No `to_dict`/`from_dict` needed; only `.input_i` is read by the wiring (for `done.measured_i`).

**FFmpeg measure-JSON keys consumed** (measure pass — FFmpeg emits all values as JSON **strings**, the parser coerces each to `float` and asserts `math.isfinite`, all five required):

| key | type in FFmpeg JSON | → `MeasuredLoudness` field | example (fixture) |
|---|---|---|---|
| `input_i` | string (LUFS) | `input_i` | `"-21.75"` → `-21.75` |
| `input_tp` | string (dBTP) | `input_tp` | `"-18.06"` → `-18.06` |
| `input_lra` | string (LU) | `input_lra` | `"0.00"` → `0.0` |
| `input_thresh` | string (LUFS) | `input_thresh` | `"-31.75"` → `-31.75` |
| `target_offset` | string (LU) | `target_offset` | `"0.05"` → `0.05` |

FFmpeg also emits `output_i`, `output_tp`, `output_lra`, `output_thresh`, and `normalization_type` in the same object — these are **ignored** (not read for the apply pass, not stored on `MeasuredLoudness`).

**Apply-pass filter string (golden, for `acx`, derived from the verified fixture below; note FFmpeg's `"0.00"` becomes Python `0.0`):**
```
loudnorm=I=-19.0:TP=-3.0:LRA=11.0:measured_I=-21.75:measured_TP=-18.06:measured_LRA=0.0:measured_thresh=-31.75:offset=0.05:linear=true:print_format=summary
```

**Measure-pass argv (golden, for `acx`, `ffmpeg="ffmpeg"`, `concat="/x/concat.txt"`):**
```
["ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
 "-f", "concat", "-safe", "0", "-i", "/x/concat.txt",
 "-af", "loudnorm=I=-19.0:TP=-3.0:LRA=11.0:print_format=json",
 "-f", "null", "-"]
```

**No DB schema, no migration.** This feature touches **no** SQLite/alembic schema, no `omnivoice_data/` table, no `job_store` column. The only persisted state is the SSE event list appended to `job_store` via `_emit` → `job_store.append_event(job_id, json.dumps(payload))` (`audiobook.py:380`), which stores the event JSON as opaque text — the new `mastering` event and the extended `done.loudness` block flow into that store with **no schema change** (it's a text/JSON event log, not typed columns). No alembic revision is created. No on-disk cache-key/layout change (see Cache interaction). So there is no migration to write or test.

## Test plan

### Strategy: pure-first, handler-direct, no `main`/torch/GPU import locally

The whole feature is structured so that **the parts most worth testing never touch torch, a model, a GPU, or `main`** — matching the codebase's established discipline (the "Local pytest segfault (torch/Triton)" memory; the docstring contracts at `longform_render.py:22-24` "pure … unit tested without ffmpeg, torch, or a GPU"; and the precedent files `test_longform_limits.py` ("Direct handler calls (no main/torch import)") and `test_longform_jobs.py` ("we call the pure builder … no `main`/torch import")). Three layers, each with a hard rule:

1. **Pure layer (no ffmpeg, no torch)** — the five new builders + parser + the extended `build_render_cmd`. These are string/argv in → string/argv out. They import only `services.longform_render` (which imports only stdlib: `hashlib/json/os/re/math/dataclasses/pathlib/typing`). **Asserts:** exact golden strings/argv (the spec pins them all), the off/unknown/whitespace branches, the truth-table for `build_render_cmd`, and the exhaustive adversarial parser failure inputs → `None`. Lives in `tests/test_longform_render.py` (extend the existing loudness section).

2. **Orchestrator layer (mocked `run_ffmpeg`, no ffmpeg, no torch)** — `services.loudness.measure_loudness`. The **key technique**: `monkeypatch.setattr("services.loudness.run_ffmpeg", fake)` — patch the name *as imported into `services.loudness`*, so the real `ffmpeg_utils.run_ffmpeg` (and therefore any subprocess spawn) is never reached. The fake is an `async def` returning a 3-tuple (or `raise`-ing) per the branch under test. `measure_loudness` is itself an `async def`; drive it with `asyncio.run(...)` (the same pattern `test_longform_limits.py:28,39` uses for `audiobook_import`/`longform_render`). **Asserts:** the never-raises contract on every failure branch, that `run_ffmpeg` is *not* spawned for off/unknown presets, the exact argv handed to `run_ffmpeg`, `job_id`+`capture` forwarding, timeout-default inheritance, exception-class handling (`TimeoutError` swallowed, `CancelledError` propagated), and the no-leak logging assertion. `services.loudness` imports only `services.longform_render`, `services.ffmpeg_utils`, and `logging` — **no torch** (note: `services.ffmpeg_utils` itself imports `asyncio/os/subprocess/shutil` + `services.proc_registry`, none of which pull torch — confirmed by reading the module; importing it is segfault-safe).

3. **Integration layer (real ffmpeg, skip-if-missing, stub synth — no model/GPU/`main`)** — drives the *actual* `_render_longform_sse` end-to-end through `TestClient` on `/longform/render`, but **monkeypatches `_prepare_synth`** (`audiobook.py:239`) to return a deterministic stub synth (sine/zeros tensors) instead of loading a real engine. This is the crucial trick that keeps the integration test **out of the model/GPU path** while still exercising the real ffmpeg measure→apply→mux. `_render_longform_sse` calls `synth, sr, resolve, engine_id = await _prepare_synth(default_voice)` at `:405`; replacing `_prepare_synth` swaps the entire engine load for a stub. The stub synth still produces `torch.Tensor`s (so `import torch` is needed *in the test*, guarded by `pytest.importorskip("torch")` exactly as `test_audiobook.py:123` does), but **no model weights load and no GPU is touched** — `torch.ones(...)`/a CPU sine is enough. ffmpeg is gated by `if not find_ffmpeg(): pytest.skip(...)` (the repo idiom at `test_stories_encode.py:46-47`).

Rationale for the split: layers 1+2 are the *correctness contract* (every branch, every golden value, every failure path) and run torch-free/segfault-free in the standard local loop. Layer 3 is the *empirical proof* (the file actually lands in the ACX window) and is the only layer needing real ffmpeg + a CPU torch tensor — it's the smallest possible surface for that proof.

> **Why a stub synth, not real synthesis:** the ACX-window assertion only needs *some* non-silent audio of known content; the actual TTS engine is irrelevant to whether `loudnorm` two-pass hits −19 LUFS. Loading a real engine would (a) require a model download / GPU and (b) risk the torch/Triton segfault locally. The stub (a short CPU sine or `torch.ones`) gives reproducible, non-silent audio that ffmpeg can measure — that's all the loudness math needs.

### Unit (pure, no ffmpeg, no torch) — `tests/test_longform_render.py`

Extend the import block at `:11-20` to add `MeasuredLoudness, build_loudnorm_measure_filter, parse_loudnorm_measure, build_loudnorm_apply_filter, build_loudnorm_measure_cmd`. Add `import math` to the test module (for `isfinite` assertions). Concrete tests:

- **`test_measure_filter_golden`** — `build_loudnorm_measure_filter("acx") == "loudnorm=I=-19.0:TP=-3.0:LRA=11.0:print_format=json"`; `build_loudnorm_measure_filter("podcast") == "loudnorm=I=-16.0:TP=-1.5:LRA=11.0:print_format=json"` (exact equality, mirrors the existing `test_loudnorm_acx_filter` at `:25-27`).
- **`test_measure_filter_off_or_unknown_is_none`** — parametrize `[None, "", "off", "none", "bogus"]` → `None` (mirror the existing `:38-40` parametrize).
- **`test_measure_filter_case_insensitive`** — `build_loudnorm_measure_filter("ACX") == build_loudnorm_measure_filter("acx")` and `== build_loudnorm_measure_filter("Acx")` (mirror `:34-35`).
- **`test_measure_filter_whitespace_not_stripped`** — `build_loudnorm_measure_filter(" acx ") is None` (documents the "do not strip" decision; the same value would also yield `None` from `build_loudnorm_filter` today).
- **`test_parse_happy_fixture`** — `parse_loudnorm_measure(<verified n8.1.1 stderr fixture>) == MeasuredLoudness(-21.75, -18.06, 0.0, -31.75, 0.05)`; additionally assert each field individually by name *and* `math.isfinite` on all five (so a future field-reorder regression is caught).
- **`test_parse_rejects_each_bad_input`** — one parametrize covering, each → `None`: `None`, `""`, `"   \n\t"`, `"no braces here"`, `'{ "input_i": "-21.75"'` (unbalanced/truncated), `'{ "input_i": "-21.75", }'` (trailing comma), `'{ input_i: "-21.75" }'` (unquoted key), a full-but-missing-`target_offset` block, `'{ "input_i": "n/a", … }'`, `'{ "input_i": "", … }'`, `'[1,2,3]'` (array), `'"scalar"'` and `'5'` (scalars), and a block whose five keys are nested under `{ "sub": { … } }` only.
- **`test_parse_rejects_neg_inf_silent_clip`** — a complete block with `"input_i": "-inf"` (other four finite) → `None`; also `"inf"` and `"nan"` variants → `None`. This is the silent-program path; pin it explicitly because `float("-inf")` *succeeds* and only `math.isfinite` rejects it.
- **`test_parse_picks_last_balanced_block`** — stderr containing an earlier config-dump `{...}` (different values) followed by the real measure block → returns the **later** block's values (assert `input_i` equals the later block's, not the earlier).
- **`test_parse_ignores_extra_keys`** — a block carrying `output_i/output_tp/output_lra/output_thresh/normalization_type` plus the five required → parses successfully, extra keys ignored.
- **`test_parse_skips_trailing_noise`** — the *exact* n8.1.1 fixture (which has the trailing `[out#0/null …]` + `size=N/A …` lines after the JSON) → parses correctly, proving the trailing-line skip.
- **`test_parse_is_linear_no_redos`** (★ CodeQL-safety pin) — feed a backtracking-bait input (`"{" * 200_000` or `"{ " + " " * 200_000`) and assert it returns `None` **promptly** (wrap in a generous wall-clock budget, e.g. assert it completes within a few hundred ms; the linear scan is O(n)). Documents that the parser is a balanced-brace scan, not a regex, and guards against a future "just regex it" regression. (See Constraints → CodeQL.)
- **`test_apply_filter_golden_acx`** — `build_loudnorm_apply_filter("acx", <fixture m>)` equals the golden apply string above (exact equality, including `linear=true:print_format=summary` and the Python-serialized `measured_LRA=0.0`).
- **`test_apply_filter_golden_podcast`** — same with `I=-16.0:TP=-1.5` prefix.
- **`test_apply_filter_none_when_measured_none`** — `build_loudnorm_apply_filter("acx", None) is None` (defensive guard).
- **`test_apply_filter_none_for_unknown_preset`** — parametrize `[None, "", "off", "bogus"]` with a real `MeasuredLoudness` → `None` (lookup-miss wins over a present `measured`).
- **`test_measure_cmd_golden_argv`** — `build_loudnorm_measure_cmd("ffmpeg", "/x/concat.txt", "<acx measure filt>")` equals the 16-element golden argv above by **list equality**; plus targeted asserts: `argv[0]=="ffmpeg"`, `argv[3:5]==["-loglevel","info"]`, `argv[-3:]==["-f","null","-"]`, the `-af` token is immediately followed by the verbatim `filt`, and `"/dev/null" not in argv and "NUL" not in argv` (portable null sink, identical on every OS — Constraints → cross-platform parity).
- **`build_render_cmd` truth-table** (extends the existing `test_render_cmd_loudnorm_adds_af` at `:159-162`):
  - **`test_render_cmd_apply_when_measured`** — `loudness="acx", measured=<m>` → the `-af` arg contains `measured_I=` **and** `linear=true` (the two-pass apply filter).
  - **`test_render_cmd_singlepass_when_no_measured`** — `loudness="acx", measured=None` → `-af` present, contains `loudnorm=` but **not** `measured_I` (single-pass fallback).
  - **`test_render_cmd_ignores_stray_measured_on_off`** — `loudness=None, measured=<m>` → **no** `-af` at all; `loudness="off", measured=<m>` → **no** `-af`.
  - **`test_render_cmd_default_equivalence_no_measured`** (★ backward-compat pin) — `build_render_cmd("ffmpeg","c","m","o", loudness=None)` (no `measured=` kwarg) produces a list **byte-equal** to a frozen golden argv captured from the pre-change builder (i.e. the new `measured=None` default changes nothing for existing callers). Assert full list equality, not a substring.

### Orchestrator (mocked `run_ffmpeg`, no ffmpeg, no torch) — `tests/test_loudness.py` (new)

Module header mirrors `test_longform_limits.py`: docstring "Direct async-function calls (no main/torch/ffmpeg import). `run_ffmpeg` is monkeypatched." Patch target in **every** test: `monkeypatch.setattr("services.loudness.run_ffmpeg", fake)` (patch the name *bound in `services.loudness`*). Drive with `asyncio.run(measure_loudness(...))`. Concrete tests:

- **`test_measure_loudness_happy_parses_fixture`** — `fake` is `async def` returning `(0, b"", <fixture stderr bytes>)` → `measure_loudness("ffmpeg", "/x/concat.txt", "acx", job_id="j")` returns `MeasuredLoudness(-21.75, -18.06, 0.0, -31.75, 0.05)`. (Note the stub returns the **3-tuple** `(returncode, stdout_bytes, stderr_bytes)` — `ffmpeg_utils.py:419`; a 2-tuple stub would be wrong.)
- **`test_measure_loudness_nonzero_rc_returns_none`** — `fake` returns `(1, b"", b"...")` → returns `None`, no raise; assert a WARNING was logged (use `caplog.at_level(logging.WARNING, logger="omnivoice.loudness")`).
- **`test_measure_loudness_rc_none_returns_none`** — `fake` returns `(None, b"", b"...")` → `None` (`None != 0` falls back).
- **`test_measure_loudness_empty_stderr_returns_none`** — `fake` returns `(0, b"", b"")` → `None`, WARNING.
- **`test_measure_loudness_unparseable_returns_none`** — `fake` returns `(0, b"", b"garbage no json")` → `None`, WARNING.
- **`test_measure_loudness_timeout_does_not_propagate`** (★ the most important orchestrator test) — `fake` does `raise asyncio.TimeoutError()` → `measure_loudness` returns `None` (NOT a propagated exception). Assert no exception escapes `asyncio.run(...)` and a WARNING is logged. This pins the "slow measure degrades to single-pass" contract; a regression where the `except` misses `TimeoutError` would surface here.
- **`test_measure_loudness_oserror_returns_none`** — `fake` does `raise OSError("spawn failed")` → `None`, WARNING.
- **`test_measure_loudness_cancellation_propagates`** (★) — `fake` does `raise asyncio.CancelledError()` → `measure_loudness` **re-raises** `asyncio.CancelledError` (assert with `pytest.raises(asyncio.CancelledError)`). Proves the `except Exception` (not bare `except:`) lets request-teardown cancel the generator. Pairs with the timeout test to pin the exact exception-class boundary.
- **`test_measure_loudness_skips_off_without_spawning`** — parametrize preset `["off", "bogus", " acx ", ""]`; `fake` records call count; assert `measure_loudness(...) is None` **and** `fake` was never invoked (the `build_loudnorm_measure_filter is None` short-circuit fires before any spawn). This is the gate-vs-spawn proof.
- **`test_measure_loudness_correct_argv`** — capture the first positional arg `fake` receives; assert it equals `build_loudnorm_measure_cmd("ffmpeg", "/x/concat.txt", "loudnorm=I=-19.0:TP=-3.0:LRA=11.0:print_format=json")` (the orchestrator wires the right command for `acx`).
- **`test_measure_loudness_forwards_job_id_and_capture`** — capture `fake`'s kwargs; assert `job_id == "j"` and `capture is True` are forwarded (so the proc tracker / timeout reaping covers the measure proc), **and** that `timeout` is *not* overridden — either no `timeout=` kwarg passed, or it equals `1800.0` (inherits the `run_ffmpeg` default; pins "don't shorten the timeout").
- **`test_measure_loudness_no_leak_in_logs`** (★ local-first pin) — run the failure path (`fake` returns `(1, b"", <fixture bytes containing the concat path>)`), capture WARNING records via `caplog`, and assert **none** of the emitted log messages contains: the raw `concat_list_path` (`/x/concat.txt`), any `TOKEN`/`KEY`/`SECRET` substring (case-insensitive), or a `/home/`/`/Users/` HOME path. Only the static message + the integer rc may appear. Pins the no-leak orchestrator contract from Constraints → local-first.

### Integration (real ffmpeg, skip-if-missing, stub synth) — `tests/test_loudness_integration.py` (new)

> **CORRECTION (grounded):** the original spec pointed at `tests/test_longform_jobs.py`, but that file tests the *job library recovery* logic (`build_longform_library`, `_done_payload_from_events`) over a seeded `job_store` — it never renders audio or drains an SSE stream and never imports `main`/torch (header confirms "no `main`/torch import"). It's the wrong home. Use a **new file**. (`tests/test_stories_encode.py` already demonstrates the `find_ffmpeg()`-skip + `TestClient` pattern and is a good structural reference.)

**Harness (shared fixtures in the new file):**
- `import torch` via `torch = pytest.importorskip("torch")` (mirrors `test_audiobook.py:123`) — needed only for the stub synth's tensors; **no model, no GPU**.
- Skip the whole module's render tests if `find_ffmpeg()` is falsy: `if not find_ffmpeg(): pytest.skip("ffmpeg not available", allow_module_level=True)` (the repo idiom — `test_stories_encode.py:46-47`).
- Build a FastAPI app with just the audiobook router (mirror `test_stories_encode.py:16-19`): `app.include_router(audiobook.router)`; `TestClient(app)`.
- **Stub the engine load:** `monkeypatch.setattr(audiobook, "_prepare_synth", fake_prepare)` where `fake_prepare` is `async def fake_prepare(default_voice): return (stub_synth, 24000, lambda vid: vid, "stubengine")`. `stub_synth(text, voice_id, speed=None)` returns a short non-silent CPU tensor (e.g. a 0.3 s 440 Hz sine at sr=24000, scaled to ~-21 dBFS so it's not clipping — gives the measure pass real loudness to normalize). This swaps the *entire* model/GPU path for a deterministic CPU stub.
- A helper `drain_sse(resp)` that splits the `TestClient` streaming response on `\n\n` and `json.loads` each `data:` payload into a list of event dicts (so tests assert over the ordered event list). Point `OUTPUTS_DIR`/job dirs at a `tmp_path` (set `OMNIVOICE_DISABLE_FILE_LOG=1` env at module top, mirroring `test_longform_jobs.py:11`).
- A `remeasure(out_path)` helper that runs `ffmpeg -hide_banner -loglevel info -i <out> -af loudnorm=I=<i>:TP=<tp>:LRA=11.0:print_format=json -f null -` and parses the resulting `input_i`/`input_tp` with the same `parse_loudnorm_measure` (dogfooding the parser as the verifier).

**Concrete tests:**
- **`test_acx_lands_in_window`** — POST `/longform/render` with a 2-chapter plan and `loudness="acx"`; drain SSE. Assert: a `mastering` event with `preset=="acx"` appears **before** the `done` event; `done["loudness"]["preset"]=="acx"`, `target_i==-19.0`, `target_tp==-3.0`, `two_pass is True`, and `math.isfinite(done["loudness"]["measured_i"])`. Then `remeasure(out)` and assert **integrated within ±1 LU of −19.0** and **true-peak ≤ −3.0 dBTP** (the ACX window — the core acceptance criterion). (Allow a small tolerance, e.g. `-3.0` with +0.5 dB slack for re-measure jitter, and document it.)
- **`test_podcast_lands_in_window`** — same shape with `loudness="podcast"`; assert `done.loudness.target_i==-16.0`, `target_tp==-1.5`, and re-measure ≈ −16 LUFS / ≤ −1.5 dBTP (±1 LU / +0.5 dB slack).
- **`test_off_path_emits_no_loudness_block`** — parametrize `loudness=["off", None]` (the second omitting the field). Assert: **no** `mastering` event in the stream, and `"loudness" not in done`. (The `done` event has exactly the legacy six keys.)
- **`test_off_path_reuses_acx_cache`** (★ resume-not-invalidated pin) — render the *same* plan twice over the *same* `cache_dir`: first with `loudness="acx"`, then with `loudness="off"`. Assert the second run reports `cached_chapters == total` (every chapter WAV reused regardless of loudness — proving the cache key is loudness-agnostic and resume isn't invalidated). This is the integration-layer counterpart to the untouched `chapter_cache_key` unit tests.
- **`test_single_chapter_acx_in_window`** — one-chapter plan, `loudness="acx"` → completes, `remeasure` lands in-window (no single-chapter special-casing regression).
- **`test_partial_failure_masters_survivors`** — stub synth raises on the 2nd of 3 chapters' first span (so chapter 2 fails, chapters 1 & 3 survive); `loudness="acx"`. Assert: a `chapter_error` event for index 1, `done["failed_chapters"] == [1]`, `done["loudness"]["two_pass"] is True`, and `remeasure(out)` is in-window (we master what we ship). Proves the survivor-concat path.
- **`test_all_chapters_failed_never_measures`** — stub synth always raises; `loudness="acx"`. Assert: no `mastering` event, and the terminal event is `{"type":"error","error":"all chapters failed to render"}` (the `:434-436` guard fires before the measure step). No `done`.
- **`test_no_chapters_never_measures`** — POST an empty `chapters: []` plan with `loudness="acx"`. Assert: terminal `error` `"nothing to render (no chapters)"`, no `mastering` event.

**Fallback at the integration layer:** intentionally **not** tested here (corrupting real ffmpeg output to force a measure failure is brittle and OS-dependent). Every `None`/fallback branch is owned by the mocked `tests/test_loudness.py` layer above; the integration suite asserts only the *happy* two-pass and the structural/cache invariants. This split is deliberate — the fallback logic is pure orchestrator control flow, perfectly exercisable with a stubbed `run_ffmpeg`, with zero ffmpeg flakiness.

### Captured stderr fixture (re-verified against this machine's ffmpeg n8.1.1, `ffmpeg version n8.1.1 Copyright (c) 2000-2026`)

Store this verbatim as a module-level string constant in both `tests/test_longform_render.py` (parser unit tests) and `tests/test_loudness.py` (encoded as bytes for the `run_ffmpeg` stub) — or share it via a small `tests/conftest.py` fixture to avoid drift:
```
[Parsed_loudnorm_0 @ 0x7f7bc4003e00] 
{
	"input_i" : "-21.75",
	"input_tp" : "-18.06",
	"input_lra" : "0.00",
	"input_thresh" : "-31.75",
	"output_i" : "-19.05",
	"output_tp" : "-15.31",
	"output_lra" : "0.00",
	"output_thresh" : "-29.05",
	"normalization_type" : "linear",
	"target_offset" : "0.05"
}
[out#0/null @ 0x557c3d94b4c0] video:0KiB audio:750KiB subtitle:0KiB other streams:0KiB global headers:0KiB muxing overhead: unknown
size=N/A time=00:00:02.00 bitrate=N/A speed= 133x elapsed=0:00:00.01
```
(Produced by `ffmpeg -hide_banner -loglevel info -f lavfi -i "sine=frequency=440:duration=2" -af "loudnorm=I=-19.0:TP=-3.0:LRA=11.0:print_format=json" -f null -`. Confirms the trailing `[out#…]`/`size=…` lines that the parser must skip past to grab the last balanced `{...}`. Note: FFmpeg prints `"0.00"`; Python parses to `0.0` and re-serializes as `0.0` in the apply filter — the golden string above and the `MeasuredLoudness` repr both use `0.0`. The tab-indented JSON is also why the parser must not line-anchor: indentation/whitespace differs across ffmpeg builds and OS terminals — the balanced-brace scan is whitespace-agnostic.)

**Adversarial fixtures to add (each its own `None`-returning parse test, per `test_parse_rejects_each_bad_input` / `test_parse_rejects_neg_inf_silent_clip` above):** `"-inf"` silent-clip block, a truncated block (`{ "input_i" : "-21.75"` with no closing brace), a block missing `target_offset`, a config-dump `{...}` followed by the real measure block (assert last wins — `test_parse_picks_last_balanced_block`), a JSON-array `[...]` payload, a JSON-scalar payload, keys nested under a sub-object only, and the backtracking-bait input (large run of unbalanced braces) asserted to return `None` promptly (`test_parse_is_linear_no_redos`).

### Local gates (developer loop, ordered cheapest-first)

1. `uv run pytest tests/test_longform_render.py tests/test_loudness.py` — the **pure + mocked** layers. These import **no torch** (pure layer is stdlib-only; `services.loudness` pulls only `ffmpeg_utils`/`longform_render`/`logging`), so **no segfault risk** per the "Local pytest segfault (torch/Triton)" memory. This is the primary fast loop — run it on every edit. Run via `uv run` (per the "Use uv for Python" memory), never `.venv/bin/python`.
2. `uv run pytest tests/test_loudness_integration.py` — the real-ffmpeg layer. Locally this **skips entirely** if ffmpeg is absent; if present, it imports `torch` for the stub synth's tensors (CPU only). If running it locally segfaults on this machine's torch/Triton, **deselect it locally and let CI validate** (per the segfault memory — CI is the source of truth for the torch-touching path). It does **not** import `main` (it builds a minimal `FastAPI()` with just `audiobook.router`).
3. `bunx vitest run` — frontend is untouched in slices 1–2, but run it per the merge-discipline memory (it's part of the local loop regardless). For **PR 3 only** (locale-file edits): additionally `uv run pytest tests/test_no_hardcoded_cjk.py` (the localization gate) and the i18n key-parity check; the vitest run then also covers any new SSE-event TS types.

### CI gates that apply

- **pytest (full suite)** — CI runs everything incl. `tests/test_loudness_integration.py` *with* a real ffmpeg available, so the ACX-window acceptance criterion is actually verified in CI (the integration test is the only place the ±1 LU assertion runs). All existing `test_longform_render.py` tests (`:25-261`) must stay green unchanged (the `measured=None` default-equivalence test pins that). Per the "Merge discipline: CI gates" memory, **never merge before PR checks are green** — Monitor `gh pr checks`.
- **CodeQL `py/polynomial-redos`** — gates the PR; satisfied because the parser introduces **no regex over the ffmpeg-stderr** (linear balanced-brace scan). `test_parse_is_linear_no_redos` documents the design intent; the actual CodeQL query passing is the gate. `_BITRATE_RE` (`:37`) is untouched.
- **bandit** — `chapter_cache_key`'s `sha1(..., usedforsecurity=False)` is unchanged; the new code adds no `subprocess.run(shell=True)`, no `eval`, no weak-hash. Clean.
- **`tests/test_no_hardcoded_cjk.py`** — runs in CI; relevant **only if** PR 3 touches `frontend/src/i18n/locales/*.json`. Slices 1–2 add no CJK and no `_ALLOWED_FILES` entry (backend strings are ASCII filter args / English log messages).
- **i18n key-parity** — relevant only for PR 3 (new `t('...')` keys must exist across all 21 locale files).
- **vitest** — frontend tests; relevant for PR 3 (new TS event types / UI copy). Slices 1–2 don't touch the frontend.
- **Docs-only / markdown gates** — n/a (slices 1–2 change no documented behavior; see Constraints → docs-sync). Per the "Docs-only PRs skip CI watch" memory, this is *not* a docs-only PR, so the full `gh pr checks` watch applies.

## Constraints

This section states explicitly how each VoiceStudio hard rule (CLAUDE.md / PROJECT.md) is satisfied. Every relevant rule has a row; "n/a" rules are listed so a reviewer can confirm they were considered, not skipped.

- **Default-behavior cross-platform parity (strict rule, 2026-05-20).** Loudness is **opt-in** — `loudness=None` default on both `AudiobookRequest` (`audiobook.py:151`) and `LongformRenderRequest` (`audiobook.py:510`). So the **default** render (the out-of-the-box, no-toggle behavior) is byte-for-byte identical on macOS / Windows / Linux and unchanged from today; this clears the "default features must work on every platform" bar by not being a default at all. When the user *does* opt in, the two-pass path uses only FFmpeg CLI args present in every FFmpeg ≥ 4.x (`loudnorm` shipped in 3.x; `print_format=json`, `measured_*`, `linear`, and the portable `-f null -` sink all long-standing and OS-independent — **no `/dev/null` vs `NUL` divergence**, no shell, no platform branching anywhere in `build_loudnorm_measure_cmd`). The stderr decode uses `.decode("utf-8", "replace")` so a Windows ffmpeg emitting cp-encoded bytes never raises and the ASCII JSON block parses identically. The parser locates the JSON by balanced-brace scan (not line anchoring), so tab-vs-space indentation, address widths, and trailing-line variants across platforms/ffmpeg builds don't matter. There is **no platform-only feature** introduced; nothing needs an opt-in toggle for a single OS. **Tested by:** `test_measure_cmd_golden_argv` (portable null sink, no `/dev/null`/`NUL`); `test_measure_loudness_non_utf8_stderr_still_parses` (cp-bytes decode); `test_parse_skips_trailing_noise` (whitespace/trailing-line tolerance). ✓
- **Backward-compatible project data (alembic / lazy migration rule).** No DB schema touched → **no alembic migration needed** (explicitly: no SQLite table, no `job_store` column added — the new `mastering` event + `done.loudness` block ride the existing opaque-JSON event log via `job_store.append_event`, `audiobook.py:380`). No `omnivoice_data/` shape change. No localStorage shape change → **no lazy-migration shim needed**. Critically, the chapter cache key (`chapter_cache_key`, `longform_render.py:109-135`, `sha1(..., usedforsecurity=False)`) and its signature are **left untouched** (no `loudness`/`measured` field added), so every existing user's cached chapter WAVs at `OUTPUTS_DIR/longform_cache/<key>.wav` keep matching after this change — no forced re-render, no manual migration. The new `done.loudness` field is additive-optional (absent for the off/None/legacy path), so an old frontend reading an old-shaped `done`, or a new frontend reading either shape, both interoperate without a data migration. **Tested by:** the untouched `test_cache_key_*` suite (`:198-229`) staying green proves the key signature didn't change; `test_off_path_reuses_acx_cache` (integration) proves cross-preset cache reuse; `test_render_cmd_default_equivalence_no_measured` proves the builder change is byte-identical for existing callers. ✓
- **Backward-compatible engine compatibility.** No engine code, no model weights, no on-disk model state touched. Already-installed IndexTTS/CosyVoice/etc. are not reinstalled or re-keyed. The integration test deliberately **stubs `_prepare_synth`** so no engine is exercised at all — proof that the feature is engine-agnostic. ✓
- **Local-first guarantee preserved.** **Zero network.** No cloud call, no account, no API key, no telemetry endpoint. The measure pass is a local `ffmpeg -f null -` invocation; everything stays on the user's machine. The app is fully functional with this feature un-opted-into (it is off by default). Logging stays local (`logging.getLogger("omnivoice.loudness")` → the existing `backend.log` surface) and the orchestrator logs only a static English message + the integer rc — it does **not** log the raw ffmpeg stderr (which contains the local `concat_path` under `OUTPUTS_DIR`) and never logs any `*TOKEN*/*KEY*/*SECRET*` value or a `/Users/<name>/` HOME path. This also keeps the feature's logs compatible with the opt-in bug-reporter's scrubbing rules. **Tested by:** `test_measure_loudness_no_leak_in_logs` (asserts no concat path / secret / HOME path in WARNING records). ✓
- **CodeQL `py/polynomial-redos` (regex-on-user-input lens).** The measure-output parser (`parse_loudnorm_measure`) parses (effectively user-influenced) ffmpeg-stderr **without any regular expression** — it uses a single linear balanced-brace scan + `json.loads` + `float()`/`math.isfinite()`. There is therefore **no regex reachable from external/user-controlled input** introduced by this task, so the `py/polynomial-redos` query has nothing to flag. The module's only existing regex, `_BITRATE_RE = re.compile(r"^\d{2,3}k$")` (`longform_render.py:37`), is fully anchored and length-bounded (`{2,3}`), has no overlapping/nested quantifiers, and is **not modified** by this task. **Tested by:** `test_parse_is_linear_no_redos` (feeds backtracking-bait, asserts prompt `None`), documenting the linear-scan choice and guarding against a future "just regex it" regression. (Per the "CodeQL ReDoS regex" memory, the safest answer is to introduce no regex at all here.) ✓
- **Localization (no hardcoded non-English / CJK; all UI via i18n `t()`).** The backend emits only stable machine identifiers (`"acx"`, `"podcast"`, numeric targets) in the `mastering`/`done.loudness` events — **no user-facing display strings** cross the wire. All UI copy goes through i18n keys; the existing **top-level flat** keys `loudness`, `loudness_off`, `loudness_acx`, `loudness_podcast` (`frontend/src/i18n/locales/en.json:130-133`) already cover the dropdown (note: they are *not* nested under an `audiobook.*` namespace — earlier drafts wrote `audiobook.loudness*`; the correct keys are bare `loudness*`). For PR slice 3, any new user-facing string (e.g. "mastered to −19.0 LUFS", "single-pass fallback") must be added as a new `t('...')` key and translated across **all 21** locale files (`frontend/src/i18n/locales/*.json` — verified count is exactly 21: `ar de en es fr hi id it ja ko nl pl pt ru sv th tr uk vi zh-CN zh-TW`) **in the same PR** (CLAUDE.md localization + docs-sync rules). No hardcoded CJK is introduced anywhere; no entry needs adding to `tests/test_no_hardcoded_cjk.py`'s `_ALLOWED_FILES` (the new backend strings are ASCII filter args / log messages, the i18n strings live only in `frontend/src/i18n/`). The `tests/test_no_hardcoded_cjk.py` gate runs in CI on any locale edit. ✓
- **Versioning (continuous-to-main patch, no RCs).** Code-only change; **no version bump** beyond main's standing next-patch (`X.Y.(Z+1)` already in `tauri.conf.json` / `Cargo.toml` / `pyproject.toml`). No `-rc` tag, no codename, no `v0.4` deferral — this absorbs into the open v0.3.x line. Ships continuous-to-main; the owner tags a patch from main when worth cutting. ✓
- **Docs-sync (hard rule).** Slices 1–2 alter **no** documented behavior (no README/CONTRIBUTING/SECURITY/SUPPORT/LICENSE/`docs/**` describes loudness internals; the SSE/UI change is additive and not documented). So docs-sync is satisfied with no doc edit. **If** PR slice 3 adds user-visible UI copy (a Stories loudness control, a "mastered to …" line), that PR must update the i18n keys (above) and any user-facing doc that lists Audiobook/Stories export options, in the **same PR**. ✓
- **No new dependencies.** Uses existing `find_ffmpeg`/`run_ffmpeg` (`ffmpeg_utils.py:56,378`) and stdlib `json`/`math` (add `import math` to `longform_render.py`). `uv tree` unchanged; no PyPI add. The test layers add no dep either — `torch` (integration only) is already pinned and gated by `pytest.importorskip`. ✓
- **GSD workflow.** Start via `/gsd-quick` (small, well-scoped fix) before any Edit/Write, per the CLAUDE.md GSD enforcement rule. ✓
- **Process-kill / abort (best-effort, no new endpoint).** The measure pass passes `job_id=job_id` to `run_ffmpeg` so the timeout/reaping (`register_proc`/`unregister_proc` + kill/wait, `ffmpeg_utils.py:398-437`) and any `kill_job_procs(job_id)` (`proc_registry.py:40`) cover it. A killed measure proc returns non-zero rc → orchestrator returns `None` (no raise) → single-pass fallback. **No new abort endpoint is added** — the longform router has no abort wiring today; the measure pass merely participates in the existing best-effort kill surface and never leaks a process (registered → reaped in the `finally` at `ffmpeg_utils.py:420-436`). **Tested by:** `test_measure_loudness_forwards_job_id_and_capture` (proves the proc is registered under `job_id`); `test_measure_loudness_nonzero_rc_returns_none` (abort == non-zero rc → fallback). ✓

## Dependencies

- None new. FFmpeg already resolved (`find_ffmpeg`, `ffmpeg_utils.py:56-95`) and confirmed present (system n8.1.1; also the `imageio-ffmpeg` bundle and Tauri `FFMPEG_PATH` sidecar paths, all handled in `find_ffmpeg`'s 3-tier resolution). Two-pass works with all three sources since it's pure CLI args — and identically across all three because none of the args are OS- or source-specific.
- Depends on no other open task. Independent of #27 (parser unification) and #24/#31 (longform store). Touches the same `_render_longform_sse` as those but only adds an isolated step — low merge-conflict surface.

## Risk

- **Measure-pass JSON parsing fragility (MED).** FFmpeg formats the block with tabs, wraps it with `[Parsed_loudnorm_0 @ 0xADDR]`, may emit `-inf` for silent input, and (verified n8.1.1) prints `[out#…]`/`size=…` lines *after* the block. Mitigation: parse the **last** balanced `{...}` block via a linear no-regex scan, coerce to float, treat any non-finite / missing key / non-dict / unbalanced / malformed as failure → single-pass fallback. Covered by the exhaustive adversarial parse unit tests (`test_parse_rejects_each_bad_input`, `test_parse_rejects_neg_inf_silent_clip`, `test_parse_picks_last_balanced_block`, `test_parse_skips_trailing_noise`). (The no-regex scan also moots any `py/polynomial-redos` exposure — see Constraints.)
- **Timeout swallowing the wrong exception (MED — newly emphasized).** `asyncio.TimeoutError` is a subclass of `Exception` and *must* be caught in the orchestrator so a slow/huge measure degrades to single-pass instead of aborting the render via the outer `except Exception` at `audiobook.py:466`. Conversely, `asyncio.CancelledError` (request teardown) is `BaseException`, not `Exception`, so an `except Exception` correctly lets cancellation propagate. Both behaviors are pinned by orchestrator unit tests `test_measure_loudness_timeout_does_not_propagate` and `test_measure_loudness_cancellation_propagates` — together they nail down the exact exception-class boundary. Using a bare `except:` would be a bug (would swallow cancellation) and `test_measure_loudness_cancellation_propagates` would fail.
- **Extra render time (LOW).** The measure pass decodes the full program once (no encode). For a multi-hour audiobook this adds a decode-only pass (fast — n8.1.1 measured a 2 s sine at ~133× realtime; multi-hour WAVs decode at similar speed; a ~60h book is still well under the 1800s timeout). Only incurred when a preset is opted into. Surfaced via the `mastering` SSE event so the UI can show progress.
- **`linear=true` edge case (LOW).** If the measured loudness range is too wide for a single linear gain to hit the target, FFmpeg internally falls back to dynamic mode for that file — still better than today's single-pass and still within ACX window. No action needed; documented in the apply-filter docstring. The integration `test_acx_lands_in_window` re-measure asserts the window regardless of which internal mode ffmpeg chose.
- **Log-level coupling (LOW).** The mux still runs at `-loglevel error` (`longform_render.py:263`, unchanged); only the **measure** cmd uses `info`. We never depend on the mux emitting loudnorm stats. Confirmed the measure block prints regardless of surrounding info-level chatter.
- **Mux rc still unchecked (LOW, pre-existing — not introduced here).** The mux `run_ffmpeg` (`audiobook.py:448`) discards its 3-tuple return today (the call is a bare `await run_ffmpeg(...)`, no `rc, out, err =` binding); a failed mux silently emits `done`. This task does not fix that (out of scope) and does not worsen it — the apply filter only changes the `-af` arg. Flagged so a reviewer doesn't attribute the latent behavior to this change. Not covered by a test (intentionally out of scope).
- **Two front doors, asymmetric wiring (MED).** Both Audiobook and Stories route through `_render_longform_sse`, so the backend change covers both. **But** Stories' `generateAll` (`StoriesEditor.jsx:368-371`) doesn't currently send a `loudness` value — only Audiobook does. So out of the box, only `/audiobook` exercises two-pass; `/longform/render` will until a `loudness` arg (and ideally a Stories UI control) is added in PR slice 3. The integration test deliberately posts to `/longform/render` *with an explicit `loudness="acx"`* (`test_acx_lands_in_window`) to prove the backend path independent of whether the Stories UI surfaces a control yet. (Note: when slice 3 adds a Stories control, the cross-platform-parity rule applies — any new control's default must be `off`/`null` so the default Stories export stays identical across OSes.)
- **Integration test flakiness / torch-segfault locally (LOW, test-only).** The integration layer needs real ffmpeg + a CPU torch tensor; it skips cleanly without ffmpeg, and if local torch/Triton segfaults it is deselected locally and validated in CI (per the segfault memory). The re-measure assertion uses a ±1 LU / +0.5 dB slack to absorb cross-build loudnorm jitter so the ACX-window check doesn't false-fail. The mocked layer (which owns every fallback branch) has no such flakiness.

## PR slices

1. **PR 1 — pure builders + parser (no behavior change).** Add `MeasuredLoudness` (5-float frozen dataclass), `build_loudnorm_measure_filter`, `parse_loudnorm_measure`, `build_loudnorm_apply_filter`, `build_loudnorm_measure_cmd` to `longform_render.py:138-168` (+ `import math`); extend `build_render_cmd` (`:232-285`) with the `measured: Optional[MeasuredLoudness] = None` kwarg (defaults `None` → identical output, asserted by `test_render_cmd_default_equivalence_no_measured`). Full unit coverage in `test_longform_render.py` (extend the import block at `:11-20` + the loudness section at `:23-48`): the golden measure-filter/apply-filter/measure-cmd assertions, the exhaustive adversarial parse cases (`test_parse_rejects_each_bad_input`, `test_parse_rejects_neg_inf_silent_clip`, `test_parse_picks_last_balanced_block`, `test_parse_skips_trailing_noise`, `test_parse_ignores_extra_keys`), the CodeQL backtracking-bait prompt-`None` test (`test_parse_is_linear_no_redos`), and the `build_render_cmd` truth-table tests. Nothing calls the new code yet, so main is unaffected. **Local gate:** `uv run pytest tests/test_longform_render.py` (torch-free). No version bump, no doc change (docs-sync n/a), no new dep. Mergeable alone.
2. **PR 2 — orchestrator + wiring.** Add `services/loudness.py::measure_loudness(ffmpeg, concat_list_path, preset, *, job_id) -> Optional[MeasuredLoudness]` (never-raises contract, all `None` branches, no-leak logging); wire the measure step + `measured=measured` + `mastering`/conditional `done.loudness` into `_render_longform_sse` (`audiobook.py:438-465`), and add `LOUDNESS_PRESETS` to the import block at `:32-37`. Add `tests/test_loudness.py` (mocked `run_ffmpeg` — `test_measure_loudness_happy_parses_fixture`, `_nonzero_rc_returns_none`, `_rc_none_returns_none`, `_empty_stderr_returns_none`, `_unparseable_returns_none`, `_timeout_does_not_propagate`, `_oserror_returns_none`, `_cancellation_propagates`, `_skips_off_without_spawning`, `_correct_argv`, `_forwards_job_id_and_capture`, `_no_leak_in_logs`) + `tests/test_loudness_integration.py` (skip-if-no-ffmpeg, stub `_prepare_synth`: `test_acx_lands_in_window`, `test_podcast_lands_in_window`, `test_off_path_emits_no_loudness_block`, `test_off_path_reuses_acx_cache`, `test_single_chapter_acx_in_window`, `test_partial_failure_masters_survivors`, `test_all_chapters_failed_never_measures`, `test_no_chapters_never_measures`). This flips the behavior on for `/audiobook` (and `/longform/render` when a `loudness` value is sent). **Local gate:** `uv run pytest tests/test_longform_render.py tests/test_loudness.py` (torch-free fast loop); integration test skips or is deselected locally, runs in CI. No version bump, no new dep. Mergeable after PR 1.
3. **PR 3 — (optional) UI surface + Stories parity.** (a) Show "mastered to −19.0 LUFS" from `done.loudness` in `AudiobookTab.jsx` (`done` handler at `:159-164`), defensively handling the `loudness`-absent and `two_pass==false`/`measured_i==null` cases (e.g. a single-pass-fallback message when two-pass failed) — **all copy via new `t('...')` keys, no hardcoded strings.** Add the TS `DoneLoudness`/`MasteringEvent` types (shapes pinned in API/data shapes) to the SSE-event typing. (b) **Wire `loudness` into Stories** — add it to the `longformRender({...})` call in `StoriesEditor.jsx:368-371` and add a loudness dropdown (in `StoriesEditor` or `ExportModal.jsx`, both currently have no loudness control) **defaulting to `off`/`null`** so the default Stories export stays cross-platform-identical. (c) Add any new i18n keys across **all 21** locale files (existing flat `loudness*` keys at `en.json:130-133` are reusable). **Local gate (PR 3 specific):** `bunx vitest run` + `uv run pytest tests/test_no_hardcoded_cjk.py` + the i18n key-parity check; update any user-facing doc listing export options in the same PR (docs-sync). Pure additive UX, still no version bump / no new dep.

(Slices 1+2 can be a single PR if the owner prefers; the split keeps the pure/impure boundary reviewable and lets the runner change land behind a green pure-builder PR.)

## Acceptance criteria

- [ ] With `loudness="acx"`, the rendered m4b/mp3 measures within **±1 LU of −19.0 LUFS integrated** and **true-peak ≤ −3 dBTP** (verified by the re-measure pass in `test_acx_lands_in_window` — the integration test, run with real ffmpeg in CI) — i.e. inside the ACX window. Single-pass today does not guarantee this.
- [ ] With `loudness="podcast"`, output lands near −16 LUFS / ≤ −1.5 dBTP (matching `LOUDNESS_PRESETS["podcast"]`, `longform_render.py:155`) — `test_podcast_lands_in_window`.
- [ ] `loudness=off`/`null`/unknown/whitespace produces byte-identical mux argv to today (no `-af`, no measure pass, no `mastering` event, **no** `done.loudness` key) and reuses the same cached chapter WAVs (resume not invalidated — `chapter_cache_key` and its signature unchanged; `test_off_path_emits_no_loudness_block` + `test_off_path_reuses_acx_cache` assert `cached_chapters == total` on a repeat run; `test_render_cmd_default_equivalence_no_measured` asserts argv byte-equality).
- [ ] Both `/audiobook` and `/longform/render` get accurate two-pass mastering through the single shared `_render_longform_sse` change *when a preset is supplied* (note: Stories must send a `loudness` value — PR 3 — to exercise it from the UI; the backend path is proven by `test_acx_lands_in_window` posting to `/longform/render` directly).
- [ ] **The render still completes on EVERY measure-failure path** — non-zero rc (incl. `rc is None`), hard timeout, empty stderr, unparseable/truncated/non-finite JSON, silent clip, or an aborted/killed measure proc — falling back to single-pass `loudnorm`, with `done.loudness.two_pass == false` and `done.loudness.measured_i == null`. The orchestrator never raises; a measure failure never produces an `error` event. Each branch covered by a named mocked orchestrator test in `tests/test_loudness.py`.
- [ ] `done` event carries the `loudness` block (`preset` + `target_i` + `target_tp` + `two_pass` + `measured_i`) **only** when a preset was requested, and omits it entirely otherwise; `mastering` event (`{type, preset}`) emitted before the measure pass for preset requests only (asserted in `test_acx_lands_in_window` / `test_off_path_emits_no_loudness_block`).
- [ ] `asyncio.CancelledError` during the measure pass propagates (request teardown still cancels the generator — `test_measure_loudness_cancellation_propagates`); `asyncio.TimeoutError` does NOT propagate (degrades to single-pass — `test_measure_loudness_timeout_does_not_propagate`). Both pinned by tests.
- [ ] Empty/partial-chapter states are handled by the existing guards (no chapters → `error` / `test_no_chapters_never_measures`; all failed → `error` / `test_all_chapters_failed_never_measures`; some failed → master the survivors / `test_partial_failure_masters_survivors`; single chapter → normal path / `test_single_chapter_acx_in_window`) — no new failure introduced upstream of the measure step.
- [ ] **Constraints satisfied (verifiable):** default behavior unchanged & identical on macOS/Windows/Linux (opt-in, OS-independent argv incl. portable `-f null -` — `test_measure_cmd_golden_argv`; cp-bytes decode — `test_measure_loudness_non_utf8_stderr_still_parses`); local-first (no network, no leaked HOME/secret in logs — `test_measure_loudness_no_leak_in_logs`); backward-compatible data (no alembic/localStorage migration, no DB schema/column, cache key untouched — `test_cache_key_*` stay green, `done.loudness` additive-optional); CodeQL clean (no new regex on ffmpeg-stderr — linear balanced-brace scan, `test_parse_is_linear_no_redos`; `_BITRATE_RE` untouched); localization (no hardcoded UI strings — backend emits machine ids only; any PR-3 copy via `t()` across all 21 locales + CJK gate); versioning (code-only, no bump, no RC); no new dep (`uv tree` unchanged).
- [ ] All existing `test_longform_render.py` tests (`:25-261`) still pass unchanged; new pure tests + mocked orchestrator tests + skip-if-no-ffmpeg integration tests pass. `uv run pytest tests/test_longform_render.py tests/test_loudness.py` green locally (torch-free, no segfault); full CI green incl. the real-ffmpeg integration suite.
- [ ] No new Python dependency; `uv tree` unchanged. No DB/migration/model-state change. No platform-specific default behavior.
