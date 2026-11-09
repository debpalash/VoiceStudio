# Desktop UI E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On every PR, a CI job launches the real Electron app on macOS, Windows and Linux and runs one shared UI suite. A second PR runs every offline renderer smoke.

**Architecture:** Tests use Playwright `_electron` against the unpackaged build (`electron/out/main/index.js`) and a fake HTTP backend. The harness gives each test a fresh profile, and on every test it enforces three guards: no network leaks, no renderer errors, and no main-process errors. Part B adds a content-based smoke classifier, a runner, and an inventory test.

**Tech Stack:** `playwright` ^1.63 (root, no new deps), Electron from `electron/node_modules`, `node --test`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-17-desktop-ui-e2e-design.md`

## Global Constraints

- One expected behaviour on every OS. `process.platform` branches may only change *how the OS is driven* (executable path, `xvfb-run`), never an expected user-visible outcome.
- No fixed sleeps. Wait on locators or conditions. No test retries.
- No `--no-sandbox`, and no weakening of context isolation or CSP in app code.
- Every remote action is pinned to a commit SHA, copied from the existing `ci.yml` / `snap-store.yml` pins.
- No new dependencies. If one becomes unavoidable, regenerate `bun.lock` and confirm `bun install --frozen-lockfile` passes.
- No CJK text in tests. The profile path uses Latin non-ASCII: `VoiceStudio Tëst `.
- Launch env (from `native-watch-ui-smoke.mjs`): `ELECTRON_RENDERER_URL=''`, `OMNIVOICE_PORT=<fake>`, `VOICESTUDIO_SKIP_BACKEND=1`, `VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES=1`, `VOICESTUDIO_DISABLE_UPDATER=1`, `VOICESTUDIO_ELECTRON_PROXY_PORT=<free port>`.
- Allowed request targets: `app:`, `file:`, `data:`, `blob:`, `devtools:`, and http(s) to `127.0.0.1` / `localhost` / `[::1]` on any port. `STAR_COUNT_URL = 'https://api.github.com/repos/debpalash/VoiceStudio'` is answered by a stub and never reaches the network.
- Commits use the configured git identity only. No agent trailers or "Generated with" lines (CLAUDE.md, checked by `commit-identity`).
- Docs and changelog land in the same PR (CONTRIBUTING → Testing; `CHANGELOG.md` `## [Unreleased]` → `### CI`, one-liners).

## Review Focus

1. Backend unreachable at launch: the app shows its retry state and does not crash or throw. Test added in Task 4.
2. Unclean exit (process killed), then relaunch on the same profile: the app starts normally. This catches leftover Windows profile locks. Test added in Task 6.
3. Renderer reload: the bridge still works, with no `pageerror` and no duplicate-listener errors. Test added in Task 3.
4. A route requests a backend path the fake backend doesn't have: the test fails with that path named, instead of passing silently. Enforced in Task 5.

---

## PR 1: three-OS desktop gate (Part A)

### Task 1: Shared fixtures (routes list, configurable fake backend)

**Files:**
- Create: `electron/tests/fixtures/routes.mjs`
- Modify: `electron/tests/route-layout-smoke.mjs:5-~40` (import the list instead of declaring it)
- Modify: `electron/tests/fake-health-backend.mjs`
- Test: `electron/tests/fake-health-backend.test.mjs`

**Interfaces:**
- Produces: `export const ROUTES: readonly string[]`, the exact list currently in `route-layout-smoke.mjs`.
- Produces: `startHealthBackend({ fixtures = {} } = {}) → Promise<{ port: number, requests: Array<{ method: string, path: string, status: number, body: string }>, close(): Promise<void> }>`. `fixtures` is merged over the defaults; a value may be a function `(request, body) => json`. Existing callers that pass no argument keep working.

