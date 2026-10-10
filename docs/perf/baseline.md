# Performance baseline

Reference numbers for the "feels bloated and slow" work. Each later change is
judged against this table. Measured on `main` at `06c6e077` (0.5.7).

## Renderer (measured)

Reproduce: `node scripts/perf/renderer-baseline.mjs` (builds the web renderer to a temp dir). These are static counts and sizes from the build output, not browser load or paint timings.

| Metric | Value |
|---|---|
| Files referenced by `index.html` (fetched at page load) | 69 |
| Size of those files | 1,858 KB raw / 559 KB gzip |
| Entry chunk | 820 KB raw / 253 KB gzip |
| CSS among those files | 255 KB raw / 36 KB gzip |
| JS chunks | 261 (20 are locales, 4.3 MB, lazy) |
| `refetchInterval` declarations | 37 in 27 files (+ 9 `setInterval`) |

Entry chunk by source (via sourcemaps):

| Source | Size |
|---|---|
| `react-dom` | 301 KB |
| App code in the entry | ~440 KB |
| `@tanstack/router-core` | 47 KB |
| `lucide-react` | 19 KB |

Within the app code, `features/settings` is 96 KB, `components/app-shell` 67 KB, `features/dub` 36 KB, `features/clone` 14 KB and `features/transcriptions` 13 KB. These are page features, so something in the shell imports them statically and defeats route splitting.

Other findings:

- `media-player` (vidstack, 153 KB) and `waveform-player` (44 KB) are referenced by `index.html`, so they download at page load.
- Largest lazy chunks are `add-scalar-classes` (2.2 MB), `dash.all.min` (803 KB), `scalar-api-reference` (609 KB) and `hls` (562 KB). They only load on demand, so they cost install size, not startup.

## Backend (measured, macOS arm64 only)

Reproduce: `.venv/bin/python scripts/perf/backend_startup.py` after `uv sync`
(boots twice on a throwaway data dir). Measured on an M-series Mac, 32 GB,
Python 3.11, no models installed.

| Step | first launch | relaunch |
|---|---|---|
| env_prefs | 0.1 s | 0.1 s |
| native_preload | 0.0 s | 0.0 s |
| ml_imports | 1.8 s | 2.2 s |
| api_routes | 1.1 s | 1.1 s |
| db_migrate | 0.5 s | 0.5 s |
| services_start | 10.3 s | 8.1 s |
| **Routes live** | **13.9 s** | **12.6 s** |
| Idle RSS (20 s after ready) | 702 MB | 656 MB |

Findings:

- **The first launch of a freshly installed environment is slow.** Right after `uv sync` the first boot took 82 s (`services_start` alone 74 s), and 50–63 s on two further fresh environments, against 7–14 s on a relaunch. It is not bytecode compilation: an environment synced with `--compile-bytecode` still took 62.8 s on its first boot, and the process used only 14 s of CPU in 50 s of wall time, so it mostly waits. The likely cause is the OS checking newly written native libraries on first load (macOS here; Windows Defender is a known equivalent). That is unconfirmed. Windows and Linux are unmeasured, and so is a packaged build. The desktop setup runs `uv sync --frozen --no-dev` ([runtime-project.ts:648](../../electron/src/main/runtime-project.ts)) and then the first launch pays the wait.
- **`services_start` is mostly one function.** `reconcile_active_profile()` takes about 8 of the 12.6 s on a relaunch. Within it, `engines/omnivoice_subprocess` `is_available()` imports the whole `omnivoice/models/omnivoice.py` module (6.6 s) just to answer "is it installed". Translation-engine and ASR availability probes add about 1.7 s and 1.5 s.
- **Boot imports** `torch` (1.4 s), `torchaudio` (1.5 s), `sklearn` (2.2 s), `faster_whisper` (1.0 s), `transformers` (0.8 s) and `spacy` (0.8 s).
- **Not imported at boot:** `pyannote.audio` (27.5 s to import), `litellm` (22.8 s), `lightning` (8.9 s), `gradio` (3.7 s). Those are lazy already, so moving them to extras saves install size, not startup.
- **Idle:** about 650–700 MB RSS and 2–3 % CPU with no model loaded. Model warm-ups were not exercised because no ASR model was installed.
- **Install size:** `.venv` is 1.8 GB. Largest: `torch` 345 MB, `mlx` 207 MB, `llvmlite` 130 MB, `litellm` 87 MB, `gradio` 80 MB, `onnxruntime` 77 MB.

### After: import-free availability probe

`OmniVoiceSubprocessBackend.is_available()` now checks `find_spec` instead of importing the model. A/B on the same environment:

| | Routes live (relaunch) | Routes live (first) | `services_start` (relaunch) | Idle RSS |
|---|---|---|---|---|
| Before | 14.1 s | 16.0 s | 9.6 s | 653 MB |
| After | 6.0 s | 9.3 s | 2.6 s | 440 MB |

What the remaining ~2.6 s of `services_start` is (boot import trace after the change):

