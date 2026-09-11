#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# desktop-prod.sh — Build & launch VoiceStudio as a "fresh install"
#
# This gives you the EXACT same experience as a user downloading the
# installer (DMG on macOS, AppImage on Linux):
#   • Full Rust bootstrap (venv creation, uv sync, model setup)
#   • Splash screen with live logs
#   • Region selector, version badge, etc.
#
# Usage:
#   bun desktop-prod          # build debug + wipe + launch
#   bun desktop-prod:run      # re-launch last build (skip compile, keep data)
#   bun desktop-prod:upgrade  # rebuild, but keep data (test upgrade)
#
# NOTE on the flags (#1333): --skip-build and --keep-data are INDEPENDENT.
# --skip-build only skips the compile; on its own it still wipes app data,
# which is why `desktop-prod:run` passes --keep-data too. Wiping is the
# default because this script exists to emulate a first install; a plain
# re-launch is not that, and must not cost you your voice profiles.
#
# For a stricter NEW-USER emulation on macOS (webview localStorage, prefs,
# caches wiped too + launch with a dev-tools-hidden environment), see
# `bun desktop-fresh` (scripts/desktop-fresh.mjs).
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Always run from the repo root — every path below (frontend/, ${TAURI_DIR},
# …) is repo-root-relative, so invoking the script from any other directory
# used to mis-resolve them (#962 hardening).
cd "$(dirname "${BASH_SOURCE[0]}")/.."
REPO_ROOT="$(pwd -P)"

APP_ID="com.debpalash.omnivoice-studio"
TAURI_DIR="frontend/src-tauri"
APP_NAME="VoiceStudio"
TAURI_BUILD_ROOT="$REPO_ROOT/$TAURI_DIR/target/debug"
if [ -d "$TAURI_BUILD_ROOT" ]; then
  TAURI_BUILD_ROOT="$(cd "$TAURI_BUILD_ROOT" && pwd -P)"
fi

# ── Detect platform ───────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
  Darwin)              PLATFORM="macos" ;;
  Linux)               PLATFORM="linux" ;;
  MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;  # Git Bash / MSYS2 / Cygwin on Windows
  *)                   echo "❌ Unsupported platform: $OS"; exit 1 ;;
esac

# ── Platform-specific paths ───────────────────────────────────────────────
# Two directories matter for fresh-install simulation:
#   APP_DATA     — Tauri's bundle dir (keyed by APP_ID); holds the post-install
#                  Python venv + webview state.
#   BACKEND_DATA — Where backend/core/config.py::get_app_data_dir() writes:
#                  SQLite db, voice profiles, generation outputs, logs. This is
#                  NOT under APP_ID — it's a separate hardcoded name. Cleaning
#                  only APP_DATA leaves all user data behind, defeating the
#                  fresh-emulation promise.
if [ "$PLATFORM" = "macos" ]; then
  APP_DATA="$HOME/Library/Application Support/${APP_ID}"
  BACKEND_DATA="$HOME/Library/Application Support/OmniVoice"
  TAURI_LOGS="$HOME/Library/Logs/${APP_ID}"
  WEBKIT_DATA="$HOME/Library/WebKit/${APP_ID}"
  HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}"
elif [ "$PLATFORM" = "windows" ]; then
  # Git Bash exposes Windows env vars. Backend writes to %APPDATA%\OmniVoice
  # (backend/core/config.py::get_app_data_dir) and relocates the HF cache to
  # %LOCALAPPDATA%\OmniVoice\hf_cache. Tauri keys its data by APP_ID under
  # LOCALAPPDATA; WebView2 state lives in EBWebView. All paths are APP_ID/
  # data-dir-scoped, and each rm is guarded by `[ -d ]`, so a slightly-off
  # path is a no-op, never a wrong delete.
  APP_DATA="${LOCALAPPDATA}/${APP_ID}"
  BACKEND_DATA="${APPDATA}/OmniVoice"
  TAURI_LOGS="${LOCALAPPDATA}/${APP_ID}/logs"
  WEBKIT_DATA="${LOCALAPPDATA}/${APP_ID}/EBWebView"
  HF_CACHE="${HF_HOME:-${LOCALAPPDATA}/OmniVoice/hf_cache}"