- [ ] **Step 1: Write failing tests** in `fake-health-backend.test.mjs`:
  - `defaults answer /health` → `{ status: 'ok', version: 'native-smoke' }`
  - `override replaces one fixture` → `fixtures: { '/api/settings/analytics': { available: true, prompted: false, opted_in: false } }` is served for that path
  - `unknown path is 404 and recorded` → `requests.at(-1)` equals `{ method: 'GET', path: '/nope', status: 404, body: '' }`
  - `POST body is recorded` → POST `'{"opted_in":false}'` to the analytics path, then `requests.at(-1).body === '{"opted_in":false}'`
- [ ] **Step 2: Run** `node --test electron/tests/fake-health-backend.test.mjs`. Expected: FAIL (no `requests`, no override).
- [ ] **Step 3: Implement** the override merge and the request log in `fake-health-backend.mjs`. Create `fixtures/routes.mjs` and switch `route-layout-smoke.mjs` to import it.
- [ ] **Step 4: Run** the test again; expect PASS. Run `node --check electron/tests/route-layout-smoke.mjs`; expect no output.
- [ ] **Step 5: Commit** `test(electron): share route list and make fake backend configurable`

### Task 2: Local-first allowlist

**Files:**
- Create: `electron/tests/local-first.mjs`
- Test: `electron/tests/local-first.test.mjs`

**Interfaces:**
- Produces: `isAllowedRequest(url: string): boolean`, `STAR_COUNT_URL: string`, `STAR_COUNT_STUB = { stargazers_count: 1234 }`.

- [ ] **Step 1: Write failing table test.**
  - Allowed: `app://-/index.html`, `file:///x`, `data:,x`, `blob:app://-/u`, `devtools://devtools/x`, `http://127.0.0.1:3900/health`, `http://localhost:49306/api`, `http://[::1]:1/`
  - Rejected: `https://api.github.com/repos/debpalash/VoiceStudio` (stubbed, not allowed), `https://us.i.posthog.com/e`, `https://huggingface.co/`, `http://127.0.0.1.evil.com/`, `http://localhost.evil.com/`, `ws://example.com`
- [ ] **Step 2: Run** `node --test electron/tests/local-first.test.mjs`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the function. Parse with `new URL`. Compare `hostname` exactly; no substring or prefix matching.
- [ ] **Step 4: Run**; expect PASS.
- [ ] **Step 5: Commit** `test(electron): local-first request allowlist`

### Task 3: Harness and launch test (cases 1, 6, 7 + reload)

**Files:**
- Create: `electron/tests/desktop-harness.mjs`
- Create: `electron/tests/desktop-ui.test.mjs`
- Modify: `electron/package.json` scripts: add `"test:desktop-ui": "node --test --test-concurrency=1 tests/desktop-ui.test.mjs"`

**Interfaces:**
- Consumes: `startHealthBackend` (Task 1), `isAllowedRequest`, `STAR_COUNT_URL`, `STAR_COUNT_STUB` (Task 2).
- Produces:
  - `electronExecutable(): string` (per-OS path under `electron/node_modules/electron/dist`, as in `native-media-picker-smoke.mjs`)
  - `makeProfile(): string` (`mkdtempSync(join(tmpdir(), 'VoiceStudio Tëst '))`)
  - `withApp(t: TestContext, opts: { profile?: string, fixtures?: object, seed?: boolean, backend?: 'up' | 'down' }, body: (s: Session) => Promise<void>): Promise<void>`
  - `Session = { app: ElectronApplication, page: Page, profile: string, backend: FakeBackend | null, relaunch(): Promise<Session> }`
  - `ARTIFACT_DIR = electron/test-results/<process.platform>`
- `withApp` contract:
  - `seed` defaults to `true`. When true, `context.addInitScript` sets `voicestudio.setup.complete.v1='1'` and `voicestudio.locale='en'`.
  - Starts `context.tracing` with screenshots and snapshots.
  - Installs the request guard twice: renderer via `context.route('**')`, main via `session.defaultSession.webRequest.onBeforeRequest` through `app.evaluate`. The guard aborts and records disallowed URLs, and fulfils `STAR_COUNT_URL` with `STAR_COUNT_STUB`. It then reloads the page once, so requests made before the hooks existed are seen.
  - Collects `pageerror` and console `error` events. `ALLOWED_CONSOLE_ERRORS` is an exported, commented regex list and starts empty.
  - After `body`, asserts zero violations, zero errors, and that `<profile>/main-process-errors.json` is missing or empty.
  - On throw: writes the trace zip, `final.png`, a copy of `app.getPath('logs')` and `main-process-errors.json` into `ARTIFACT_DIR/<test name>/`, then rethrows.
  - Always: closes the app, closes the backend, and removes the profile unless the caller passed one.

