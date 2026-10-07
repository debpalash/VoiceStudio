# Project Structure

Every folder has a single job. Every file at the root earns its place.

## Layout

```
VoiceStudio/
│
├── README.md / README_CN.md / README_JA.md ⟵ user-facing overview (English / Chinese / Japanese)
├── CHANGELOG.md                 ⟵ release history; Electron releases use the tagged section
├── CLAUDE.md / AGENTS.md        ⟵ the working contract for AI agents — keep the two in sync
├── LICENSE, LICENSE-NOTICE.md, SPONSORS.md
│
├── pyproject.toml               ⟵ Python project manifest (+ pytest / lint config)
├── uv.lock                      ⟵ Python lockfile
├── package.json                 ⟵ monorepo manifest (Bun workspaces + Turborepo) and THE app
│                                   version (pyproject.toml + backend/core/version.py mirror it)
├── bun.lock                     ⟵ JS lockfile — repo-root, covers the electron/ workspace
├── turbo.json                   ⟵ turborepo pipeline
│
├── .coderabbit.yaml             ⟵ CodeRabbit PR review config (fed CLAUDE.md)
├── greptile.json                ⟵ Greptile PR review config (fed CLAUDE.md)
├── skills-lock.json             ⟵ pins the sources + hashes of .agents/skills/
├── .gitleaks.toml               ⟵ secret-scan config
├── .gitmodules                  ⟵ omnivoice-gallery submodule
├── .python-version
├── .dockerignore                ⟵ Docker build context filter
├── alembic.ini                  ⟵ DB migration config (stays at root by alembic convention)
│
├── .gitignore                   ⟵ a repo-local .env stays ignored, but user config is NOT
│                                   kept here: the durable env file is ~/.config/omnivoice/env
│                                   (backend/core/user_env.py), written by the Settings panel
│
├── backend/                     ⟵ FastAPI server
│   ├── main.py                  the one entry point; its boot order is load-bearing —
│   │                            read the comments before reordering anything
│   ├── api/routers/             42 routers, auto-included; thin HTTP/WS surface
│   │   └── setup/               first-run wizard, model download
│   ├── core/                    config, db, job queue, event bus, auth/CSRF, path security,
│   │                            opt-in analytics, version, diagnostics
│   ├── services/                98 modules of business logic — TTS, dubbing pipeline,
│   │                            audio DSP, GPU gateway, engine routing, model lifecycle
│   ├── engines/                 per-engine adapters: indextts, supertonic3, confucius4,
│   │                            dots_tts, moss_tts_v15, pockettts, audiocpp,
│   │                            omnivoice_gguf, omnivoice_subprocess, _asr_sidecar, _echo,
│   │                            voxcpm2_subprocess, moss_tts_nano_subprocess, cosyvoice_subprocess
│   ├── worker/                  remote / distributed workers — scheduler, pool, routing,
│   │                            breaker, capacity, plus protocol/ and inbound/
│   ├── mcp_shim/                MCP server entry point (docs/mcp.md)
│   ├── speech_client/           speech sidecar client entry point
│   ├── schemas/                 pydantic request/response shapes
│   ├── migrations/versions/     alembic revisions — every schema change goes through here
│   ├── plugins/                 plugin drop-in point (see services/plugin_sdk.py)
│   ├── config/models.yaml       model catalogue
│   └── tests/                   the isolated pytest session — see "Where tests live"
│
├── electron/                    ⟵ the only desktop app and web UI (Electron + React 19)
│   ├── src/main/                lifecycle, backend supervisor, runtime setup, updater, native IPC
│   ├── src/preload/             typed contextBridge → window.voicestudio
│   ├── src/renderer/            desktop React app: routes/, features/, components/, i18n/locales/
│   ├── src/shared/              modules shared by the desktop and browser builds (api/, i18n/,
│   │                            store/, ui/, utils/) — not a runnable app
│   ├── tests/                   packaged-app, installer and workflow smoke tests
│   ├── build/                   packaging inputs: icons, entitlements, Info.plist
│   ├── scripts/                 dev launcher, AppImage update-info embedding
│   ├── vite.web.config.ts       browser build (`bun run build:web`) → frontend/dist/
│   └── electron-builder.config.mjs   installers: DMG/zip, NSIS, AppImage + deb
│
├── native/desktop-bridge/       ⟵ Rust helper used by Electron
│
├── frontend/dist/               ⟵ build output of `bun run build:web` (gitignored): the web UI
│                                   the backend serves in Docker and, from Electron packages, to LAN devices
│
├── omnivoice/                   ⟵ the underlying TTS model package
│   ├── models/
│   ├── cli/                     CLI entry points (omnivoice-infer, etc.)
│   ├── data/                    data utilities used by the model
│   ├── eval/                    evaluation scripts
│   ├── scripts/                 one-off utilities that ship with the package
│   ├── training/
│   └── utils/
│
├── tests/                       ⟵ the main pytest session (testpaths in pyproject.toml)
│   ├── conftest.py              hermetic OMNIVOICE_DATA_DIR — never touches real app state
│   ├── backend/, scripts/       mirrors of the source trees they cover
│   ├── smoke/                   fast end-to-end checks (own CI job, HF_HUB_OFFLINE=1)
│   ├── evals/                   quality evals (evals.yml)
│   ├── probe/, fixtures/
│   └── frontend/                `node:test` checks for shared helpers and dev scripts
│
├── scripts/                     ⟵ dev / build / release scripts (shell, python, mjs)
│   ├── install.sh / install.ps1 shell + PowerShell installers served at voicestudio.sh/install
│   ├── dev-backend.mjs          `bun run dev:api` backend supervisor
│   ├── setup.py                 `bun run setup:api` post-sync platform setup (incl. ROCm opt-in)
│   ├── electron-smoke-test.mjs  `bun run smoke-test` packaged-app launch check
│   ├── prepare_electron_release.py, check_electron_release_assets.py   release asset gates
│   ├── check-docs-drift.py      the docs-drift.yml checker (docs/features.yaml is canonical)
│   └── build-omnivoice-tts.sh   builds the bin/ sidecars
│
├── bin/                         ⟵ prebuilt omnivoice-tts sidecars, one per platform
│
├── .agents/skills/              ⟵ canonical skill copies (vite, fastapi-python), pinned by
│                                   skills-lock.json — followed by path, never symlinked
├── skills/                      ⟵ skills this repo publishes (voicestudio, voicestudio-maintainer)
│
├── deploy/                      ⟵ Docker deployment configs + the installer worker
│   ├── Dockerfile               CUDA by default; CI builds the ROCm variant from the same
│   │                            file via BASE_IMAGE / GPU_FLAVOR overrides
│   ├── docker-compose.yml       one-click local deployment
│   ├── torch-constraints.txt    pinned torch resolution for the image
│   ├── dockerhub-overview.md    synced to the Docker Hub overview page at release
│   └── install-worker/          live voicestudio.sh/install Cloudflare worker; bundles
│                                scripts/install.sh + install.ps1 (docs/install/script.md)
│
├── docs/                        ⟵ developer docs, screenshots, branding
│   ├── ROADMAP.md               where this project is going
│   ├── STRUCTURE.md             you are here
│   ├── RELEASING.md             the release checklist — every deployment channel
│   ├── features.yaml            canonical feature inventory (drives docs-drift.yml)
│   ├── adr/                     architecture decision records
│   ├── agents/                  agent-facing docs (issue tracker, triage labels, domain)
│   ├── engines/, dubbing/, install/, setup/, migration/, features/, playbooks/, specs/
│   ├── media/, screenshot-*.png, preview.png, logo.*
│   └── languages.md, training.md, data_preparation.md, evaluation.md, voice-design.md
│
├── examples/                    ⟵ runnable demos + sample inputs (agentic/, config/)
│
├── notebooks/                   ⟵ OmniVoice_Studio_Colab.ipynb
│
├── omnivoice-gallery/           ⟵ git submodule — the published voice gallery
│
├── omnivoice_data/              ⟵ Docker bind-mount target (gitignored)
│                                   DB + HF cache live here when running via compose
│
├── .github/workflows/           ⟵ ci, electron-build, electron-release, docker, security,
│                                   docs-drift, evals, install-smoke, build-omnivoice-tts,
│                                   cla, commit-identity, cosyvoice-dependencies
└── .git/
```