else
  # Linux: backend uses ~/.omnivoice (not XDG — see backend/core/config.py).
  APP_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/${APP_ID}"
  BACKEND_DATA="$HOME/.omnivoice"
  TAURI_LOGS="${XDG_DATA_HOME:-$HOME/.local/share}/${APP_ID}/logs"
  WEBKIT_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/${APP_ID}/webview"
  HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}"
fi

# ── Flags ──────────────────────────────────────────────────────────────────
SKIP_BUILD=false
KEEP_DATA=false
KEEP_MODELS=false
PILL_MODE=false

for arg in "$@"; do
  case "$arg" in
    --skip-build)  SKIP_BUILD=true ;;
    --keep-data)   KEEP_DATA=true ;;
    --keep-models) KEEP_MODELS=true ;;
    --pill)        PILL_MODE=true ;;
    -h|--help)
      echo "Usage: $0 [--skip-build] [--keep-data] [--keep-models] [--pill]"
      echo ""
      echo "  --skip-build   Skip cargo build, use last compiled binary."
      echo "                 Does NOT imply --keep-data: on its own it still"
      echo "                 wipes app data. Pair the two to just re-launch."
      echo "  --keep-data    Don't wipe app data (test upgrade path)"
      echo "  --keep-models  Wipe app/backend data for a fresh app, but KEEP the"
      echo "                 HF model cache — fresh first-run without re-downloading"
      echo "                 the multi-GB weights. Ignored when --keep-data is set."
      echo "  --pill         Launch in dictation-widget mode (no main window)"
      echo ""
      echo "Environment:"
      echo "  FRESH_NUKE_HF=1  Also wipe the HF cache when it is the SHARED global"
      echo "                   cache (~/.cache/huggingface). By default only an"
      echo "                   VoiceStudio-scoped cache path is removed."
      exit 0
      ;;
  esac
done

# Is a path unambiguously VoiceStudio-scoped (safe to auto-delete)? The HF
# cache defaults to the SHARED ~/.cache/huggingface on macOS/Linux — the app
# only relocates it on Windows (backend/core/config.py) — and HF_HOME can
# point anywhere. Wiping a shared cache would delete models unrelated to
# VoiceStudio, so non-scoped paths are kept unless FRESH_NUKE_HF=1.
# (Kept in sync with isAppScoped() in scripts/desktop-common.mjs.)
is_app_scoped() {
  case "$1" in
    *[Oo]mni[Vv]oice*|*com.debpalash*) return 0 ;;
    *)                                 return 1 ;;
  esac
}

# ── Kill before launch (and before any wipe) ───────────────────────────────
# Two independent reasons, which is why this is unconditional (#1333 review):
#
# 1. Wipe: a backend that survives it becomes a zombie — /health keeps
#    answering from memory, the next launch attaches to it, and every real
#    route 500s off deleted files + an empty DB.
# 2. Launch: the app registers tauri_plugin_single_instance, whose callback
#    IGNORES the new argv and merely refocuses the window the running process
#    already has. So starting a second copy over a live one silently does
#    nothing — `desktop-prod:run` would refocus the OLD build instead of
#    running the one just compiled, and `desktop-prod:run:pill` would leave
#    you looking at studio mode with --pill quietly discarded.
#
# Reason 2 applies whatever the data policy is, so this must not sit inside
# the KEEP_DATA branch.
#
# The kill is scoped to THIS checkout's build artifacts. A bare `${APP_NAME}.app`
# pattern also matches an installed /Applications copy, and killing that costs a
# developer unsaved work in a session this script never started (greptile) —
# previously masked because the kill only ran on wipe runs, where the developer
# had already asked for a clean slate. An installed copy still cannot be ignored
# outright (single-instance would swallow this launch), so it gets a warning.
warn_installed_instance() {
  local installed
  installed="$(pgrep -f "${APP_NAME}.app" 2>/dev/null || true)"
  # Drop anything already matched as our own dev build.
  local p keep=""
  for p in $installed; do
    case " $pids " in *" $p "*) ;; *) keep="$keep $p" ;; esac
  done
  [ -z "${keep// /}" ] && return 0
  echo "⚠️  An installed ${APP_NAME} is running (pid(s):$keep)."
  echo "   Not touching it — that is your session, and killing it would cost"
  echo "   you unsaved work. But single-instance keys on the bundle id, so it"
  echo "   will swallow this launch: quit it first, or you'll keep looking at"
  echo "   the installed app instead of this build."
  echo ""
}

