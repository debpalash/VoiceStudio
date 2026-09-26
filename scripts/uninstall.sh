#!/usr/bin/env bash
#
# VoiceStudio — clean uninstaller (macOS + Linux).
#
# Finds every folder VoiceStudio wrote (app data, the managed Python env, config,
# logs) and — separately, because it's a SHARED cache — the Hugging Face model
# cache, prints each with its size, and removes them. Dry-run by default: it
# prints what it WOULD delete and stops, so you always see the plan first.
#
#   scripts/uninstall.sh            # dry-run: list targets + sizes, delete nothing
#   scripts/uninstall.sh --yes      # delete the VoiceStudio data/env/config/logs
#   scripts/uninstall.sh --yes --models   # also delete the shared HF model cache
#
# Honors custom locations via the same env vars the app reads:
#   OMNIVOICE_DATA_DIR, OMNIVOICE_CACHE_DIR, HF_HOME, HF_HUB_CACHE
# Export the ones you set for VoiceStudio before running, and it targets those.
#
# It NEVER deletes the app binary itself (that's a per-platform step — see
# docs/install/uninstall.md), and it never touches anything outside the paths
# it lists. Mirrors backend/core/config.py and the Electron setup flow.
set -euo pipefail

APPLY=0
INCLUDE_MODELS=0
REMOVE_APP=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) APPLY=1 ;;
    --models) INCLUDE_MODELS=1 ;;
    --app) REMOVE_APP=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed '1d'
      exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

IDENTIFIER="com.debpalash.omnivoice-studio"
OS="$(uname -s)"

# ── Resolve platform default paths (mirrors the app) ────────────────────────
data_default=""
config_default=""
logs_extra=()
models_default=""
case "$OS" in
  Darwin)
    data_default="$HOME/Library/Application Support/OmniVoice"
    config_default="$HOME/Library/Application Support/$IDENTIFIER"
    logs_extra=("$HOME/Library/Logs/OmniVoice" "$HOME/Library/Logs/$IDENTIFIER")
    models_default="$HOME/.cache/huggingface"
    ;;
  Linux)
    data_default="$HOME/.omnivoice"
    config_default="${XDG_DATA_HOME:-$HOME/.local/share}/$IDENTIFIER"
    # The BACKEND writes its own logs outside the app-data dir — see
    # backend_log_path() in src-tauri/src/backend.rs. Missing this left a stray
    # log dir behind on every Linux uninstall.
    logs_extra=("${XDG_STATE_HOME:-$HOME/.local/state}/VoiceStudio")
    models_default="$HOME/.cache/huggingface"
    ;;
  *)
    echo "This script supports macOS and Linux. On Windows use scripts/uninstall.ps1." >&2
    exit 1 ;;
esac

# ── Apply env overrides the app honors ──────────────────────────────────────
DATA_DIR="${OMNIVOICE_DATA_DIR:-$data_default}"
# Model cache precedence matches the app: OMNIVOICE_CACHE_DIR → HF_HOME → HF_HUB_CACHE → default.
MODELS_DIR="${OMNIVOICE_CACHE_DIR:-${HF_HOME:-${HF_HUB_CACHE:-$models_default}}}"
# Durable per-user env file — backend/core/user_env.py, same path on every OS.
# It persists OMNIVOICE_CACHE_DIR (and can hold HF_TOKEN); leaving it behind
# silently redirected a fresh reinstall's model cache to the old location.
USER_ENV_DIR="$HOME/.config/omnivoice"

# ── Collect existing targets ────────────────────────────────────────────────
app_targets=()

# ── Optional: the prebuilt app installed by `curl … | sh` (default mode) ────
if [ "$REMOVE_APP" -eq 1 ]; then
  case "$OS" in
    Darwin)
      for app_dir in "/Applications/VoiceStudio.app" "$HOME/Applications/VoiceStudio.app"; do
        [ -e "$app_dir" ] && app_targets+=("$app_dir")
      done
      ;;
    Linux)
      [ -e "$HOME/.local/bin/VoiceStudio" ] && app_targets+=("$HOME/.local/bin/VoiceStudio")
      ;;
  esac
fi

[ -e "$DATA_DIR" ] && app_targets+=("$DATA_DIR")
[ -e "$config_default" ] && app_targets+=("$config_default")
[ -e "$USER_ENV_DIR" ] && app_targets+=("$USER_ENV_DIR")
for d in "${logs_extra[@]:-}"; do [ -n "$d" ] && [ -e "$d" ] && app_targets+=("$d"); done

