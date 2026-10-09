# Performance baseline

Reference numbers for the "feels bloated and slow" work. Each later change is
judged against this table. Measured on `main` at `06c6e077` (0.5.7).

## Renderer (measured)

Reproduce: `node scripts/perf/renderer-baseline.mjs` (builds the web renderer to a temp dir).

| Metric | Value |
|---|---|
| Files `index.html` loads before first paint | 69 |
| Eager size | 1,858 KB raw / 559 KB gzip |
| Entry chunk | 820 KB raw / 253 KB gzip |
| Eager CSS | 255 KB raw / 36 KB gzip |
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

- `media-player` (vidstack, 153 KB) and `waveform-player` (44 KB) load before first paint.
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

- **The first launch right after install took 82 s** (`services_start` alone 74 s), against 14 s once Python had written its bytecode cache. The desktop setup runs `uv sync --frozen --no-dev` without `--compile-bytecode` ([runtime-project.ts:648](../../electron/src/main/runtime-project.ts)), so a fresh install probably pays this on its first launch. Not yet confirmed on a packaged build.
- **`services_start` is mostly one function.** `reconcile_active_profile()` takes about 8 of the 12.6 s on a relaunch. Within it, `engines/omnivoice_subprocess` `is_available()` imports the whole `omnivoice/models/omnivoice.py` module (6.6 s) just to answer "is it installed". Translation-engine and ASR availability probes add about 1.7 s and 1.5 s.
- **Boot imports** `torch` (1.4 s), `torchaudio` (1.5 s), `sklearn` (2.2 s), `faster_whisper` (1.0 s), `transformers` (0.8 s) and `spacy` (0.8 s).
- **Not imported at boot:** `pyannote.audio` (27.5 s to import), `litellm` (22.8 s), `lightning` (8.9 s), `gradio` (3.7 s). Those are lazy already, so moving them to extras saves install size, not startup.
- **Idle:** about 650–700 MB RSS and 2–3 % CPU with no model loaded. Model warm-ups were not exercised because no ASR model was installed.
- **Install size:** `.venv` is 1.8 GB. Largest: `torch` 345 MB, `mlx` 207 MB, `llvmlite` 130 MB, `litellm` 87 MB, `gradio` 80 MB, `onnxruntime` 77 MB.

Not yet measured:

| Metric | macOS arm64 | Windows | Linux |
|---|---|---|---|
| Startup steps | above | — | — |
| Warm-up cost with an ASR model installed | — | — | — |
| Idle `/api` requests per minute (needs the running UI) | — | — | — |
| Installer size | — | — | — |
