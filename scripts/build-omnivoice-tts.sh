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

# A bare `-j` runs every compile at once. With GGML_CPU_ALL_VARIANTS that is
# dozens of cc1plus processes, which the kernel OOM-kills on a 16 GB runner
# ("Killed signal terminated program cc1plus"). Bound it by the CPU count.
BUILD_JOBS="${OMNIVOICE_BUILD_JOBS:-$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo "${NUMBER_OF_PROCESSORS:-2}")}"
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

# A dynamically-linked build (the portable x86-64 CPU or Vulkan build)
# leaves the ggml runtime as shared libraries inside the build tree; only
# copying the executable ships a binary that dies on first spawn with
# exit 127 — "libggml.so.0: cannot open shared object file" — because the
# EXIT trap deletes the build tree, .so files and all (#1348). Copy them
# next to the binary; a static build simply has nothing matching. The
# backend puts bin/ on the loader path when it spawns the binary.
# Every library copied here is also recorded in COPIED_LIBS so the manifest
# step below checksums it: the runtime loads these at spawn time (with
# GGML_BACKEND_DL it picks its CPU/Vulkan backend from them), so they are
# verified like the executable.
COPIED_LIBS="$WORK/copied-libs"
: > "$COPIED_LIBS"
copy_shared_libs() {
    find build \( -name 'libggml*.so*' -o -name 'libggml*.dylib' -o -name 'ggml*.dll' \) \
        -type f -print0 2>/dev/null |
        while IFS= read -r -d '' lib; do
            cp -v "$lib" "$BIN_DIR/"
            basename "$lib" >> "$COPIED_LIBS"
        done
    # Dereference any symlinked SONAMEs (libggml.so.0 -> libggml.so) so the
    # copies in bin/ are real files under every name the loader asks for.
    find build \( -name 'libggml*.so*' -o -name 'libggml*.dylib' \) \
        -type l -print0 2>/dev/null |
        while IFS= read -r -d '' lib; do
            cp -vL "$lib" "$BIN_DIR/"
            basename "$lib" >> "$COPIED_LIBS"
        done
}

# MSVC builds link the C++ runtime dynamically, and a Windows host without the
# Visual C++ Redistributable cannot start the binary (exit 0xC0000135). The
# desktop installer ships it (#2780), so deploy the runtime app-locally next
# to the executable, as Microsoft documents for redistribution. These x64
# copies also serve Windows on ARM, which runs this binary under emulation.
copy_msvc_runtime() {
    # OMNIVOICE_MSVC_RUNTIME_DIR overrides the source directory (tests).
    local runtime_dir="${OMNIVOICE_MSVC_RUNTIME_DIR:-}" dll
    if [[ -z "$runtime_dir" ]]; then
        runtime_dir="$(cygpath -u "${SYSTEMROOT:-C:\\Windows}")/System32"
    fi
    for dll in vcruntime140.dll vcruntime140_1.dll msvcp140.dll msvcp140_1.dll \
        msvcp140_2.dll vcomp140.dll; do
        if [[ -f "$runtime_dir/$dll" ]]; then
            cp -v "$runtime_dir/$dll" "$BIN_DIR/"
            printf '%s\n' "$dll" >> "$COPIED_LIBS"
        fi
    done
    if [[ ! -f "$BIN_DIR/msvcp140.dll" || ! -f "$BIN_DIR/vcruntime140.dll" ]]; then
        echo "Visual C++ runtime DLLs not found in $runtime_dir" >&2
        return 1
    fi
}

