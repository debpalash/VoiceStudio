#!/bin/zsh
# Starts VoiceStudio in preview mode (Electron shell + local FastAPI backend).
# Used by the VoiceStudio.app launcher so the desktop app always opens the
# checkout that owns the verified .venv, OmniVoice model cache and Kabyle support.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
unset ELECTRON_RUN_AS_NODE
mkdir -p "$HOME/Library/Logs"

# A second launch only brings the running window forward; spawning again would
# collide on the shell's dev-proxy port.
if curl -fsS --max-time 2 http://127.0.0.1:3900/health >/dev/null 2>&1; then
  /usr/bin/osascript -e 'tell application "System Events" to set frontmost of (first process whose name is "Electron") to true' >/dev/null 2>&1
  exit 0
fi

cd "/Users/akselyan/Desktop/Voice studio/electron" || exit 1
exec ./node_modules/.bin/electron . >>"$HOME/Library/Logs/VoiceStudio-launcher.log" 2>&1
