# macOS Build, Signing & Notarization — Requirements & Verification

> **Archived Tauri notes.** Current Electron signing and notarization are
> documented in [RELEASING.md](RELEASING.md).

The canonical checklist for producing and verifying the VoiceStudio macOS
desktop bundle. It pairs with two helper scripts and the release workflow:

- **`scripts/verify-macos-signing.sh`** — runs every verification command below
  and reports PASS / WARN / FAIL. Strict mode (`--require-signed`) fails on any
  unsigned/un-notarized component.
- **`scripts/macos-dev-unquarantine.sh`** — local-dev-only quarantine stripper.
- **`.github/workflows/release.yml`** — builds, optionally signs (opt-in), and
  runs `verify-macos-signing.sh` on the macOS leg.

> **Current state of this repo:** Apple Developer ID signing + notarization is
> **opt-in and OFF by default** (see the *Configure Apple signing* step in
> `release.yml`). Default stable + all preview builds are **ad-hoc signed** —
> `tauri.conf.json > bundle > macOS > signingIdentity = "-"` gives the bundle a
> *valid* seal (free, no Apple account), so a downloaded copy shows the
> **GUI-bypassable "unidentified developer"** prompt (right-click → Open) instead
> of the un-bypassable **"app is damaged"** error a broken/missing seal produces.
> It is **not** notarized, so users still confirm the first launch once (see
> [`docs/install/macos.md`](install/macos.md#gatekeeper-quarantine), issues
> #134/#72). For a warning-free double-click, enable signing: fix the Apple
> secrets, set the repo variable `MACOS_SIGNING_ENABLED = true` (the env-set
> `APPLE_SIGNING_IDENTITY` overrides the `"-"` default) — at which point the
> verification below switches to **strict** automatically and gates the release.

### Signing tiers at a glance

| Tier | Cost | What the user sees | Terminal? |
|------|------|--------------------|-----------|
| **Unsigned / broken seal** | free | "app is damaged" — **no GUI bypass** | yes (`xattr`) |
| **Ad-hoc signed** (current default) | free | "unidentified developer" → right-click → Open / "Open Anyway" | **no** |
| **Developer ID, not notarized** | $99/yr | "unidentified developer" → right-click → Open | no |
| **Developer ID + notarized + stapled** | $99/yr | nothing — clean double-click | no |

---

## Requirements

When modifying or releasing this Tauri application:

1. **Produce a valid macOS `.app` bundle** via the current Tauri release process
   (`tauri-apps/tauri-action` in `release.yml`; locally, `bun desktop-prod`).
2. **Verify the app launches** on the latest supported macOS version.
3. **Signature integrity** — before release, run:
   ```bash
   codesign --verify --deep --strict --verbose=4 "<APP_PATH>"
   ```
4. **Gatekeeper acceptance**:
   ```bash
   spctl -a -vv "<APP_PATH>"
   ```
5. **No unsigned nested components** — no unsigned binaries, frameworks, helper
   apps, or dynamic libraries may exist inside the bundle.
6. **Sign nested before root** — every nested component is signed *before* the
   root application bundle.
7. **Production releases:**
   - Use an **Apple Developer ID** certificate.
   - **Notarize** the application.
   - **Staple** the notarization ticket (`xcrun stapler staple`).
   - macOS 15 (Sequoia) also rejects a DMG that *wraps* a signed `.app` but isn't
     itself notarized — **re-notarize + staple the DMG**, then re-upload it (see
     `docs/DESKTOP_RELEASE.md`, Phase E).
8. **Validate downloaded builds do NOT trigger:**
   - "App is damaged and can't be opened"
   - "Move to Trash"
   - any Gatekeeper rejection message
9. **Test both scenarios:** fresh download, and existing-user upgrade
   (`bun desktop-prod` = fresh; `bun desktop-prod:upgrade` = rebuild keeping data).
10. **Fail closed** — if signing or notarization fails, **stop the release and
    report the exact error** rather than producing an unsigned artifact. The
    strict-mode verification step in `release.yml` enforces this on the signed path.

### Required verification commands

```bash
codesign --verify --deep --strict --verbose=4 "VoiceStudio.app"
spctl -a -vv "VoiceStudio.app"
xcrun notarytool history          # needs Apple credentials / keychain profile
```

A release is considered **successful only when all verification checks pass.**

---

## How to verify

```bash
# Auto-discover the most recent built .app and report status (dev default):
scripts/verify-macos-signing.sh

# Verify a specific bundle or DMG:
scripts/verify-macos-signing.sh "path/to/VoiceStudio.app"
scripts/verify-macos-signing.sh ~/Downloads/VoiceStudio*.dmg

# Production gate — fail on ANY unsigned/un-notarized component:
scripts/verify-macos-signing.sh "VoiceStudio.app" --require-signed
```

The script runs checks 3–8 plus stapler/notarytool, and exits non-zero in strict
mode if anything fails — the machine-checkable form of requirement #10.

`xcrun notarytool history` is included when credentials are present in the
environment (`NOTARYTOOL_KEYCHAIN_PROFILE`, or `APPLE_ID` + `APPLE_PASSWORD` +
`APPLE_TEAM_ID`); otherwise it is skipped with a note.

---

## Local development

For **local test artifacts only**, you may remove the quarantine attribute so an
unsigned dev build launches without the right-click → Open dance:

```bash
scripts/macos-dev-unquarantine.sh "path/to/VoiceStudio.app"
```

> ⚠️ This is a **development convenience only** and is **never a substitute** for
> proper Developer ID signing + notarization of production releases. Shipped
> artifacts must be signed and notarized so end users never need it.

---

## Enabling signed + notarized stable releases

1. Obtain an Apple Developer ID (~$99/yr) and a Developer ID Application cert.
2. Add the secrets used by `release.yml`: `APPLE_CERTIFICATE`,
   `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`,
   `APPLE_PASSWORD`, `APPLE_TEAM_ID`.
3. Set repo variable **`MACOS_SIGNING_ENABLED = true`**.
4. Tag a `v*` release. The build then signs, notarizes, and the *Verify macOS
   signing* step runs in **strict** mode — a failure stops the release (req #10).
5. Confirm the DMG itself is notarized + stapled (macOS 15 requirement, #7).

See `docs/DESKTOP_RELEASE.md` (Phase E) for the full pipeline rationale.
