#!/bin/sh
# Package-managed install: pacman owns updates, not the in-app updater.
export VOICESTUDIO_DISABLE_UPDATER=1
exec /opt/VoiceStudio/voicestudio-electron "$@"
