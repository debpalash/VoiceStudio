# Desktop UI end-to-end tests on macOS, Windows and Linux

Status: draft for owner review · 2026-10-17 · branch `test/electron-ui-e2e`

## Why

CLAUDE.md requires default features to behave the same on macOS, Windows and
Linux, and calls a divergence a P0. Today nothing checks that on a pull request
at the level of the real desktop window:

| Layer | Runner | Count | Runs in CI |
|---|---|---|---|
| Renderer smokes: Vite-served UI in Chromium with a mocked `window.voicestudio` bridge | Playwright `chromium` | ~70 | 4, Linux only (`ci.yml` "Electron renderer workflow smokes") |
| Real Electron app, unpackaged (`electron/out/main/index.js`) | Playwright `_electron` | ~11 `native-*-smoke` | none |
| Packaged installer startup + setup | Playwright `_electron` (`packaged-smoke.mjs`) | 1 | macOS, Windows, Linux, but only on manual rehearsal, release tags and Microsoft Store runs |

So preload, IPC, main-process, user-data-path and window regressions on macOS
or Windows surface at release time, and roughly 66 renderer smokes never run.
The renderer smokes also fake `platform: 'win32'`, so they never see the real OS.

## Goals

1. **Per-PR desktop gate on all three OSes.** A new `ci.yml` job launches the
   real (unpackaged) Electron app with Playwright `_electron` on `macos-15`,
   `windows-2022` and `ubuntu-24.04`, and runs one shared, OS-agnostic suite.
2. **Run every renderer smoke that can run offline**, once on Linux, chosen by
   a rule rather than a hardcoded list, so new smokes cannot silently go unrun.
3. **Debuggable failures**: a Playwright trace, a screenshot and the main-process
   log are uploaded per failing OS.

## Non-goals

- Packaged-installer testing. `packaged-smoke.mjs` stays on rehearsal/release/store.
- Real inference, Python backend, model downloads, GPU. The suite uses a fake backend.
- The Rust native helper (dictation insertion, capture, global shortcuts). It is
  only built at packaging time; tests that need it stay where they are.
- Real OS dialogs, mic permission prompts, Gatekeeper/SmartScreen. Dialogs are
  stubbed in the main process; the rest stays manual.
- Pixel/screenshot diffing. Fonts and DPI differ by OS; screenshots are evidence
  on failure, not assertions.
- Windows ARM64 and Intel macOS legs (cost; packaging rehearsal covers them).

## Anti-goals (would be a failure even if green)

- **Flaky gate.** A test that sometimes fails trains people to re-run and ignore
  it. No fixed sleeps; wait on conditions. Retries are not a fix.
- **Per-OS expectations for default behaviour.** The suite asserts one behaviour
  for all three OSes. An `if (process.platform …)` that changes an *expected
  user-visible outcome* is forbidden; it may only adapt *how* the test drives
  the OS (executable path, `xvfb-run`). A real divergence is a product bug.
- **Weakening the app to make it testable.** No disabling the Chromium sandbox,
  context isolation or CSP in app code. Test hooks go through env vars the main
  process already honours (below) or new ones that are inert unless set.
- **Network leaks.** The suite must not reach the internet.

## Constraints

- Reuse what is pinned: root `playwright` (^1.63), Electron from
  `electron/node_modules`, `node --test` as the runner. Do not add
  `@playwright/test` unless the builder shows the library cannot do something
  required (then: regenerate `bun.lock`, verify `bun install --frozen-lockfile`).
- Every remote action is pinned to a commit SHA (CONTRIBUTING → CI supply chain).
- Launch pattern already proven in `native-watch-ui-smoke.mjs`:
  `electron.launch({ executablePath, args: [out/main/index.js, --user-data-dir=<tmp>],
  env: { ELECTRON_RENDERER_URL: '', OMNIVOICE_PORT: <fake>, VOICESTUDIO_SKIP_BACKEND: '1',
  VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES: '1' } })`, with `startHealthBackend()`
  from `fake-health-backend.mjs`. Also set `VOICESTUDIO_DISABLE_UPDATER=1`.
- Fresh temp `--user-data-dir` per test; removed in `finally`.
- Docs-sync: `.github/CONTRIBUTING.md` (quality gates / running tests) is
  updated in the same PR.
- Keep-main-green: the PR must pass the full existing CI matrix plus the new job.

## Design

### Part A: shared desktop suite (`electron-ui` job, 3 OSes)