kill_running_instances() {
  local pids=""
  # Windows has no working path below: Git Bash ships neither pgrep nor lsof,
  # and MSYS `kill` cannot signal a native Win32 pid. Every lookup is
  # `|| true`-guarded, so the whole function degraded to a silent no-op — the
  # stale exe kept a lock on its own image and the next build died with
  # "failed to remove file ... Access is denied. (os error 5)", while a wipe run
  # deleted the data out from under a still-live app. The helper is Win32-aware,
  # scopes ownership to this checkout's build output, binds each kill to the
  # process instance (pids are reusable), and fails rather than returning while
  # a process survives — matching desktop_prod_processes.py on Linux.
  if [ "$PLATFORM" = "windows" ]; then
    local win_pids
    if ! win_pids="$(bun "$REPO_ROOT/scripts/desktop-prod-windows-processes.mjs" "$TAURI_BUILD_ROOT")"; then
      echo "❌ A running VoiceStudio from this checkout could not be stopped."
      echo "   Close it (or end omnivoice-studio.exe in Task Manager) and retry."
      exit 1
    fi
    if [ -n "$win_pids" ]; then
      echo "🔪 Terminating running VoiceStudio processes: $(echo "$win_pids" | tr '\n' ' ')"
      echo "   All stopped."
      echo ""
    fi
    return 0
  fi
  # One pattern covers both launch shapes: the raw binary and the .app bundle
  # both live under `${TAURI_DIR}/target/debug/`, and `pgrep -f` sees the
  # absolute path, of which that is a substring.
  pids="$(pgrep -f "${TAURI_DIR}/target/debug/.*omnivoice-studio" 2>/dev/null || true)"
  # APPIMAGE_EXTRACT_AND_RUN replaces the command with a /tmp extraction path,
  # so pgrep cannot connect the live shell (or its inherited backend) to this
  # checkout. APPIMAGE remains in both processes' environments and is the
  # stable ownership proof. Missing this case let a fresh run delete the live
  # SQLite/log directory, then single-instance merely refocused the broken app.
  if [ "$PLATFORM" = "linux" ]; then
    local appimage_pids
    appimage_pids="$(python3 scripts/desktop_prod_processes.py "$TAURI_BUILD_ROOT")"
    if [ -n "$appimage_pids" ]; then
      echo "🔪 Terminated extracted VoiceStudio AppImage processes: $(echo "$appimage_pids" | tr '\n' ' ')"
    fi
  fi
  warn_installed_instance
  local port_pid
  for port_pid in $(lsof -nP -iTCP:3900 -sTCP:LISTEN -t 2>/dev/null || true); do
    if ps -p "$port_pid" -o command= 2>/dev/null | grep -qiE 'omnivoice|com\.debpalash'; then
      pids="$pids $port_pid"
    fi
  done
  # shellcheck disable=SC2086
  pids="$(echo $pids | tr ' ' '\n' | sort -u | tr '\n' ' ')"
  [ -z "${pids// /}" ] && return 0
  echo "🔪 Terminating running VoiceStudio processes:$pids"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  local i=0
  while [ $i -lt 10 ]; do
    sleep 0.5
    # shellcheck disable=SC2086
    if ! kill -0 $pids 2>/dev/null; then break; fi
    i=$((i + 1))
  done
  # shellcheck disable=SC2086
  kill -9 $pids 2>/dev/null || true
  echo "   All stopped."
  echo ""
}

