## Active desktop: Electron only

Electron (`electron/`) is the only desktop and web UI. The Tauri shell and legacy
UI entrypoints are removed; do not restore build, runtime, release, or CI paths for
them. Triage reports from final Tauri installations toward the migration guide and
preserve their immutable updater feeds. Some modules under `electron/src/shared/` remain
temporarily shared by Electron; they are not a runnable app. New UI, IPC, setup
instructions, tests, and browser assets belong in Electron; validate Electron on
macOS, Windows and Linux.

## Mobile: native iOS and Android (owner-set 2026-10-17)

Every release builds desktop, iOS, Android and Docker from the same release tag
and version (owner-set 2026-10-26). Phone downloads belong on the same GitHub
Release; a missing channel is a release bug.

`mobile/ios/` (Swift/SwiftUI) and `mobile/android/` (Kotlin/Compose) are native
apps that run models **on the phone** (TTS, dictation, voice cloning, dub
translation; owner-set 2026-10-18) and can also drive a user's own VoiceStudio
computer. Phone first: once models are downloaded they work offline with no
computer; pairing adds the heavy engines, chosen per task. The companion-only
build is the base, not the product. They implement one shared contract,
`mobile/contract/SPEC.md`: a mobile feature ships on both or neither.
Theme, API and strings are generated, never hand-copied: `bun
scripts/export-mobile-tokens.ts` (from the Electron theme), `uv run python
scripts/export_openapi.py` (from the backend) and the Electron locale catalogs;
`tests/test_mobile_contract.py` fails on drift. Desktop/web UI still belongs in
Electron only.

# Agent Rules — VoiceStudio

Binding for every AI agent (Claude, Codex, Cursor, review bots, …). CLAUDE.md is the full constitution; this is the operating contract. When they conflict, CLAUDE.md wins.

## Token economy (owner directive, 2026-07-20; tightened 2026-07-28)
- **Default to the shortest response that fully answers.** Outlines and tables over prose; no preamble, no recap of what you just did, no re-explaining a fix the diff already shows. Applies to every response, not just status updates.
- Lead with the outcome. No narration, no restating diffs, no filler praise, no plans you're about to execute anyway.
- Status updates: one line. Final reports: only what changes the reader's next action.
- Don't re-derive what CI, linters, or review bots already computed — read their output first (`gh pr checks`, bot comments via `gh api .../pulls/N/comments`).
- Mechanical rules live in deterministic tests, never in agent effort: changelog style (`tests/test_changelog_style.py`), locale parity (`tests/test_locale_parity.py`), version lockstep (`tests/test_app_version.py`), CJK (`tests/test_no_hardcoded_cjk.py`).
- Run targeted tests while iterating; full suites only before landing.
- Tests and CI simulate CI honestly: `HF_HUB_OFFLINE=1` + empty `HF_HUB_CACHE` — a populated dev cache masks real failures.

## Cross-platform parity: behaviour, not performance
- The parity rule covers user-visible BEHAVIOUR. Hardware acceleration varies by host by design (CUDA/MPS/DirectML, Triton availability, `torch.compile`); skipping an optimization where it physically cannot work is not a parity violation.
- Do not "fix" a parity finding by disabling a working optimization everywhere. That trades a real regression for a semantic one.
- A feature the user can see and use on one OS but not another IS a violation. Judge by what the user can do, not by how fast it runs.

## Merge protocol (hard rules)
1. Never merge without review. Harvest CodeRabbit + Greptile comments first; never merge with an unread Critical/P1.
2. Never accept a PR as-is: fix findings ON the PR branch pre-merge (maintainer commits fine; credit contributors in the changelog fragment). No merge-then-fix, no comment-and-walk-away.
3. Merge current `main` into stale branches before judging their CI — PR-green under an old workflow ≠ main-green.
4. Gate: "Tests (backend + frontend)" green + MERGEABLE.
5. After EVERY merge: watch `main`'s own post-merge runs to green (`gh run list --branch main`). Red main = drop everything and fix.
6. Start every PR, fix and feature from freshly fetched `origin/main` (owner rule, 2026-10-26): `git fetch origin`, confirm local `main` has no incoming or outgoing commits (resolve first if it does), branch from `origin/main`, and merge the latest `main` again right before pushing. `main` requires branches to be up to date before merging; that setting stays on.

