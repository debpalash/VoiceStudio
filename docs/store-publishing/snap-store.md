# Snap Store runbook

## Current status

- Branch: `feat/snap-store`, based on `feat/store-publishing-foundation`.
- Lifecycle: every `v*` tag uploads to `candidate`; stable is a manual promote.
- Package: Electron x64 Snap alongside the existing AppImage and Debian packages.
- Confinement: `strict`, using the `core22` base supported by the pinned
  electron-builder. Recheck the supported bases before the first submission.
- Update owner: Snap Store. The in-app updater is unavailable in this build.
- Snap name: `voicestudio` (`SNAP_NAME` in the workflow).

The workflow never changes listing copy, visibility or the `stable` channel on
its own.

## Package and interface contract

Build with `VOICESTUDIO_DISTRIBUTION=snap-store`. The resulting package embeds
`snap-store` and `store` as its distribution and update-owner metadata. Direct
Linux builds still produce AppImage and Debian packages with their existing
updater metadata.

The Snap requests only interfaces needed by maintained desktop behaviour:

| Interface | Purpose | Connection expectation |
| --- | --- | --- |
| `browser-support` | Electron APIs under strict Snap confinement (`allow-sandbox: false`) | Automatic by default |
| `desktop`, `desktop-legacy`, `x11`, `wayland`, `unity7`, `gsettings` | Desktop integration | Automatic on supported desktops |
| `network` | User-started runtime and model downloads | Automatic |
| `network-bind` | Local backend and opt-in LAN sharing | Automatic |
| `audio-playback` | Preview and generated audio playback | Automatic |
| `audio-record` | User-started recording and dictation | Manual unless the Store grants auto-connect |
| `opengl` | GPU/display access | Automatic |
| `home` | Non-hidden user files and project folders | Automatic on traditional distributions |

`removable-media` is intentionally absent. Electron's file chooser uses the
desktop portal, which grants access to the exact user-selected file, including
files on removable media. Add a broader interface only if confinement testing
proves a maintained workflow cannot use the portal, and document the Store
review request here first.

Snap rewrites `HOME` into its writable user-data area. Consequently Electron
settings, the managed runtime, backend data, models and caches remain writable
and survive refresh through Snap's data-copy lifecycle. User-selected imports
and exports use portal paths. Validate custom runtime and data relocation
separately: a selected portal file is not guaranteed to retain executable
permissions.

## Build and inspect

Dispatch `.github/workflows/snap-store.yml` from any branch to build without
uploading, or on an x64 Linux builder:

```bash
export VOICESTUDIO_DISTRIBUTION=snap-store
export VOICESTUDIO_RUST_TARGET=x86_64-unknown-linux-gnu
node scripts/validate_store_foundation.mjs --distribution=snap-store
bun install --frozen-lockfile
bun run check:electron
cd electron
bun x electron-builder --config electron-builder.config.mjs --linux snap --x64 --publish never
node tests/packaging-contract.mjs --artifact
node ../scripts/verify_snap_artifact.mjs
node ../scripts/create_store_artifact_manifest.mjs --distribution=snap-store --platform=linux --arch=x64
node ../scripts/create_snap_store_assets.mjs
```

Inspect these workflow artifacts before installing:

- `VoiceStudio-<version>-linux-x64.snap`
- `VoiceStudio-<version>-snap-verification.json`
- `store-artifact-manifest-linux-x64.json`
- `snap-store-assets/icon.png`, `banner.png` (3:1 listing banner), five screenshots and `listing-assets.json`

The package verifier also requires electron-builder's desktop runtime scripts
and libraries to be expanded at the Snap root. A nested template archive or a
missing `desktop-init.sh` is a blocking packaging failure.

The asset manifest derives only from `store/assets.json`; do not fork listing
copy or image sources in the Snap dashboard. The workflow never edits the listing,
so upload `icon.png`, `banner.png` and the screenshots in the Snap dashboard's
Listing page by hand. The banner is rendered from `store/snap/banner.html` at
1920x640 (open it in Chromium and capture the viewport) and must stay 3:1.

## Snapcraft Builds (dashboard)

