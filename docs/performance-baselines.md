# Performance baselines

Startup, idle and bundle-size numbers that the performance work in
[#3924](https://github.com/debpalash/VoiceStudio/issues/3924) is judged against.
Each change that claims a speed-up quotes a before/after from the same harness
on the same machine. Earlier one-off measurements (first-launch slowness,
per-import memory, model warm-up memory) are in [perf/baseline.md](perf/baseline.md).

## How to measure

`scripts/bench/` is offline and touches nothing of the user's: it downloads
nothing, loads no model, and runs every backend on a throwaway data dir with
the model caches, the env file and analytics redirected into it. Outbound HTTP
from the backend goes to a dead local proxy, the Electron app runs on a
throwaway profile with the updater off, and every non-local renderer request
is aborted.

```bash
bun install --frozen-lockfile && bun run --cwd electron build   # for bundle and app
.venv/bin/python scripts/bench/bench.py                          # everything, 5 runs
.venv/bin/python scripts/bench/bench.py --only backend,imports --runs 3
.venv/bin/python scripts/bench/bench.py --only app \
    --app /Applications/VoiceStudio.app/Contents/MacOS/VoiceStudio  # an installed build
node scripts/bench/bundle.mjs                                    # bundle sizes only
```

Results go to `bench-results/<time>.json` and `.md` (ignored by git). Every
timing is reported as median / p90 over N runs.

| Part | What it measures |
|---|---|
| `backend` | Spawn to the first HTTP answer and to `/health` 200, the `/startup/progress` step durations, then idle RSS of the process tree (median of samples) and idle CPU over 30 s, starting 30 s after ready so background reconcile has finished. One unmeasured boot creates the database; measured runs are relaunches, as for a returning user. |
| `imports` | One extra boot under `python -X importtime`: the slowest outermost imports, including those done by background startup work. |
| `bundle` | Raw and gzip size of each chunk in `electron/out/renderer`, what `index.html` loads at startup, the ten largest chunks. |
| `app` | Launch to first contentful paint and to the Home screen (backend ready, the backend already running), then main, renderer and GPU process CPU over 30 s at idle on Home (`app.getAppMetrics()`). The profile is seeded as "setup complete" once; measured launches reuse it. |

Not covered yet: Windows and Linux numbers, a first launch right after
install (see [perf/baseline.md](perf/baseline.md)), and inference.

## Baseline: 0.5.8-beta.3, macOS (2026-11-09)

Apple M2, 16 GB, macOS 27.0.1 (Darwin 27.0.1), Python 3.11.15, `main` at
`d40fd359c` (0.5.8-beta.3). Installed app: `/Applications/VoiceStudio.app`
0.5.8-beta.3. No models in the isolated caches. 5 runs each.

### Backend (dev `.venv`, repo backend)

| Metric | median / p90 |
|---|---|
| Spawn to first HTTP answer | 0.22 / 0.29 s |
| Spawn to `/health` ready | 1.50 / 1.67 s |
| &nbsp;&nbsp;`env_prefs` | 0.07 / 0.10 s |
| &nbsp;&nbsp;`ml_imports` | 0.53 / 0.61 s |
| &nbsp;&nbsp;`api_routes` | 0.52 / 0.53 s |
| &nbsp;&nbsp;`db_migrate` | 0.15 / 0.16 s |
| &nbsp;&nbsp;`services_start` | 0.02 / 0.02 s |
| Idle RSS, process tree | 457 / 457 MB |
| Idle CPU | 0.5 / 0.5 % |

On a fresh data dir (first boot of a new install's database) ready took
2.63 / 3.83 s.

Slowest imports during boot (cumulative, one run): `faster_whisper` 1071 ms,
`mlx_whisper` 864 ms, `torch` 627 ms, `alembic` 189 ms, `fastapi` 143 ms,
`mcp.server.fastmcp` 130 ms, `api.routers.system` 94 ms.

### Electron app

| Metric | unpackaged build (`main`) | installed 0.5.8-beta.3 |
|---|---|---|
| Launch to first contentful paint | 686 / 693 ms | 837 / 851 ms |
| Launch to Home shown | 713 / 766 ms | 881 / 921 ms |
| Idle CPU, main process | 0.1 / 0.5 % | 0.1 / 0.1 % |
| Idle CPU, renderer | 0.5 / 1.0 % | 0.2 / 0.3 % |
| Idle CPU, GPU process | 0.3 / 1.4 % | 0.1 / 0.1 % |
| Idle CPU, all Chromium processes | 0.9 / 2.9 % | 0.3 / 0.5 % |

The installed app is a packaged build (asar archive, signed); why it paints
about 150 ms later than the unpackaged build is not measured yet. Idle CPU here is a
fresh launch sitting on Home. A long-running installed instance sampled for
30 s (left on whatever page it was on, not launched by the harness) used
about 8.7 % of one core in total: renderer 3.2 %, GPU 1.4 %, main 1.2 %,
backend 1.4 % and the native desktop bridge 1.5 %.

### Renderer bundle (`electron/out/renderer`)

| | raw | gzip |
|---|---|---|
| Loaded by `index.html` (78 files) | 4076 KB | 970 KB |
| Entry chunk `index.js` | 1917 KB | 449 KB |
| All JS (272 chunks) | 22485 KB | 5766 KB |
| All CSS | 702 KB | 100 KB |

Largest chunks (gzip): `add-scalar-classes` 1120 KB (lazy), `index` 449 KB
(startup), `scalar-api-reference` 282 KB, `dash.all.min` 260 KB, `hls` 259 KB,
`workflows-page` 158 KB (all lazy), then locale catalogs of about 100 KB each
(lazy).

## Findings

1. **The startup bundle carried the whole first-run setup wizard.**
   `SetupGate` imported the wizard's settings panels (model library, preflight,
   model settings, mirrors, privacy, shortcuts) statically, so every launch
   parsed them although only a first run shows them. Fixed below.
2. **The native desktop bridge polls at 125 Hz while a shortcut is
   registered.** `native/desktop-bridge/src/shortcuts.rs` wakes every 8 ms to
   pump the OS event queue when a dictation shortcut is set (250 ms otherwise).
   That is the 1.5 % idle CPU measured above, all day. Blocking on the
   platform's message wait (with a timeout for commands) instead of sleeping
   would bring it near zero, but it touches key-release latency on all three
   OSes and needs native testing on each.
3. **Media players load at startup.** `@vidstack/react` and `wavesurfer.js`
   (about 290 KB raw, 74 KB gzip) are in the startup set because the app shell
   imports `NowPlayingDock` (waveform player), the context sidebar imports
   `VoicesSidebar`, and `dictation-demo` imports `AudioPreviewButton`. Loading
   the dock and those panels on demand would move the player out of startup.
4. **Background ASR probes import full ASR stacks into the API process.**
   With no ASR model installed, the availability checks still import
   `mlx_whisper` (0.86 s, +116 MB RSS) and `faster_whisper` (1.2 s, +50 MB) in
   the backend process, about a third of its idle RSS. They run after
   `/health` is ready, and they exist to catch broken native libraries
   (#692), so the fix is to probe in a short-lived child or cache the result
   per installed version, not to drop the import.

## Changes measured against this baseline

### Setup wizard loaded on demand

`SetupGate` now only decides whether setup is needed; the wizard is
`setup-wizard.tsx`, loaded with `React.lazy` when it is shown. A first run
shows the same loading screen for the moment the chunk takes to load. Guarded
by `setup-gate-lazy.test.tsx` (fails if the gate imports the wizard's panels
eagerly) and by the CI bundle budget below. The launch timings improved by
50–70 ms at the median, but individual runs overlap (before 628–697 ms, after
590–682 ms to first paint), so treat them as indicative; the size drop is exact.

| | before | after |
|---|---|---|
| Loaded by `index.html`, raw | 4076 KB | 3833 KB (−6.0 %) |
| Loaded by `index.html`, gzip | 970 KB | 920 KB (−5.2 %) |
| Entry chunk `index.js`, raw / gzip | 1917 / 449 KB | 1783 / 424 KB |
| Launch to first contentful paint (unpackaged) | 686 / 693 ms | 619 / 665 ms |
| Launch to Home shown (unpackaged) | 713 / 766 ms | 659 / 717 ms |

## Bundle budget in CI

The Linux leg of CI's desktop UI job runs `node scripts/bench/bundle.mjs --check`
on the app it already builds. The step (not the job) fails when the startup
set, the total JS or CSS, or the largest startup chunk grows more than 10 % in
gzip size over `scripts/bench/bundle-baseline.json`. If growth is intended,
refresh the file in the same PR with
`node scripts/bench/bundle.mjs --write-baseline` after
`bun run --cwd electron build`.
