#!/usr/bin/env bash
# Since omnivoice.cpp 040c8b3 the pinned ggml runs
# `find_package(SPIRV-Headers CONFIG REQUIRED)` for its Vulkan backend. Linux
# packages (spirv-headers) ship that CMake config; the Windows LunarG SDK ships
# the headers but no config, so the Vulkan module silently failed to configure.
# Install the Khronos SPIRV-Headers release tagged for the SDK version the
# workflow installs (vulkan-sdk-1.4.309.0), checked against its pinned commit,
# into a private prefix and put that on CMAKE_PREFIX_PATH.
SPIRV_HEADERS_TAG="vulkan-sdk-1.4.309.0"
SPIRV_HEADERS_COMMIT="09913f088a1197aba4aefd300a876b2ebbaa3391"
ensure_spirv_headers_config() {
    # Tests and unusual hosts can supply a prefix that already has the config.
    if [[ -n "${OMNIVOICE_SPIRV_HEADERS_PREFIX:-}" ]]; then
        export CMAKE_PREFIX_PATH="$OMNIVOICE_SPIRV_HEADERS_PREFIX${CMAKE_PREFIX_PATH:+;$CMAKE_PREFIX_PATH}"
        return 0
    fi
    local src="$WORK/spirv-headers-src" prefix="$WORK/spirv-headers-prefix" actual
    git clone --quiet --depth 1 --branch "$SPIRV_HEADERS_TAG" \
        https://github.com/KhronosGroup/SPIRV-Headers.git "$src" || return 1
    actual="$(git -C "$src" rev-parse HEAD)"
    if [[ "$actual" != "$SPIRV_HEADERS_COMMIT" ]]; then
        echo "SPIRV-Headers $SPIRV_HEADERS_TAG is $actual, expected $SPIRV_HEADERS_COMMIT" >&2
        return 1
    fi
    cmake -S "$src" -B "$WORK/spirv-headers-build" -DCMAKE_INSTALL_PREFIX="$prefix" \
        -DSPIRV_HEADERS_ENABLE_TESTS=OFF -DSPIRV_HEADERS_ENABLE_INSTALL=ON || return 1
    cmake --install "$WORK/spirv-headers-build" --config Release || return 1
    # Native cmake wants a Windows path; MSYS does not convert inside a list.
    prefix="$(cygpath -m "$prefix" 2>/dev/null || printf '%s' "$prefix")"
    export CMAKE_PREFIX_PATH="$prefix${CMAKE_PREFIX_PATH:+;$CMAKE_PREFIX_PATH}"
}

# Optional GPU module: the executable can still start without a Vulkan driver.
try_vulkan_build() {
    if [[ "$PLATFORM" == windows-* ]]; then
        if [[ -z "${VULKAN_SDK:-}" ]] && ! command -v glslc >/dev/null 2>&1; then
            return 1
        fi
        if ! ensure_spirv_headers_config; then
            echo "→ SPIRV-Headers could not be installed; retrying $PLATFORM with CPU." >&2
            return 1
        fi
    # A here-string, not `printf | c++`: under pipefail a compiler that exits
    # before reading its input made printf die of SIGPIPE, so the probe could
    # report no Vulkan headers on a host that has them.
    elif ! command -v glslc >/dev/null 2>&1 ||
        ! c++ -fsyntax-only -x c++ - 2>/dev/null \
            <<<$'#include <spirv/unified1/spirv.hpp>\nint main(){}'; then
        return 1
    fi
    # Pinned ggml rejects native CPU flags with dynamically loaded backends.
    # Runtime-dispatched variants retain CPU optimizations on compatible hosts.
    if cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON \
            -DGGML_BACKEND_DL=ON -DBUILD_SHARED_LIBS=ON \
            -DGGML_NATIVE=OFF -DGGML_CPU_ALL_VARIANTS=ON &&
        cmake --build build --config Release -j "$BUILD_JOBS"; then
        return 0
    fi
    echo "→ Vulkan build failed; retrying $PLATFORM with CPU." >&2
    rm -rf build
    return 1
}
