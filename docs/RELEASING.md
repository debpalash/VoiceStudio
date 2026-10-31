# Releasing VoiceStudio

Electron is the only maintained desktop distribution. Tauri ended at v0.5.3;
its signed updater feeds and installers are immutable compatibility assets.

Shared store inputs and platform handoffs are defined in
[`STORE-PUBLISHING.md`](STORE-PUBLISHING.md). Microsoft Store, Snap Store and
Mac App Store are not maintained release channels until each has completed its
first verified production publication.

Snap Store packaging runs through `.github/workflows/snap-store.yml`. Each
`v*` tag builds the strict Snap and uploads it to the Store's `candidate`
channel using `SNAPCRAFT_STORE_CREDENTIALS` from the `snap-store` environment.
Stable is never automatic: after the runbook checks, the repository owner
dispatches the workflow with `promote_revision`. Follow the
[`Snap Store runbook`](store-publishing/snap-store.md) for confinement checks,
candidate verification, evidence and rollback. Do not add a Snap to the
maintained-channel table below until its stable listing is independently
install-verified.

Microsoft Store EXE preparation uses the owner-dispatched
`microsoft-store.yml` workflow and the
[`Microsoft Store runbook`](store/microsoft-store.md). It builds a distinct
Store installer without changing direct-download artifacts. Staging its
immutable versioned URL requires owner approval; Partner Center submission and
publication remain manual owner actions.

Mac App Store preparation uses the manual, private `mac-app-store.yml` workflow
and the [`Mac App Store runbook`](../store/mac-app-store.md). The workflow cannot
build while the bundled-runtime compliance gate is unresolved and never uploads
to App Store Connect. MAS packages use App Store updates; direct macOS packages
keep the existing application updater and Developer ID notarization path.

## Credentials

Electron publication uses these GitHub Actions secrets:

- `ELECTRON_MACOS_CSC_LINK` and `ELECTRON_MACOS_CSC_KEY_PASSWORD` for the
  Developer ID Application `.p12` certificate and its password.
- `ELECTRON_MAC_APP_STORE_CSC_LINK`,
  `ELECTRON_MAC_APP_STORE_CSC_KEY_PASSWORD`,
  `ELECTRON_MAC_APP_STORE_INSTALLER_LINK`,
  `ELECTRON_MAC_APP_STORE_INSTALLER_KEY_PASSWORD` and
  `ELECTRON_MAC_APP_STORE_PROVISIONING_PROFILE` for private MAS packaging.
  These are separate from Developer ID credentials and do not authorize upload
  or publication.
- `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD` (not the Apple ID login password)
  and `APPLE_TEAM_ID` for macOS notarization and stapling.
- `ELECTRON_WINDOWS_CSC_LINK` and `ELECTRON_WINDOWS_CSC_KEY_PASSWORD` for
  Windows Authenticode signing. The macOS certificate cannot sign Windows apps.
