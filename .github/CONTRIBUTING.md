# Contributing to VoiceStudio

Thanks for your interest in improving VoiceStudio! This guide covers everything you need to get started.

## Quick Links

| | |
|---|---|
| 💬 **Chat** | [Discord](https://discord.gg/bzQavDfVV9) |
| 🐛 **Bugs** | [GitHub Issues](https://github.com/debpalash/VoiceStudio/issues) |
| 🏷️ **Good First Issues** | [Filtered list](https://github.com/debpalash/VoiceStudio/labels/good%20first%20issue) |
| 📋 **Roadmap** | [docs/ROADMAP.md](../docs/ROADMAP.md) |

---

> **Before your first pull request:** unless exempt under the
> [CLA checker’s policy](#contribution-licensing), sign the
> [Contributor License Agreement](CLA-1.0.md) once. GitHub cannot stop you opening a pull request first, so it is checked at
> merge instead: an unsigned pull request cannot merge unless a maintainer applies
> the `cla-override` label after manual review. You can sign now on the
> [signing issue (#2586)](https://github.com/debpalash/VoiceStudio/issues/2586) and
> your signature covers your future contributions. Every other non-exempt human commit
> author and co-author must also sign before the check passes. Details are in
> [Contribution licensing](#contribution-licensing).

---

## Adding a TTS or ASR engine

New engines are hired for a **named job**, not added to a list — the bar, the current job map,
and the out-of-tree path are in [docs/engine-acceptance.md](../docs/engine-acceptance.md).
Read it before opening a proposal; the licence check in particular ends most of them.

## Development Setup

### Prerequisites

- [Git](https://git-scm.com/)
- `curl` (used by the Bun / uv / rustup install one-liners on macOS and Linux)
- [Bun](https://bun.sh/) (frontend package manager)
- [uv](https://docs.astral.sh/uv/) (Python environment manager)
- [ffmpeg](https://ffmpeg.org/) (audio/video processing)
- [Rust / Cargo](https://rustup.rs/) (`native/desktop-bridge` and its imported Rust modules)
- Python 3.11+ (`requires-python` in `pyproject.toml`; managed automatically by `uv`)

Linux desktop development needs the native helper libraries. On Debian or
Ubuntu, install the same packages used by CI:

```bash
sudo apt-get update
sudo apt-get install -y \
  libasound2-dev libxdo-dev libxtst-dev libx11-dev libxkbcommon-dev \
  libwayland-dev libssl-dev pkg-config build-essential curl
```

See the [Linux source-build guide](../docs/install/linux.md#building-from-source)
for Fedora and Arch packages.

### Clone & Run

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun run dev
```

This installs the source dependencies (skipping local API setup for an external
backend or Intel Mac) and launches Electron with hot reload. The supervisor
manages backend startup; do not launch a second backend. See [Electron
setup](../electron/README.md).

For screenshots, use the maintained Electron capture script described in
[README media](../docs/media/electron/README.md). To share a runnable app, use
the release installers or `bun run dist`; source users should clone the
repository with its lockfiles and follow the setup commands above.

CPU-only Linux/Windows hosts automatically select CPU wheels; see
[CPU setup and overrides](../electron/README.md#running-without-a-gpu).
When regenerating `uv.lock` for compute variants, preserve unrelated package
versions: NumPy remains 2.0.2 on Python 3.12 Linux/Windows and 2.2.6 elsewhere.
Validate both frozen CPU/CUDA exports with `tests/test_cpu_install.py`; a NumPy
upgrade needs its own compatibility review.

```bash
bun run build       # build Electron
bun run start       # launch the built Electron app
bun run dist        # package locally without publishing
bun run smoke-test  # packaged startup, first-run consent, and native bridge
bun run smoke-test -- --install  # also install and start the managed backend
bun run dev:web     # maintained Electron renderer in a browser + backend
```

`bun run dev:web` starts both services:

| Service | URL | What it does |
|---------|-----|---|
| **Backend** | `localhost:3900` | FastAPI server — TTS, ASR, diarization, dubbing pipeline |
| **Frontend** | `localhost:3901` | Electron renderer served by Vite in a browser |

`bun run dev` keeps one desktop dev instance per checkout: starting it again
stops the earlier dev app and its dev server (only this checkout's, never an
installed VoiceStudio or another project) instead of failing with "Port 3902 is
already in use". To run two side by side, start the second one with
`VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES=1` plus its own ports, for example
`VOICESTUDIO_UI_PORT=3912 VOICESTUDIO_ELECTRON_PROXY_PORT=3913`.

On macOS, development launches coordinate bundle preparation and retain each
active dev server’s branded Electron bundle across main-process restarts.
Unused bundles are removed on later launches; cleanup failures do not prevent
a valid cached bundle from starting. Native OS locks release automatically when
all holders exit, including after a crash.

Product builds read the app name, application ID, and the backend/web/Electron
development ports from `config/app.json`. Change that manifest instead of
copying new defaults into launchers or runtime modules.

The backend runs through `scripts/dev-backend.mjs` (the `dev:api` script): the
uvicorn command is unchanged, but if the backend **dies** (OOM kill, hard
crash), the wrapper prints a boxed exit banner with the exit code/signal and
the last 20 lines of `omnivoice.log` before the dev stack shuts down — so the
cause doesn't scroll away with the terminal. The same death is also reported
as a crash notice in the UI the next time the backend starts (see
[docs/install/troubleshooting.md §14c](../docs/install/troubleshooting.md)).

### Retired desktop (Tauri)

Tauri is sunset after v0.5.3 and receives no further development or backports.
Use Electron for desktop contributions and reproduce desktop bugs there.
Existing users should follow the [migration guide](../docs/electron-migration.md).
The Tauri shell and its legacy UI entry points have been removed. Shared
modules and native helpers used by Electron remain maintained.

---

## Project Structure

```
VoiceStudio/
├── backend/                 # Python FastAPI server
│   ├── api/                 # Route handlers
│   ├── core/                # Config, prefs, constants
│   ├── engines/             # Engines in their own module (lazy-registered)
│   └── services/            # TTS engines, ASR, dubbing, audio DSP
│       └── tts_backend.py   # ← Multi-engine TTS registry
├── electron/                # The only desktop and web UI
│   └── src/
│       ├── main/            # Electron main process (IPC, updater, runtime)
│       ├── preload/         # Narrow renderer bridges
│       ├── renderer/src/    # App shell, features, shadcn ui/, i18n catalog
│       └── shared/          # Pages, components, hooks, Zustand store/, i18n
├── native/                  # Rust desktop helper used by Electron
├── deploy/                  # Dockerfile, compose, install worker
├── docs/                    # User, install, release and design docs
├── scripts/                 # Build, release and CI helper scripts
└── tests/                   # Repo-wide pytest suite (CI: tests/ + backend/tests/)
```

---

## How to Contribute

### Bug Reports

Open an [issue](https://github.com/debpalash/VoiceStudio/issues/new) with:

1. **What happened** vs **what you expected**
2. **Steps to reproduce**
3. **OS, GPU, and Python version** (find in Settings → Logs)
4. **Error logs** (Settings → Logs → copy relevant lines)

### Pull Requests

1. **Fork** the repo and create a branch from `main`
2. **Keep PRs focused** — one feature or fix per PR
3. **Run tests** before pushing:
   ```bash
   # Backend tests (the same two runs as CI; -n auto runs tests/ in parallel,
   # optional locally, it is about 4x faster on 4 cores)
   uv run pytest tests/ -q -n auto
   uv run pytest backend/tests/ -q

   # Frontend build check
   bun run check:electron
   ```
4. **Write a clear PR title** — it becomes the squash-merge commit message
5. **Add a changelog fragment** if users will notice the change: one line in `changelog.d/<PR#>.<type>.md` (see [`changelog.d/README.md`](../changelog.d/README.md)). Don't edit `CHANGELOG.md` directly, so your PR never conflicts with others
5. **Don't include** local machine stats, file paths, or private system info in PR descriptions

### Adding a New TTS Engine

VoiceStudio's TTS backend is a plugin registry. Adding a new engine takes ~50 lines:

1. Open `backend/services/tts_backend.py`
2. Create a class extending `TTSBackend`:

```python
class MyEngineBackend(TTSBackend):
    id = "my-engine"
    display_name = "My Engine (description)"

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import my_engine  # noqa: F401
            return True, "ready"
        except ImportError:
            return False, "my_engine not installed. pip install my-engine"

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def supported_languages(self) -> list[str]:
        return ["en", "zh"]

    def generate(self, text: str, **kw) -> torch.Tensor:
        # ... call your engine, return [1, num_samples] tensor
```

3. Register it at the bottom of the file: in-file classes go in `_REGISTRY`;
   an engine that lives in its own `backend/engines/<name>` module goes in
   `_LAZY_REGISTRY` as `"id": ("engines.<name>", "ClassName")`
4. Add the same id under `tts_engines` in `docs/features.yaml` with its README
   string (`readme:`) or a doc page (`doc:`) — the daily docs-drift job
   (`python scripts/check-docs-drift.py`) flags registry ids missing from it
5. It then appears in Settings → TTS Engine

---

## Code Style

### Python (Backend)

- **Formatter**: We don't enforce one globally — match the style of the file you're editing
- **Logging**: Use `logger.warning()` / `logger.error()`, never bare `print()`
- **Exceptions**: Avoid bare `except: pass` — catch specific exceptions
- **Type hints**: Use them for public API functions and class methods

### JavaScript/React (Frontend)

- **Components**: Functional components with hooks
- **State**: Zustand slices in `electron/src/shared/store/`
- **Brand assets**: Reuse the canonical mark, palette, naming, and compatibility rules in [`docs/branding.md`](../docs/branding.md); do not redraw or rename runtime identifiers ad hoc
- **CSS**: Tailwind v4 utilities + the shadcn/ui primitives in `electron/src/renderer/src/components/ui/`, themed by the tokens in `electron/src/renderer/src/styles/` (`globals.css`, `t3-theme.css`). Prefer utilities; add or extend a co-located `.css` file only for what utilities cannot express (`@keyframes`, pseudo-elements, styling hooks on library-generated DOM).
- **Naming**: `PascalCase` for components, `camelCase` for hooks and utils

### Rust (shared native helpers)

- **Format**: `cargo fmt` before committing
- **Scope**: `native/desktop-bridge` and modules it imports; do not revive the archived Tauri shell.

---

## Frontend file structure & size limits

Frontend code stays modular so an edit loads one small file, not a 1900-line
one. The rules:

- **Size caps:** **soft 300 lines**, **hard 500 lines** per component file
  (`.jsx`/`.tsx`). Anything over 500 lines must be split.
- **Pages are thin orchestrators.** A file in `electron/src/shared/pages/` is just
  layout + routing + state wiring that composes feature components — no inline
  sub-component over ~50 lines.
- **One component per file.** Co-locate `Foo.jsx` + `Foo.test.jsx` together in a
  per-page feature folder under `electron/src/shared/components/` (e.g.
  `components/settings/`, `components/dub/`). Style with utilities + shadcn
  first (see the CSS rule above).
- **Shared bits go in a `primitives/` folder** inside the feature folder
  (`components/settings/primitives/` is the existing example).
- **Enforced in review, not by lint:** `bun run lint` (oxlint) has no
  `max-lines` rule, so reviewers apply the caps.

---

## Commit Messages

Write clear, concise messages. The PR title becomes the squash-merge commit.

```
good: fix: prevent CUDA OOM during concurrent transcription + TTS
good: feat: add CosyVoice 3 TTS backend adapter
good: docs: add platform compatibility matrix to README

bad:  fixed stuff
bad:  update
bad:  WIP
```

Commit with a real identity (your GitHub noreply address works). The
`commit-identity` PR check (`scripts/check_commit_identities.py`) fails when a
PR commit's author, committer, or `Co-authored-by:`/`Signed-off-by:` email is
a placeholder (`test@local`, `you@example.com`, `mergetest`, hostname-style
`*.local`), an AI agent, or on the hashed block list, or when a commit message
or the PR description credits an AI agent. Fix an identity on the listed
commits only: `git rebase -i origin/main`, mark each listed commit `edit`, and
at each stop run `git commit --amend --no-edit --reset-author` and
`git rebase --continue` (a blanket `--exec` would also take over other
people's commits); fix agent credit by rewording the commit messages (`git rebase -i`, then
`reword`) and editing the PR description.

---

## Testing

Native-call timeout tests should synchronize with confirmed worker entry before
starting their short test deadline, and join released workers during cleanup.
Cover delayed startup separately so runner scheduling does not masquerade as a
native-call timeout or leak work into later tests.

Shared React tests await `cleanupReact()` before resetting persistence and
disposing JSDOM. Keep deferred focus restoration in that same realm; advance
only the current fake-timer tick rather than flushing unrelated future timers.

```bash
# Run the backend suites CI runs (kept separate: backend/tests is isolated)
uv run pytest tests/ -q
uv run pytest backend/tests/ -q

# Run a specific test file
uv run pytest tests/test_app_version.py -q

# Electron desktop validation, from the repository root
bun run check:electron

# Desktop UI tests: launch the real (unpackaged) app against a fake backend.
# CI runs these on macOS, Windows and Linux. On Linux without a display,
# prefix the last command with `xvfb-run -a`. Failures leave a Playwright
# trace and screenshot in electron/test-results/.
bun run --cwd electron build
bun run --cwd electron test:desktop-ui

# Offline renderer smokes (what CI runs): start the smoke server, then the runner.
# New *-smoke.mjs files are classified automatically (chromium = runs here,
# *-real-smoke = needs a live backend, _electron = drives the real app);
# `node --test electron/tests/smoke-inventory.test.mjs` fails on an unclassified one.
OMNIVOICE_PORT=3999 bun run --cwd electron smoke:server &
OMNIVOICE_PORT=3999 VOICESTUDIO_UI_URL=http://localhost:3912 PLAYWRIGHT_CHANNEL=chromium \
  node electron/tests/run-renderer-smokes.mjs [name-filter ...]

# UI crawler (the "UI crawler (n/3)" CI jobs, VOICESTUDIO_CRAWL_SHARD=n/3), against the same smoke server:
# clicks every enabled button on every route, then the buttons a filled-in form
# enables, then one level into each dialog, while API writes answer normally,
# with 500, with a network error, or with broken JSON. It fails on a spinner
# left after requests settle, a button still greyed out after its write failed,
# a failed write with no visible feedback, an uncaught error, or a
# console.error. Fix the button; KNOWN_FINDINGS stays empty.
OMNIVOICE_PORT=3999 VOICESTUDIO_UI_URL=http://localhost:3912 \
  VOICESTUDIO_CRAWL_MODES=http-500 VOICESTUDIO_CRAWL_ROUTES=/clone \
  node electron/tests/ui-crawler.mjs

# Shared native helper, when changed
cargo check --manifest-path native/desktop-bridge/Cargo.toml
```

### Cached local checks (Dagger, optional)

[Dagger](https://docs.dagger.io/install) runs the frontend tests, the mobile
token check and both backend pytest suites in containers whose steps are cached
by content, so a re-run after an unrelated edit takes seconds. It needs Docker.
The first run downloads container images and dependencies (as CI does); after
that your code and results stay on your machine. CI is unchanged: `ci.yml`
stays the gate.

```bash
dagger call check             # all of the above, in parallel
dagger call check --git=.git  # also run tests that read real git history
                              # (in a linked worktree: --git=$(git rev-parse --git-common-dir))
dagger call frontend-tests    # or: mobile-tokens, python-tests
```

---

## What code review looks like

Every PR is reviewed by two AI reviewers before a human looks at it:

- **CodeRabbit** posts a short collapsed summary (no diagrams), inline
  findings, and warning-mode pre-merge checks against the project's hard
  rules (configured in `.coderabbit.yaml`). Finishing-touch repair prompts
  for findings, failing CI, and merge conflicts are disabled.
- **Greptile** reviews with the same project rubrics and learns from 👍/👎
  reactions on its comments — react to train it.

Both are advisory, not gating: CI and the maintainer's approval decide. Don't
be surprised by detailed bot comments minutes after you open a PR — address
what's right, push back (in a reply) on what's wrong.

**Commit & PR conventions:** conventional-commit style with a scope
(`fix(dub): …`, `feat(setup): …`) and link the issue (`Closes #N` / `Refs #N`)
in the title or body.

### Contributing with AI agents

Plenty of contributions here are built with Claude Code, Cursor, and similar
agents — welcome, with the same quality bar as hand-written PRs (real bug,
correct fix, regression test; see the quality gates below).

You submit agent-assisted work as your own, under your own git identity. Don't
credit agents in commits or the PR description: no `Co-authored-by:` trailer
for an AI agent, no "Generated with …" line, no agent session or share links
(claude.ai, chatgpt.com) and no `Claude-Session:` trailer. The
`commit-identity` check fails PRs that carry them. Co-authors who are people
are welcome, including people with first names that also name an agent, such
as Claude, Jules, or Devin. Known agent email addresses remain blocked. The
identity workflow runs the base branch's policy and publishes `Commit identity
policy`; changing the checker or workflow in your PR does not bypass it.

Keep one open PR per head commit. CLA checks reject duplicate heads because
GitHub commit statuses are shared by SHA, even when PR descriptions or authors
differ. Close duplicates and comment `recheck` on the survivor, then rerun its
trusted identity workflow.

One practical tip: this codebase is large, and re-explaining it to your agent
every session burns context and tokens fast. A persistent memory layer fixes
that — the agent recalls the architecture, conventions, and your past findings
instead of re-reading the tree each time. [**memxt**](https://github.com/debpalash/memxt)
(100% local, MCP-based, built by this project's maintainer) exists for exactly
this; any MCP memory server works. Pair it with the repo's agent skill —
`npx skills add debpalash/VoiceStudio` — so your agent knows the project's
hard rules from the first prompt.

## Quality gates your PR must pass

Documentation-only and test-only changes skip runtime tests, desktop/mobile builds
and Docker image rebuilds on pull requests and pushes to `main`. The required
`Tests (backend + frontend)` check reports the explicit skip so branch protection
still works. Mixed changes, workflow/build configuration, dependencies and bundled
app icons still run their normal checks; unknown or truncated change lists run
all checks. Manual runs and scheduled audits remain full runs. CLA, commit identity
and secret scanning still run. Run changed tests locally before submitting a
test-only PR. Docker Hub overview edits still publish through the small
`dockerhub-description.yml` workflow without rebuilding images.

- **Gallery pin:** a PR that changes the `omnivoice-gallery` submodule must
  move it forward along the gallery's `main` (`.github/workflows/gallery-pin.yml`).
  If it fails on a PR that never meant to touch the gallery, the checkout had a
  stale submodule: run `git submodule update omnivoice-gallery` and drop that
  change from the commit.
- **First CI run from a fork:** a pull request from a fork waits for a
  maintainer to approve it (the `external-pr` environment) before CI starts.
  Branches in this repository start at once.
- **CI scope:** the Electron-only jobs (UI crawler, Desktop UI, Linux native
  package, and `Tests (Electron)` plus `Electron renderer smokes`, the Electron halves of `Tests (backend + frontend)`) are skipped on a
  pull request whose every changed file is an *edit or addition* under `backend/`
  (except `backend/assets/`), `omnivoice/`, `docs/` or `mobile/`, a Python file under `tests/` or `scripts/`,
  a root `*.md`, `Makefile`, `uv.lock`, or a workflow other than `ci.yml` and
  `electron-*.yml`. Any other path (including `pyproject.toml`, `package.json`,
  `bun.lock` and shared test fixtures), any file that is not a plain edit or addition (**deleted, renamed**, copied or type-changed), 3,000 or
  more files, and every push to `main` run the full matrix; so does a failed
  detection. Skipped jobs show as skipped in the PR. The rule is the `changes`
  job in `.github/workflows/ci.yml`.
  The `Security` workflow works the same way: CodeQL, bandit and the dependency
  audits are skipped for a PR that only edits files under `docs/` or `mobile/` or
  Markdown, while the secret scan always runs (rule: its own `changes` job).
  The Docker image build on `main` skips merges that only touch `docs/`, `mobile/`,
  `changelog.d/` or `CHANGELOG.md`.
- **Closed PRs stop their CI:** merging or closing a PR cancels its queued and
  running pull-request workflows (`.github/workflows/cancel-closed-pr-runs.yml`);
  the repository is capped at 20 concurrent jobs, so they would only delay others.
  Maintainers can run the same cleanup for the whole repository from the Actions
  tab (`cancel-closed-pr-runs`, dry run by default).
- **Cross-platform parity (hard rule):** anything that ships in default mode
  must behave identically on macOS, Windows, and Linux. Platform-specific
  *implementation* is fine; platform-divergent *default behavior* is a P0.
  Platform-only features go behind an explicit opt-in (Settings toggle, env
  var, or CLI flag).
- **i18n — every locale (hard rule):** every user-facing string goes through
  `t('...')` and the key must exist in **every** file of the catalog the code
  uses: `electron/src/shared/i18n/locales/` (checked by
  `tests/test_locale_parity.py`) or `electron/src/renderer/src/i18n/locales/`
  (checked by `bun run --cwd electron locale:check`). Translate; don't copy
  English into non-English locales. CI fails on hardcoded CJK outside the allowlist in
  `tests/test_no_hardcoded_cjk.py` (extend `_ALLOWED_FILES` with a
  justification for legitimate functional CJK).
- **DB schema changes** go through an alembic migration with a tested upgrade
  path — existing `omnivoice_data/` must keep working with no manual steps.
- **Engine back-compat:** already-installed engines (model weights on disk)
  must not require reinstall or re-download.
- **Local-first:** no new outbound calls, and the app must work fully offline
  with every prompt declined. The only sanctioned ones are:
  - **First-run setup the user starts:** the pinned, SHA-256-verified
    ffmpeg/ffprobe download from GitHub (`zackees/ffmpeg_bins`) when no usable
    copy is found (`backend/services/media_tools.py`); the huggingface.co vs
    hf-mirror.com reachability probe that picks a download endpoint on
    restricted networks, skipped when the user set an endpoint
    (`backend/services/endpoint_race.py`); the dependency install's download
    of the packages pinned in `uv.lock`, from their locked URLs or the
    regional mirror the user chose or the Auto region selected, including the
    parallel multi-connection wheel prefetch
    (`electron/src/main/wheel-prefetch.ts`; owner-approved 2026-10-27); the
    Auto region's speed check, which reads the start of one locked wheel from
    files.pythonhosted.org and mirrors.aliyun.com (one ranged request each,
    timed out, no retries) and keeps PyPI on any doubt
    (`electron/src/main/pypi-mirror.ts`); and Hugging Face model downloads
    (gated on install state or an explicit user action).
  - **Windows ROCm setup the user explicitly selects** (owner-approved
    2026-10-26): the matched AMD SDK and PyTorch wheels from `repo.radeon.com`,
    and the pinned HIP CTranslate2 archive from OpenNMT/CTranslate2 GitHub
    Releases. Every artifact is SHA-256 pinned in
    `scripts/windows-rocm-recipe.json` and verified before use. This opt-in
    installer keeps every other package at its installed (locked) version and
    uses the usual package indexes only for anything new; verification
    and the optional local-model smoke test never acquire models or use the network.
    The VC++ Redistributable is a manual prerequisite; opted-in Windows ROCm
    source setup checks it locally and stops when it is missing.
  - **Galleries the user opens or enables:** the community gallery manifest
    from jsDelivr plus its GitHub-hosted previews
    (`backend/api/routers/community.py`); the voice-preview gallery's signed
    downloads from `omnivoice-gallery` GitHub Releases, only after it is turned
    on in Settings (`backend/services/gallery.py`).
  - **Packaged-app update checks** against GitHub Releases; downloads wait for
    the user (`electron/src/main/updater.ts`).
  - **yt-dlp updates** from PyPI, only when the user clicks update
    (`backend/services/media_tools.py`).
  - **VoiceStudio Cloud account features, only after the user turns them on**
    (first-run setup or Settings → Account; off by default, skipping = off):
    browser sign-in against `voicestudio.sh/identity` and the account API at
    `appapi.voicestudio.sh` from Electron main (`electron/src/main/account/`),
    and, only after the user also turns cloud sync on, settings sync with
    `appapi.voicestudio.sh/v1/sync` from the backend
    (`backend/services/cloud_sync/`), which can call only with a token main
    hands out. Turning either off stops every request at once; switching
    account features off sends one best-effort token revocation. Hidden in
    packaged builds until VoiceStudio Cloud launches.
  - Bug reports as prefilled GitHub Issue URLs opened in the user's browser.
  - **Phone content reports, only when the user taps Send** in a generated
    result's Report sheet: `appapi.voicestudio.sh/v1/content-reports` with the
    reason, optional note and typed text, app version and model; no audio,
    account or device identifier (`mobile/contract/SPEC.md` → Reporting
    generated content; required by Google Play's AI-generated content policy).
  - PostHog analytics only after a yes at the first-run consent prompt
    (`backend/core/analytics.py`, allowlisted content-free metadata).
  - The GitHub star count (no credentials or referrer, refreshed every 20
    minutes while shown).

  Adding to this list needs owner approval. Never log or persist secrets or
  absolute home paths.
- **Security posture:** the backend serves loopback HTTP — treat every
  query/path/form parameter as hostile. User-chosen filesystem destinations
  are authorized in Electron main (native save dialog), never via HTTP params.
- **CI supply chain:** every remote action in `.github/workflows/` is pinned
  to a full 40-char commit SHA with a trailing `# vX.Y.Z` comment
  (`tests/test_actions_pinned.py`); Dependabot bumps the pins weekly.

## Contribution licensing

VoiceStudio is **AGPL-3.0-only**, and the maintainer also offers a
**commercial license** (see [LICENSE-NOTICE.md](../LICENSE-NOTICE.md)). Before
a pull request can merge, every non-exempt human contributor — the opener,
commit authors and co-authors — signs the [Contributor License Agreement](CLA-1.0.md)
once. The [checker](scripts/cla_check.py) exempts its configured maintainer and
bot account IDs. You keep
your copyright. The agreement lets Yupcha Softwares Private Limited, the
company that maintains VoiceStudio, ship your work in both the AGPL-3.0 app and
commercial builds. In return, the company commits that while your contribution
is in the public VoiceStudio repository, it stays available there under
AGPL-3.0 or another OSI-approved licence (CLA section 4). AI agents can't be co-authors (see
[Contributing with AI agents](#contributing-with-ai-agents)); the person
submitting the work signs for it.

For an unsigned pull request, the daily CLA reminder starts a 14-day waiting
period, displays its UTC deadline, and sends one reminder after seven days. At
the deadline maintainers may independently address the underlying issue without
using the contribution. The pull request stays open and signatures remain
welcome after the deadline. The timer begins when the reminder first detects an
unsigned contributor, including for existing pull requests.

The **CLA** check comments on your pull request when someone still needs to
sign. To sign, post this as a new comment, on its own line:

```text
I have read the VoiceStudio CLA 1.0 and I hereby sign it.
```

The signature covers your earlier and future contributions. The CLA check
identifies other required signers or cases needing manual review.
New contributors can sign first: post the same line on the
[signing issue (#2586)](https://github.com/debpalash/VoiceStudio/issues/2586)
before opening a pull request, or on the pull request itself if you prefer. The
same signature applies if a maintainer folds your pull request into another one.
If a pull request contains `Supersedes #N`, its CLA check also covers the
non-exempt human opener, commit authors and co-authors of pull request #N.
Contributed before and have no open pull request? Post
the same line on the [issue labelled `cla`](https://github.com/debpalash/VoiceStudio/issues?q=label%3Acla). If it lists a commit it cannot link to a GitHub
account, either add that commit email to your account (Settings → Emails), or
rewrite the commits with an email that is on it (`git commit --amend
--reset-author`, or an interactive rebase) and push again. Comment `recheck` to
run the check without pushing. Contributing as part of your job? Your employer
signs the [Corporate CLA](CCLA-1.0.md) first, and you still sign the CLA
yourself. Adding a `Signed-off-by:` line (DCO) is appreciated but does not
replace the CLA.

---

## Need Help?

- **Stuck on setup?** Ask in [Discord #help](https://discord.gg/bzQavDfVV9)
- **Not sure where to start?** Check [good first issues](https://github.com/debpalash/VoiceStudio/labels/good%20first%20issue)
- **Want to discuss a big change?** Open a [discussion](https://github.com/debpalash/VoiceStudio/discussions) or Discord thread before coding

Thank you for contributing! 🎙️
