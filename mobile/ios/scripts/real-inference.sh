#!/bin/sh
set -eu
IOS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ROOT=$(CDPATH= cd -- "$IOS_DIR/../.." && pwd)
# Preparation is explicit; the tests themselves never open a network connection.
"$ROOT/mobile/native/ios/build.sh" macos-sherpa
SHERPA_ONNX_LIBRARY=$(cat "$ROOT/mobile/native/ios/build/macos/sherpa-path.txt")
export SHERPA_ONNX_LIBRARY
export VOICESTUDIO_REAL_INFERENCE=1
exec "$IOS_DIR/scripts/check-swift.sh" test --filter realSherpa