## Rules of the root

1. **Nothing at the root is a runtime artifact.** Outputs, temp files, local DBs, crash logs — all go to `~/Library/Application Support/OmniVoice/` (or the OS equivalent), *never* into the repo. The one exception is `omnivoice_data/`, which exists as a bind-mount anchor for Docker.

2. **No ad-hoc scripts at the root.** One-off debug scripts live in `scripts/`. Tests live in one of the three homes below, never at the root.

3. **Each subdirectory owns one concern.** If you can't describe what goes in a directory in one sentence, it's wrong.

4. **Every package has a manifest.** `backend/`, `electron/`, `omnivoice/` each have their own deps declared via `pyproject.toml` / `package.json` — they are independently testable. The JS lockfile is the **repo-root** `bun.lock` (Bun workspace), and `deploy/Dockerfile` installs from it with `--frozen-lockfile`.

## Where tests live

Three homes, each with its own runner. CI runs all three inside the single `test` job in
`ci.yml`, as separate steps. The split is deliberate, not drift:

| Home | Runner | Why it's separate |
|---|---|---|
| `tests/` | `pytest tests/` — the `testpaths` default | The main suite. Its `conftest.py` points `OMNIVOICE_DATA_DIR` at a throwaway dir so a run can never touch the developer's real app state (#878). |
| `backend/tests/` | `pytest backend/tests/` — its own pytest session (the `Run pytest (backend/tests, isolated)` step) | Runs as an isolated session against `backend/`'s bare imports. Its `conftest.py` sets the same hermetic data dir; **never** reintroduce module-level `sys.modules` stubs there — they leak process-wide at collection time and poison mixed runs. |
| `electron/src/**/*.test.{ts,tsx}` | `bun run test` (vitest, jsdom); CI runs it through `bun run check:electron` | Co-located with the module under test. `electron/tests/` holds the packaged-app and workflow smoke tests; `tests/frontend/` is the older `node:test` set (`bun run test:frontend`). |

