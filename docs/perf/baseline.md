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

## Backend (not yet measured)

Measuring needs the full Python environment (torch and the model stack), which
this pass did not have. Static findings from `backend/main.py`:

- Startup runs in steps reported by `/startup/progress`: `env_prefs`, `native_preload`, `ml_imports`, `api_routes`, `db_migrate`, `services_start`.
- `ml_imports` imports `torchaudio` (a code comment says 10–20 s cold) and `services.model_manager`. `api_routes` imports all 40+ routers eagerly.
- `preload_model()` starts at boot. Dictation ASR warms about 30 s later and the AudioSeal watermark about 35 s later.

To fill in the table, run on a clean data dir, per platform:

```bash
python -X importtime backend/main.py 2> importtime.log        # slowest imports
curl -s http://127.0.0.1:3900/startup/progress                # per-step timings, poll until ready
```

| Metric | macOS arm64 | Windows | Linux |
|---|---|---|---|
| Spawn to `/health` | — | — | — |
| Spawn to routes live | — | — | — |
| Idle RSS / VRAM after boot | — | — | — |
| Idle `/api` requests per minute | — | — | — |
| `uv sync` size and installer size | — | — | — |