Documentation/test-only CI policy (owner-set 2026-10-31): skip runtime tests and
product builds for documentation-only or test-only changes. Keep the required
check green through an explicit detected skip; run changed tests locally. Mixed
runtime/config/dependency changes still run CI, as do bundled app assets and
licence/catalog data. CLA, identity and secret checks remain enabled.

## Change rules (see CLAUDE.md for full text)
- Root-cause the class, not the instance; fail-before/pass-after regression test; smallest correct change.
- Default behavior identical on macOS/Windows/Linux; platform-only features go behind explicit opt-in. Divergent default = P0.
- Local-first: no new required network calls; any HF download gated on installed-ness or explicit user action; all synthetic audio through the `mark_synthetic` chokepoint. Sanctioned calls are listed in `.github/CONTRIBUTING.md` → Quality gates: user-started first-run setup (ffmpeg from GitHub `zackees/ffmpeg_bins`, the huggingface.co/hf-mirror.com reachability probe, `uv.lock`-pinned package downloads from their locked URLs or the chosen/auto-selected regional mirror incl. the parallel wheel prefetch, the Auto region's one-wheel PyPI vs Aliyun speed check, HF model downloads), opening/enabling the galleries (jsDelivr manifest, GitHub Releases), packaged update checks, user-clicked yt-dlp updates from PyPI, user-selected Windows ROCm setup (owner-approved 2026-10-26, hash-pinned: `repo.radeon.com` wheels and the OpenNMT/CTranslate2 GitHub release), consent-gated analytics, prefilled-URL bug reports, phone content reports sent from a result's Report sheet, the Pro licence check after a key is entered, and VoiceStudio Cloud sign-in/account calls (and settings sync once cloud sync is on) only after the user turns them on (off by default; switching off stops them at once). Owner-approved exception (2026-09-28): the public GitHub star count may refresh automatically every 20 minutes, without credentials/referrer or user content; offline use remains unaffected.
- Every user-facing string via i18n, present in ALL 21 `electron/src/renderer/src/i18n/locales/*.json` files (the catalog the app loads) with real translations; `tests/test_locale_parity.py` and Electron `locale:check` enforce it. The legacy `electron/src/shared/i18n/` catalog is loaded only by shared-module tests.
- Docs-sync in the same PR. Changelog: one quiet one-liner per PR in `changelog.d/<PR#>.<type>.md` (not `CHANGELOG.md`), ending `(#N)` + `— thanks @user!` for community work; the release script folds fragments into Unreleased.
- Tagged release announcements lead with the biggest user-visible change; redesigns need real UI screenshots and migrations need installer links and steps. Verify all contributor credits from the tag comparison and included PRs; list authors and bug reporters separately (see `docs/RELEASING.md`).
- Versioning: root `package.json` is the single source of truth; never bump without the owner asking.
- JavaScript dependency changes require regenerating root `bun.lock` (Docker runs `--frozen-lockfile`).
- Attribution: commits, PR descriptions and comments carry only the submitter's git identity. Never credit an agent (no agent `Co-authored-by:`, "Generated with …", session links) and never add other names/emails. Human co-authors are fine. Enforced by the `commit-identity` check.
- Issues: absorb or decline — never defer to a future version. Check the open-PR queue before implementing community-reported fixes.

## Shared select controls

- Use `electron/src/shared/components/SearchableSelect.jsx` for all new or redesigned select boxes. Reuse `VoiceSelector` for voice choices. Do not introduce native `<select>` controls.
- Provide a localized `ariaLabel`; use `menuPortal` inside scrolling or clipping containers. Preserve keyboard selection and disabled states.

## Agent skills

Project development skills are pinned in `skills-lock.json` and installed under
`.agents/skills/`: Vite and FastAPI.
Repository rules and tracker mappings override generic skill guidance.

### Issue tracker

GitHub Issues on `debpalash/VoiceStudio`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `docs/adr/` plus a root `CONTEXT.md` that is created lazily and does not exist yet — skip it when absent. See `docs/agents/domain.md`.
