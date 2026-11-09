#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
python3 "$root/../mp3/verify_source.py"
export GIT_CEILING_DIRECTORIES="$root/build/sources"
if [[ "${1:-}" != "--skip-fetch" ]]; then python3 "$root/fetch.py"; fi
if [[ "${1:-}" == "--fetch-only" ]]; then exit 0; fi
sdk="${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}"
ndk="$sdk/ndk/27.1.12297006"
cmake="$sdk/cmake/3.22.1/bin/cmake"
ninja="$sdk/cmake/3.22.1/bin/ninja"
[[ -f "$ndk/build/cmake/android.toolchain.cmake" ]] || { echo 'Install Android NDK 27.1.12297006' >&2; exit 1; }
toolchains=("$ndk/toolchains/llvm/prebuilt/"*)
toolchain="${toolchains[0]}"
omni_commit="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["omnivoice.cpp"]["source"]["commit"])' "$root/../pins.json")"
llama_commit="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["llama.cpp"]["source"]["commit"])' "$root/../pins.json")"
for abi in arm64-v8a x86_64; do
    common=(-G Ninja -DCMAKE_MAKE_PROGRAM="$ninja" -DCMAKE_TOOLCHAIN_FILE="$ndk/build/cmake/android.toolchain.cmake" -DANDROID_ABI="$abi" -DANDROID_PLATFORM=android-29 -DANDROID_STL=c++_shared -DCMAKE_BUILD_TYPE=Release -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF -DGGML_LLAMAFILE=OFF -DGGML_CPU_KLEIDIAI=OFF -DCMAKE_SHARED_LINKER_FLAGS=-Wl,-z,max-page-size=16384)
    "$cmake" -S "$root/build/sources/omnivoice.cpp-$omni_commit" -B "$root/build/omni-$abi" "${common[@]}" -DOMNIVOICE_SHARED=OFF -DGGML_METAL=OFF -DGGML_BLAS=OFF
    "$cmake" --build "$root/build/omni-$abi" --target omnivoice-core -j 2
    "$cmake" -S "$root/build/sources/llama.cpp-$llama_commit" -B "$root/build/llama-$abi" "${common[@]}" -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=OFF -DLLAMA_BUILD_SERVER=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_COMMIT="$llama_commit" -DLLAMA_BUILD_NUMBER=11716 -DGGML_METAL=OFF -DGGML_BLAS=OFF
    "$cmake" --build "$root/build/llama-$abi" --target llama -j 2
    "$cmake" -S "$root" -B "$root/build/jni-$abi" "${common[@]}" -DOMNI_SOURCE="$root/build/sources/omnivoice.cpp-$omni_commit" -DLLAMA_SOURCE="$root/build/sources/llama.cpp-$llama_commit" -DRUNTIME_ABI="$abi"
    "$cmake" --build "$root/build/jni-$abi" -j 2
    mkdir -p "$root/build/jniLibs/$abi"
    cp "$root/build/jni-$abi/"libvs_*.so "$root/build/jniLibs/$abi/"
    cp "$toolchain/sysroot/usr/lib/$([[ "$abi" == arm64-v8a ]] && echo aarch64-linux-android || echo x86_64-linux-android)/libc++_shared.so" "$root/build/jniLibs/$abi/"
done

# Legal texts are checked in as app assets; retire copies from earlier builds.
rm -f "$root/build/assets/licenses/omnivoice-MIT.txt" "$root/build/assets/licenses/omnivoice-ggml-MIT.txt" "$root/build/assets/licenses/llama-MIT.txt"
mkdir -p "$root/build/assets"
python3 "$root/verify-notices.py"

python3 "$root/verify.py" "$ndk"