human_size() { du -sh "$1" 2>/dev/null | cut -f1 || echo "?"; }

echo "VoiceStudio uninstaller ($OS)"
echo "----------------------------------"
if [ "${#app_targets[@]}" -eq 0 ]; then
  echo "No VoiceStudio app data / env / config folders found at the default or"
  echo "env-configured locations. Nothing to remove."
else
  echo "App data, managed Python env, config, and logs:"
  for t in "${app_targets[@]}"; do printf "  %-6s %s\n" "$(human_size "$t")" "$t"; done
fi

models_present=0
if [ -e "$MODELS_DIR" ]; then
  models_present=1
  echo
  echo "Model cache (Hugging Face weights — SHARED with other HF tools):"
  printf "  %-6s %s\n" "$(human_size "$MODELS_DIR")" "$MODELS_DIR"
  echo "  ↳ pass --models to include this (it may hold models from OTHER apps too)."
fi

echo
if [ "$APPLY" -ne 1 ]; then
  echo "DRY RUN — nothing deleted. Re-run with --yes to remove the listed folders"
  [ "$models_present" -eq 1 ] && echo "         (add --models to also remove the shared model cache)."
  [ "$REMOVE_APP" -ne 1 ] && echo "         (add --app to also remove the prebuilt app binary)."
  echo "See docs/install/uninstall.md to also remove the app binary itself."
  exit 0
fi

# ── Opt-in uninstall ping (before anything is deleted) ──────────────────────
# If — and only if — the user opted in to anonymous analytics, send a single
# best-effort `app_uninstalled` event before the data (and the consent record
# it lives in) goes away. The backend writes analytics_info.json next to
# prefs.json ONLY while analytics is enabled (explicit consent + a build that
# ships a token) and deletes it on opt-out, so the file's presence is itself
# consent-gated; the prefs.json check is belt and braces. Content-free: the
# event carries the app version, the OS name, and the random per-install id —
# nothing else. Never blocks or fails the uninstall (2s timeout, silent
# failure). Not opted in ⇒ nothing is sent and nothing is printed.
send_uninstall_ping() {
  local info="$DATA_DIR/analytics_info.json" prefs="$DATA_DIR/prefs.json"
  [ -f "$info" ] && [ -f "$prefs" ] || return 0
  grep -q '"analytics_enabled"[[:space:]]*:[[:space:]]*true' "$prefs" 2>/dev/null || return 0
  command -v curl >/dev/null 2>&1 || return 0
  local token host distinct_id app_version platform
  token=$(sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$info" | head -n1)
  host=$(sed -n 's/.*"host"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$info" | head -n1)
  distinct_id=$(sed -n 's/.*"distinct_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$info" | head -n1)
  app_version=$(sed -n 's/.*"app_version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$info" | head -n1)
  platform=$(sed -n 's/.*"platform"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$info" | head -n1)
  [ -n "$token" ] && [ -n "$host" ] && [ -n "$distinct_id" ] || return 0
  echo "Sending anonymous uninstall ping (you opted in to analytics)."
  curl -m 2 -s -o /dev/null -X POST "$host/capture/" \
    -H 'Content-Type: application/json' \
    -d "{\"api_key\":\"$token\",\"event\":\"app_uninstalled\",\"distinct_id\":\"$distinct_id\",\"properties\":{\"app_version\":\"$app_version\",\"platform\":\"$platform\"}}" \
    || true
}
send_uninstall_ping

# ── Delete ──────────────────────────────────────────────────────────────────
deleted=0
for t in "${app_targets[@]:-}"; do
  [ -z "$t" ] && continue
  echo "Removing $t"
  rm -rf -- "$t" && deleted=$((deleted + 1))
done
if [ "$INCLUDE_MODELS" -eq 1 ] && [ "$models_present" -eq 1 ]; then
  echo "Removing $MODELS_DIR"
  rm -rf -- "$MODELS_DIR" && deleted=$((deleted + 1))
elif [ "$models_present" -eq 1 ]; then
  echo "Kept model cache ($MODELS_DIR) — re-run with --models to remove it."
fi

echo
echo "Done — removed $deleted folder(s)."
echo "To remove the app itself, see docs/install/uninstall.md (drag to Trash /"
echo "Add-or-remove-programs / delete the .AppImage)."
