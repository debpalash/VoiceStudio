# Mac App Store runbook

Status: private Apple silicon development-signed sandbox validation is in
progress. App Store distribution remains blocked; no package has been submitted.

App Store Connect: [VoiceStudio Pro for Mac](https://appstoreconnect.apple.com/apps/6829361961/distribution/macos/version/inflight).

Bundle identifier: `sh.voicestudio.pro`, recorded in
`store/metadata.json` → `identity.macAppStore`. Only MAS packages use this
identifier; other distribution targets retain `com.voicestudio.desktop`.
Provisioning profiles must authorize the MAS identifier.

The manual `mac-app-store.yml` workflow prepares private, signed MAS packages
for arm64 and x64. It has read-only repository permissions, never uploads to App
Store Connect and never starts TestFlight or publication. Direct Developer ID
DMG/ZIP builds retain their existing configuration, updater and notarization.

## Current blockers

The workflow must remain blocked at
`validate_store_foundation.mjs --distribution=mac-app-store` until all of these
are resolved in the shared compliance ledger and packaging contract:

- approve every locked Python runtime and wheel for redistribution, including
  the PyAV/FFmpeg chain;
- complete signed sandbox validation of the relocatable Apple silicon Python
  runtime, including model generation and offline restart. Intel Macs retain
  their existing remote-backend UI and do not bundle an arm64 interpreter;
- finish security-scoped access for imported media and persistent user-selected
  directories; export writes already use scoped bookmarks;
- validate the native dictation helper, microphone, accessibility, local
  backend listener and child processes in a signed sandbox build;
- resolve any user-visible scope difference consistently across platforms with
  owner approval before changing the shared metadata.

Do not change the `bundled-only` runtime policy or mark a downloaded executable
as approved merely to make the gate pass.

The MAS build stages Python and media tools before signing; it does not ship
`uv`, launch a user-selected interpreter or install a managed `.venv`. Runtime
code comes from the signed resources. Model weights remain explicit downloads
into the sandbox container. Optional engines that normally install sidecars or
execute model-supplied Python still need packaged code and sandbox validation;
do not silently remove those capabilities or declare that catalogue ready.

The private development candidate has passed deep/strict signature validation
and an offline backend startup from its own `sh.voicestudio.pro` container on
2026-11-01. This does not establish Apple Distribution signing, receipt
validation, microphone/accessibility behavior or persistent external-folder
access. Those checks remain separate release evidence.

## Apple requirements checked

Checked 2026-10-14 against Apple documentation:

- [App Sandbox is required for Mac App Store distribution](https://developer.apple.com/documentation/security/app-sandbox).
- [Open and save panels grant access to user-selected files; persistent access uses security-scoped bookmarks](https://developer.apple.com/documentation/security/accessing-files-from-the-macos-app-sandbox).
- [Builds are associated with the App Store Connect record by bundle ID and version](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds/).

Re-check these pages immediately before submission because Apple can change
upload, SDK and review requirements.

## Build and validation

The manual workflow stages a fresh, frozen Python 3.11 environment on Apple
silicon and builds the GGUF helper at the pinned source revision. For an
equivalent private local runtime input:

```bash
export UV_PROJECT_ENVIRONMENT=/private/tmp/voicestudio-mas-python
export UV_PYTHON_PREFERENCE=only-managed
uv python install 3.11
uv sync --frozen --no-dev --no-editable --no-install-project --python 3.11
"$UV_PROJECT_ENVIRONMENT/bin/python" scripts/stage_mas_python.py \
  --python "$UV_PROJECT_ENVIRONMENT/bin/python" --lock uv.lock \
  --output electron/build/python-runtime
"$UV_PROJECT_ENVIRONMENT/bin/python" scripts/stage_mas_media.py \
  --download --output electron/build/media-tools
```

Both staging commands refuse to overwrite existing outputs. They inventory
bytes and preserve licence notices; their manifests explicitly retain pending
redistribution clearance. No staging result overrides the compliance gate.

Phonemizer's pinned espeak adapter normally copies a native library into a
temporary directory, which the Mac sandbox refuses to load. The MAS stager
records a source-hash-checked SDK patch and provides 128 separately signed
library images inside the runtime. Each live wrapper leases its own native
state; cleanup terminates that instance before it can be reused. No native
code is created or copied at runtime. A changed upstream SDK fails staging
until the adapter is reviewed again. Direct downloads retain their upstream
phonemizer behavior.

Local checks that do not require Apple credentials:

```bash
node scripts/validate_store_foundation.mjs --distribution=mac-app-store
bun run --cwd electron typecheck
VOICESTUDIO_DISTRIBUTION=mac-app-store node electron/tests/packaging-contract.mjs
uv run pytest tests/test_mac_app_store_workflow.py -q
uv run pytest tests/test_mas_python_runtime.py tests/test_mas_media_runtime.py \
  tests/test_bundled_code_policy.py tests/test_bundled_espeak.py -q
```

The first command is expected to fail while the bundled-runtime ledger remains
unresolved. A successful signed workflow must retain:

- `VoiceStudio-MacAppStore-<version>-mac-<arch>.pkg`;
- `apple-signing-darwin-<arch>.json`;
- `store-artifact-manifest-darwin-<arch>.json`.

The provenance manifest must identify `mac-app-store`, `store` updates,
`bundled-only` runtime code, `sandbox-scoped` file access and valid Apple
Distribution evidence. Record TestFlight receipt and launch evidence separately
after owner-approved upload; build-time signature evidence is not receipt proof.

`packaging-contract.mjs --artifact` also verifies the complete signed Python
inventory against `uv.lock` and the target architecture, rejects unrecorded
files, and checks the signed media binaries against their acquisition record.
Native signing refreshes only the affected hashes before sealing the app.

## Owner actions

Before a private build, the owner provides repository secrets for the App Store
application certificate, installer certificate and provisioning profile:

- `ELECTRON_MAC_APP_STORE_CSC_LINK`
- `ELECTRON_MAC_APP_STORE_CSC_KEY_PASSWORD`
- `ELECTRON_MAC_APP_STORE_INSTALLER_LINK`
- `ELECTRON_MAC_APP_STORE_INSTALLER_KEY_PASSWORD`
- `ELECTRON_MAC_APP_STORE_PROVISIONING_PROFILE`

The owner must also reserve the App Store Connect record, approve seller/legal
details, privacy answers, age rating, territories and any scope reduction. Never
place credentials or provisioning files in the repository.

After both architecture artifacts pass signed sandbox testing, request separate
owner approval before uploading to App Store Connect, enabling TestFlight,
submitting for review, scheduling or publishing.

## Rollback and delisting

Before publication, discard the private workflow artifacts and fix the branch.
After publication, stop sale in App Store Connect if immediate removal is
required. Otherwise fix or revert the code, obtain approval for a higher patch
version and submit that version; never move or recreate an existing version or
alter the direct-download update feed.

Lifecycle: `built -> validated -> submitted -> approved -> scheduled -> published -> verified`.