`snap/snapcraft.yaml` lets the snapcraft.io **Builds** tab build the snap too.
It repackages the official Linux `.deb` of the latest GitHub release (checked
against the release's `SHA256SUMS.txt`) with the same strict confinement and
interfaces as the release workflow, and disables the in-app updater because
snapd owns updates. `tests/test_snapcraft_yaml.py` keeps its interfaces equal
to the `snap-store` target in `electron/electron-builder.config.mjs`. The
release workflow above remains the primary path; if Snapcraft Builds is
connected, release its revisions to `edge` and promote as usual.

## Confinement validation

Install the rehearsal locally, then connect the recording interface:

```bash
sudo snap install --dangerous <file>.snap
sudo snap connect voicestudio:audio-record
snap connections voicestudio
snap run voicestudio
```

The Snap follows Canonical's [Electron guidance for browser-support](https://snapcraft.io/docs/reference/interfaces/browser-support-interface/):
`allow-sandbox: false` and `--no-sandbox` in the packaged Snap launcher, while
retaining `confinement: strict`. The interface auto-connects by default without
the privileged `allow-sandbox: true` Store approval. Fresh installations should
start from the desktop launcher without a terminal or manual sandbox setup.
An explicitly disconnected interface is a user choice; inspect
`snap connections voicestudio` and reconnect it manually if needed. When testing
an upgrade from an older local revision, verify the existing connection state
rather than assuming the metadata change reconnects it.

The launcher flag is limited to Snap artifacts. AppImage, Debian, macOS,
Windows and development launches retain their existing sandbox configuration.
The old preflight and optional Zenity dialog are removed.

Security tradeoff: strict Snap confinement still restricts the application via
AppArmor, seccomp and namespaces, but Chromium's per-process sandbox is disabled
for every process in this Snap, including the embedded website preview and
sponsor form. These are different security boundaries, not equivalent layers.
Keep context isolation, disabled Node integration for remote content, isolated
browser sessions and denied browser permissions. Electron's general
[guidance](https://www.electronjs.org/docs/latest/tutorial/sandbox) advises against
production `--no-sandbox`; this package deliberately follows Canonical's
Snap-specific approach. Do not extend this flag to other distributions or
switch to classic/devmode confinement.

Complete every item on a clean supported host:

- clean install, automatic `browser-support` connection and first launch from both desktop and terminal;
- first-run managed Python installation and offline relaunch;
- model installation followed by offline synthesis;
- microphone capture, audio playback and dictation;
- website preview, address-bar navigation and sponsor-form display;
- GPU discovery and one GPU-backed generation where hardware is available;
- file import/export in the home directory and on removable media through the portal;
- project open/save and watch-folder access;
- custom runtime and data-location selection, restart and cleanup;
- opt-in LAN sharing, including backend binding and a second-device request;
- refresh to a newer candidate revision and rollback to the previous revision;
- removal without affecting AppImage/Debian data or installation.

A failed item is a branch blocker. Prefer a narrower interface or portal flow;
do not switch to classic confinement merely to pass the test.

## Submission and evidence

One-time setup, by the owner:

1. Register the name and confirm the Snapcraft account controls it:
   `snapcraft register voicestudio`, then `snapcraft status voicestudio`.
2. Create a GitHub environment named `snap-store` (optionally with required
   reviewers) and store upload credentials in it as
   `SNAPCRAFT_STORE_CREDENTIALS`:

   ```bash
   snapcraft export-login --snaps=voicestudio \
     --acls=package_access,package_push,package_update,package_release \
     --expires=<date> credentials.txt
   ```

Pushing a final `vX.Y.Z` tag then builds the Snap, checks the tag matches `package.json`,
uploads it and releases it to `candidate`. The workflow uploads first and checks
the newest `v*` tag just before releasing, so an older tag's revision is
uploaded without moving `candidate`. The run
summary records the Store revision and channel. A tag build can be retried by dispatching the workflow from the tag
with `upload` enabled.

Stable promotion requires a separate owner approval after independent install,
refresh and rollback verification: the repository owner dispatches the
workflow with `promote_revision` set to the verified candidate revision. The
job refuses any other triggering account.

Before promoting to stable, obtain Store auto-connect approval for
`audio-record` and verify recording on a fresh Store installation. The
`browser-support` plug uses its default auto-connect policy without requesting
privileged Chromium sandbox access. Record the actual connection state and
first-launch evidence from both a fresh install and an upgrade.

For each submitted revision retain:

- the package and `store-artifact-manifest-linux-x64.json`;
- the Store revision number and channel;
- the downloaded Snap assertion and its SHA-256 digest;
- automated/manual review findings and their resolutions;
- `snap connections`, install, refresh, rollback and offline-test logs;
- the workflow run URL and source commit.

Set `signingVerification` in the final provenance record only after the Store
assertion has been downloaded and matched to the submitted revision.

Recheck Canonical's current documentation before submission: [strict
confinement](https://snapcraft.io/docs/explanation/security/snap-confinement/),
[interfaces](https://snapcraft.io/docs/reference/interfaces/), [desktop file
portals](https://snapcraft.io/docs/explanation/snap-development/xdg-desktop-portals/)
and [revision/channel management](https://documentation.ubuntu.com/snapcraft/stable/how-to/publishing/manage-revisions-and-releases/).

## Rollback or delisting

To recover one test machine, use `sudo snap revert voicestudio` or specify the
known-good revision. For a channel rollback, have the owner release the previous
known-good revision back to that channel, verify a refresh on a separate host,
and record the new channel map. Closing a channel or changing listing visibility
is an owner action; document its effect on existing installations before doing
either.