- [ ] **Step 1: Write failing tests** in `desktop-ui.test.mjs`:
  - `launch: window, title and bridge match the real OS`. The title matches `/VoiceStudio/`. `typeof window.voicestudio.files.saveData === 'function'`. `window.voicestudio.app.platform === process.platform`.
  - `reload keeps the bridge working`. After `page.reload()`, the same bridge assertions pass (guards are checked by `withApp`).
- [ ] **Step 2: Build and run** `bun run --cwd electron build && xvfb-run -a bun run --cwd electron test:desktop-ui` (drop `xvfb-run` off Linux). Expected: FAIL (harness missing).
- [ ] **Step 3: Implement** `desktop-harness.mjs` to the contract above.
- [ ] **Step 4: Run**; expect PASS. Then sabotage: temporarily add `fetch('https://example.com')` to the renderer entry, rebuild, and confirm the run FAILS naming that URL. Revert.
- [ ] **Step 5: Commit** `test(electron): desktop UI harness with local-first and error guards`

### Task 4: First run, consent, backend down (cases 2, 2b; Review Focus 1)

**Files:** Modify `electron/tests/desktop-ui.test.mjs`

- [ ] **Step 1: Write tests:**
  - `first run: setup gate completes to home` (`seed: false`). The setup gate shows the `heading` "VoiceStudio". Complete it with the controls `setup-gate-smoke.mjs:97-107` drives, adapted to `models_ready: true`. Assert `location.hash` is `#/` and that `voicestudio.setup.complete.v1 === '1'`.
  - `consent: declining analytics sends nothing`. Use `fixtures: { '/api/settings/analytics': { available: true, prompted: false, opted_in: false } }`. Assert the buttons "Yes, share anonymous stats" and "No thanks" are both visible. Click "No thanks". Assert the backend recorded a POST to `/api/settings/analytics` whose body has `opted_in: false`, and that the prompt is gone. `withApp`'s guard covers the "sends nothing" part.
  - `backend down: app shows retry, no crash` (`backend: 'down'`, meaning a free port with nothing listening). Wait for the retry button (en `backend.retry` text); no `pageerror`.
- [ ] **Step 2: Run** `test:desktop-ui` and confirm the new tests fail for the right reason before adjusting the selectors. Then make them pass.
- [ ] **Step 3: Commit** `test(electron): first-run, consent and backend-down desktop cases`

### Task 5: Every route renders (case 3; Review Focus 4)

**Files:** Modify `electron/tests/desktop-ui.test.mjs`, and `fake-health-backend.mjs` (defaults) only where a route needs a fixture.

- [ ] **Step 1: Write test** `routes: every route renders`. For each `ROUTES` entry:
  - set `location.hash = '#' + route`;
  - wait for the `main` landmark to be visible;
  - assert no element matches the app's error-boundary selector (find it in `electron/src/renderer/src`; reuse the one `route-layout-smoke.mjs` checks if it has one).
  - After the loop, assert `backend.requests.filter((r) => r.status === 404)` is empty. The failure message lists the paths.
- [ ] **Step 2: Run**. For each 404 path reported, add the smallest valid default fixture, matching the shape the renderer reads. Re-run until it passes. Never drop a route.
- [ ] **Step 3: Commit** `test(electron): every route renders in the desktop app`

### Task 6: Settings persistence and profile paths (case 4; Review Focus 2)

**Files:** Modify `electron/tests/desktop-ui.test.mjs`

