#!/bin/sh
set -eu
IOS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ROOT=$(CDPATH= cd -- "$IOS_DIR/../.." && pwd)
OMNIVOICE_LIBRARY=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$ROOT/mobile/native/ios/build/macos/runtime-paths.json" 'omnivoice.cpp')
LLAMA_LIBRARY=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$ROOT/mobile/native/ios/build/macos/runtime-paths.json" 'llama.cpp')
export OMNIVOICE_LIBRARY LLAMA_LIBRARY
export VOICESTUDIO_REAL_NATIVE=1
exec "$IOS_DIR/scripts/check-swift.sh" test --filter realNative
