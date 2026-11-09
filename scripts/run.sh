#!/bin/sh
# VoiceStudio — universal launcher.
#
# Works on macOS, Linux, and WSL. Starts the FastAPI backend, waits for it
# to become healthy, then opens the web UI in the default browser.
#
# Usage:
#   ./run.sh            # normal launch
#   ./run.sh --no-open  # skip auto-opening browser
# Press Ctrl+C to shut down.
set -e

# ── Output style ────────────────────────────────────────────────────────────
if [ -n "${NO_COLOR:-}" ]; then
    C_OK="" C_DIM="" C_ERR="" C_RST=""
elif [ -t 1 ] || [ -n "${FORCE_COLOR:-}" ]; then
    _ESC="$(printf '\033')"
    C_OK="${_ESC}[38;5;108m"
    C_DIM="${_ESC}[38;5;245m"
    C_ERR="${_ESC}[91m"
    C_RST="${_ESC}[0m"
else
    C_OK="" C_DIM="" C_ERR="" C_RST=""
fi

have() { command -v "$1" >/dev/null 2>&1; }

# ── Parse flags ─────────────────────────────────────────────────────────────
NO_OPEN=false
for arg in "$@"; do
    case "$arg" in
        --no-open) NO_OPEN=true ;;
    esac
done

# ── Resolve script directory ───────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
cd "$SCRIPT_DIR"

# ── Detect platform ────────────────────────────────────────────────────────
OS="linux"
case "$(uname -s)" in
    Darwin)               OS="macos" ;;
    MINGW*|MSYS*|CYGWIN*)  OS="windows" ;;  # Git Bash / MSYS2 — uv run works cross-platform
    *) if grep -qi microsoft /proc/version 2>/dev/null; then OS="wsl"; fi ;;
esac

# ── PATH: ensure common shell-installed tools are available ────────────────
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"
case "$OS" in
    macos)
        export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
        ;;
esac

# ── Sanity check ───────────────────────────────────────────────────────────
if [ ! -d .venv ]; then
    printf "${C_ERR}✗ No .venv/ — run ./install.sh first.${C_RST}\n" >&2
    exit 1
fi

# ── Log directory (platform-aware) ─────────────────────────────────────────
case "$OS" in
    macos)   LOG_DIR="$HOME/Library/Application Support/OmniVoice" ;;
    windows) LOG_DIR="${LOCALAPPDATA:-$HOME/AppData/Local}/VoiceStudio" ;;
    *)       LOG_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/VoiceStudio" ;;
esac
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/omnivoice-run.log"

# ── Start backend ──────────────────────────────────────────────────────────
PORT="${OMNIVOICE_PORT:-$(python3 -c 'import json; print(json.load(open("config/app.json", encoding="utf-8"))["ports"]["backend"])')}"
export OMNIVOICE_PORT="$PORT"
echo "${C_OK}▸${C_RST} Starting backend on port ${PORT} (log: ${C_DIM}${LOG_FILE}${C_RST})…"

uv run uvicorn main:app --app-dir backend --host 127.0.0.1 --port "$PORT" \
    >"$LOG_FILE" 2>&1 &
BACKEND_PID=$!

# ── Cleanup on exit ────────────────────────────────────────────────────────
cleanup() {
    echo ""
    echo "${C_DIM}▸ Shutting down backend (pid $BACKEND_PID)…${C_RST}"
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── Wait for backend health ────────────────────────────────────────────────
echo "${C_DIM}▸ Waiting for backend…${C_RST}"
_deadline=60
_elapsed=0
while [ "$_elapsed" -lt "$_deadline" ]; do
    # Try curl first, fall back to wget
    if have curl; then
        curl -sf "http://127.0.0.1:${PORT}/system/info" -o /dev/null 2>/dev/null && break
    elif have wget; then
        wget -qO /dev/null "http://127.0.0.1:${PORT}/system/info" 2>/dev/null && break
    fi
    # Check that backend process is still alive
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        printf "${C_ERR}✗ Backend process exited unexpectedly.${C_RST}\n" >&2
        echo "  Last 20 lines of log:" >&2
        tail -n 20 "$LOG_FILE" >&2 || true
        exit 1
    fi
    sleep 1
    _elapsed=$((_elapsed + 1))
done

# Final health check
_healthy=false
if have curl; then
    curl -sf "http://127.0.0.1:${PORT}/system/info" -o /dev/null 2>/dev/null && _healthy=true
elif have wget; then
    wget -qO /dev/null "http://127.0.0.1:${PORT}/system/info" 2>/dev/null && _healthy=true
fi

if [ "$_healthy" != true ]; then
    printf "${C_ERR}✗ Backend didn't start in ${_deadline}s. See %s for errors.${C_RST}\n" "$LOG_FILE" >&2
    tail -n 20 "$LOG_FILE" >&2 || true
    exit 1
fi

URL="http://127.0.0.1:${PORT}/"
echo "${C_OK}▸ Backend up.${C_RST} Opening UI at ${C_OK}${URL}${C_RST}"

# ── Open browser (cross-platform) ──────────────────────────────────────────
if [ "$NO_OPEN" != true ]; then
    if [ "$OS" = "macos" ] && have open; then
        open "$URL"
    elif [ "$OS" = "wsl" ]; then
        # WSL: use Windows browser via PowerShell or cmd.exe
        if have powershell.exe; then
            powershell.exe -NoProfile -Command "Start-Process '$URL'" >/dev/null 2>&1 &
        elif have cmd.exe; then
            cmd.exe /c start "" "$URL" >/dev/null 2>&1 &
        elif have xdg-open; then
            xdg-open "$URL" >/dev/null 2>&1 &
        else
            echo "  Open in your browser: $URL"
        fi
    elif have xdg-open; then
        xdg-open "$URL" >/dev/null 2>&1 &
    else
        echo "  Open in your browser: $URL"
    fi
fi

echo ""
echo "VoiceStudio is running."
echo "Press Ctrl+C to shut down."

# Block until user hits Ctrl+C or backend exits.
wait "$BACKEND_PID"
