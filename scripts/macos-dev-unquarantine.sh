#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# macos-dev-unquarantine.sh — strip com.apple.quarantine from a LOCAL TEST
# artifact so you can launch an unsigned dev/preview build without the
# right-click → Open dance.
#
#   ⚠️  LOCAL DEVELOPMENT CONVENIENCE ONLY.
#   This is NEVER a substitute for proper Developer ID signing + notarization
#   of production releases. Real users must receive a signed, notarized build
#   (see docs/macos-signing-verification.md) — do not ship artifacts and tell
#   users to run this. Production signing is gated in electron-release.yml.
#
# Usage:
#   scripts/macos-dev-unquarantine.sh "path/to/VoiceStudio.app"
#   scripts/macos-dev-unquarantine.sh ~/Downloads/VoiceStudio*.dmg
#   scripts/macos-dev-unquarantine.sh        # auto-discover newest built .app
# ──────────────────────────────────────────────────────────────────────────
set -uo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "macos-dev-unquarantine: not macOS — nothing to do."
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-}"

if [ -z "$TARGET" ]; then
  TARGET="$(find "$REPO_ROOT/electron/release" -maxdepth 2 -type d -name '*.app' 2>/dev/null | head -1)"
  [ -z "$TARGET" ] && { echo "ERROR: no built .app found — pass an explicit path." >&2; exit 2; }
fi

[ -e "$TARGET" ] || { echo "ERROR: path does not exist: $TARGET" >&2; exit 2; }

echo "⚠️  DEV-ONLY: stripping com.apple.quarantine from:"
echo "    $TARGET"
echo "    (not a substitute for signing + notarization — see docs/macos-signing-verification.md)"

xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null || true

if xattr -pr com.apple.quarantine "$TARGET" >/dev/null 2>&1; then
  echo "✗ quarantine attribute still present — try: sudo xattr -dr com.apple.quarantine \"$TARGET\""
  exit 1
fi
echo "✓ quarantine cleared — the unsigned build will now launch locally."
