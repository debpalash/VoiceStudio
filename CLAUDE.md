## Active desktop: Electron only

Electron (`electron/`) is the only maintained desktop app. Tauri is sunset and
archived; do not add features, repair its UI, or backport Electron changes to it.
Triage Tauri-only reports toward the Electron migration guide; investigate shared
backend defects only when they affect Electron or the supported API. Keep archived
source, migration data, and existing compatibility contracts intact. Some code under
`frontend/` is still shared with Electron or serves the web app: trace consumers
before editing or removing it. New desktop UI, IPC, setup instructions and tests
belong in Electron; validate Electron on macOS, Windows and Linux.

<!-- GSD:project-start source:PROJECT.md -->
## Project

**VoiceStudio**

VoiceStudio is an open-source, fully-local ElevenLabs alternative — a desktop app for voice cloning, voice design, video dubbing, and real-time dictation across 646 languages. It runs entirely on the user's machine (CUDA/MPS/ROCm/CPU auto-detect), with no API keys, no accounts, and no cloud dependencies. It's an active beta with a growing user base who hit it with real workloads (50-video batches, multi-engine setups, edge-OS platforms) and report friction in GitHub Issues and Discord. The current version lives in `frontend/package.json` (the single source of truth — see Versioning); the latest stable tag is on the [Releases page](https://github.com/debpalash/VoiceStudio/releases/latest). With `AUTO_VERSION_BUMP` off (the current owner setting), `main` holds at the released version between releases.

**Core Value:** **A first-run that actually works.** A user who downloads the installer (or clones the repo) should reach a working voice-cloning or dubbing output without hitting a wall — and when something does go wrong, the error or docs should tell them exactly what to do.

Everything else (new engines, fancy features) is downstream of "the thing installs and runs reliably across platforms, with the engines and pipelines users already depend on staying compatible."

### Constraints

- **Existing engine compatibility**: Users with already-installed engines (IndexTTS, CosyVoice, etc.) must not have to reinstall. Fixes touching engine code must be backward-compatible with on-disk model state.
- **Cross-platform parity**: Every fix must work on macOS (Apple Silicon + Intel), Windows (x64), and Linux (AppImage + deb). No platform-only regressions; the cross-platform bug bash (PR #51) is the baseline.
- **Default features must work on every platform (strict rule, 2026-05-20):** A feature that ships in default mode — out-of-the-box, no user customization, no opt-in toggle — must behave identically on macOS, Windows, and Linux. Platform-specific *implementation code* is allowed for OS APIs / shells / packaging, but the user-visible *default behavior* cannot diverge. Platform-only features (e.g., a macOS-only global shortcut, a Windows-only path picker) must go behind explicit user opt-in: Settings toggle, env var, or CLI flag. When a default doesn't work on a platform, that's a P0 bug — either fix it on the missing platform or move it behind opt-in. No third option. **This rule governs BEHAVIOUR, not PERFORMANCE** (clarified 2026-07-30, council): hardware acceleration is expected to vary by host — CUDA, MPS, DirectML, Triton availability and `torch.compile` are all host-dependent by design, and reading the rule to forbid that would forbid GPU support itself. An optimization that is skipped where it cannot work (missing Triton, an arch the wheel lacks, a path its toolchain cannot link) is NOT a parity violation; a *feature* the user can see and use on one OS but not another is.
- **Backward-compatible project data**: Existing `omnivoice_data/` (user voices, projects, settings) must keep working without manual migration. Any DB schema change goes through alembic with a tested upgrade path.
- **Local-first guarantee preserved**: nothing leaves the machine without the user's **explicit yes**, and the app must remain fully functional with everything declined. Auto bug reporting is opt-in and submits only to GitHub Issues (prefilled-URL, from the user's own browser). Product analytics (owner-sanctioned 2026-07-16) is opt-in PostHog EU with a **first-run consent prompt** — two equal-weight Yes/No buttons, never default-on, skipping = off; consent-gated, allowlisted content-free metadata only (`backend/core/analytics.py`); every build — installer, Docker, and source alike (owner reversal 2026-07-20, #1193) — carries the in-repo publishable write-only token and shows the same consent ask, with env/baked token overriding it. No required cloud calls, accounts, or API keys.
- **Beta release cadence (no RC, no ceremony — strict rule, 2026-05-20):** the v0.3.x line has **no release candidates, no 48h soak, no formal release ceremony**. Every fix goes continuous-to-main; the owner tags a patch (`v0.3.Z`) from main whenever the current state is worth cutting. No `-rc` tags. No phased release. No `v0.4` deferrals while the v0.3.x line is open — every open issue and every open community PR gets absorbed into the v0.3.x line or explicitly declined. Users follow `main` for previews; users wanting stable stay on the latest tagged release. ROADMAP.md's Phase 6 "Release/Verify/Retro" entries are obsolete unless the user revives them.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

The May-2026 stack research that used to live here served five capabilities that have all since shipped (HF-token Settings panel, prefilled-URL bug reporting, uv mirror fallback for restricted networks, the Supertonic-3 engine, in-repo Markdown docs). Follow the patterns in the code itself; the durable *don'ts* that research established:

- **No third-party endpoints for bug reporting or crash dumps** (`sentry-tauri` was evaluated and rejected) — bug reporting stays opt-in via prefilled GitHub-issue URLs, submitted from the user's own browser. The one sanctioned third-party endpoint is the opt-in PostHog EU product analytics (owner-set 2026-07-16), which is consent-gated behind the first-run prompt, ships allowlisted content-free metadata only, and must never grow exception/DOM autocapture. Its publishable write-only project token is committed in-repo (owner reversal 2026-07-20, #1193 — source builds get the same consent-gated analytics as installers; env/baked token overrides), allowed by `tests/test_no_committed_analytics_token.py` in exactly `backend/core/analytics.py` + `frontend/src/utils/analytics.ts`.
- **No PAT/token-based GitHub posting from the app** — the user submits from their own browser.
- **Don't recommend `setx` for env vars on Windows** (silent truncation, no current-shell propagation) — use the in-app Settings panel or PowerShell `[Environment]::SetEnvironmentVariable`.
- **Don't adopt Material for MkDocs** for any future docs site (maintenance mode since Nov 2025) — Astro Starlight is the precedent if docs ever outgrow the repo.
- **`hf_transfer` is deprecated** — default `huggingface_hub` (hf-xet) handles downloads.

For anything new: prefer what's already pinned in `pyproject.toml` / `frontend/package.json`, and check `uv tree` for conflicts before adding a dependency.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

**Versioning (hard rule, owner-set 2026-06-11; single-source 2026-06-16):** **`frontend/package.json` is the SINGLE SOURCE OF TRUTH for the maintained Electron app version** — Electron's builder reads it directly and Vite injects `__APP_VERSION__` for the UI and bug reports. Two toolchain-required **mirrors** are kept equal to it and bumped in lockstep: `pyproject.toml` and `backend/core/version.py`'s `_FALLBACK_VERSION`. The archived Tauri manifests stay frozen at their final release and are not version mirrors. Guarded by `tests/test_app_version.py`. Version bumps are manual and happen only when the owner asks; until then, `main` may equal the latest released version. Consequences:
- Every PR and preview build uses the canonical version. Preview artifacts add the workflow run number without changing the checked-in version.
- Releasing = obtain owner approval for the version, bump the canonical version and maintained mirrors together, then tag that exact version from `main` after validation.
- Docker: `ghcr.io/debpalash/omnivoice-studio:latest` = **main** (rolling preview); `:X.Y.Z` + `:X.Y` + `:stable` = tagged releases. `:latest` is the preview channel by design — stable users pin `:stable` or a version tag.
- Do not bump minor/major or invent RCs/codenames without the owner asking. No "defer to next version" labels — scope is absorbed or declined, never re-versioned.

**Docs-sync (hard rule, owner-set 2026-06-11):** any change that alters something these docs describe — README.md, `.github/CONTRIBUTING.md`, `.github/SECURITY.md`, `.github/SUPPORT.md`, LICENSE, or `docs/**` (install flows, Docker tag semantics, platform support, versioning/release behavior, review process, supported versions) — must update those docs **in the same PR** as the change. If a doc impact is discovered after merge, the docs fix is the immediate next commit, not backlog. Stale docs are treated as bugs.

**Release notes / changelog (hard rule, owner-set 2026-06-16):** every tagged release gets a **high-quality, user-facing `## [X.Y.Z] — DATE` section in `CHANGELOG.md`** before (or in the same hour as) the tag — never the "Auto-generated release for vX.Y.Z…" fallback. The desktop release workflow extracts that section verbatim as the GitHub Release body, so a missing/empty section ships a bare release. Quality bar (owner-restyled 2026-07-17, replaces the old bold-lead paragraphs): **quiet and scannable change entries** — after an optional tagged-release introduction (see the presentation rule below), a short `**Highlights**` bullet list (plain words, one line each), then `### Changed` / `### Added` / `### Docs` / `### Fixed` / `### License` / `### CI` subsections where each entry is a **single one-liner** with the `(#NNN)` issue/PR ref and contributor credit (`— thanks @user!`) where applicable. Change entries are written for users, grouped by theme, with no multi-line entry paragraphs or raw commit dumps. This applies to **preview builds too**: preview release notes summarize what's new on `main` since the last stable, in the same style. Workflow: as features merge, keep `## [Unreleased]` current; at release time rename it to the version + date. If a release was already cut with the fallback body, the next action is to backfill `CHANGELOG.md` **and** `gh release edit <tag>` the live body — not backlog.

**Release presentation and credits (owner-set 2026-09-17):** Tagged release announcements lead with the biggest user-visible change; redesigns need real UI screenshots and migrations need installer links and steps. Verify all contributor credits from the tag comparison and included PRs; list authors and bug reporters separately (see `docs/RELEASING.md`). Keep Highlights to 3–5 bullets; the release introduction can include prose, images, and a download table before the concise change entries.

**Localization (hard rule):** No hardcoded non-English (CJK) **user-facing text** anywhere in the codebase except the translation layer (`frontend/src/i18n/`). All UI strings go through i18n (`t('...')` keys in `locales/*.json`); native language names live in `i18n/index.ts` (`LANGUAGES`). Functional CJK is allowed and tracked via the allowlist in `tests/test_no_hardcoded_cjk.py` — text-processing regexes, model/engine vocabulary & identifiers (e.g. CosyVoice speaker IDs), localized error matching, demo/eval data, and test fixtures. CI fails on any hardcoded CJK outside the allowlist; to add legitimate functional CJK, extend `_ALLOWED_FILES` there with a justification.

**Release deployment channels (hard rule, owner-set 2026-07-16):** a version bump is not "released" until **every** maintained channel ships it — the full checklist lives in `docs/RELEASING.md`. The channels are the four-platform Electron GitHub Release, GHCR and Docker Hub in CUDA and ROCm flavors, and the Docker Hub overview page synced from `deploy/dockerhub-overview.md`. The final Tauri feeds are immutable compatibility assets copied into releases for old clients; they are never rebuilt. Verify all channels after tagging. Preview/RC builds always source from `main`; never cut a side-branch build.

**Fix quality (hard rule, owner-set 2026-06-16):** Fix issues *properly* and future-maintenance-proof — don't stop at the symptom. Root-cause fully, fix the whole **class** of the bug (not just the one reported instance), add a fail-before/pass-after regression test, and harden against recurrence (e.g. if a lockfile drift only fails in Docker, also make CI catch it). Go the extra mile where it durably pays off. Be token-efficient about it — extra **effort**, not extra **verbosity**: no padding, no redundant re-checks, the smallest correct change that is also recurrence-proof. Don't be shy to spend the effort a proper fix needs; do be shy about wasting tokens.

**Keep main green (hard rule, owner-set 2026-06-16):** A merge must **never break `main`'s CI**. Before a change lands, verify the *full* active CI matrix and `deploy/Dockerfile`, not only the checks you happened to run. Dependency / lockfile / config changes must be validated against all maintained consumers. The lockfile is the repo-root `bun.lock`; any workspace package change requires regenerating it and confirming `bun install --frozen-lockfile` passes. Re-check CodeQL/Security on code changes and the Electron native helper on Rust changes. Do not use the archived Tauri build as a gate.

Other conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

- `vite` — Vite configuration, assets, HMR, builds, and Vitest guidance.
- `fastapi-python` — FastAPI and Pydantic implementation patterns.

Canonical copies live under `.agents/skills/`; `skills-lock.json` pins their sources and hashes. Claude should follow these paths directly, avoiding cross-platform symlinks.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## Workflow

Direct repo edits are authorized (owner decision, 2026-07-08). The GSD command gate that used to live here referenced `/gsd-quick` / `/gsd-debug` / `/gsd-execute-phase` skills that are not installed in this environment; the owner chose to keep working directly rather than restore them. The working conventions that matter are in **Conventions** above — versioning, docs-sync, changelog, localization, fix quality, keep-main-green — plus: gate every merge on the "Tests (backend + frontend)" check passing and the PR being MERGEABLE, and check the open-PR queue before implementing any community-reported fix (contributors may have already submitted one).

**Harvest bot reviews before merging (rule, 2026-07-20):** CodeRabbit and Greptile auto-review every PR (tuned via `.coderabbit.yaml` / `greptile.json`, both fed CLAUDE.md as context). Before merging ANY PR — including your own — read their inline comments (`gh api repos/<owner>/<repo>/pulls/<N>/comments` filtered by bot login) and triage: fix real findings, ignore noise, never merge with an unread Critical/P1. They are the free first review pass; reserve deep agent-driven review for what they can't judge (architecture, cross-file semantics, product intent). Mechanical rules belong in deterministic CI tests, not in any AI reviewer.

**Token economy (owner directive, 2026-07-20; tightened 2026-07-28):** default to the shortest response that fully answers — outlines and tables over prose, no preamble, no recap of work just done, no re-explaining what the diff shows; applies to every response, not just status updates. Lead with the outcome; one-line statuses; no narration, filler, or diff-restating. Read what CI/linters/review bots already computed instead of re-deriving it. Mechanical rules belong in deterministic tests (changelog style, locale parity, version lockstep, CJK — all in `tests/`), never in agent effort. Targeted tests while iterating; full suites only before landing. `AGENTS.md` carries this contract for all agents — keep the two in sync.

**Never accept a PR as-is (owner directive, 2026-07-20):** review findings — bot, agent, or human — get FIXED on the PR branch before merge (maintainer commits are fine and credit the contributor in the changelog); do not merge with known issues, do not merge-then-fix, do not leave findings as comments for someone else. Also merge current `main` into stale community branches before judging their CI, so the PR runs today's workflow gates (PR-green under an old workflow ≠ main-green).
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->

## Agent skills

### Issue tracker

GitHub Issues on `debpalash/VoiceStudio`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
