# Changelog fragments

Add one file per pull request instead of editing `CHANGELOG.md`, so PRs never
conflict on it. `scripts/release.py` folds the fragments into `## [Unreleased]`
and deletes them when a release is cut.

- **Name:** `<PR number>.<type>.md`, for example `3263.fixed.md`. Add a short
  suffix for a second entry on the same PR (`3263-b.docs.md`).
- **Type:** `changed`, `added`, `docs`, `fixed`, `license` or `ci`, the
  `### ...` subsections of the changelog.
- **Content:** one line, written for users, ending with `(#NNN)` and, for
  community work, `— thanks @user!`. A leading `- ` is optional.

```
Saved voice rows keep their hover actions visible over long names (#3263)
```

`tests/test_changelog_fragments.py` checks every fragment. Entries already in
`## [Unreleased]` stay valid, but new entries go in fragments, not in
`CHANGELOG.md`.
