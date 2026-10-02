# Repository settings

GitHub stores these settings outside the repository, so no file or CI check can
enforce them. Apply them once as a repository admin, then re-check them after
changing workflow or job names. The `gh api` commands below assume an
authenticated `gh` with admin access to `debpalash/VoiceStudio`.

## Rolling out the CLA check

Do these in order, after the pull request that adds `.github/workflows/cla.yml`
is merged:

1. Create the labels `cla` (signing issues) and `cla-override` (maintainer
   review): `gh label create cla` and `gh label create cla-override`.
2. Open the public signing issue, label it `cla`, and pin it.
3. Comment `recheck` on each open pull request, so it gets a `CLA` status.
   Pull requests opened before these workflows existed also need native
   `Commit identities` and `CLA gate` runs, plus `Commit identity policy`: merge current `main` into them
   (the merge protocol asks for that anyway), or close and reopen them. A
   comment-triggered CLA run updates the signature status but does not satisfy
   the native required job.
4. Then apply the `main` ruleset below. A required check that never reported
   blocks the merge with "Expected — waiting for status".
5. Apply the `cla-signatures` ruleset once the first signature creates the
   branch, or before; the ruleset can exist first.

When you fold other people's pull requests into one, write `Supersedes #N` in
the description: the check then asks their authors to sign too.

## Ruleset: `main`

Requires a pull request, the backend/frontend test job, the commit-identity
workflow and its privileged policy status, the CLA workflow job and its signature status, and
blocks force-push and deletion. Without the pull request rule, a direct push
whose commit already carries passing checks would be accepted. The rule needs
no approving review, because `@debpalash` is the only maintainer and GitHub
does not let authors approve their own pull requests.

Each CLA recheck marks the head pending before looking up contributors and
signatures. Once GitHub accepts that transition, later lookup failures leave it
pending instead of retaining an older approval. Failed lookups of superseded
PRs are not treated as absent contributors. Status-write failures fail the
required `CLA gate` job even if GitHub cannot replace an older `CLA` status.
Rerun that failed pull-request workflow after GitHub recovers; an issue-comment
run cannot replace its native check. Both checks must pass before merging.

Commit statuses belong to a SHA, not a particular PR. Both eligibility checkers therefore
reject every head shared by multiple open PRs, including CLA maintainer overrides;
one PR cannot overwrite another's obligations with a passing result. It scans
all open PRs, fails closed on listing errors or the 1,000-PR safety cap, and
checks again before normal success. Closing a duplicate re-evaluates CLA for a remaining
PR at that SHA. If a duplicate instead moves to another commit, comment `recheck`
on the surviving old-head PR. After removing duplicates, rerun the survivor's
trusted identity workflow: duplicate heads leave its status blocked until that
fresh evaluation succeeds, so a sibling's description result is not reused.

These checks are asynchronous, not an atomic lock on GitHub metadata at merge
time. Wait for the newest head and metadata checks after edits, including
closing duplicates. A metadata change detected during evaluation leaves the
status pending and fails the native job; rerun that target workflow. Initial
rollout is deliberately manual: land the trusted workflows before making their
contexts required. Repository writers who can publish commit statuses remain
inside the administrative trust boundary.

The `context` values must match the check names shown on a pull request:

- `Tests (backend + frontend)`: the `name:` of the `test` job in `.github/workflows/ci.yml`.
- `Commit identities`: the native job in `.github/workflows/commit-identity.yml`.
  It runs only base-owned code through `pull_request_target` and fails on API,
  fetch, or policy-execution errors. PR commits are fetched as git objects;
  PR files are never checked out and there is no PR-owned bootstrap fallback.
- `Commit identity policy`: the privileged commit status written by
  `.github/scripts/identity_check.py`. An unprivileged fork workflow can copy
  a native job name, but cannot overwrite this status with its read-only token.
  Require both the native job and this status. The policy reads current PR
  metadata and checks the head/base/description again before publishing. A
  stale event cannot approve a newer head, and the policy checkout must match
  the current base SHA; synchronize the PR with `main` to obtain a fresh run.
- `CLA gate`: the native job in `.github/workflows/cla.yml`, required from
  GitHub Actions. Pull-request events run this job even when an API call fails,
  so rejected status writes cannot leave the merge gate green.
- `CLA`: the commit status that `.github/scripts/cla_check.py` sets on the
  pull request's head commit. It checks signature eligibility; the native job
  alone can succeed while contributors still need to sign. Require both.