## What lives where

| Kind of thing | Goes in |
|---|---|
| User-facing product code | `backend/`, `electron/` |
| The TTS model (independent of the studio) | `omnivoice/` |
| A new TTS/ASR engine adapter | `backend/engines/<engine>/` |
| Everything executable but not user-facing | `scripts/` |
| Prebuilt platform sidecars | `bin/` |
| Python tests | `tests/` (or `backend/tests/` when the isolated session is required) |
| Electron / UI unit tests | next to the module, as `*.test.ts(x)` |
| Developer + user docs (Markdown) | `docs/` |
| Architecture decision records (ADRs) | `docs/adr/` |
| Agent-facing docs | `docs/agents/` |
| Runnable demos and sample data | `examples/` |
| Runtime data (never committed) | `~/Library/Application Support/OmniVoice/` on Mac |

## What *doesn't* live at the root anymore

Removed in the cleanup pass:

| File | Why it was there | Where it went |
|---|---|---|
| `test_crash.py`, `test_server.py`, `test_whisper.py`, `test_mock.py`, `test_pyannote.py` | One-off debug scripts from an April 14 crash investigation. Imported symbols that no longer exist after the router refactor. | Deleted (already gitignored, referenced dead code). |
| `benchmark.py` | Another stale debug script; imported `backend.main._get_db` which no longer exists. | Deleted. |
| `output.wav`, `test.wav` | Runtime artifacts. | Deleted / moved out. |
| `crash_log.txt` | Runtime log. Now written to `$DATA_DIR/crash_log.txt`. | Deleted. |
| `omnivoice.zip` (148 MB) | Offline reference archive of the project itself. | Moved out of the repo to `../omnivoice.zip.bak`. |
| `data/` | Only contained `.DS_Store`. | Deleted. |
| `legacy_gradio/` | The pre-React Gradio UI. Kept for historical reference. | Archived to `research/legacy_gradio/`, then removed in the 2026-07-12 cleanup (git history). |
| Scattered `.DS_Store` files | macOS Finder droppings. | Deleted from every non-ignored directory. |

Removed in the 2026-07-12 cleanup pass (all preserved in git history):

| Dir | Why it was there | Where it went |
|---|---|---|
| `.planning/` (74 files) | GSD-era planning archive: phases, quick plans, issue clusters. The GSD workflow was retired 2026-07-08. | Deleted; the four load-bearing decision docs moved to `docs/adr/`. |
| `specs/` | spec-kit specs for features 001–007 — all shipped. | Deleted; `docs/specs/` is the current home. |
| `design/` | ASCII mockups of the pre-React target UX, superseded by the shipped app. | Deleted. |
| `research/` | Archived legacy Gradio UI + April-2026 competitor notes. | Deleted. |
| `.agents/` | Rules for a third-party agent tool no longer in use. | Deleted — then reintroduced with a different job: `.agents/skills/` now holds the canonical skill copies pinned by `skills-lock.json`. |

## Scaling path (proposed, not yet executed)

The current flat layout works fine for the current size. If the project grows to include additional apps (a mobile companion, a plugin SDK, multiple backends), migrate to a Turborepo-style monorepo:

```
VoiceStudio/
├── apps/
│   ├── api/                 ← was backend/
│   └── desktop/             ← was electron/ (desktop + browser UI)
├── packages/
│   ├── omnivoice-model/     ← was omnivoice/
│   └── tts-adapters/        ← new; the pluggable TTS interface from ROADMAP phase 3
├── config/
│   └── docker/
├── tests/
└── docs/
```

**Do not execute this migration without a dedicated PR.** It breaks:
- `pyproject.toml` `[tool.hatch.build.targets.{sdist,wheel}]` paths
- `package.json` workspaces and scripts
- `turbo.json`, `Dockerfile`, `docker-compose.yml` paths
- Electron Builder, native-helper, updater and packaged-smoke paths
- every import that reads `from backend.main import …` (tests, scripts)
- the root `package.json` as the version source of truth and its mirrors

Migrate when adding the second `apps/*` or the second `packages/*`. Not before.

## Conventions

- **Filenames:** snake_case for Python, kebab-case or PascalCase for JS/TS components, lowercase for Markdown.
- **Tests mirror source paths** where a mirror exists: `tests/backend/` mirrors `api/ core/ engines/ services/`, so `backend/services/ffmpeg_utils.py` → `tests/backend/services/test_ffmpeg_utils.py`. Everything else stays flat — `tests/backend/test_*.py` for backend-wide cases, `tests/test_*.py` for cross-cutting ones. A React component's test sits next to the component.
- **One-off scripts** go into `scripts/` with a descriptive name, not `test_*.py` at the root.
- **New top-level directories** require a PR that updates *this file*.