kill_running_instances

# ── Wipe app data for fresh-install simulation ─────────────────────────────
if [ "$KEEP_DATA" = false ]; then
  echo "🧹 Cleaning all VoiceStudio data for fresh prod emulation..."
  echo ""

  # 1. App data (Tauri bundle dir: post-install venv + webview state)
  if [ -d "${APP_DATA}" ]; then
    echo "   ✓ App data:     ${APP_DATA} — removed"
    rm -rf "${APP_DATA}"
  else
    echo "   ○ App data:     (already clean)"
  fi

  # 1b. Backend data (SQLite db, voice profiles, outputs, logs)
  #     — separate dir hardcoded in backend/core/config.py, NOT under APP_ID.
  if [ -d "${BACKEND_DATA}" ]; then
    BD_SIZE=$(du -sh "${BACKEND_DATA}" 2>/dev/null | cut -f1)
    echo "   ✓ Backend data: ${BACKEND_DATA} (${BD_SIZE}) — removed"
    rm -rf "${BACKEND_DATA}"
  else
    echo "   ○ Backend data: (already clean)"
  fi

  # 2. HF model cache (downloaded .safetensors, tokenizers, etc.)
  #    --keep-models preserves it so a "fresh app" run doesn't re-pull multi-GB
  #    weights (the model-download is the slow, bandwidth-heavy part of a clean
  #    run; everything else still resets for an honest first-run emulation).
  #    Only a VoiceStudio-scoped path is auto-removed: on macOS/Linux the app
  #    uses the SHARED ~/.cache/huggingface, which also holds models from
  #    other projects — wiping it needs the explicit FRESH_NUKE_HF=1 opt-in.
  if [ "$KEEP_MODELS" = true ]; then
    if [ -d "${HF_CACHE}" ]; then
      HF_SIZE=$(du -sh "${HF_CACHE}" 2>/dev/null | cut -f1)
      echo "   ◆ HF cache:     ${HF_CACHE} (${HF_SIZE}) — KEPT (--keep-models)"
    else
      echo "   ○ HF cache:     (already clean)"
    fi
  elif [ ! -d "${HF_CACHE}" ]; then
    echo "   ○ HF cache:     (already clean)"
  elif is_app_scoped "${HF_CACHE}" || [ "${FRESH_NUKE_HF:-0}" = "1" ]; then
    HF_SIZE=$(du -sh "${HF_CACHE}" 2>/dev/null | cut -f1)
    echo "   ✓ HF cache:     ${HF_CACHE} (${HF_SIZE}) — removed"
    rm -rf "${HF_CACHE}"
  else
    HF_SIZE=$(du -sh "${HF_CACHE}" 2>/dev/null | cut -f1)
    echo "   ◆ HF cache:     ${HF_CACHE} (${HF_SIZE}) — KEPT (shared global cache)"
    echo "     ↳ Not VoiceStudio-scoped; wiping it would delete models unrelated to"
    echo "       this app. Models will be REUSED, not re-downloaded. To wipe anyway:"
    echo "       FRESH_NUKE_HF=1 bun desktop-prod"
  fi

  # 3. Tauri log dir
  if [ -d "${TAURI_LOGS}" ]; then
    echo "   ✓ Tauri logs:   ${TAURI_LOGS} — removed"
    rm -rf "${TAURI_LOGS}"
  else
    echo "   ○ Tauri logs:   (already clean)"
  fi

  # 4. WebView cache / local storage
  if [ -d "${WEBKIT_DATA}" ]; then
    echo "   ✓ WebKit data:  ${WEBKIT_DATA} — removed"
    rm -rf "${WEBKIT_DATA}"
  else
    echo "   ○ WebKit data:  (already clean)"
  fi

  echo ""
  echo "   ✅ All clean — next launch bootstraps from zero."
else
  echo "📦 Keeping existing app data (upgrade test mode)"
fi

