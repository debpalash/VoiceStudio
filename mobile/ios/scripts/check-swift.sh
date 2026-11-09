#!/bin/sh
# No package dependencies. CLT ships Testing.framework outside SwiftPM's default search path.
set -eu
IOS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export CLANG_MODULE_CACHE_PATH="$IOS_DIR/.cache/clang"
export SWIFTPM_MODULECACHE_OVERRIDE="$IOS_DIR/.cache/swift"
SCRATCH_PATH=${VOICESTUDIO_SWIFT_SCRATCH:-"$IOS_DIR/.build"}
ACTION=${1:-test}
shift "$(( $# > 0 ? 1 : 0 ))"
DEVELOPER_DIR_PATH=$(xcode-select -p)
FRAMEWORKS="$DEVELOPER_DIR_PATH/Library/Developer/Frameworks"
if [ "$ACTION" = test ] && [ -d "$FRAMEWORKS/Testing.framework" ]; then
    swift test --disable-xctest --disable-sandbox --package-path "$IOS_DIR/VoiceStudioKit" --scratch-path "$SCRATCH_PATH" \
        -Xswiftc -F -Xswiftc "$FRAMEWORKS" \
        -Xswiftc -Xfrontend -Xswiftc -disable-cross-import-overlays \
        -Xlinker -F -Xlinker "$FRAMEWORKS" \
        -Xlinker -rpath -Xlinker "$FRAMEWORKS" "$@"
else
    swift "$ACTION" --disable-sandbox --package-path "$IOS_DIR/VoiceStudioKit" --scratch-path "$SCRATCH_PATH" "$@"
fi
