# macOS Signing & Notarization — Verification

How to verify the Electron macOS app's signature, notarization, and Gatekeeper
acceptance. Credentials and the release procedure live in
[RELEASING.md](RELEASING.md#credentials); end-user Gatekeeper help lives in
[install/macos.md](install/macos.md#gatekeeper-quarantine).

## How releases are signed

`electron-release.yml` packages macOS with electron-builder. When the
Developer ID certificate (`ELECTRON_MACOS_CSC_LINK`,
`ELECTRON_MACOS_CSC_KEY_PASSWORD`) and Apple notarization credentials
(`APPLE_API_KEY`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER`) are all present,
the build enables Hardened Runtime and notarization together
(`electron/electron-builder.config.mjs`). A publishing run refuses missing
credentials unless the owner explicitly sets `allow_unsigned=true`, and before
publication it runs:

```bash
codesign --verify --deep --strict "VoiceStudio.app"
spctl --assess --type execute --verbose=2 "VoiceStudio.app"
xcrun stapler validate "VoiceStudio.app"
```

Without credentials (artifact rehearsals, local `bun run dist`), packages keep
electron-builder's unsigned/ad-hoc defaults; users then see the
"unidentified developer" prompt described in the install guide.

| Tier | What the user sees | Terminal needed? |
|------|--------------------|------------------|
| Unsigned / broken seal | "app is damaged" — no GUI bypass | yes (`xattr`) |
| Ad-hoc signed | "unidentified developer" → right-click → Open / "Open Anyway" | no |
| Developer ID, not notarized | "unidentified developer" → right-click → Open | no |
| Developer ID + notarized + stapled | clean double-click | no |

## Requirements for a signed release

1. Every nested binary, framework, helper app and dynamic library (including the
   Rust native helper and bundled `uv`) is signed before the root bundle; no
   unsigned nested components.
2. The app is signed with a **Developer ID Application** certificate,
   notarized, and the ticket is stapled.
3. The microphone entitlement (`com.apple.security.device.audio-input` in
   `electron/build/entitlements.mac.plist`) is present in the signed app.
4. A downloaded DMG from the draft release opens without "damaged", "Move to
   Trash", or any other Gatekeeper rejection on a clean Mac, both as a fresh
   install and as an upgrade over the previous release.
5. If signing or notarization fails, stop the release and report the exact
   error rather than publishing an unsigned artifact.

## Verifying a bundle locally

`scripts/verify-macos-signing.sh` runs the codesign, Gatekeeper, stapler and
(when credentials are available) `notarytool` checks and reports PASS / WARN /
FAIL:

```bash
# Auto-discover the most recent built .app:
scripts/verify-macos-signing.sh

# A specific bundle or DMG (for example from electron/release/):
scripts/verify-macos-signing.sh "path/to/VoiceStudio.app"
scripts/verify-macos-signing.sh ~/Downloads/VoiceStudio-*.dmg

# Release gate — fail on any unsigned or un-notarized component:
scripts/verify-macos-signing.sh "VoiceStudio.app" --require-signed
```

`xcrun notarytool history` runs when credentials are present in the environment
(`NOTARYTOOL_KEYCHAIN_PROFILE`, or `APPLE_API_KEY` + `APPLE_API_KEY_ID` +
`APPLE_API_ISSUER`); otherwise it is skipped with a note.

For local packaging and verification, `APPLE_API_KEY` is the path to the `.p8`
file. In GitHub Secrets, it is the PEM file content; the workflow prepares the
path before invoking electron-builder. The API key authenticates notarization;
the Developer ID certificate and its password still sign the application.

The packaging workflow loads key preparation from its own commit, even when
checking out an older release tag. With complete certificate/API credentials,
it explicitly enables notarization and Hardened Runtime in electron-builder,
so older password-based configuration cannot silently skip notarization.

## Local development

For **local test artifacts only**, remove the quarantine attribute so an
unsigned build launches without the right-click → Open step:

```bash
scripts/macos-dev-unquarantine.sh "path/to/VoiceStudio.app"
```

This is never a substitute for Developer ID signing and notarization of
published releases.

The archived Tauri app used a different pipeline (`tauri-action`,
`APPLE_CERTIFICATE*` secrets, `MACOS_SIGNING_ENABLED`); none of it applies to
Electron.