# ── Build debug binary ─────────────────────────────────────────────────────
if [ "$SKIP_BUILD" = false ]; then
  echo ""
  echo "🔨 Building debug bundle (this takes 1-3 min first time)..."

  # Remove stale bundles so we never accidentally launch old code
  if [ "$PLATFORM" = "macos" ]; then
    APP_BUNDLE="${TAURI_DIR}/target/debug/bundle/macos/${APP_NAME}.app"
    [ -d "$APP_BUNDLE" ] && rm -rf "$APP_BUNDLE"
  elif [ "$PLATFORM" = "linux" ]; then
    # A stale AppImage would be picked up by the `find` in the launch step
    # below even if this build's bundling fails (tolerated case — see grep).
    rm -rf "${TAURI_DIR}/target/debug/bundle/appimage"
  fi

  # Linux: linuxdeploy uses FUSE to mount itself; if FUSE is unavailable
  # (containers, some hardened kernels), set APPIMAGE_EXTRACT_AND_RUN=1 to
  # extract-and-run instead. Safe to always set on Linux.
  if [ "$PLATFORM" = "linux" ]; then
    export APPIMAGE_EXTRACT_AND_RUN=1
  fi

  # Local emulation builds must exit 0, so:
  #   - createUpdaterArtifacts is overridden to false (inline --config merge,
  #     mirrors bundle.createUpdaterArtifacts in tauri.conf.json; kept in sync
  #     with UPDATER_ARTIFACTS_OFF in scripts/desktop-common.mjs). Dev
  #     machines have no TAURI_SIGNING_PRIVATE_KEY, so the updater-artifact
  #     signing step used to fail the build AFTER all bundles were produced.
  #     Local runs never need updater artifacts — release.yml builds and
  #     signs them.
  #   - only the bundle this script launches is built: .app on macOS (no
  #     dmg), AppImage on Linux (no deb — its bundling is broken in
  #     tauri-cli, see release.yml), nothing on Windows (raw debug .exe).
  # #962: invoke the Tauri CLI via the frontend workspace's `tauri` script,
  # NOT `bunx tauri`. In the bun workspace monorepo `@tauri-apps/cli` is a
  # frontend/package.json dependency, and `bunx` resolves by npm package
  # name — when the locally installed bin isn't exactly where bunx looks it
  # falls back to fetching the unrelated `tauri` (v1) package from npm and
  # dies with "could not determine executable to run for package tauri".
  # `bun run --cwd frontend tauri` always resolves the workspace-local CLI.
  UPDATER_ARTIFACTS_OFF='{"bundle":{"createUpdaterArtifacts":false}}'
  case "$PLATFORM" in
    macos)   BUNDLE_FLAGS=(--bundles app) ;;
    linux)   BUNDLE_FLAGS=(--bundles appimage) ;;
    windows) BUNDLE_FLAGS=(--no-bundle) ;;
  esac
  BUILD_LOG=$(mktemp)
  set +e
  bun run --cwd frontend tauri build --debug "${BUNDLE_FLAGS[@]}" \
    --config "$UPDATER_ARTIFACTS_OFF" 2>&1 | tee "$BUILD_LOG"
  BUILD_EXIT=$?  # pipefail is on (set -euo pipefail) → the build's status, not tee's
  set -e
  if [ $BUILD_EXIT -ne 0 ]; then
    # The ONLY tolerated failure, specifically detected: linuxdeploy needs
    # FUSE and can still die in containers/hardened kernels despite
    # APPIMAGE_EXTRACT_AND_RUN=1. Tolerate it only when the raw debug binary
    # was actually produced — the launch step below falls back to it.
    # Anything else (compile error, config error, signing, …) fails loudly.
    if [ "$PLATFORM" = "linux" ] \
       && grep -qi "failed to run linuxdeploy" "$BUILD_LOG" \
       && [ -f "${TAURI_DIR}/target/debug/omnivoice-studio" ]; then
      echo "⚠️  AppImage packaging failed (linuxdeploy/FUSE) — falling back to the raw debug binary."
    else
      echo "❌ Build failed with exit code $BUILD_EXIT"
      rm -f "$BUILD_LOG"
      exit "$BUILD_EXIT"
    fi
  fi
  rm -f "$BUILD_LOG"

  echo "✅ Build complete."
