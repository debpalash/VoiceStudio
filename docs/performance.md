# Performance guide

Where the time goes when VoiceStudio feels slow, what you can tune, and what you
should leave alone. Everything here applies to the current release; numbers
marked "measured" come from `scripts/bench_pipeline.py` on a 16 GB Apple
Silicon M2 — your hardware will differ, but the *ratios* hold.

## First: the classic causes of "it got slow"

Before touching any knob, check these — they account for most slowness reports:

1. **A voice profile with an empty Transcript field.** Cloning needs the
   reference clip's transcript. If the profile doesn't have one, the app runs a
   full Whisper transcription of the clip — and before v0.3.15 it did that on
   **every single generate** (the "TTS got much slower after updating, CPU
   pegged at 100%" regression, #1032). Since v0.3.15 the auto-transcription
   runs once and is saved onto the profile, but a profile that still has an
   empty transcript (e.g. imported or hand-edited data) keeps paying an ASR
   pass per generation. **Fix:** open the voice's editor and check the
   Transcript box — if it's empty, type or paste what the reference clip says
   (or just generate once on v0.3.15+ and confirm the box filled itself in).
2. **The first generation after a (re)start is always the slowest.** Model
   weights load lazily (~8 s), CUDA builds torch.compile kernels, Apple Silicon
   warms Metal kernels. Judge speed from the *second* generation onward.
3. **Memory pressure.** On a 16 GB unified-memory machine, a browser with 40
   tabs next to a dub means the OS pages the model in and out — or kills the
   backend outright ("Can't reach the local backend"). Check Settings →
   Models for what's resident, and Settings → Performance for free RAM. See
   [Flush caches / Unload resident model](#flush-caches--unload-resident-model)
   for freeing memory without a restart.
4. **You're generating on CPU without realizing it.** A driver update, a
   CUDA/torch mismatch, or simply running on hardware with no supported GPU
   path silently drops you to CPU — everything works, just several times
   slower. Three places tell you the truth:
   - **Settings → Performance → Device & compute** shows the live compute
     device (`cuda` / `mps` / `cpu`), a "GPU active" badge, and RAM/VRAM
     readouts.
   - **Settings → About → Run self-check** (the `/system/diagnose` endpoint)
     warns explicitly: *"cpu (no GPU acceleration detected)"* with a hint
     about drivers.
   - **Model Catalogue → Engines** shows a routing badge per engine — "GPU active",
     "CPU fallback", or "CPU" — with the *reason* shown as small text under
     the badge (full text on hover).
   Note: **GPU acceleration on Windows is NVIDIA/CUDA-only** — AMD and Intel
   GPUs run CPU-only there (see [Windows install notes](install/windows.md)).
5. **You aborted a dub earlier (fixed in v0.3.23).** Dubbing moves the TTS
   model to CPU to free VRAM for the ASR model, then moves it back when the
   transcription finishes. Before v0.3.23 that move-back only ran on the fully
   successful path, so cancelling a dub, hitting a dub error, or closing the
   tab mid-transcription left the TTS model stranded on CPU — and **every**
   later generation ran there, 10-50x slower with the CPU pegged, until the
   ~15-minute idle unload happened to fire. Restarting the backend cleared it,
   which made it look random or time-of-day related (#1191). Since v0.3.23 the
   move-back runs on every exit path, *and* each generation verifies the model
   is on the expected device and moves it back itself — so no future code path
   can strand it again. If you are on an older build, restart the backend.

## What a generation actually spends time on

For a cloned voice, one generation is: encode the reference clip (~0.4 s,
measured; cached after the first use for the voices you reuse — a dub's
per-line clips are each used once, so there's nothing for a cache to save
there) → synthesize (the bulk; scales with output length) → post-process
(mastering, watermark; fractions of a second). Long texts are split into
chunks synthesized sequentially — time scales roughly linearly with text
length.

For a dub, the stages are: audio extraction + vocal separation (one-time,
minutes for long videos) → transcription (on the best accelerator available —
Apple Silicon uses MLX since v0.3.21, NVIDIA uses CUDA; CPU-only installs fall
back to the processor) → translation (parallel, 6 concurrent requests for LLM
providers) → per-segment synthesis (sequential, the bulk of the time) →
mixing and export (mostly stream-copied, fast).

## Knobs you can actually turn

All of these are environment variables read by the backend at start. Set them
in `~/.config/omnivoice/env` (created by the installer) or your shell profile.
None of them are required — the defaults are chosen for the common case.

| Variable | Default | What it does |
|---|---|---|
| `OMNIVOICE_DEVICE` | `auto` | Pin the compute device (`cuda` / `rocm` / `xpu` / `mps` / `cpu`) instead of auto-detect. Same control lives in **Settings → Performance & Device** (the env var wins over the UI pick). Honored only for devices the host actually has — a family that isn't detected is noted and ignored, never obeyed blindly. Applies at the next backend start. |
| `OMNIVOICE_FLASHINFER` | `0` | CUDA-only accelerated decoding for the default engine via [FlashInfer](https://github.com/flashinfer-ai/flashinfer) kernels (packed CFG attention, fused RMSNorm/RoPE/GEMM) — ~2x on upstream's benchmarks. `1` enables it; `graph` also captures CUDA graphs (best when you render one thing at a time). Requires installing the optional `flashinfer-python` package into the backend environment first (`uv pip install flashinfer-python flashinfer-jit-cache --extra-index-url https://flashinfer.ai/whl/cu128/`, matching your CUDA build). Replaces `torch.compile` for that session, pins inference to a single GPU thread (the FlashInfer attention plan is per-generation state), and keeps fused copies of the attention/MLP weights resident (~roughly half the LLM's weight size extra VRAM) — leave it off on tight-VRAM cards. If the package is missing or a FlashInfer/CUDA-graph kernel fails at runtime, the app logs the reason and falls back to the standard path; failures outside those kernels (e.g. a genuine out-of-memory) surface normally. |
| `OMNIVOICE_PROMPT_DISK_CACHE` | `1` | Persist encoded voice-clone references (`prompt_cache/` in the app data dir, ~10 KB per voice, 32 newest kept) so the first generation with a known voice after a restart skips the reference re-encode and any auto-transcription. Set `0` to keep the cache in memory only. |
| `OMNIVOICE_IDLE_TIMEOUT_S` | `900` | Seconds of idle before the TTS model unloads to free memory. Raise it (e.g. `3600`) if you generate in bursts and dislike the ~8 s reload; lower it on tight-memory machines. |
| `OMNIVOICE_SIDECAR_IDLE_TIMEOUT_S` | `300` | Same idea for sidecar engines (IndexTTS 2.5 etc.). |
| `OMNIVOICE_LLM_CONCURRENCY` | `6` | Parallel LLM translation calls during a dub. Raise for a fast API endpoint, lower if your provider rate-limits. |
| `OMNIVOICE_GPU_WORKERS` | auto | Concurrent generations on the GPU. Auto-sized from free VRAM (1 worker per 5 GB, max 4); MPS and CPU always get 1. **Do not raise this on ≤10 GB cards or Apple Silicon** — two concurrent jobs over-committing VRAM is exactly the crash class (#567) the auto-sizing exists to prevent. |
| `OMNIVOICE_CPU_POOL` | `min(8, cores)` | Thread pool for CPU-side work (translation dispatch, audio I/O). |
| `OMNIVOICE_SINGLE_ENGINE_RESIDENT` | `1` | Keep only one TTS engine in memory at a time. Set `0` on 32 GB+ machines to keep several engines warm across switches. |
| `OMNIVOICE_UNIFIED_OFFLOAD_HEADROOM_GB` | `6` | On unified memory (Apple Silicon): if free RAM is below this when a dub needs the transcription model, the TTS model is fully released first (it reloads on the next generation). Raise to be more aggressive about freeing, lower on 32 GB+ machines to avoid the reload. |
| `OMNIVOICE_INDEXTTS_FP16` | `1` | IndexTTS half-precision. Leave on. |
| `OMNIVOICE_ASR_VRAM_PREFLIGHT` | `1` | Downgrade transcription precision instead of crashing when VRAM is short (CUDA). Leave on. |
| `OMNIVOICE_GENERATE_TIMEOUT_S` | `300` | Abandon a generation after this many seconds **of actual compute** on an accelerated (GPU-family) host — the clock starts when a worker picks the job up, never while it waits in line. It's a floor, not a ceiling: the budget grows with the text (+1 s per 40 characters past the first 1200), so long inputs rarely need this raised. Also settable from **Settings → Performance & Device → Compute-time budget** (persists to `prefs.json`; takes effect on the next backend restart, same as `OMNIVOICE_DEVICE` above). |
| `OMNIVOICE_CPU_GENERATE_TIMEOUT_S` | `600` | Same budget, for hosts that render on the CPU — correct CPU synthesis legitimately takes longer than the accelerated floor, so it gets its own, higher one. An explicit value here always governs CPU-family generation, independent of `OMNIVOICE_GENERATE_TIMEOUT_S` above — that var only doubles as a CPU floor when *this* one is left unset (a legacy shortcut: setting only `OMNIVOICE_GENERATE_TIMEOUT_S` lowers the watchdog everywhere with one var). Also settable from **Settings → Performance & Device**, which flags a row an external env var is already shadowing instead of claiming a save will apply. |
| `OMNIVOICE_ENGINE_IMPORT_PROBE_TIMEOUT_S` | `60` | How long to wait while checking that a sidecar engine's virtualenv can import the engine. Only affects how quickly a *broken* venv is ruled out — a probe that runs out of time is treated as "unproven", and the venv is used anyway, so a slow machine is never told its engine is missing. Per-engine override: `OMNIVOICE_INDEXTTS_IMPORT_PROBE_TIMEOUT_S` (and the same shape for `CONFUCIUS4`, `DOTS_TTS`, `MOSS_TTS_V15`). |
| `OMNIVOICE_GPU_QUEUE_TIMEOUT_S` | `1800` | How long a job may sit in the GPU queue before it's reported as a saturated pool (a retryable condition — nothing ran). Waiting is normal on 1-worker machines; lower this only if you'd rather fail fast than queue. |

**torch.compile** is probe-based, not platform-based: it's attempted only
where the runtime check says it can work (a CUDA device with Triton importable
and a supported GPU architecture) and skipped automatically everywhere else —
MPS, CPU, and the typical Windows install (Triton ships no Windows wheel).
The one user-facing control is Settings → Performance → "Disable
torch.compile" (shown on Windows), for the rare setup where a partial Triton
install makes the probe pass but the compile attempt itself crash — see
[Windows install notes](install/windows.md).

## Warnings before a slow generation

The 300 s budget used to be discovered the hard way: you pressed Generate,
waited out the whole budget, and were then told the job was too heavy. Two
checks now run **before** the request leaves the app, at the one call every
synthesis path shares (Generate, voice previews, the compare modal, the stories
editor, profile previews, and streaming).

| Situation | What you see |
| --- | --- |
| The engine declares a VRAM floor above what this GPU has, or routing fell back to CPU | The routing caveat, naming your card, the engine's floor, and the ways around it |
| The host synthesizes on the CPU **and** the text is over 1200 characters | A heads-up that this generation may exceed the time budget |
| The host synthesizes on Apple Silicon (MPS) **and** the text is over 1200 characters | The same heads-up — MPS gets the accelerated-host budget (`OMNIVOICE_GENERATE_TIMEOUT_S`), which a long render can still legitimately exceed |

**Why 1200 characters:** it is the same figure the budget itself uses. The first
1200 characters get the flat `OMNIVOICE_GENERATE_TIMEOUT_S`, and only past that
does the budget start growing (+1 s per 40 characters). Below the threshold you
are inside a budget the backend already considers generous, so ordinary
sentences on a CPU laptop stay quiet.

Both warnings are **advisory** — nothing is blocked. A driver can page to system
RAM, and a short input fits where a long one does not, so the engine still runs
if you want it to. Each fires **once per engine per session**, keyed on the
reason, so a genuinely different problem still gets through but the same
sentence is not repeated on every synthesis. Switching engines re-arms it.

If you are already on a CPU-tuned engine (OmniVoice GGUF, Supertonic-3) the
warning drops the "try a CPU-tuned engine" suggestion — it would be advice to
switch to what you are already using.

## Flush caches / Unload resident model

This is the feature the VRAM-starved timeout error ("TTS generate ran for more
than 300s … Flush caches / Unload the resident model") points at. It frees
RAM/VRAM **without restarting the app**, and it never loses data — an
unloaded model simply reloads lazily (~8 s) on the next generation.

One thing Flush **can't** free: the job that just timed out. An abandoned
generation cannot be killed from Python — its thread runs to completion and
holds its VRAM until it does, so a Flush (or a retry) issued seconds after a
timeout is competing with a job that is still on the device. Wait for it to
drain, or restart the backend, and then Flush.

**Where it lives:**

- **Top toolbar → Flush** (the button next to the model-status badge). The
  dropdown lists every model currently in memory — the TTS model, its
  co-loaded ASR, the diarization pipeline, and any resident engines or
  sidecars — with its device and VRAM use, and a per-model **Unload** button
  where unloading is possible (WhisperX is released together with the TTS
  model, so it has no button of its own). An engine left resident after you
  switched away from it is marked *"not active — safe to unload"*. Below the
  list are the two bulk actions:
  - **Flush caches** — runs a multi-pass garbage collection and releases the
    accelerator's cached memory (CUDA/MPS/XPU `empty_cache`). Models stay
    loaded, so there's no reload cost; this recovers cache/fragmentation
    memory only.
  - **Unload all + flush** — the above **plus** fully unloads the resident
    TTS model. Frees the most memory; the next generation pays the ~8 s
    reload.
- **Model Catalogue → Models** — rows whose weights are resident right now show an
  "In memory" badge with the same per-model **Unload** button.

**From a script** (the local API on port 3900), the same operations:

```bash
curl -X POST "http://127.0.0.1:3900/system/flush-memory"                    # flush caches
curl -X POST "http://127.0.0.1:3900/system/flush-memory?unload_model=true"  # + unload TTS model
curl "http://127.0.0.1:3900/model/loaded"                                   # what's resident
# unload one model — ids: tts | diarization | sidecar:<id> | sidecars
curl -X POST "http://127.0.0.1:3900/model/unload/tts"
```

**When to use it:**

- **After a VRAM-starved 503 timeout** — a resident model and your generate
  were contending for GPU memory. Unload all + flush, then retry.
- **Before a dub on a tight-memory machine** — transcription needs room the
  resident TTS model is holding (on Apple Silicon the app does this
  automatically, see `OMNIVOICE_UNIFIED_OFFLOAD_HEADROOM_GB` above).
- **After switching engines** — with `OMNIVOICE_SINGLE_ENGINE_RESIDENT=0`,
  or for sidecar engines, the previous engine can stay in memory; the
  dropdown shows it and marks it safe to unload.
- **Mid batch-run on a small GPU** — an occasional
  `POST /system/flush-memory` between jobs keeps cache growth from
  starving later generations.

**When it won't help:** many generate errors are *not* memory problems, and
their messages say so explicitly ("the Flush button won't help here") —
missing env vars, network failures during a model download, a broken native
component. Believe the message; Flush only fixes memory contention. Also
note the app already frees memory on its own when idle
(`OMNIVOICE_IDLE_TIMEOUT_S`) — Flush is for when you need the memory *now*,
between jobs.

If the timeout error keeps recurring even right after an unload, see
[troubleshooting §14](install/troubleshooting.md#14-cant-reach-the-local-backend-during-generation--transcription--dubbing)
— the same starvation class has more remedies there (smaller ASR model,
CPU ASR, the crash-isolated ASR engine).

## Platform notes

- **Apple Silicon**: everything runs on the GPU via MPS/MLX. One generation at
  a time by design — unified memory means TTS and ASR compete for the same
  RAM, and the app actively unloads one to make room for the other on 16 GB
  machines. More RAM directly improves dub throughput (fewer unload/reload
  cycles).
- **NVIDIA**: fp16 + torch.compile on by default. ≥16 GB VRAM parallelizes up
  to 3-4 concurrent generations (API/batch workloads); ≤10 GB deliberately
  serializes.
- **CPU-only**: expect ~2x slower than MPS, more against CUDA. Prefer the
  smaller/faster engines (see Model Catalogue → Engines) and short reference clips.

## Measuring instead of guessing

`scripts/bench_pipeline.py` (repo checkouts) profiles each stage one at a
time, memory-safely — it refuses to start a stage without enough free RAM,
and unloads models between stages:

```bash
# stop the app first — a running backend holds a model and skews numbers
uv run python scripts/bench_pipeline.py            # everything
uv run python scripts/bench_pipeline.py tts clone  # just these stages
```

If you report a performance issue, pasting its table (plus your platform and
RAM/VRAM) turns a guessing game into a bisect.

Measured results per engine/device — and how to contribute yours — live in
[benchmarks.md](benchmarks.md).

## Performance budgets

CI guards the hot paths above against regressions — not with wall-clock
budgets (CI hardware varies too much for a stable "≤5 % slower" threshold),
but with **operation-count budgets** in
`tests/test_perf_operation_budgets.py`, which fail on *any* regression:

- **Streaming TTS (`/ws/tts`)**: exactly one engine `generate` per sentence
  chunk, and exactly one text-normalization pass per request (never one per
  sentence).
- **Dub re-mix**: a fit-only re-mix (`regen_only=[]`) of cached segments
  makes **zero** TTS calls. The zero-decode / zero-rewrite budget activates
  with the natural-rate cached fast path (each cache is then decoded exactly
  once, by the final assembly).
- **Batch dubbing (native batches)**: N renderable segments at batch width W
  cost exactly ⌈N/W⌉ `generate_batch` calls and zero per-segment `generate`
  calls when native batching is enabled.

Updating a budget is a deliberate act: if a change legitimately adds an
operation to a guarded path, change the expected count in the same PR with a
comment justifying the new floor. Never loosen a budget just to make CI pass
— that is the regression the budget exists to catch.

## Batch and streaming behavior

 Batch dubbing renders several segments in one native forward pass when the
selected engine supports it. The width is derived from the host rather than
fixed, because a wider forward pass needs proportionally more device memory:
CPU hosts and cards with less than ~2 GB of headroom above the engine's
single-job requirement stay at one segment, and the width steps up to 2, 4,
and 8 as headroom allows. `OMNIVOICE_DUB_BATCH_WIDTH` overrides it (1 disables
batching, 16 is the ceiling). Engines without native batching inherit a
compatibility fallback that preserves the one-segment behavior.

Streaming clients also receive measured latency in the `/ws/tts` terminal
 `done` frame: `ttfa_ms` is request-to-first-audio, `gen_time_s` is the
end-to-end wall clock including delivery, and `rtf` is *synthesis* time
divided by generated-audio duration — measured around the render calls only,
so a slow client cannot inflate it. The backend log records the same values, so a slow first chunk is
 distinguishable from a fast first chunk followed by a long render.

## Things that look like knobs but aren't

- **Deleting and re-adding a voice** doesn't speed anything up; the reference
  encode is cached per file for voices you reuse. (A dub's per-line reference
  clips are the deliberate exception — each is a distinct clip used once, so
  there's nothing for a cache to save.)
- **Killing the backend between generations** makes everything slower — you
  pay the model load every time. The idle timeout already frees memory when
  it's genuinely idle.
- **`OMNIVOICE_PRELOAD_TTS_ASR`** exists for a legacy in-process Whisper
  fallback; enabling it costs memory on every start and speeds up nothing on
  a default install.
