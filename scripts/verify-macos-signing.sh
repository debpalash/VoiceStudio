#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# verify-macos-signing.sh — Gatekeeper / code-signing / notarization checks
#
# Implements the verification half of docs/macos-signing-verification.md.
# Runs the canonical Apple checks against a built bundle and reports a clear
# PASS / WARN / FAIL summary:
#
#   codesign --verify --deep --strict --verbose=4   (signature integrity)
#   spctl -a -vv --type execute                      (Gatekeeper acceptance)
#   per-nested-component codesign -v                 (no unsigned binaries)
#   xcrun stapler validate                           (notarization ticket)
#   xcrun notarytool history                         (notarization log, opt-in)
#
# Two modes:
#   • default (report-only) — for unsigned dev / preview builds. Missing
#     signature/notarization is reported as WARN, exit 0. This is the repo's
#     normal state: Apple signing is OPT-IN and OFF by default (see electron-release.yml
#     "Configure Apple signing"); unsigned bundles are expected there.
#   • --require-signed (strict) — for production. ANY unsigned component,
#     Gatekeeper rejection, or missing notarization ticket is a FAIL (exit 1),
#     so a broken release stops instead of shipping an unsigned artifact.
#
# Usage:
#   scripts/verify-macos-signing.sh [APP_OR_DMG_PATH] [--require-signed]
#
#   # auto-discover the Electron release bundle:
#   scripts/verify-macos-signing.sh
#   # strict gate against a specific bundle:
#   scripts/verify-macos-signing.sh "path/to/VoiceStudio.app" --require-signed
#   # verify a DMG (mounts read-only, finds the .app inside, detaches):
#   scripts/verify-macos-signing.sh "VoiceStudio_0.3.5_aarch64.dmg"
#
# Env equivalents: REQUIRE_SIGNED=1
# ──────────────────────────────────────────────────────────────────────────
set -uo pipefail

# ── macOS only ────────────────────────────────────────────────────────────
if [ "$(uname -s)" != "Darwin" ]; then
  echo "verify-macos-signing: not macOS (uname=$(uname -s)) — nothing to verify; skipping."
  exit 0
fi

# ── Parse args ────────────────────────────────────────────────────────────
TARGET=""
REQUIRE_SIGNED="${REQUIRE_SIGNED:-0}"
for a in "$@"; do
  case "$a" in
    --require-signed) REQUIRE_SIGNED=1 ;;
    --report-only)    REQUIRE_SIGNED=0 ;;
    -h|--help)        sed -n '2,40p' "$0"; exit 0 ;;
    *)                TARGET="$a" ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOUNT=""               # set if we attach a DMG, detached on exit
cleanup() { [ -n "$MOUNT" ] && hdiutil detach "$MOUNT" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# ── Resolve a .app bundle to verify ───────────────────────────────────────
resolve_app() {
  local t="$1"
  if [ -z "$t" ]; then
    t="$(find "$REPO_ROOT/electron/release" -maxdepth 2 -type d -name '*.app' 2>/dev/null | head -1)"
    [ -z "$t" ] && { echo "ERROR: no .app found under electron/release. Build first (bun run dist:dir) or pass an explicit path." >&2; exit 2; }
    echo "$t"; return
  fi
  case "$t" in
    *.dmg)
      MOUNT="$(hdiutil attach -nobrowse -readonly "$t" | tail -1 | grep -oE '/Volumes/.*$')"
      [ -z "$MOUNT" ] && { echo "ERROR: failed to mount DMG: $t" >&2; exit 2; }
      local app; app="$(find "$MOUNT" -maxdepth 2 -name '*.app' | head -1)"
      [ -z "$app" ] && { echo "ERROR: no .app inside DMG: $t" >&2; exit 2; }
      echo "$app"; return ;;
    *.app)
      [ -d "$t" ] || { echo "ERROR: not a directory: $t" >&2; exit 2; }
      echo "$t"; return ;;
    *) echo "ERROR: unsupported target (want .app or .dmg): $t" >&2; exit 2 ;;
  esac
}