| Import at boot | Cost | Pulled in by |
|---|---|---|
| `argostranslate` (`spacy`, `stanza`, `onnxruntime`) | ~1.2 s | translation-engine probe |
| `faster_whisper` (`ctranslate2`, `transformers`) | ~1.1 s | ASR availability probe |

Both probes import on purpose: they exist to catch a broken native stack (CTranslate2's executable-stack failure, #692) before the engine is offered. Replacing them with `find_spec` would only move the same import to the user's first translation or transcription, and would lose that check, so they are left alone. `sklearn` and `boto3` no longer load at boot.

Resident memory added by each import, in boot order (fresh interpreter, macOS arm64):

| Import | Added | Note |
|---|---|---|
| `torch` | 168 MB | needed by the in-process engines |
| `sklearn` | 104 MB | no longer loaded at boot (it came in via the model import) |
| `spacy` | 40 MB | via `argostranslate` |
| `argostranslate.translate` | 38 MB | translation probe |
| `transformers` | 28 MB | via `ctranslate2` |
| `faster_whisper` | 26 MB | ASR probe |
| `pyannote.audio` | 150 MB | not loaded at boot |

Idle RSS at ready: 653 MB before, 436 MB after. The TTS model preload is skipped on this host ("OmniVoice uses crash isolation"), so model memory is unmeasured; on CUDA hosts it preloads at boot.

### With the OmniVoice model installed (macOS arm64, 32 GB)

Model `k2-fsa/OmniVoice` (3.27 GB repo; 2.45 GB model plus an 806 MB audio tokenizer), no ASR model. Memory is the whole process tree, because the model runs in a sidecar process. Method: boot, wait 20 s, `POST /generate` a one-sentence text twice, then wait for idle release with `OMNIVOICE_SIDECAR_IDLE_TIMEOUT_S=45`.

| Moment | Tree RSS |
|---|---|
| Ready (11.8 s, 9.1 s on two runs) | 546 MB |
| Ready + 20 s | 550 MB |
| Peak during first generate | 2.4–2.5 GB |
| After first generate | 1.6–1.8 GB (sidecar ~1.0 GB, API ~540 MB) |
| After sidecar idle release | 639 MB (about 90 MB more than before the generate) |

| | Time |
|---|---|
| First generate (loads the model) | 15.7–21.6 s |
| Second generate | 2.5 s |

Findings:

- **The boot does not preload on this host.** Apple Silicon takes the crash-isolated sidecar path, so `preload_model()` returns early (`model_manager.py`). The model loads on the first generate, which is where the 13–19 s goes. On CUDA hosts the preload loads the model at boot instead. That was not measured here.
- **Loading is the memory peak.** The 2.45 GB checkpoint pushes the tree to about 2.5 GB, then settles at 1.6–1.8 GB while the sidecar stays warm.
- **Idle release works but is slow to start.** The sidecar was gone about 75 s after a 45 s timeout (the reaper ticks about every 30 s). At the default timeout of 300 s the model stays resident for roughly 5.5 minutes after the last generate.
- **The API process keeps about 90 MB** after the sidecar exits.

### With an ASR model installed (macOS arm64)

Model `mlx-community/whisper-large-v3-turbo` (1.61 GB), no TTS requests. Backend booted with `OMNIVOICE_IDLE_TIMEOUT_S=45` so the idle release shows in a short run (the default is 900 s). Readings are `ps` RSS over the process tree. macOS excludes compressed memory from RSS, so single readings can dip (one run read 136 MB at ready + 20 s); trust the large steps, not the small ones.

| Moment | Tree RSS |
|---|---|
| Ready (11.3 s) | 519 MB |
| Ready + 30 s (dictation warm-up) | 1.69 GB |
| After the 45 s idle release | ~350 MB |
| After a transcription (first / second) | 1.95 GB (3.9 s / 1.3 s) |
| After the release that follows | 343–396 MB |

Findings:

- **The dictation model loads by default, unprompted.** The log shows `Capture ASR backend selected: mlx-whisper` and the 1.6 GB model warming 30 s after boot, with no user action. It added about 1.2 GB. The code comment says this is deliberate ("BY DEFAULT", skipped under 4 GB free RAM).
- **It stays resident for the idle timeout, 900 s by default.** That is 15 minutes of ~1.2 GB for someone who never dictates. Here it unloaded about 60 s after the warm-up, as set by the 45 s override.
- **Unloading works.** Memory drops back to roughly the boot level, and the next transcription pays the reload (3.9 s, then 1.3 s warm).
- **The AudioSeal watermark warm-up** logged "skipped: checkpoint is not cached", so it adds nothing here.
- `/transcribe` in accurate mode returned 409 because `whisper-large-v3-mlx` is not installed; not measured.

Not yet measured:

| Metric | macOS arm64 | Windows | Linux |
|---|---|---|---|
| Startup steps | above | — | — |
| Warm-up cost with an ASR model installed | — | — | — |
| Idle `/api` requests per minute (needs the running UI) | — | — | — |
| Installer size | — | — | — |
