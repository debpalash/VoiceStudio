#!/usr/bin/env bash
# Build the `omnivoice-tts` binary from a pinned omnivoice.cpp commit SHA.
#
# Phase 4 Plan 04-01 (GGUF-03 / GATE-03). Runs in CI for every PR that
# touches `backend/engines/omnivoice_gguf/` and on every release. The
# bundled output goes to `bin/omnivoice-tts-<platform>` (with .exe on
# Windows) and a SHA-256 line is appended to `bin/checksums.sha256`.
#
# Usage:
#   scripts/build-omnivoice-tts.sh \
#       --platform {darwin-arm64|darwin-x86_64|windows-x86_64|linux-x86_64|linux-aarch64} \
#       --commit-sha <40hex>
#
# Required tools: git, cmake, ninja or make, a working C++17 compiler,
# `sha256sum` (Linux) or `shasum -a 256` (macOS) or `CertUtil` (Windows).
#
# Hard exits:
#   * exit 2 — macOS Apple Silicon + cmake -DGGML_METAL=ON failed. The
#     caller (CI matrix) treats this as a documented Pitfall 1 fallback:
#     macOS Apple Silicon stays on the in-process VoiceStudioBackend.
set -euo pipefail

PLATFORM=""
COMMIT_SHA=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform)
            PLATFORM="$2"
            shift 2
            ;;
        --commit-sha)
            COMMIT_SHA="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '1,30p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$PLATFORM" || -z "$COMMIT_SHA" ]]; then
    echo "Both --platform and --commit-sha are required." >&2
    exit 1
fi

if ! [[ "$COMMIT_SHA" =~ ^[0-9a-fA-F]{40}$ ]]; then
    echo "--commit-sha must be a 40-char hex SHA (got: $COMMIT_SHA)" >&2
    exit 1
fi

case "$PLATFORM" in
    darwin-arm64|darwin-x86_64|windows-x86_64|linux-x86_64|linux-aarch64) ;;
    *)
        echo "Unknown --platform: $PLATFORM" >&2
        exit 1
        ;;
esac

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO_ROOT/scripts/lib/omnivoice-vulkan.sh"
BIN_DIR="$REPO_ROOT/bin"
mkdir -p "$BIN_DIR"

WORK="$(mktemp -d -t omnivoice-cpp-build.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

echo "→ Cloning omnivoice.cpp at $COMMIT_SHA into $WORK"
git clone --recurse-submodules https://github.com/ServeurpersoCom/omnivoice.cpp.git "$WORK/src"
git -C "$WORK/src" checkout "$COMMIT_SHA"
git -C "$WORK/src" submodule update --init --recursive

cd "$WORK/src"

# A dynamically-linked build (buildcpu.sh, or cmake when it finds BLAS)
# leaves the ggml runtime as shared libraries inside the build tree; only
# copying the executable ships a binary that dies on first spawn with
# exit 127 — "libggml.so.0: cannot open shared object file" — because the
# EXIT trap deletes the build tree, .so files and all (#1348). Copy them
# next to the binary; a static build simply has nothing matching. The
# backend puts bin/ on the loader path when it spawns the binary.
copy_shared_libs() {
    find build \( -name 'libggml*.so*' -o -name 'libggml*.dylib' -o -name 'ggml*.dll' \) \
        -type f -print0 2>/dev/null |
        while IFS= read -r -d '' lib; do
            cp -v "$lib" "$BIN_DIR/"
        done
    # Dereference any symlinked SONAMEs (libggml.so.0 -> libggml.so) so the
    # copies in bin/ are real files under every name the loader asks for.
    find build \( -name 'libggml*.so*' -o -name 'libggml*.dylib' \) \
        -type l -print0 2>/dev/null |
        while IFS= read -r -d '' lib; do
            cp -vL "$lib" "$BIN_DIR/"
        done
}

OUT_NAME="omnivoice-tts-$PLATFORM"
case "$PLATFORM" in
    windows-x86_64) OUT_NAME="$OUT_NAME.exe" ;;
esac

case "$PLATFORM" in
    linux-x86_64|linux-aarch64)
        if ! try_vulkan_build; then
            rm -rf build
            if [[ "$PLATFORM" == linux-x86_64 && -x ./buildcpu.sh ]]; then
                ./buildcpu.sh
            else
                cmake -B build -DCMAKE_BUILD_TYPE=Release
                cmake --build build --config Release -j
            fi
        fi
        cp -v build/omnivoice-tts "$BIN_DIR/$OUT_NAME"
        copy_shared_libs
        ;;
    windows-x86_64)
        # MSYS / git-bash uses the runner's configured MSVC toolchain.
        if ! try_vulkan_build; then
            cmake -B build -DCMAKE_BUILD_TYPE=Release
            cmake --build build --config Release -j
        fi
        cp -v build/Release/omnivoice-tts.exe "$BIN_DIR/$OUT_NAME"
        copy_shared_libs
        ;;
    darwin-x86_64)
        # x86_64 Macs build the CPU variant; Metal on Intel is undocumented.
        cmake -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=x86_64
        cmake --build build --config Release -j
        cp -v build/omnivoice-tts "$BIN_DIR/$OUT_NAME"
        copy_shared_libs
        ;;
    darwin-arm64)
        # Apple Silicon — try Metal (no published buildmetal.sh; we
        # invoke the GGML flag directly per Pitfall 1 / A1).
        if cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DCMAKE_OSX_ARCHITECTURES=arm64; then
            if cmake --build build --config Release -j; then
                cp -v build/omnivoice-tts "$BIN_DIR/$OUT_NAME"
                copy_shared_libs
            else
                echo "→ Metal build failed during compilation; macOS Apple Silicon falls back to in-process VoiceStudioBackend per Pitfall 1." >&2
                exit 2
            fi
        else
            echo "→ Metal cmake configure failed; macOS Apple Silicon falls back to in-process VoiceStudioBackend per Pitfall 1." >&2
            exit 2
        fi
        ;;
esac

# Compute SHA-256 and append to bin/checksums.sha256.
cd "$BIN_DIR"
if command -v sha256sum >/dev/null 2>&1; then
    DIGEST="$(sha256sum "$OUT_NAME" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
    DIGEST="$(shasum -a 256 "$OUT_NAME" | awk '{print $1}')"
else
    echo "No sha256sum/shasum found; cannot compute checksum." >&2
    exit 1
fi

# Rewrite the manifest atomically so we don't accumulate stale entries
# for renamed platforms.
MANIFEST="$BIN_DIR/checksums.sha256"
TMP_MANIFEST="$(mktemp)"
if [[ -f "$MANIFEST" ]]; then
    grep -v "  $OUT_NAME$" "$MANIFEST" > "$TMP_MANIFEST" || true
fi
printf '%s  %s\n' "$DIGEST" "$OUT_NAME" >> "$TMP_MANIFEST"
sort -k2 -o "$MANIFEST" "$TMP_MANIFEST"
rm -f "$TMP_MANIFEST"

echo "✓ Built $OUT_NAME ($DIGEST)"