APP="$(resolve_app "$TARGET")"

# Detect an ad-hoc signature: a VALID seal but not Developer-ID / not notarized.
# This is the free default (tauri.conf.json bundle.macOS.signingIdentity "-") —
# it makes a downloaded copy show the GUI-bypassable "unidentified developer"
# prompt instead of the un-bypassable "damaged" error.
IS_ADHOC=0
if codesign -dvv "$APP" 2>&1 | grep -q 'Signature=adhoc'; then IS_ADHOC=1; fi

# ── Result accounting ─────────────────────────────────────────────────────
FAILS=0
WARNS=0
hr() { printf '%s\n' "────────────────────────────────────────────────────────────"; }
# Demote a problem to WARN in report-only mode, FAIL in strict mode.
problem() { # problem <message>
  if [ "$REQUIRE_SIGNED" = "1" ]; then echo "  ✗ FAIL: $1"; FAILS=$((FAILS+1));
  else echo "  ⚠ WARN: $1"; WARNS=$((WARNS+1)); fi
}
ok() { echo "  ✓ $1"; }

echo
hr
echo "macOS signing verification"
echo "  bundle : $APP"
echo "  mode   : $([ "$REQUIRE_SIGNED" = 1 ] && echo 'REQUIRE-SIGNED (strict, production)' || echo 'report-only (dev/preview; unsigned OK)')"
hr

# ── 1. codesign --verify --deep --strict --verbose=4 ──────────────────────
echo "[1/6] codesign --verify --deep --strict --verbose=4"
CS_OUT="$(codesign --verify --deep --strict --verbose=4 "$APP" 2>&1)"; CS_RC=$?
printf '%s\n' "$CS_OUT" | sed 's/^/      /'
if [ $CS_RC -eq 0 ]; then ok "signature valid (deep, strict)"; else problem "codesign verification failed (rc=$CS_RC) — bundle is unsigned or signature is broken"; fi

# ── 2. spctl Gatekeeper assessment ────────────────────────────────────────
# Surfaces the exact "damaged" / "Move to Trash" / rejection text users hit.
echo "[2/6] spctl -a -vv --type execute  (Gatekeeper)"
SPCTL_OUT="$(spctl --assess -a -vv --type execute "$APP" 2>&1)"; SPCTL_RC=$?
printf '%s\n' "$SPCTL_OUT" | sed 's/^/      /'
if [ $SPCTL_RC -eq 0 ]; then
  ok "Gatekeeper accepts the bundle (signed + notarized)"
elif [ "$IS_ADHOC" = 1 ] && [ $CS_RC -eq 0 ]; then
  if [ "$REQUIRE_SIGNED" = 1 ]; then
    echo "  ✗ FAIL: ad-hoc only, not notarized — Gatekeeper won't auto-accept a production release"; FAILS=$((FAILS+1))
  else
    echo "  ⓘ ad-hoc signed (valid seal, not notarized): a downloaded copy shows the"
    echo "      \"unidentified developer\" prompt — users open it WITHOUT Terminal via"
    echo "      right-click → Open, or Settings → Privacy & Security → \"Open Anyway\"."
    echo "      Notarize (paid Apple ID) for a warning-free double-click."
  fi
else
  problem "Gatekeeper rejected (rc=$SPCTL_RC) and the signature is invalid/missing — users would see \"app is damaged\" / \"Move to Trash\" with NO GUI bypass"
fi

# ── 3. No unsigned nested components ──────────────────────────────────────
# Tauri/Apple require every nested Mach-O (helpers, frameworks, dylibs,
# external sidecars) to be signed before the root bundle. Walk them all.
echo "[3/6] nested components — every Mach-O must be signed"
NESTED_BAD=0; NESTED_TOTAL=0
while IFS= read -r f; do
  # Only Mach-O files (skip scripts, data, resources).
  file -b "$f" 2>/dev/null | grep -q 'Mach-O' || continue
  NESTED_TOTAL=$((NESTED_TOTAL+1))
  if ! codesign -v "$f" >/dev/null 2>&1; then
    NESTED_BAD=$((NESTED_BAD+1))
    echo "      unsigned: ${f#"$APP"/}"
  fi