- [ ] **Step 1: Write tests:**
  - `settings: theme and language survive restart`:
    - Open `#/settings/appearance`. In the "Dark" group, click the "Studio" button and assert `aria-pressed="true"` (as `theme-picker-smoke.mjs:31-44` does).
    - Change the UI language to German in `#/settings/general`.
    - Call `relaunch()` on the same profile. Assert `voicestudio.theme.v2` still records Studio, the button is still pressed, and `voicestudio.locale === 'de'`.
    - Assert `app.getPath('userData')` and `app.getPath('sessionData')` both resolve inside `profile`, comparing real paths case-insensitively on win32.
  - `unclean exit: relaunch on same profile starts normally`. Kill the Electron process (`app.process().kill('SIGKILL')`), then `relaunch()`. Assert the window opens and the bridge assertions from Task 3 pass.
- [ ] **Step 2: Run**; expect PASS after any selector fixes.
- [ ] **Step 3: Commit** `test(electron): settings persistence and unclean-exit relaunch`

### Task 7: Dialog IPC round trip (case 5)

**Files:** Modify `electron/tests/desktop-ui.test.mjs`

- [ ] **Step 1: Write test** `dialogs: open and save round-trip through main`:
  - **Open:** stub `showOpenDialog` → `{ canceled: false, filePaths: [<profile>/models] }`. Call `files.authorizeModelsDirectory()` and assert the returned path equals it. Stub `{ canceled: true, filePaths: [] }` and assert the call returns `null`.
  - **Save:** stub `showSaveDialog` → `{ canceled: false, filePath: <profile>/out/test.json }`. Call `files.saveData` with a small payload; take the argument shape from `ipc.ts` `filesSaveData`. Assert the file exists and its content matches. Then stub a cancel and assert the call returns its cancel result.
- [ ] **Step 2: Run**; expect PASS.
- [ ] **Step 3: Commit** `test(electron): native dialog IPC round trip`

### Task 8: CI job, docs, changelog

**Files:**
- Modify: `.github/workflows/ci.yml` (new job `electron-ui`)
- Modify: `.github/CONTRIBUTING.md` (`## Testing` code block)
- Modify: `CHANGELOG.md` (`## [Unreleased]` → `### CI`)

- [ ] **Step 1: Add the job.**
  - `name: Desktop UI (${{ matrix.label }})`; matrix `macos-15` / `windows-2022` / `ubuntu-24.04` with labels `macOS` / `Windows` / `Linux`; `fail-fast: false`; `timeout-minutes: 20`; `defaults.run.shell: bash`.
  - Steps:
    1. checkout, Node 22, Bun `1.4.2`, and the bun cache (copy the pinned SHAs from the `test` job);
    2. `bun install --frozen-lockfile`;
    3. `bun run --cwd electron build`;
    4. Linux only: `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`;
    5. run: Linux `xvfb-run -a bun run --cwd electron test:desktop-ui`, other OSes the same command without `xvfb-run`;
    6. `if: failure()`: upload `electron/test-results/`, named `desktop-ui-${{ matrix.label }}`, with `actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2`.
- [ ] **Step 2: Docs.** In CONTRIBUTING `## Testing`, add `bun run --cwd electron build && bun run --cwd electron test:desktop-ui`, with a one-line note: on Linux without a display, prefix the command with `xvfb-run -a`.
- [ ] **Step 3: Changelog.** Under `### CI`, add the one-liner: `Desktop UI tests now launch the real app on macOS, Windows and Linux for every pull request.`
- [ ] **Step 4: Local checks:**
  - `uv run pytest tests/test_changelog_style.py tests/test_changelog_parse.py -q`, plus `uv run pytest tests -q -k "workflow or pinned"` for the workflow and action-pin guards;
  - `bun run check:electron`;
  - `node --test electron/tests/*.test.mjs`.
