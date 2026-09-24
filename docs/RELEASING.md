# Releasing VoiceStudio

Electron is the only maintained desktop distribution. Tauri ended at v0.5.3;
its signed updater feeds and installers are immutable compatibility assets.

## Credentials

Electron publication uses these GitHub Actions secrets:

- `ELECTRON_MACOS_CSC_LINK` and `ELECTRON_MACOS_CSC_KEY_PASSWORD` for the
  Developer ID Application `.p12` certificate and its password.
- `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD` (not the Apple ID login password)
  and `APPLE_TEAM_ID` for macOS notarization and stapling.
- `ELECTRON_WINDOWS_CSC_LINK` and `ELECTRON_WINDOWS_CSC_KEY_PASSWORD` for
  Windows Authenticode signing. The macOS certificate cannot sign Windows apps.
- The repository `GITHUB_TOKEN` for draft creation and asset uploads.
- `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` for the Docker Hub mirror.

The archived `TAURI_SIGNING_PRIVATE_KEY*` secrets are retained only so the final
Tauri release can be audited. Do not use `.github/workflows/release.yml` for a new
version. `TAURI_SUNSET_TAG` must continue to identify v0.5.3 so Electron releases
can copy the final signed `latest.json` and `latest-user.json` feeds unchanged.
Those feeds must always point to immutable v0.5.3 Tauri assets; old Tauri clients
must never receive an Electron installer.

## Versioning

`frontend/package.json` is the single source of truth for the maintained app
version. Electron Builder reads it directly. Keep these active mirrors equal:

- `pyproject.toml`
- `backend/core/version.py` (`_FALLBACK_VERSION`)

The archived Tauri manifests stay frozen at their final release. Do not bump or
rebuild them. `tests/test_app_version.py` enforces the active version contract.
Version bumps are manual and require owner approval. Until a bump is requested,
`main` may remain at the latest released version. For a release, bump the
canonical version and both maintained mirrors together, then tag that exact
version only after validation.

## Before tagging

1. Merge only after required CI and review are green.
2. Run the artifact-only Electron rehearsal, `.github/workflows/electron-build.yml`,
   and inspect all four outputs: Linux x64, Windows x64, macOS arm64, macOS x64.
   It first checks the setup screen, then installs and starts the managed Python
   runtime in a separate temporary profile on Linux, Windows and Apple Silicon.
   Intel Macs retain packaging/setup checks under their existing
   [UI/remote-only contract](install/macos.md). The local-runtime test downloads
   runtime packages, keeps model downloads disabled, verifies the live backend
   connection and clean shutdown, and removes the test profile. A setup-screen
   check alone is not evidence that runtime installation works.
3. Verify a packaged launch and managed backend startup on the changed platforms.
4. Rename `## [Unreleased]` in `CHANGELOG.md` to `## [X.Y.Z] — YYYY-MM-DD`.
   Lead with the largest user-visible change, keep Highlights to 3–5 bullets,
   include migration steps and real screenshots when relevant, and verify human
   contributors and bug reporters from the tag comparison and included PRs.
5. Run `uv run pytest tests/test_app_version.py tests/test_changelog_style.py -q`.
6. Confirm the version matches the intended tag.

## Build a release draft

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

The tag starts `.github/workflows/electron-release.yml`. It builds all four
platform targets, validates packaged startup and updater metadata, creates or
updates a draft, and uploads:

- Electron installers and platform-specific `electron-stable-*.yml` feeds
- `SHA256SUMS.txt`
- authored CHANGELOG release notes
- immutable copies of the final Tauri updater feeds for old clients

Tag pushes never publish. Inspect the draft and downloaded installers first.

If the workflow itself needs a fix after tagging, keep the tag immutable. Merge
and validate the workflow fix on `main`, then dispatch `electron-release.yml`
from `main` with `release_tag=vX.Y.Z`. Every build still checks out the exact tag;
only the workflow definition comes from `main`.

## Publish

Dispatch `electron-release.yml` for the exact tag with `publish=true`. Signing
and notarization are required by default. The owner may explicitly set
`allow_unsigned=true`; the workflow then adds the installer-trust disclosure to
the release notes. Never select that exception without the owner's decision.

After publication, verify each maintained channel:

| Channel | Workflow | Verification |
|---|---|---|
| GitHub Release | `electron-release.yml` | Four platform targets, updater feeds, checksums, CHANGELOG notes, retained v0.5.3 Tauri feeds |
| GHCR CUDA | `docker.yml` | `:X.Y.Z`, `:X.Y`, `:stable` manifests |
| GHCR ROCm | `docker.yml` | `:X.Y.Z-rocm`, `:X.Y-rocm`, `:stable-rocm` manifests |
| Docker Hub | `docker.yml` | Matching CUDA/ROCm tags |
| Docker Hub overview | `docker.yml` | Read the `Update Docker Hub description` step log; the step may continue after a 403 |
| Rolling containers | `docker.yml` on `main` | `:latest`, `:main`, and `:rocm` timestamps move |

A missing channel is a release bug. There are no RC tags; previews source from
`main`, never a side branch. The Electron artifact workflow is the desktop
rehearsal channel; no Tauri or desktop-preview build is maintained.

## Package requirements

Linux packages must carry the native helper's non-glibc libraries under
`resources/native/lib`. Packaging checks reject missing or host-resolved
libraries. AppImages use the static runtime, which needs no host libfuse2; test
the downloaded image on a clean host because build-runner libraries can hide
relocation errors.

Linux release AppImages include `gh-releases-zsync` update information and a
versioned `.AppImage.zsync` asset for AppImageUpdate/AppImageLauncher. Stable
images follow the latest stable GitHub release; previews follow the `preview`
release instead of downgrading to stable. The build uses `readelf` and
`zsyncmake` (Ubuntu package `zsync`) to embed this information before
regenerating electron-updater's blockmap and checksums; the existing in-app
updater still uses its separate channel manifest. Verify both update paths
against the final downloaded artifact, not the pre-publish build.

Electron update manifests use platform and architecture channels. Confirm every
manifest names an uploaded installer, reports the tagged version, and matches the
artifact bytes. Exercise at least one installed update hop before publication.

Signed Electron macOS releases require a paid Apple Developer identity and the
secrets above. A draft build without credentials can remain unsigned; a normal
publish refuses missing signing/notarization credentials and verifies both the
signature and stapled ticket. Do not accept `allow_unsigned` merely to close a
signing report. Tauri signing keys cannot sign Electron packages. Existing
artifact names, app IDs, data paths, and updater channels are compatibility
contracts.

## Retry and rollback

Re-run failed jobs in the same workflow run when possible. The release job
replaces only the current draft's Electron assets. It refuses to overwrite a
published release.

Clients only accept newer versions. To roll back, fix or revert the code, choose
a higher patch version with owner approval, test it, and publish that version.
Do not move or recreate an existing tag.