else
  echo "⏭️  Skipping build (--skip-build)"
fi

# ── Build launch args ──────────────────────────────────────────────────────
LAUNCH_ARGS=()
if [ "$PILL_MODE" = true ]; then
  LAUNCH_ARGS+=("--pill")
  echo "📌 Launch mode: pill (dictation-only widget, no main window)"
fi

# ── Find and launch the app ────────────────────────────────────────────────
if [ "$PLATFORM" = "macos" ]; then
  APP_BUNDLE="${TAURI_DIR}/target/debug/bundle/macos/${APP_NAME}.app"
  BINARY="${TAURI_DIR}/target/debug/omnivoice-studio"

  if [ -d "$APP_BUNDLE" ]; then
    echo ""
    echo "🚀 Launching ${APP_NAME} (.app bundle)..."
    echo "   Bundle: ${APP_BUNDLE}"
    # macOS `open` needs -n to spawn a fresh instance (plain `open` would just
    # focus an already-running one — stale process, freshly wiped data),
    # --args to forward flags.
    if [ ${#LAUNCH_ARGS[@]} -gt 0 ]; then
      open -n "$APP_BUNDLE" --args "${LAUNCH_ARGS[@]}"
    else
      open -n "$APP_BUNDLE"
    fi
  elif [ -f "$BINARY" ]; then
    echo ""
    echo "🚀 Launching ${APP_NAME} (raw binary — no .app bundle)..."
    echo "   Binary: ${BINARY}"
    "$BINARY" "${LAUNCH_ARGS[@]}" &
  else
    echo "❌ No bundle or binary found. Run without --skip-build first."
    exit 1
  fi
elif [ "$PLATFORM" = "windows" ]; then
  # Windows: launch the raw debug .exe (Git Bash can exec it directly).
  BINARY="${TAURI_DIR}/target/debug/omnivoice-studio.exe"
  if [ -f "$BINARY" ]; then
    echo ""
    echo "🚀 Launching ${APP_NAME} (Windows debug .exe)..."
    echo "   Binary: ${BINARY}"
    "$BINARY" "${LAUNCH_ARGS[@]}" &
  else
    echo "❌ No .exe found at ${BINARY}. Run without --skip-build first."
    exit 1
  fi
else
  # Linux: prefer AppImage, fall back to raw binary
  APPIMAGE=$(find "${TAURI_DIR}/target/debug/bundle/appimage" -name "*.AppImage" -type f 2>/dev/null | head -1)
  BINARY="${TAURI_DIR}/target/debug/omnivoice-studio"

  if [ -n "$APPIMAGE" ] && [ -f "$APPIMAGE" ]; then
    echo ""
    echo "🚀 Launching ${APP_NAME} (AppImage)..."
    echo "   AppImage: ${APPIMAGE}"
    chmod +x "$APPIMAGE"
    "$APPIMAGE" "${LAUNCH_ARGS[@]}" &
  elif [ -f "$BINARY" ]; then
    echo ""
    echo "🚀 Launching ${APP_NAME} (raw binary)..."
    echo "   Binary: ${BINARY}"
    "$BINARY" "${LAUNCH_ARGS[@]}" &
  else
    echo "❌ No AppImage or binary found. Run without --skip-build first."
    exit 1
  fi
fi

echo "   App data: ${APP_DATA}"
echo ""
echo "✅ App launched. Check the splash screen for bootstrap logs."
if [ "$PILL_MODE" = true ]; then
  echo "   To re-run pill mode without rebuilding: bun desktop-prod:run:pill"
  echo "   To switch back to studio: bun desktop-prod:run"
else
  echo "   To re-run without rebuilding: bun desktop-prod:run"
  echo "   To launch as dictation widget: bun desktop-prod:pill"
fi