# Multi-config generators (Visual Studio) put the executable under
# build/Release/; single-config ones (Ninja, Makefiles) put it in build/.
built_windows_exe() {
    local candidate
    for candidate in build/Release/omnivoice-tts.exe build/omnivoice-tts.exe; do
        if [[ -f "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    echo "omnivoice-tts.exe not found in build/Release/ or build/" >&2
    return 1
}

# x86-64 CPU builds must run on every x86-64 CPU, not just the build host.
# A plain configure defaults to GGML_NATIVE=ON, compiling for the CI runner's
# CPU (AVX-512 on current hosted runners); the shipped runtime then dies with
# an illegal instruction on older or AVX2-only CPUs (#2780). Instead build
# every CPU variant as a loadable backend and let ggml pick the best one the
# host supports at startup (the same flags the Vulkan path uses).
portable_x86_cpu_build() {
    cmake -B build -DCMAKE_BUILD_TYPE=Release \
        -DGGML_BACKEND_DL=ON -DBUILD_SHARED_LIBS=ON \
        -DGGML_NATIVE=OFF -DGGML_CPU_ALL_VARIANTS=ON "$@"
    cmake --build build --config Release -j "$BUILD_JOBS"
}

# The desktop app supports macOS 13.3+; without an explicit target the binary
# would require the build host's macOS version.
case "$PLATFORM" in
    darwin-*) export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-13.3}" ;;
esac

OUT_NAME="omnivoice-tts-$PLATFORM"
case "$PLATFORM" in
    windows-x86_64) OUT_NAME="$OUT_NAME.exe" ;;
esac

case "$PLATFORM" in
    linux-x86_64|linux-aarch64)
        if ! try_vulkan_build; then
            rm -rf build
            if [[ "$PLATFORM" == linux-x86_64 ]]; then
                # Not upstream buildcpu.sh: it compiles for the build host.
                portable_x86_cpu_build
            else
                cmake -B build -DCMAKE_BUILD_TYPE=Release
                cmake --build build --config Release -j "$BUILD_JOBS"
            fi
        fi
        cp -v build/omnivoice-tts "$BIN_DIR/$OUT_NAME"
        copy_shared_libs
        ;;
    windows-x86_64)
        # MSYS / git-bash uses the runner's configured MSVC toolchain.
        if ! try_vulkan_build; then
            portable_x86_cpu_build
        fi
        exe="$(built_windows_exe)"
        cp -v "$exe" "$BIN_DIR/$OUT_NAME"
        copy_shared_libs
        copy_msvc_runtime
        ;;
    darwin-x86_64)
        # x86_64 Macs build the CPU variant; Metal on Intel is undocumented.
        portable_x86_cpu_build -DCMAKE_OSX_ARCHITECTURES=x86_64
        cp -v build/omnivoice-tts "$BIN_DIR/$OUT_NAME"
        copy_shared_libs
        ;;
    darwin-arm64)
        # Apple Silicon — try Metal (no published buildmetal.sh; we
        # invoke the GGML flag directly per Pitfall 1 / A1).
        if cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DCMAKE_OSX_ARCHITECTURES=arm64; then
            if cmake --build build --config Release -j "$BUILD_JOBS"; then
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

# Compute SHA-256 for the binary and every library copied above, and write
# them to bin/checksums.sha256.
cd "$BIN_DIR"
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        echo "No sha256sum/shasum found; cannot compute checksum." >&2
        return 1
    fi
}
DIGEST="$(sha256_of "$OUT_NAME")"

# Rewrite the manifest atomically, replacing (not accumulating) the entries
# this build owns, so renamed platforms and rebuilt libraries never leave a
# stale digest behind.
MANIFEST="$BIN_DIR/checksums.sha256"
TMP_MANIFEST="$(mktemp)"
OWNED="$(mktemp)"
{ printf '%s\n' "$OUT_NAME"; sort -u "$COPIED_LIBS"; } > "$OWNED"
if [[ -f "$MANIFEST" ]]; then
    awk 'NR == FNR { owned[$0] = 1; next } !($2 in owned)' "$OWNED" "$MANIFEST" > "$TMP_MANIFEST"
fi
while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    printf '%s  %s\n' "$(sha256_of "$name")" "$name" >> "$TMP_MANIFEST"
done < "$OWNED"
sort -k2 -o "$MANIFEST" "$TMP_MANIFEST"
rm -f "$TMP_MANIFEST" "$OWNED"

echo "✓ Built $OUT_NAME ($DIGEST)"
