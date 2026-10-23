#!/usr/bin/env bash
# Optional GPU module: the executable can still start without a Vulkan driver.
try_vulkan_build() {
    if [[ "$PLATFORM" == windows-* ]]; then
        if [[ -z "${VULKAN_SDK:-}" ]] && ! command -v glslc >/dev/null 2>&1; then
            return 1
        fi
    elif ! command -v glslc >/dev/null 2>&1 ||
        ! printf '#include <spirv/unified1/spirv.hpp>\nint main(){}\n' |
            c++ -fsyntax-only -x c++ - 2>/dev/null; then
        return 1
    fi
    # Pinned ggml rejects native CPU flags with dynamically loaded backends.
    # Runtime-dispatched variants retain CPU optimizations on compatible hosts.
    if cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON \
            -DGGML_BACKEND_DL=ON -DBUILD_SHARED_LIBS=ON \
            -DGGML_NATIVE=OFF -DGGML_CPU_ALL_VARIANTS=ON &&
        cmake --build build --config Release -j; then
        return 0
    fi
    echo "→ Vulkan build failed; retrying $PLATFORM with CPU." >&2
    rm -rf build
    return 1
}