New file `electron/tests/desktop-ui.test.mjs` (node:test), plus a small helper
`electron/tests/desktop-harness.mjs` that owns: resolving the Electron
executable per OS, starting the fake backend, launching with a temp profile,
starting `context.tracing` (screenshots + snapshots), collecting `pageerror`,
console errors and requests, and on failure saving trace, screenshot and the
app's main log into `electron/test-results/<os>/`.

Facts the cases rely on (checked in code):

- The renderer is served from the app's custom protocol (`electron/src/main/protocol.ts`)
  and routes with TanStack `createHashHistory` (`electron/src/renderer/src/router.tsx`),
  so a route is reached with `location.hash = '#' + path`.
- First-run state is renderer `localStorage`: `voicestudio.setup.complete.v1`
  (`lib/setup-progress.ts`) and `voicestudio.locale`. Tests other than case 2
  seed both with `context.addInitScript`, as the renderer smokes do.
- The fake backend answers `/api/settings/analytics` with `prompted: true`, which
  suppresses the consent prompt; case 2b overrides it.
- Save dialogs are reached through `window.voicestudio.files.saveData` /
  `saveAudio` (`ipc.ts` `filesSaveData` / `filesSaveAudio`).
- The star badge fetch is made by the renderer (`components/app-shell/github-star.tsx`).
- Main-process errors are journaled to `<userData>/main-process-errors.json`
  (`index.ts`); logs live in `app.getPath('logs')`.
- `test-results/` is already gitignored.

The temp profile path contains a space and non-ASCII Latin characters
(e.g. `VoiceStudio Tëst <random>`), so Windows and macOS path-encoding bugs in
user-data handling show up. CJK text stays out of tests (localization rule).

Test cases, identical expectations on every OS:

1. **Launch and bridge**: first window opens; title is VoiceStudio; the preload
   bridge `window.voicestudio` exposes its namespaces; `window.voicestudio.app.platform`
   equals the real `process.platform` (no fake).
2. **First run**: with a fresh profile and nothing seeded, the setup gate shows.
   Completing it through the UI lands on the home route.
   **2b. Analytics consent**: with the fixture overridden to `prompted: false`, the
   consent prompt shows two buttons. Declining keeps the app usable and sends no
   analytics request; case 6's guard enforces the second part.
3. **Every route renders**: move the route list out of `route-layout-smoke.mjs`
   into `electron/tests/fixtures/routes.mjs`, and import it from both. For each
   route, set the hash and wait for the route's main region. There must be no
   error boundary and no `pageerror`. If a route requests a backend path the fake
   backend lacks (a 404), add a minimal fixture; never skip the route.
4. **Settings survive restart**: change theme and language through Settings,
   close the app, relaunch on the same profile, assert both persisted, and assert
   every file the app wrote is under the given `--user-data-dir` (nothing in the
   real home profile). No main-process-owned setting is reachable while the
   backend is skipped (`runtime-preferences.json` is only writable in
   `setup_required`), and `packaged-smoke.mjs` already covers that path.
5. **Dialog IPC round trip**: stub `dialog.showOpenDialog` / `showSaveDialog` via
   `app.evaluate`, as `native-media-picker-smoke.mjs` does.
   - Drive both through the preload bridge, as that smoke does: open with
     `files.authorizeModelsDirectory`, save with `files.saveData`. The
     OS-specific risk is in IPC and the main process. The UI controls are
     already covered by case 3 and the renderer smokes.
   - Assert the stubbed path round-trips, the saved file exists on disk, and a
     cancelled dialog returns the cancel value.
6. **Local-first guard**: across the whole run, every request goes to the
   `app:` protocol, `file:`, `devtools:`/`data:`/`blob:`, or a loopback host
   (`127.0.0.1`, `localhost`, `[::1]`) on any port. That covers the fake
   backend and the app's backend proxy on `VOICESTUDIO_ELECTRON_PROXY_PORT`.
   Any other host fails the test.
   - Observe requests in two places: renderer requests with `context.route('**')`,
     installed before the first navigation, and main-process requests with
     `session.defaultSession.webRequest` through `app.evaluate`.
   - Stub the star-count request with a canned count; `github.com` is never
     allowlisted wholesale.
   - The builder must close the race where requests fire before the hooks are
     installed, for example by asserting on the full request list after a reload.
7. **No unexpected errors**: zero `pageerror`, zero console `error` entries
   (except an explicit, commented allowlist in the harness), and no new entries
   in `main-process-errors.json`.

Fake backend: give `startHealthBackend()` an optional per-test fixture override,
and add fixtures only as these tests need them. Keep it compatible with the
existing native smokes.

