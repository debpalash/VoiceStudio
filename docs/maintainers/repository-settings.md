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
   Pull requests opened before `commit-identity.yml` existed also need a
   `Commit identities` run: merge current `main` into them (the merge
   protocol asks for that anyway), or close and reopen them.
4. Then apply the `main` ruleset below. A required check that never reported
   blocks the merge with "Expected — waiting for status".
5. Apply the `cla-signatures` ruleset once the first signature creates the
   branch, or before; the ruleset can exist first.

When you fold other people's pull requests into one, write `Supersedes #N` in
the description: the check then asks their authors to sign too.

## Ruleset: `main`

Requires a pull request, the backend/frontend test job, the commit-identity
check and the CLA check, and
blocks force-push and deletion. Without the pull request rule, a direct push
whose commit already carries passing checks would be accepted. The rule needs
no approving review, because `@debpalash` is the only maintainer and GitHub
does not let authors approve their own pull requests.

Each CLA recheck marks the head pending before looking up contributors and
signatures. Once GitHub accepts that transition, later lookup failures leave it
pending instead of retaining an older approval. Failed lookups of superseded
PRs are not treated as absent contributors. Status-write failures fail the
workflow; retry a failed run after GitHub recovers and verify its CLA status
before merging.

The `context` values must match the check names shown on a pull request:

- `Tests (backend + frontend)`: the `name:` of the `test` job in `.github/workflows/ci.yml`.
- `Commit identities`: the `name:` of the job in `.github/workflows/commit-identity.yml`.
  Without it, agent and placeholder identities are reported but not blocked.
- `CLA`: the commit status that `.github/scripts/cla_check.py` sets on the
  pull request's head commit. Require this status, not the `cla` job: the job
  succeeds whenever the checker runs, and comment-triggered runs are not
  attached to the pull request.

`integration_id` 15368 is GitHub Actions, so only workflow runs can satisfy the
test check. The `CLA` entry omits it, so the status counts whichever token the
workflow uses. `strict_required_status_checks_policy` requires branches to be up
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
**Block force pushes** and **Require status checks to pass**, then add both
checks with source **GitHub Actions**.

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