- `APP_STORE_CONNECT_KEY_ID`, `APP_STORE_CONNECT_ISSUER_ID` and
  `APP_STORE_CONNECT_API_KEY` in the `app-store-connect` environment for the
  iOS TestFlight upload ([`mobile.md`](mobile.md#testflight-maintainers)).
  TestFlight is not a release channel.
- `ANDROID_UPLOAD_KEYSTORE`, `ANDROID_UPLOAD_KEYSTORE_PASSWORD`,
  `ANDROID_UPLOAD_KEY_ALIAS`, `ANDROID_UPLOAD_KEY_PASSWORD` and
  `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` in the `google-play` environment for the
  Play internal testing upload ([`mobile.md`](mobile.md#play-internal-testing-maintainers)).
  Play internal testing is not a release channel either.
- The repository `GITHUB_TOKEN` for draft creation and asset uploads.
- `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` for the Docker Hub mirror.
- `POSTHOG_PROJECT_TOKEN` (secret) and `POSTHOG_HOST` (repository variable),
  baked into packaged builds as the analytics token and host. They override the
  in-repo publishable token; analytics still sends nothing without a yes at the
  first-run consent prompt.

The archived `TAURI_SIGNING_PRIVATE_KEY*` secrets are retained only so the final
Tauri release can be audited. The Tauri release workflow has been removed; every
new version ships through `electron-release.yml`. `TAURI_SUNSET_TAG` must continue to identify v0.5.3 so Electron releases
can copy the final signed `latest.json` and `latest-user.json` feeds unchanged.
Those feeds must always point to immutable v0.5.3 Tauri assets; old Tauri clients
must never receive an Electron installer.

## Versioning

The root `package.json` is the single source of truth for the maintained app
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
   and inspect all five outputs: Linux x64, Windows x64, Windows ARM64, macOS
   arm64, macOS x64. Windows ARM64 is experimental: a failed leg does not block
   the other four.
   It first checks the setup screen, then installs and starts the managed Python
   runtime in a separate temporary profile on Linux, Windows and Apple Silicon.
   Intel Macs retain packaging/setup checks under their existing
   [UI/remote-only contract](install/macos.md). The local-runtime test downloads
   runtime packages, keeps model downloads disabled, verifies the live backend
   connection and clean shutdown, and removes the test profile. A setup-screen
   check alone is not evidence that runtime installation works.
3. Verify a packaged launch and managed backend startup on the changed platforms.
4. Fold the `changelog.d/` fragments into `## [Unreleased]` (`scripts/release.py` does this and deletes them; by hand: `uv run python scripts/changelog_fragments.py`), then rename `## [Unreleased]` in `CHANGELOG.md` to `## [X.Y.Z] — YYYY-MM-DD`.
   Lead with the largest user-visible change, keep Highlights to 3–5 bullets,
   include migration steps and real screenshots when relevant, and verify human
   contributors and bug reporters from the tag comparison and included PRs. The
   release workflow adds a `### Contributors` block to the GitHub Release notes
   by itself: everyone who committed since the latest stable release, plus the
   authors of PRs merged in that span (ranked by commits; bots, AI agents and
   unlinked placeholders are never listed). It does **not** find bug reporters,
   and it skips the block when the changelog section already has a
   `### Contributors` heading, so write one by hand when you want roles or
   reporters credited.
5. Run `uv run pytest tests/test_app_version.py tests/test_changelog_style.py -q`.
6. Audit contributor agreements: `git fetch origin cla-signatures`, then
   `GH_TOKEN=... python scripts/cla_audit.py`. Follow up with each unsigned
   contributor it lists, asking them to sign `.github/CLA-1.0.md` (or their
   employer the Corporate CLA). This step applies once the CLA workflow and
   `scripts/cla_audit.py` are on `main`. The audit aborts if Git cannot read
   any file’s history; restore access to missing objects and rerun before
   treating its contributor list as complete.
7. Confirm the version matches the intended tag.

## Build a release draft

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

The tag starts `.github/workflows/electron-release.yml`. It builds the same five
targets (a failed experimental Windows ARM64 leg does not block the others, and
its feed is checked only when it was published), validates packaged startup and updater metadata, creates or
updates a draft, and uploads:

- Electron installers named `VoiceStudio-<version>-<os>-<arch>.<ext>` (releases up to
  v0.5.7-beta.3 used `VoiceStudio-Electron-*`) and platform-specific
  `electron-stable-*.yml` feeds; feed file names never change, and each feed names
  its own installer, so older clients update across the rename
- per-platform `store-artifact-manifest-*.json` provenance records
- `SHA256SUMS.txt`
- authored CHANGELOG release notes
- immutable copies of the final Tauri updater feeds for old clients

Tag pushes never publish the Electron GitHub Release, and they do not build
Docker images (the Snap workflow above uploads tags to `candidate` only). Inspect the
draft and downloaded installers first.

If the workflow itself needs a fix after tagging, keep the tag immutable. Merge
and validate the workflow fix on `main`, then dispatch `electron-release.yml`
from `main` with `release_tag=vX.Y.Z`. Every build still checks out the exact tag;
only the workflow definition comes from `main`.

## Publish

Dispatch `electron-release.yml` for the exact tag with `publish=true`. Signing
and notarization are required by default. The owner may explicitly set
`allow_unsigned=true`. Never select that exception without the owner's decision.
Every build, published or not, verifies what each platform leg actually ships
and the release notes open with an **Installer signing** table built from those
checks (✅ signed and notarized, ⚠️ unsigned or ad hoc, with what users will
see). A macOS bundle with an invalid signature always fails, and a platform
whose signing credentials are configured fails if its installers are not signed
with them, even with `allow_unsigned=true`.
The owner can delegate it: accounts listed in the `UNSIGNED_RELEASE_PUBLISHERS`
Actions variable (comma-separated logins, Settings → Secrets and variables →
Actions → Variables; only admins can edit it) may also dispatch or re-run an
unsigned publish. Remove a login to revoke it; an empty variable means owner only.

Publishing the GitHub Release triggers the Docker workflow for that tag. Docker
release tags are never built from a tag push, and `:latest` only ever tracks
`main`; a release does not move it.

After publication, verify each maintained channel:

| Channel             | Workflow                           | Verification                                                                                                                                                 |
| ------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| GitHub Release      | `electron-release.yml`             | Linux x64, Windows x64 (+ ARM64 when its leg passed), macOS arm64 and x64 installers, updater feeds, checksums, CHANGELOG notes, retained v0.5.3 Tauri feeds |
| Phone downloads     | `electron-release.yml` → `mobile-release.yml` | Signed Android APK/AAB and unsigned iOS sideload IPA from the same tag |
| GHCR CUDA           | `docker.yml` (release published)   | `:X.Y.Z`, `:X.Y`, `:stable` manifests                                                                                                                        |
| GHCR ROCm           | `docker.yml` (release published)   | `:X.Y.Z-rocm`, `:X.Y-rocm`, `:stable-rocm` manifests                                                                                                         |
| GHCR CUDA arm64     | `docker.yml` (release published)   | `:X.Y.Z-cuda-arm64`, `:X.Y-cuda-arm64`, `:stable-cuda-arm64` manifests (`linux/arm64`)                                                                       |
| Docker Hub          | `docker.yml` (release published)   | Matching CUDA/ROCm/CUDA arm64 tags                                                                                                                           |
| Docker Hub overview | `dockerhub-description.yml`                       | Read the `Update Docker Hub description` step log; the step may continue after a 403                                                                         |
| Rolling containers  | `docker.yml` on `main` pushes only | `:latest`, `:main`, `:rocm` and `:cuda-arm64` timestamps move; a release never moves them                                                                    |

A missing channel is a release bug. There are no RC tags; the one prerelease is
the opt-in **Beta** channel (below). Previews between betas are builds from
`main` (the `electron-build.yml` artifacts or Docker `:latest`), never a side
branch. No Tauri build is maintained.

## Release helper

`make release` (or `uv run python scripts/release.py`) cuts a release from any
branch. It shows the last release and the version on `main`, then asks **stable
or beta** and suggests the new tag (`v0.5.7`, or `v0.5.7-beta.1`, `-beta.2`, ...
for a beta; the beta suffix is always `-beta.N`). If `package.json` already names
the upcoming release it uses that version; if that version is already tagged it
moves to the next patch. Choices are arrow-key menus (up/down or `j`/`k`,
Enter; Esc cancels) that fall back to typed answers when there is no terminal.
It then asks **draft or publish**, prints the plan
(including switching to `main` and fast-forwarding it), and only proceeds when you
type the tag name.

- **Two runs, because `main` only changes through pull requests.** The first
  run bumps the three version files in lockstep, runs `uv lock`, writes the
  `CHANGELOG.md` section from `[Unreleased]` (a stable release renames it; a
  beta copies it), runs the version guards, commits on `release/vX.Y.Z[-beta.N]`
  and opens its pull request. Once that merges, run the helper again with the
  same version: `main` already has it, so it changes nothing and only tags.
- **Draft:** the tag push builds a draft release; nothing is public.
- **Publish:** the tag push, then dispatches `electron-release.yml` with
  `publish=true`. It lists which platforms have signing secrets (macOS needs the
  Developer ID and notarization set, Windows needs `ELECTRON_WINDOWS_CSC_LINK` and
  its password) and, for any platform without them, stops unless you explicitly
  accept shipping that platform unsigned. The dispatch's `allow_unsigned` flag
  only accepts platforms without credentials: a platform with signing configured
  must still produce signed installers, and the notes' **Installer signing**
  table shows each platform's measured result.
- `--publish vX.Y.Z[-beta.N]` (`make release-publish TAG=…`) publishes an existing
  draft build later.
- `--dry-run` (`make release-dry`) prints the plan and changes nothing.

It does not replace the post-release verification below; run it every time.

## Mobile apps

Phone apps build from the same `vX.Y.Z` or `vX.Y.Z-beta.N` tag as desktop.
`electron-release.yml` calls `mobile-release.yml` and waits for the signed
Android APK/AAB and unsigned iOS sideload IPA before creating or publishing the
GitHub Release. All downloads share its version and `SHA256SUMS.txt`. A failed
phone build blocks publication; do not publish a desktop-only release manually.
Docker CUDA, ROCm and CUDA arm64 builds are dispatched for that same tag after
publication; verify their completion before calling the release complete.

App Store signing and store promotion remain separate from GitHub downloads.
The unsigned IPA requires signing with the user's own Apple ID to sideload; it
is not a TestFlight build. Apple certificate availability cannot suppress this
artifact. Manual `mobile-release` runs can set `release_ref` to an existing tag
and `signed_ios=false` to recover phone artifacts without changing the tag.
Legacy `mobile-v*` tags remain supported only for historical standalone builds.


## Beta channel

Beta is a second release channel cut from `main`, for testers who opt in under
**Settings → Updates → Update channel** ([user guide](update-channels.md)). It
has no soak period, no side branch and no cherry-picks.

| | Stable | Beta |
|---|---|---|
| Tag and `package.json` version | `vX.Y.Z` | `vX.Y.Z-beta.N` |
| GitHub Release | full release, becomes `latest` | **prerelease**, never `latest` |
| Update feed | `electron-stable-<os>-<arch>` | `electron-beta-<os>-<arch>` |
| Docker | `:X.Y.Z`, `:X.Y`, `:stable` (and `-rocm`, `-cuda-arm64`) | `:beta`, `:X.Y.Z-beta.N` (and `-rocm`, `-cuda-arm64`) |
| Snap / Microsoft Store / Mac App Store | yes | no (Stable-only) |
| Tauri sunset feeds | copied into the release | not copied |

Cutting a beta: with owner approval of the version, bump `package.json`,
`pyproject.toml` and `_FALLBACK_VERSION` to `X.Y.Z-beta.N` together, run `uv lock`
(it records the PEP 440 form `X.Y.ZbN`; the lockstep test compares versions, not
strings), add a
`## [X.Y.Z-beta.N]` section to `CHANGELOG.md`, tag from `main`, then run
`electron-release.yml` with `publish`. The workflow derives the channel from
the version (`prepare_electron_release.py --print-channel`), stamps it into every
platform feed, publishes with `--prerelease --latest=false`, and dispatches
Docker with `promote_stable=false`. Any other prerelease shape (`-rc`, `-alpha`)
is rejected.

Promoting to Stable is a normal `vX.Y.Z` release from `main`. Beta users roll
onto it automatically because the updater compares semver (`X.Y.Z-beta.N` sorts
below `X.Y.Z`). The verification table above applies to Stable; for a beta also
confirm the GitHub Release is flagged *Pre-release*, `releases/latest` still
points at the last stable, and `:stable` and `:X.Y` did not move.

## Package requirements

Linux packages must carry the native helper's non-glibc libraries under
`resources/native/lib`. Packaging checks reject missing or host-resolved
libraries. AppImages use the static runtime, which needs no host libfuse2; test
the downloaded image on a clean host because build-runner libraries can hide
relocation errors.

Every direct desktop installer except Intel macOS ships the OmniVoice GGUF
runtime under `resources/bin/` (#2780). `electron-release.yml` and the
packaging rehearsal call `build-omnivoice-tts.yml` (`desktop: true`) and stage
each platform's artifact into `electron/build/omnivoice-tts/`; Windows ARM64
ships the x64 build. Signing rewrites the binary, so the build refreshes
`bin/checksums.sha256` after signing (`electron/omnivoice-tts-bundle.mjs`; on
macOS before the bundle is sealed). `packaging-contract.mjs` fails a release
whose installer lacks the runtime, whose manifest no longer matches the shipped
bytes, or (macOS) whose runtime is unsigned. A failed runtime build fails the
release; there is no fallback that ships without it.

Linux release AppImages include `gh-releases-zsync` update information and a
versioned `.AppImage.zsync` asset for AppImageUpdate/AppImageLauncher. Release
images follow the latest stable GitHub release. The build uses `readelf` and
`zsyncmake` (Ubuntu package `zsync`) to embed this information before
regenerating electron-updater's blockmap and checksums; the existing in-app
updater still uses its separate channel manifest. Images up to v0.5.7-beta.3 look
for `VoiceStudio-Electron-*-linux-x64.AppImage.zsync`, so AppImageUpdate cannot
move them to a renamed release; they update through the in-app updater. Verify both update paths
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

## Merge queue (maintainers)

With "Require branches to be up to date" on, every merge to `main` puts every other open PR behind, and each needs an update plus a full CI run. The merge queue replaces that: a PR that passes its checks joins the queue, GitHub builds a temporary commit of `main` plus the queued PRs, runs CI once on it, and merges.

The workflows are ready: `ci.yml` runs on `merge_group`, and `merge-queue-checks.yml` reports the PR-level required checks ("CLA gate", "Commit identities", "CLA", "Commit identity policy") on the queue's commit, because they cannot run there. `tests/test_merge_queue.py` keeps those names equal to the ruleset's required list; change both together.

To turn it on (Settings → Rules → Rulesets → `main`):

1. Add **Require merge queue**: method *Merge commit*, build concurrency 1–3, minimum group size 1, wait time 0–5 minutes, status check timeout 60 minutes.
2. Under **Require status checks to pass**, untick **Require branches to be up to date before merging**; the queue makes it unnecessary.
3. Merge PRs with **Merge when ready** (the queue button) instead of **Merge**. `gh pr merge N --merge` joins the queue once it is on.

Try it on one PR first. If a queued PR stalls, a required check name in the ruleset no longer matches a job or status the queue commit reports: compare the ruleset with `tests/test_merge_queue.py`.