### Part B: all offline renderer smokes (Linux, existing job)

Replace the four hardcoded `node electron/tests/…` lines in the
"Electron renderer workflow smokes" step with
`node electron/tests/run-renderer-smokes.mjs`, which:

- discovers `electron/tests/*smoke*` files and classifies each by content, not
  name, because names like `convert-live-smoke` are mocked and runnable:
  - imports Playwright `chromium` and is not `*-real-smoke` → **renderer**
    (runs here; 42 today);
  - `*-real-smoke` → **real-backend**;
  - imports `_electron` → **electron** (native/packaged; Part A or release paths);
  - anything else must appear in an explicit, commented list in the runner;
- runs the renderer smokes one after another against the `smoke:server` the CI
  step already starts, prints a pass/fail table, and exits non-zero on any
  failure.

A `node --test` check (`electron/tests/smoke-inventory.test.mjs`) fails if any
`*-smoke.*` file in `electron/tests` matches no category, so a new smoke is
either run by CI or consciously classified.

Triage: smokes that fail today are fixed. A smoke is deleted only if the
feature it covers no longer exists, with that stated in the commit message.
Some may need env vars beyond the four the CI
step exports today (`OMNIVOICE_PORT`, `VOICESTUDIO_UI_URL`,
`PLAYWRIGHT_CHANNEL`, `HF_HUB_*`). The runner reads any extra a smoke needs
from a small per-file table in the runner. A smoke that needs a live backend is
reclassified, not hacked.

Delivery: Part B ships as a second PR after Part A. It is the same plan, on a
branch based on Part A's branch. A large triage then cannot hold up the
three-OS gate.

### CI job shape

```
electron-ui:
  name: Desktop UI (${{ matrix.label }})
  strategy: { fail-fast: false, matrix: macos-15 / windows-2022 / ubuntu-24.04 }
  timeout-minutes: 20
  steps: checkout → setup-node 22 → setup-bun → bun cache →
         bun install --frozen-lockfile → bun run --cwd electron build →
         (Linux: xvfb-run -a) node --test electron/tests/desktop-ui.test.mjs →
         on failure: upload electron/test-results/ (pinned upload-artifact)
```

No Python, no `uv`, no native helper build. Runs on every PR and push to `main`
(no path filter, matching the rest of `ci.yml`).

Linux runner: Ubuntu 24.04 restricts unprivileged user namespaces, which can
stop Electron's sandbox from starting. The job runs
`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` before launch:
it is cheap, idempotent and avoids a confusing first failure. Never use
`--no-sandbox` in app code. `xvfb-run` ships on GitHub's Ubuntu images; the
builder confirms this on the first run and installs `xvfb` only if it is
missing. macOS and Windows run Electron from `node_modules`. That copy has no
quarantine attribute, so Gatekeeper and SmartScreen do not apply.

Failure artifacts: `electron/test-results/<os>/` holds the trace zip, a final
screenshot, a copy of `app.getPath('logs')` and `main-process-errors.json`. The
directory is already gitignored.

## Decided by the owner

- Automated UI tests on macOS, Windows and Linux (this request).
- Plan + spec on a new branch, separate from the Microsoft Store work.

## Builder's calls (marked so the owner can veto)

- Unpackaged app for the per-PR gate; packaged app stays on release paths.
- `playwright` library + `node --test`, no new dependency.
- No path filter; the job runs on every PR.
- New job is not added to branch-protection required checks until it has been
  green on `main` for two weeks; then the owner adds it.
- Renderer smokes run on Linux only, because they test DOM behaviour against a
  mocked bridge, which does not change by OS; OS behaviour is Part A's job.
- Case 2b (analytics consent decline) and the odd-character profile path are
  additions beyond the original proposal. Both are cheap, and both guard
  hard rules in CLAUDE.md: local-first consent and cross-platform parity.
- Case 4 uses renderer-persisted settings plus a "nothing written outside
  userData" check, because no main-owned setting is reachable without a backend.
- Part B is a separate, follow-on PR.
- The Linux `sysctl` step runs upfront, not only after a failure.

## Done when

- `electron-ui` is green on all three OSes on the PR, and the full existing CI is green.
- Deliberately breaking the preload bridge, a route, or settings persistence makes
  the job fail on every OS (checked once locally on Linux, by reasoning for the others).
- Every offline renderer smoke runs in CI, and the inventory test guards it.
- CONTRIBUTING documents how to run both suites locally.
