#!/usr/bin/env bash
# Installs the NDK and CMake pinned for the on-device runtimes. Google's download
# occasionally hands back a corrupt archive, so a failed attempt clears sdkmanager's
# partial downloads and tries again instead of failing the whole build.
set -u

sdkmanager="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"
attempts=3
delay="${ANDROID_SDK_RETRY_DELAY:-20}"

for ((attempt = 1; attempt <= attempts; attempt++)); do
  if yes | "$sdkmanager" "ndk;27.1.12297006" "cmake;3.22.1" > /dev/null; then
    exit 0
  fi
  echo "sdkmanager failed (attempt $attempt of $attempts)" >&2
  rm -rf "$ANDROID_HOME/.temp" "$ANDROID_HOME/.downloadIntermediates"
  if ((attempt < attempts)); then sleep "$delay"; fi
done
exit 1