- [ ] **Step 5: Commit** `ci: run desktop UI tests on macOS, Windows and Linux`
- [ ] **Step 6: Push and open PR 1.** Wait for every check, not just `electron-ui`. Harvest CodeRabbit and Greptile comments (`gh api repos/debpalash/VoiceStudio/pulls/<N>/comments`) and fix real findings on the branch. If a leg fails, read the uploaded artifacts before changing anything. The PR body notes: "Add `Desktop UI (*)` to required checks after two weeks green on main (owner)."

---

## PR 2: run every offline renderer smoke (Part B)

Branch `test/renderer-smoke-runner`, based on PR 1's branch; rebase onto `main` after PR 1 merges.

### Task 9: Classifier, inventory test, runner

**Files:**
- Create: `electron/tests/smoke-inventory.mjs`
- Create: `electron/tests/smoke-inventory.test.mjs`
- Create: `electron/tests/run-renderer-smokes.mjs`

**Interfaces:**
- Produces: `classifySmoke(file: string, source: string): 'renderer' | 'real-backend' | 'electron' | 'other' | 'unknown'`. Rules, checked in order:
  1. name matches `-real-smoke.` → `real-backend`;
  2. source imports `_electron` → `electron`;
  3. source imports `chromium` from `playwright` → `renderer`;
  4. file is in `OTHER_SMOKES` → `other`;
  5. otherwise → `unknown`.
- Produces: `OTHER_SMOKES: Record<string, string>` (file → one-line reason). Seed it with `renderer-smoke.vite.config.mjs`, `runtime-install-smoke.mjs`, `native-output-smoke.ts`, `native-shortcut-smoke.ts`, `native-watch-folder-smoke.ts`, `native-wayland-output-smoke.ts`.
- Produces: `listSmokes(dir: string): Array<{ file: string, kind: string }>`.
- Produces: `SMOKE_ENV: Record<string, Record<string, string>>` in the runner, for per-smoke env additions.

- [ ] **Step 1: Write failing tests:**
  - classifier table cases: `convert-live-smoke.mjs` with chromium source → `renderer`; `gallery-trim-real-smoke.mjs` → `real-backend`; `native-capture-smoke.mjs` with `_electron` → `electron`; an unknown file → `unknown`;
  - `every smoke in electron/tests is classified`: no `unknown` entries, and the message names any file that is;
  - `renderer count is 42` (as of 2026-10-17; update the number on purpose when smokes are added).
- [ ] **Step 2: Run** `node --test electron/tests/smoke-inventory.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement** the classifier, and a runner that runs the renderer smokes one after another with `node`, merging `SMOKE_ENV[file]` over `process.env`. It prints `PASS|FAIL  <ms>  <file>` per smoke and exits 1 if any smoke failed.
- [ ] **Step 4: Run** the tests; expect PASS.
- [ ] **Step 5: Commit** `test(electron): classify smokes and add renderer smoke runner`

### Task 10: Triage and switch CI to the runner

**Files:**
- Modify: failing renderer smokes, or the app code they expose as broken
- Modify: `.github/workflows/ci.yml` (the "Electron renderer workflow smokes" step)
- Modify: `.github/CONTRIBUTING.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run all renderer smokes** locally the way CI does: start `smoke:server` with the step's env, then `node electron/tests/run-renderer-smokes.mjs`. Record the failures.
- [ ] **Step 2: Triage each failure.**
  - Stale test (the UI changed on purpose): update the test.
  - Product bug: fix it, using superpowers:systematic-debugging.
  - The feature no longer exists: delete the smoke, saying so in the commit message.
  - Needs a live backend: rename it to `-real-smoke` with `git mv`.
  - One commit per smoke or per root cause.
- [ ] **Step 3: Switch CI.** Replace the four `node electron/tests/…` lines with `node electron/tests/run-renderer-smokes.mjs`. In the `test` job, add `node --test electron/tests/smoke-inventory.test.mjs` to an existing node-test step.
- [ ] **Step 4: Docs and changelog.** CONTRIBUTING `## Testing` gets the runner command. `### CI` gets the one-liner `All offline UI smoke tests now run on every pull request.`
- [ ] **Step 5: Commit, push, open PR 2.** Same gate as Task 8 Step 6.
