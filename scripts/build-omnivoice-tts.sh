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

# Released binaries must run on every CPU of their architecture, not only on
# the runner that compiled them. ggml defaults to GGML_NATIVE=ON, which targets
# the build host: an AVX-512 windows-latest runner produced a binary that dies
# with an illegal instruction on AVX2-only CPUs, such as most AMD Ryzen and
# recent Intel consumer chips (#2780). Build every x86 CPU variant as a
# runtime-loaded module instead; ggml_backend_load_all() picks the best one for
# the host (upstream omnivoice.cpp's buildall.* uses the same flags).
# BUILD_SHARED_LIBS is explicit because ggml turns it off under MinGW. ARM
# builds stay native: darwin-arm64 builds on M1-class runners, the oldest Apple
# Silicon, and linux-aarch64 ships in no installer.
PORTABLE_X86_FLAGS=(
    -DBUILD_SHARED_LIBS=ON
    -DGGML_NATIVE=OFF
    -DGGML_BACKEND_DL=ON
    -DGGML_CPU_ALL_VARIANTS=ON
)

case "$PLATFORM" in
    linux-x86_64)
        # Configured here rather than through upstream buildcpu.sh, which
        # compiles for the build host. ggml-blas becomes an optional module
        # that is skipped on hosts without OpenBLAS.
        cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_BLAS=ON "${PORTABLE_X86_FLAGS[@]}"
        cmake --build build --config Release -j
        cp -v build/omnivoice-tts "$BIN_DIR/$OUT_NAME"
        copy_shared_libs
        ;;
    linux-aarch64)
        # Apple Silicon under Asahi Linux: prefer GGML's Vulkan backend so
        # the Honeykrisp driver can accelerate generation, but degrade to
        # CPU when the Vulkan dev deps (glslc, SPIRV headers) are absent —
        # same graceful-fallback pattern as darwin-arm64 Metal above.
        # ggml-vulkan needs <spirv/unified1/spirv.hpp> at compile time
        # (Arch: spirv-headers, Debian/Ubuntu: spirv-headers) and glslc to
        # compile its compute shaders; a configure-only probe misses the
        # header, hence the compile check.
        VULKAN_BUILT=0
        if command -v glslc >/dev/null 2>&1 &&
            printf '#include <spirv/unified1/spirv.hpp>\nint main(){}\n' |
                c++ -fsyntax-only -x c++ - 2>/dev/null; then
            if cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON &&
                cmake --build build --config Release -j; then
                VULKAN_BUILT=1
            else
                echo "→ Vulkan build failed; retrying Linux ARM64 with CPU." >&2
                rm -rf build
            fi
        fi
        if [[ "$VULKAN_BUILT" -eq 0 ]]; then
            rm -rf build
            cmake -B build -DCMAKE_BUILD_TYPE=Release
            cmake --build build --config Release -j
        fi
        cp -v build/omnivoice-tts "$BIN_DIR/$OUT_NAME"
        copy_shared_libs
        ;;
    windows-x86_64)
        # CI runs this from MSYS / git-bash; CMake picks up the MSVC
        # toolchain via the GitHub Actions runner's default env.
        cmake -B build -DCMAKE_BUILD_TYPE=Release "${PORTABLE_X86_FLAGS[@]}"
        cmake --build build --config Release -j
        cp -v build/Release/omnivoice-tts.exe "$BIN_DIR/$OUT_NAME"
        copy_shared_libs
        ;;
    darwin-x86_64)
        # x86_64 Macs build the CPU variant; Metal on Intel is undocumented.
        cmake -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=x86_64 \
            "${PORTABLE_X86_FLAGS[@]}"
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
