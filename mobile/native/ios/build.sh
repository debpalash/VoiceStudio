#!/bin/sh
# All artifacts stay below build/; no repository or git state is changed.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/build.py" "${@:-ios}"
