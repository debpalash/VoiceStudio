#!/bin/sh
# Type-check the app's Foundation/AVFoundation wrappers against the available macOS SDK.
# PhoneModels now owns UIKit background jobs and is checked by the full iOS
# app build in mobile.yml; these portable wrappers remain CLT-compatible.
set -eu
IOS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export CLANG_MODULE_CACHE_PATH="$IOS_DIR/.cache/clang"
MODULES=""
for candidate in "$IOS_DIR"/.build/*/debug/Modules; do
    if [ -d "$candidate" ]; then MODULES="$candidate"; break; fi
done
if [ -z "$MODULES" ]; then
    echo 'Run check-swift.sh build first.' >&2
    exit 1
fi
swiftc -typecheck -swift-version 6 -strict-concurrency=complete -I "$MODULES" \
    -Xcc -fmodule-map-file="$IOS_DIR/VoiceStudioKit/Sources/VSNative/include/module.modulemap" \
    -Xcc -I"$IOS_DIR/VoiceStudioKit/Sources/VSNative/include" \
    "$IOS_DIR/VoiceStudio/Platform/HostMediaLoader.swift" \
    "$IOS_DIR/VoiceStudio/Platform/SmokeTransport.swift"
