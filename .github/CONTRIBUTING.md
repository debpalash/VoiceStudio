# Contributing to VoiceStudio

Thanks for your interest in improving VoiceStudio! This guide covers everything you need to get started.

## Quick Links

| | |
|---|---|
| 💬 **Chat** | [Discord](https://discord.gg/bzQavDfVV9) |
| 🐛 **Bugs** | [GitHub Issues](https://github.com/debpalash/VoiceStudio/issues) |
| 🏷️ **Good First Issues** | [Filtered list](https://github.com/debpalash/VoiceStudio/labels/good%20first%20issue) |
| 📋 **Roadmap** | [README → Roadmap](README.md#roadmap) |

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
- Python 3.10+ (managed automatically by `uv`)

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
bun install
bun run dev
```

This launches Electron with hot reload. Its runtime supervisor manages backend setup
and startup; do not launch a second backend. See [Electron setup](../electron/README.md).

```bash
bun run build       # build Electron
bun run start       # launch the built Electron app
bun run dist        # package locally without publishing
bun run smoke-test  # packaged startup, first-run consent, and native bridge
bun run smoke-test -- --install  # also install and start the managed backend
bun run dev:web     # legacy browser UI + backend
```

The legacy browser command starts both services:

| Service | URL | What it does |
|---------|-----|---|
| **Backend** | `localhost:3900` | FastAPI server — TTS, ASR, diarization, dubbing pipeline |
| **Frontend** | `localhost:3901` | React + Vite UI |

The backend runs through `scripts/dev-backend.mjs` (the `dev:api` script): the
uvicorn command is unchanged, but if the backend **dies** (OOM kill, hard
crash), the wrapper prints a boxed exit banner with the exit code/signal and
the last 20 lines of `omnivoice.log` before the dev stack shuts down — so the
cause doesn't scroll away with the terminal. The same death is also reported
as a crash notice in the UI the next time the backend starts (see
[docs/install/troubleshooting.md §14c](docs/install/troubleshooting.md)).

### Archived desktop (Tauri)

Tauri is sunset after v0.5.3 and receives no further development or backports.
Use Electron for desktop contributions and reproduce desktop bugs there.
Existing users should follow the [migration guide](../docs/electron-migration.md).
Archived sources remain for history and migration; shared backend/web code and
native helpers still used by Electron remain maintained.
The root package exposes no Tauri launch or build command, and active CI does
not compile the archived shell.

---

## Project Structure

```
VoiceStudio/
├── backend/                 # Python FastAPI server
│   ├── api/                 # Route handlers
│   ├── core/                # Config, prefs, constants
│   └── services/            # TTS engines, ASR, dubbing, audio DSP
│       └── tts_backend.py   # ← Multi-engine TTS registry
├── electron/                # Active Electron desktop: main, preload, renderer
├── native/                  # Desktop helpers used by Electron
├── frontend/                # Web UI, shared modules, and archived desktop
│   ├── src/
│   │   ├── components/      # UI components
│   │   ├── hooks/           # Custom React hooks
│   │   ├── stores/          # Zustand state slices
│   │   └── utils/           # Shared utilities
│   └── src-tauri/           # Archived Rust/Tauri desktop shell
├── deploy/                  # Docker, CI configs
├── docs/                    # Screenshots, MCP config
└── scripts/                 # Build & release scripts
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
   # Backend tests
   uv run pytest backend/ -x -q

   # Frontend build check
   bun run check:electron
   ```
4. **Write a clear PR title** — it becomes the squash-merge commit message
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

3. Register it in `_REGISTRY` at the bottom of the file
4. That's it — it auto-appears in Settings → TTS Engine

---

## Code Style

### Python (Backend)

- **Formatter**: We don't enforce one globally — match the style of the file you're editing
- **Logging**: Use `logger.warning()` / `logger.error()`, never bare `print()`
- **Exceptions**: Avoid bare `except: pass` — catch specific exceptions
- **Type hints**: Use them for public API functions and class methods

### JavaScript/React (Frontend)

- **Components**: Functional components with hooks
- **State**: Zustand stores in `src/stores/`, organized by slice
- **Brand assets**: Reuse the canonical mark, palette, naming, and compatibility rules in [`docs/branding.md`](../docs/branding.md); do not redraw or rename runtime identifiers ad hoc
- **CSS**: **Utilities-first + shadcn/ui, one stylesheet.** UI is built on the shadcn/ui primitives in `src/components/ui/` (wrapped by the `src/ui/` barrel, themed to the VoiceStudio palette), composed with Tailwind v4 utility classes. **All styling now lives in a single file — `src/index.css`**: the `@theme` / `[data-theme]` token foundation plus the irreducible set utilities can't express (`@keyframes`, glassmorphism/`backdrop-filter`, pseudo-elements, `:has()`, unlayered cascade overrides, and styling hooks on library-generated DOM like virtualized rows / WaveSurfer). The per-component `.css` files were eliminated in the CSS→Tailwind/shadcn migration — **do not create new ones.** Reach for shadcn primitives + utilities; if a rule is genuinely irreducible, add it to `src/index.css` with a provenance comment. (The only other `.css` is the test-only visual harness. See `docs/shadcn-migration.md`.)
- **Naming**: `PascalCase` for components, `camelCase` for hooks and utils

### Rust (shared native helpers)

- **Format**: `cargo fmt` before committing
- **Scope**: `native/desktop-bridge` and modules it imports; do not revive the archived Tauri shell.

---

## Frontend file structure & size limits

Frontend code stays modular so an edit loads one small file, not a 1900-line
one. The rules:

- **Size caps:** **soft 300 lines**, **hard 500 lines** per `.jsx` file.
  Anything over 500 lines must be split. (The cap does **not** apply to
  `src/index.css` — it is the single, intentional styling foundation and the
  only app stylesheet; see the CSS rule above.)
- **Pages are thin orchestrators.** A file in `frontend/src/pages/` is just
  layout + routing + state wiring that composes feature components — no inline
  sub-component over ~50 lines.
- **One component per file.** Co-locate `Foo.jsx` + `Foo.test.jsx` together in a
  per-page feature folder under `frontend/src/components/` (e.g.
  `components/settings/`, `components/dub/`). Styling is **not** co-located —
  it's utilities + shadcn, with any irreducible rules in `src/index.css`.
- **Shared bits go in a `primitives/` folder** inside the feature folder
  (`components/settings/primitives/` is the existing example).
- **Enforced by ESLint `max-lines`** (`max: 500`) — **warn-only for now** so it
  never breaks CI, with the goal of upgrading to `error` once the backlog of
  oversized files clears.

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

---

## Testing

Native-call timeout tests should synchronize with confirmed worker entry before
starting their short test deadline, and join released workers during cleanup.
Cover delayed startup separately so runner scheduling does not masquerade as a
native-call timeout or leak work into later tests.

```bash
# Run all backend tests
uv run pytest backend/ -x -q

# Run a specific test file
uv run pytest backend/tests/test_api.py -x -q

# Electron desktop validation, from the repository root
bun run check:electron

# Shared native helper, when changed
cargo check --manifest-path native/desktop-bridge/Cargo.toml
```

---

---

## What code review looks like

Every PR is reviewed by two AI reviewers before a human looks at it:

- **CodeRabbit** posts a walkthrough (with a sequence diagram, and an ASCII
  before/after sketch for UI changes), inline findings, and warning-mode
  pre-merge checks against the project's hard rules.
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

One practical tip: this codebase is large, and re-explaining it to your agent
every session burns context and tokens fast. A persistent memory layer fixes
that — the agent recalls the architecture, conventions, and your past findings
instead of re-reading the tree each time. [**memxt**](https://github.com/debpalash/memxt)
(100% local, MCP-based, built by this project's maintainer) exists for exactly
this; any MCP memory server works. Pair it with the repo's agent skill —
`npx skills add debpalash/VoiceStudio` — so your agent knows the project's
hard rules from the first prompt.

## Quality gates your PR must pass

- **Cross-platform parity (hard rule):** anything that ships in default mode
  must behave identically on macOS, Windows, and Linux. Platform-specific
  *implementation* is fine; platform-divergent *default behavior* is a P0.
  Platform-only features go behind an explicit opt-in (Settings toggle, env
  var, or CLI flag).
- **i18n — all 21 locales (hard rule):** every user-facing string goes through
  `t('...')` and the key must exist in **all 21** files under
  `frontend/src/i18n/locales/`. Translate; don't copy English into non-English
  locales. CI fails on hardcoded CJK outside the allowlist in
  `tests/test_no_hardcoded_cjk.py` (extend `_ALLOWED_FILES` with a
  justification for legitimate functional CJK).
- **DB schema changes** go through an alembic migration with a tested upgrade
  path — existing `omnivoice_data/` must keep working with no manual steps.
- **Engine back-compat:** already-installed engines (model weights on disk)
  must not require reinstall or re-download.
- **Local-first:** no new outbound calls except GitHub Issues (opt-in
  reporting) and HuggingFace model downloads. Never log or persist secrets or
  absolute home paths.
- **Security posture:** the backend serves loopback HTTP — treat every
  query/path/form parameter as hostile. User-chosen filesystem destinations
  are authorized in Electron main (native save dialog), never via HTTP params.

## Contribution licensing

VoiceStudio is **AGPL-3.0-only**, and the maintainer also offers a
**commercial license** (see [LICENSE](LICENSE)). By submitting a contribution
you agree that:

1. you have the right to submit it (your own work, or compatibly licensed);
2. it is licensed to the project under **AGPL-3.0**; and
3. you grant the project maintainer a perpetual, worldwide, non-exclusive
   right to also distribute your contribution under the project's commercial
   license terms.

This inbound grant is what keeps the dual-license model viable. If you can't
agree to (3) for a particular contribution, say so in the PR and we'll discuss
before merging. Adding a `Signed-off-by:` line (DCO) to your commits is
appreciated but not required.

---

## Need Help?

- **Stuck on setup?** Ask in [Discord #help](https://discord.gg/bzQavDfVV9)
- **Not sure where to start?** Check [good first issues](https://github.com/debpalash/VoiceStudio/labels/good%20first%20issue)
- **Want to discuss a big change?** Open a [discussion](https://github.com/debpalash/VoiceStudio/discussions) or Discord thread before coding

Thank you for contributing! 🎙️
