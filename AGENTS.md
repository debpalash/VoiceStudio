## Active desktop: Electron only

Electron (`electron/`) is the only maintained desktop app. Tauri is sunset and
archived; do not add features, repair its UI, or backport Electron changes to it.
Triage Tauri-only reports toward the Electron migration guide; investigate shared
backend defects only when they affect Electron or the supported API. Keep archived
source, migration data, and existing compatibility contracts intact. Some code under
`frontend/` is still shared with Electron or serves the web app: trace consumers
before editing or removing it. New desktop UI, IPC, setup instructions and tests
belong in Electron; validate Electron on macOS, Windows and Linux.

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
2. Never accept a PR as-is: fix findings ON the PR branch pre-merge (maintainer commits fine; credit contributors in CHANGELOG). No merge-then-fix, no comment-and-walk-away.
3. Merge current `main` into stale branches before judging their CI — PR-green under an old workflow ≠ main-green.
4. Gate: "Tests (backend + frontend)" green + MERGEABLE.
5. After EVERY merge: watch `main`'s own post-merge runs to green (`gh run list --branch main`). Red main = drop everything and fix.

## Change rules (see CLAUDE.md for full text)
- Root-cause the class, not the instance; fail-before/pass-after regression test; smallest correct change.
- Default behavior identical on macOS/Windows/Linux; platform-only features go behind explicit opt-in. Divergent default = P0.
- Local-first: no new required network calls; any HF download gated on installed-ness or explicit user action; all synthetic audio through the `mark_synthetic` chokepoint.
- Every user-facing string via i18n, present in ALL 21 `frontend/src/i18n/locales/*.json` with real translations.
- Docs-sync in the same PR. CHANGELOG Unreleased: quiet one-liners ending `(#N)` + `— thanks @user!` for community work, under a short `**Highlights**` list.
- Tagged release announcements lead with the biggest user-visible change; redesigns need real UI screenshots and migrations need installer links and steps. Verify all contributor credits from the tag comparison and included PRs; list authors and bug reporters separately (see `docs/RELEASING.md`).
- Versioning: `frontend/package.json` is the single source of truth; never bump without the owner asking.
- `frontend/package.json` dep changes require regenerating root `bun.lock` (Docker runs `--frozen-lockfile`).
- Issues: absorb or decline — never defer to a future version. Check the open-PR queue before implementing community-reported fixes.

## Shared select controls

- Use `frontend/src/components/SearchableSelect.jsx` for all new or redesigned select boxes. Reuse `VoiceSelector` for voice choices. Do not introduce native `<select>` controls.
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

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
