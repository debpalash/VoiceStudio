#!/usr/bin/env bash
# Produce a tarball that a same-arch friend can unpack + run ./install.sh.
# Excludes the local venv, node_modules, frontend/dist, build artifacts,
# HuggingFace caches, data dirs, and git history — everything that should
# be recreated or re-downloaded on the friend's machine.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

STAMP="$(date +%Y%m%d-%H%M)"
OUT="omnivoice-studio-${STAMP}.tar.gz"

echo "▸ Packaging $ROOT → $OUT"

tar --exclude='.venv' \
    --exclude='.git' \
    --exclude='.github' \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='frontend/dist' \
    --exclude='native/desktop-bridge/target' \
    --exclude='electron/release' \
    --exclude='dist' \
    --exclude='build' \
    --exclude='.turbo' \
    --exclude='bun.lock' \
    --exclude='uv.lock' \
    --exclude='omnivoice_data' \
    --exclude='.DS_Store' \
    --exclude='*.log' \
    --exclude='legacy_gradio' \
    -czf "$OUT" \
    backend pyproject.toml \
    frontend package.json \
    install.sh run.sh \
    README.md LICENSE \
    scripts

SIZE=$(du -h "$OUT" | cut -f1)
echo "✓ $OUT ($SIZE)"
echo ""
echo "Send this file to your friend. They should:"
echo "  1. tar -xzf $OUT"
echo "  2. cd $(basename "$ROOT")   # or whatever the extracted dir is named"
echo "  3. ./install.sh             # 10–15 min first time"
echo "  4. ./run.sh                 # launches the app"
