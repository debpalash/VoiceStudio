---
name: voicestudio-maintainer
description: Triage GitHub issues, review contributor pull requests, diagnose CI, and prepare explicitly requested releases for VoiceStudio's Electron desktop app and Python backend. Use for repository maintenance work, not ordinary VoiceStudio audio generation.
---

# VoiceStudio maintenance

Read the target repository's AGENTS.md, CLAUDE.md, contribution guide, and release documentation first. Their policies override this general workflow. Installing this skill does not authorize merges, releases, issue closures, or messages to contributors.

## Triage and implementation

- Inspect current code and existing PRs before duplicating a reported fix. Reuse contributor work where it solves the problem; retain attribution.
- Reproduce the reported behavior where possible. Separate confirmed failures from diagnosis, and record meaningful limitations in the PR.
- Fix the cause with the smallest complete change. Test observable behavior where it can regress; do not mistake a source-text assertion for an end-to-end test.
- Preserve local changes. Use an isolated worktree when branch switching would disturb them.
- Read existing CI failures and reviewer comments before rerunning work. Diagnose failures before retries; never hide a failing gate behind a successful piped command.
- Keep user-facing docs and required translations aligned with the implementation.

## Review and authorized landing

Review the current diff, bot findings, and required checks against current main. Resolve material findings on the PR branch before landing; do not merge first and promise a follow-up. Refresh stale branches using the repository's policy and preserve contributor commits.

Before an authorized merge, verify required checks are green, the head has not changed, and the PR is mergeable. After landing, inspect main's own runs; investigate regressions immediately. Report remaining blockers without inventing a successful verification.

## Release preparation

Preparing CI or packaging is distinct from publishing. Do not tag, bump versions, enable publishing, or cut a release unless that action is authorized.

Use the repository's version source of truth, changelog format, supported platforms, and distribution channels. Validate packaging and workflows without publishing when that is the requested scope. After an authorized release, verify actual artifacts, release notes, updater metadata, and requested registry channels; a green build alone does not prove distribution.

## Communication

Lead with the outcome and evidence. Credit concrete contributor work. Do not close stale issues merely because of age, or claim reporter confirmation that has not occurred. For authorized closures, give the resolution and what evidence would justify reopening.

## VoiceStudio-specific routing

When maintaining debpalash/VoiceStudio, consult its current rules rather than old architecture assumptions:
- Electron development and packaging: `electron/README.md`, `electron/package.json`, and `.github/workflows/`.
- Backend contracts: running `/openapi.json`, `backend/api/`, and targeted tests.
- Release channels and version ownership: `docs/RELEASING.md` and CLAUDE.md. Never infer a version bump from a fix request.
- Read CodeRabbit/Greptile findings and required CI before merging. Follow main's post-merge CI.
- Preserve local-first behavior, cross-platform behavior, model-install consent, synthetic-audio marking, and localization requirements.

## Current development commands

Run from the repository root:

```sh
bun install
bun run setup:api        # prepare the source Python runtime explicitly
bun run dev              # Electron + supervised backend
bun run typecheck        # Electron main, preload, and renderer
bun run test             # Electron tests
bun run check:electron   # types, tests, build, packaging contract
bun run dist             # local installers; publishing disabled
```

`electron/src/main/` owns lifecycle, IPC, native helpers, and backend supervision;
`electron/src/preload/` exposes the renderer bridge; `electron/src/renderer/src/`
contains the React app. Keep privileged filesystem/process work out of the renderer.
`electron/src/shared/` holds modules shared with the browser build; it is not a
runnable app. `backend/` supplies the shared Python API; `native/desktop-bridge/`
supplies native capabilities. `frontend/` only receives the browser build output
(`bun run build:web` → `frontend/dist/`, served by the backend in Docker). Do not
rename internal `omnivoice` packages, environment keys, or data paths as a
branding cleanup.

Electron is the only desktop app and web UI. The Tauri shell is removed; do not
restore it. Its final v0.5.3 updater feeds are immutable compatibility assets,
and old Tauri installs move over with `docs/electron-migration.md`.
Use `electron-build.yml` for artifact-only packaging rehearsals; inspect its
results for Windows x64, Linux, macOS Intel, and macOS Apple Silicon (Windows
ARM64 is an experimental leg that may fail without blocking).
Do not dispatch release/publishing workflows to test packaging. Signing, updater
migration, and successful installation are separate checks from a green build.
The app version comes from the root `package.json` (mirrored in `pyproject.toml`
and `backend/core/version.py`); do not bump it without an explicit versioning task.

For backend tests, use the repo's CI dependencies and an empty temporary
`HF_HUB_CACHE` with `HF_HUB_OFFLINE=1`; installed developer models must not hide
missing fixtures. Run targeted tests while editing and the required full checks
before landing. Existing CI failures remain blockers, not implied waivers.
