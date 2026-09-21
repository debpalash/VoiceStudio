## Summary

<!-- Brief description of what this PR does. -->

## Changes

<!-- List the key changes made in this PR. -->

-

## Type

<!-- Check the one that applies. -->

- [ ] 🐛 Bug fix
- [ ] ✨ New feature
- [ ] ♻️ Refactor
- [ ] 📝 Documentation
- [ ] 🧪 Tests
- [ ] 🔧 CI / Build
- [ ] 🚀 Release prep

## Testing

<!-- How did you test these changes? -->

-

## Checklist

- [ ] I've tested this locally
- [ ] I've updated relevant documentation (if applicable)
- [ ] No local machine paths, logs, or personal env details in this PR
- [ ] Maintained version files are in sync (if an owner-requested bump): `frontend/package.json`, `pyproject.toml`, `backend/core/version.py`, and lockfiles; archived Tauri versions remain frozen
- [ ] If this PR changes runtime behavior, the regression fixture at `tests/fixtures/omnivoice_data/` still loads green on the `smoke-matrix` CI job (macOS + Windows + Linux)

## Release cadence

VoiceStudio ships **continuous-to-main** — no release candidates, no soak windows.
Every merged PR is immediately part of rolling source (`main`) and Docker
`:latest`. Electron artifact rehearsals validate desktop packages without publishing.
Version bumps require owner approval; validated releases are tagged from `main`
and published explicitly under [the release checklist](../docs/RELEASING.md).
Users who want stability install an Electron release or pin Docker `:stable`.