GitHub evaluates native jobs triggered by `pull_request_target` as required
checks; `issue_comment` jobs are not eligible. See [GitHub's required-check
troubleshooting guide](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks#checks-from-some-workflow-jobs-are-not-evaluated).

`integration_id` 15368 is GitHub Actions, so only workflow runs can satisfy the
native checks. The `CLA` and `Commit identity policy` entries omit it, so the
statuses count whichever token the trusted workflows use. `strict_required_status_checks_policy` requires branches to be up
to date with `main` before merging.

```bash
gh api --method POST repos/debpalash/VoiceStudio/rulesets --input - <<'JSON'
{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request" }
  ],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "Tests (backend + frontend)", "integration_id": 15368 },
          { "context": "Commit identities", "integration_id": 15368 },
          { "context": "Commit identity policy" },
          { "context": "CLA gate", "integration_id": 15368 },
          { "context": "CLA" }
        ]
      }
    }
  ]
}
JSON
```

The bypass lets repository admins merge a pull request whose checks are not
green, for example to land an urgent fix, but never to push to `main` directly.
For the CLA, prefer the `cla-override` label: it records that a maintainer
reviewed the pull request by hand. Remove the bypass entry for a stricter
setup. Do not enable "Require review from
Code Owners" while `@debpalash` is the only code owner, because GitHub does not
let authors approve their own pull requests.

UI: **Settings → Rules → Rulesets → New ruleset → New branch ruleset**. Set the
target to the default branch, enable **Restrict deletions**, **Require a pull
request before merging** (required approvals: 0, all other options off),
**Block force pushes** and **Require status checks to pass**, then add
`Tests (backend + frontend)`, `Commit identities`, and `CLA gate` with source
**GitHub Actions**, plus the `CLA` and `Commit identity policy` commit statuses.

## Ruleset: `cla-signatures`

`.github/scripts/cla_check.py` creates this branch on the first signature and
commits signatures to it with the workflow's `GITHUB_TOKEN`. The ruleset blocks deletion and force-push only. It
does not restrict updates or require checks, so the workflow can keep committing.
You can create the ruleset before the branch exists.

```bash
gh api --method POST repos/debpalash/VoiceStudio/rulesets --input - <<'JSON'
{
  "name": "cla-signatures",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/cla-signatures"], "exclude": [] } },
  "bypass_actors": [],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" }
  ]
}
JSON
```

Check both rulesets with `gh api repos/debpalash/VoiceStudio/rulesets`.

## Secret scanning and push protection

UI: **Settings → Code security → Secret Protection**. Enable **Secret scanning**
and **Push protection**.

```bash
gh api --method PATCH repos/debpalash/VoiceStudio --input - <<'JSON'
{ "security_and_analysis": {
    "secret_scanning": { "status": "enabled" },
    "secret_scanning_push_protection": { "status": "enabled" } } }
JSON
```

The PostHog project token committed in `backend/core/analytics.py` and
`electron/src/shared/utils/analytics.ts` is a publishable write-only token by
design (see `tests/test_no_committed_analytics_token.py`). If an alert flags it,
close the alert as "used in tests" or "false positive". Do not remove the token.

## Private vulnerability reporting

`.github/SECURITY.md` names GitHub Security Advisories as the preferred channel,
and that link only works while this setting is on.

UI: **Settings → Code security → Private vulnerability reporting → Enable**.

```bash
gh api --method PUT repos/debpalash/VoiceStudio/private-vulnerability-reporting
```

## Dependabot alerts

UI: **Settings → Code security → Dependabot alerts → Enable**. This setting
turns on security alerts. Version updates for GitHub Actions are configured
separately in `.github/dependabot.yml`.

```bash
gh api --method PUT repos/debpalash/VoiceStudio/vulnerability-alerts
```

## Maintainer commit email

Each maintainer should go to **GitHub → Settings → Emails** and enable **Keep my
email addresses private** and **Block command line pushes that expose my
email**. Then set the no-reply address shown on that page as the commit
identity:

```bash
git config --global user.email "ID+USERNAME@users.noreply.github.com"
```

Commits keep their GitHub attribution, and `scripts/cla_audit.py` maps no-reply
addresses to GitHub logins without an API lookup. Commits already pushed
keep their old address.

The contributor audit inventories text blobs in `HEAD`, independent of staged
changes. Submodule gitlinks are excluded: their repositories require separate
licence and contributor audits. A failed blame of an included file stops the
audit rather than understating unsigned contributions.
