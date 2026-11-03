# Project Structure

Every folder has a single job. Every file at the root earns its place.

## Layout

```
VoiceStudio/
│
├── README.md ⟵ user-facing overview (translations: docs/readme/README_*.md)
├── CHANGELOG.md                 ⟵ release history; Electron releases use the tagged section
├── CLAUDE.md / AGENTS.md        ⟵ matching copies of the agent operating rules
├── LICENSE / LICENSE-NOTICE.md / SPONSORS.md   ⟵ legal terms, required notices, supporters
│
├── pyproject.toml               ⟵ Python metadata plus the pytest and lint settings
├── uv.lock                      ⟵ Python lockfile
├── package.json                 ⟵ monorepo manifest (Bun workspaces) and THE app
│                                   version (pyproject.toml + backend/core/version.py mirror it)
├── bun.lock                     ⟵ JS lockfile — repo-root, covers the electron/ workspace
├── flake.nix, flake.lock        ⟵ community Nix dev shells + NixOS FHS env (docs/install/nix.md)
│
├── .coderabbit.yaml / greptile.json   ⟵ bot-review setup; CLAUDE.md supplies both rule sets
├── skills-lock.json             ⟵ records the origin and digest of each .agents/skills/ copy
├── .gitleaks.toml               ⟵ repository secret-detection policy
├── .gitmodules                  ⟵ registers omnivoice-gallery
├── .python-version              ⟵ interpreter selection for Python tooling
├── .dockerignore                ⟵ Docker build context filter
├── alembic.ini                  ⟵ DB migration config (stays at root by alembic convention)
│
├── .gitignore                   ⟵ excludes a checkout-local .env; Settings persists user choices in
│                                   ~/.config/omnivoice/env through backend/core/user_env.py
│
├── backend/                     ⟵ FastAPI server
│   ├── main.py                  sole process entry; its annotated startup sequence must retain its order
│   ├── api/routers/             44 routers, auto-included; HTTP/WS adaptation stays thin here
│   │   └── setup/               setup-wizard endpoints and model acquisition
│   ├── core/                    shared infrastructure: configuration, persistence, job/event plumbing,
│   │                            auth and CSRF, safe paths, analytics consent, version and diagnostics
│   ├── services/                116 modules of business logic spanning speech, dubbing, DSP, GPU dispatch,
│   │                            engine selection and loaded-model ownership
│   ├── engines/                 per-engine adapters: audiocpp, confucius4, cosyvoice_gguf,
│   │                            cosyvoice_subprocess, dots_tts, indextts, moss_tts_nano_subprocess,
│   │                            moss_tts_v15, omnivoice_gguf, omnivoice_subprocess, pockettts,
│   │                            supertonic3, voxcpm2_subprocess, whisper_cpp, _asr_sidecar, _echo
│   ├── schemas/                 Pydantic types at the request/response boundary
│   ├── config/models.yaml       catalogue of selectable models
│   ├── migrations/versions/     mandatory Alembic home for database evolution
│   ├── plugins/                 extension landing area; contract lives in services/plugin_sdk.py
│   ├── mcp_shim/                MCP launch surface documented by docs/mcp.md
│   ├── speech_client/           client entry for the speech sidecar
│   └── tests/                   separately collected backend test session (details below)
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
├── tests/                       ⟵ default pytest collection selected by pyproject.toml `testpaths`
│   ├── conftest.py              redirects OMNIVOICE_DATA_DIR to disposable state for the whole session
│   ├── backend/ / scripts/      path-shaped coverage for those source areas
│   ├── smoke/                   quick end-to-end gates; isolated CI job runs with HF_HUB_OFFLINE=1
│   ├── evals/                   evals.yml-driven output-quality checks
│   ├── probe/ / fixtures/       diagnostic probes and reusable inputs
│   └── frontend/                retained node:test suite for utilities and developer tooling
│
├── scripts/                     ⟵ development, packaging and release commands (sh, Python, mjs)
│   ├── install.sh / install.ps1 installers delivered from voicestudio.sh/install
│   ├── dev-backend.mjs          supervisor behind `bun run dev:api`
│   ├── setup.py                 platform finishing step for `bun run setup:api`, including ROCm opt-in
│   ├── electron-smoke-test.mjs  packaged-launch probe invoked by `bun run smoke-test`
│   ├── prepare_electron_release.py, check_electron_release_assets.py   artifact verification
│   ├── check-docs-drift.py      checks docs-drift.yml against authoritative docs/features.yaml
│   └── build-omnivoice-tts.sh   produces the sidecars stored under bin/
│
├── bin/                         ⟵ one precompiled omnivoice-tts sidecar for each supported platform
├── .agents/skills/              ⟵ real-file canonical skills whose revisions are locked in skills-lock.json
├── skills/                      ⟵ distributable voicestudio and voicestudio-maintainer skill packages
│
├── deploy/                      ⟵ container delivery plus the hosted installer worker
│   ├── Dockerfile               CUDA baseline; BASE_IMAGE and GPU_FLAVOR let CI build ROCm too
│   ├── docker-compose.yml       local one-command stack
│   ├── torch-constraints.txt    image-specific torch-family lock
│   ├── dockerhub-overview.md    release-synchronised Docker Hub description
│   └── install-worker/          Cloudflare worker serving voicestudio.sh/install; embeds both installers
│                                from scripts/ (see docs/install/script.md)
│
├── docs/                        ⟵ engineering references, product imagery and brand material
│   ├── README.md                current documentation index and maintenance policy
│   ├── STRUCTURE.md             this map
│   ├── ROADMAP.md               links to the live roadmap and issue trackers
│   ├── RELEASING.md             complete multi-channel shipping checklist
│   ├── features.yaml            source inventory consumed by docs-drift.yml
│   ├── adr/                     durable architectural decisions
│   ├── agents/                  tracker, triage-role and domain instructions for agents
│   ├── engines/ dubbing/ install/ setup/ migration/ features/ playbooks/ specs/
│   ├── media/ screenshots/      supporting media and captured UI
│   ├── preview.png / logo.*     repository preview and logo assets
│   └── languages.md training.md data_preparation.md evaluation.md voice-design.md
│
├── examples/                    ⟵ executable samples and inputs, including agentic/ and config/
├── notebooks/                   ⟵ contains OmniVoice_Studio_Colab.ipynb
├── omnivoice-gallery/           ⟵ submodule that publishes the voice gallery
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

2. **No ad-hoc scripts or tests at the root.** Put one-off debugging tools in `scripts/`; tests belong in one of the three homes below.

3. **Each subdirectory owns one concern.** If you can't describe what goes in a directory in one sentence, it's wrong.

4. **Packages declare dependencies independently.** `backend/`, `electron/`, and `omnivoice/` each have their own `pyproject.toml` or `package.json`, allowing them to be tested separately. The Bun workspace shares one root `bun.lock`, and Docker uses a frozen install that fails rather than modifying it.

## Where tests live

The single `test` job in `ci.yml` invokes three independent runners. Keeping their collection boundaries separate is intentional.

| Home | Runner | Why it's separate |
|---|---|---|
| `tests/` | `pytest tests/` (also the `testpaths` default) | Primary Python collection. Its fixture redirects `OMNIVOICE_DATA_DIR` to temporary storage, preventing contact with developer data (#878). |
| `backend/tests/` | `pytest backend/tests/`; CI step: “Run pytest (backend/tests, isolated)” | Validates the backend's bare-import mode in a fresh session and uses its own disposable data root. Collection-time `sys.modules` fakes are forbidden because they escape their file and contaminate combined runs. |
| `electron/src/**/*.test.{ts,tsx}` | `bun run test` using Vitest/jsdom; CI enters through `bun run check:electron` | UI tests sit with implementation. Packaged and workflow smoke coverage belongs to `electron/tests/`; older helper/dev-script `node:test` cases live at `tests/frontend/` and use `bun run test:frontend`. |

## What lives where

| Kind of thing | Goes in |
|---|---|
| User-facing product code | `backend/`, `electron/` |
| The TTS model (independent of the studio) | `omnivoice/` |
| New TTS/ASR engine adapter | `backend/engines/<engine>/` |
| Everything executable but not user-facing | `scripts/` |
| Prebuilt platform sidecars | `bin/` |
| Python tests | `tests/` (or `backend/tests/` when isolation is required) |
| Electron / UI unit tests | next to the module, as `*.test.ts(x)` |
| Developer + user docs (Markdown) | `docs/` |
| Architecture decision records (ADRs) | `docs/adr/` |
| Agent-facing documentation | `docs/agents/` |
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
| `design/` | ASCII mockups of the pre-React target UX, superseded by the shipped app. | Deleted. |
| `research/` | Archived legacy Gradio UI + April-2026 competitor notes. | Deleted. |
| `.agents/` | Previously belonged to a retired third-party agent tool. | Removed, then recreated for canonical skill copies pinned by `skills-lock.json`. |

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
- `Dockerfile`, `docker-compose.yml` paths
- Electron Builder, native-helper, updater and packaged-smoke paths
- every import that reads `from backend.main import …` (tests, scripts)
- the root `package.json` as the version source of truth and its mirrors

Migrate when adding the second `apps/*` or the second `packages/*`. Not before.

## Conventions

- **Filenames:** snake_case for Python, kebab-case or PascalCase for JS/TS components, lowercase for Markdown.
- **One-off scripts** go into `scripts/` with a descriptive name, not `test_*.py` at the root.
- **New top-level directories** require a PR that updates *this file*.
- **Test placement:** mirrored suites follow source paths (`tests/backend/` mirrors `api/`, `core/`, `engines/` and `services/`; for example, `backend/services/ffmpeg_utils.py` maps to `tests/backend/services/test_ffmpeg_utils.py`). Otherwise backend-wide tests are flat in `tests/backend/`, cross-cutting tests are flat in `tests/`, and React component tests are colocated with their component.