done < <(find "$APP/Contents" -type f 2>/dev/null)
if [ "$NESTED_TOTAL" -eq 0 ]; then
  echo "      (no Mach-O files found to check)"
elif [ "$NESTED_BAD" -eq 0 ]; then
  ok "all $NESTED_TOTAL nested Mach-O components are signed"
else
  problem "$NESTED_BAD of $NESTED_TOTAL nested Mach-O components are unsigned (sign nested before the root bundle)"
fi

# ── 4. Notarization ticket stapled ────────────────────────────────────────
echo "[4/6] xcrun stapler validate  (notarization ticket)"
if command -v xcrun >/dev/null 2>&1; then
  STAP_OUT="$(xcrun stapler validate "$APP" 2>&1)"; STAP_RC=$?
  printf '%s\n' "$STAP_OUT" | sed 's/^/      /'
  if [ $STAP_RC -eq 0 ]; then ok "notarization ticket is stapled"; else problem "no stapled notarization ticket (rc=$STAP_RC) — notarize + staple before release"; fi
else
  echo "      xcrun not available — skipping"
fi

# ── 5. Notarization history (opt-in; needs credentials) ───────────────────
echo "[5/6] xcrun notarytool history  (audit log, opt-in)"
if command -v xcrun >/dev/null 2>&1 && [ -n "${NOTARYTOOL_KEYCHAIN_PROFILE:-}" ]; then
  xcrun notarytool history --keychain-profile "$NOTARYTOOL_KEYCHAIN_PROFILE" 2>&1 | head -20 | sed 's/^/      /' || true
elif command -v xcrun >/dev/null 2>&1 && [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_PASSWORD:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ]; then
  xcrun notarytool history --apple-id "$APPLE_ID" --password "$APPLE_PASSWORD" --team-id "$APPLE_TEAM_ID" 2>&1 | head -20 | sed 's/^/      /' || true
else
  echo "      no notarization credentials in env (NOTARYTOOL_KEYCHAIN_PROFILE or APPLE_ID/APPLE_PASSWORD/APPLE_TEAM_ID) — skipping audit log"
fi

# ── 6. Quarantine attribute (informational) ───────────────────────────────
echo "[6/6] quarantine attribute (com.apple.quarantine)"
if xattr -p com.apple.quarantine "$APP" >/dev/null 2>&1; then
  echo "      present — a downloaded copy would be gated by Gatekeeper until notarized"
  echo "      (local dev only: scripts/macos-dev-unquarantine.sh strips it — never a substitute for notarization)"
else
  ok "no quarantine attribute on this copy"
fi

# ── Summary ───────────────────────────────────────────────────────────────
hr
if [ "$FAILS" -gt 0 ]; then
  echo "RESULT: FAIL — $FAILS blocking issue(s), $WARNS warning(s)."
  echo "Release must stop. See docs/macos-signing-verification.md for remediation."
  hr; exit 1
elif [ "$WARNS" -gt 0 ]; then
  echo "RESULT: PASS (report-only) — $WARNS warning(s)."
  if [ "$IS_ADHOC" = 1 ]; then
    echo "Bundle is ad-hoc signed: users can open it WITHOUT Terminal (right-click → Open"
    echo "/ Settings → \"Open Anyway\"). Not notarized — sign + notarize (paid Apple ID)"
    echo "for a warning-free double-click."
  else
    echo "Bundle is unsigned/un-notarized, which is expected for dev/preview builds."
  fi
  echo "For a production release run with --require-signed after enabling Apple signing."
  hr; exit 0
else
  echo "RESULT: PASS — signed, Gatekeeper-accepted, notarized. Ready to ship."
  hr; exit 0
fi
