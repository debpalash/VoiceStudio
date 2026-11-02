#!/usr/bin/env bash
# Source in the packaging step: GitHub stores PEM text, electron-builder needs a path.
# Password credentials take precedence inside electron-builder; never inherit them.
set +x
unset APPLE_ID APPLE_APP_SPECIFIC_PASSWORD
if [ -n "${APPLE_API_KEY:-}${APPLE_API_KEY_ID:-}${APPLE_API_ISSUER:-}" ]; then
  for name in APPLE_API_KEY APPLE_API_KEY_ID APPLE_API_ISSUER; do
    if [ -z "${!name:-}" ]; then
      echo "::error::Notarization API credentials are incomplete: $name is missing" >&2
      return 1
    fi
  done
  : "${RUNNER_TEMP:?RUNNER_TEMP must identify the job temporary directory}"
  NOTARY_KEY_DIR="$(mktemp -d "$RUNNER_TEMP/apple-notary.XXXXXX")"
  trap 'rm -rf -- "$NOTARY_KEY_DIR"' EXIT
  (umask 077; printf '%s' "$APPLE_API_KEY" > "$NOTARY_KEY_DIR/AuthKey.p8")
  export APPLE_API_KEY="$NOTARY_KEY_DIR/AuthKey.p8"
fi
