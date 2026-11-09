cmake_minimum_required(VERSION 3.22)
project(VoiceStudioRuntimes LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
foreach(runtime omni llama)
    file(GLOB_RECURSE archives "${CMAKE_CURRENT_SOURCE_DIR}/build/${runtime}-${RUNTIME_ABI}/*.a")
    add_library(vs_${runtime} SHARED ${runtime}.cpp)
    target_include_directories(vs_${runtime} PRIVATE "${OMNI_SOURCE}/src" "${LLAMA_SOURCE}/include" "${LLAMA_SOURCE}/ggml/include")
    target_link_libraries(vs_${runtime} PRIVATE -Wl,--start-group ${archives} -Wl,--end-group android log)
    # Both runtimes vendor different ggml versions. Bind internally and export
    # only JNI: loading either order cannot interpose the other runtime's ABI.
    target_link_options(vs_${runtime} PRIVATE -Wl,--exclude-libs,ALL -Wl,-Bsymbolic -Wl,-z,max-page-size=16384)
    target_compile_options(vs_${runtime} PRIVATE -fvisibility=hidden)
endforeach()

# Pinned, vendored encoder sources are shared with SwiftPM; no binary download.
set(VS_COMMON "${CMAKE_CURRENT_SOURCE_DIR}/../../ios/VoiceStudioKit/Sources/VSNative")
file(GLOB LAME_SOURCES CONFIGURE_DEPENDS "${VS_COMMON}/Lame/libmp3lame/*.c")
add_library(vs_mp3 SHARED mp3.c "${VS_COMMON}/Mp3Encoder.c" ${LAME_SOURCES})
target_include_directories(vs_mp3 PRIVATE "${VS_COMMON}/include" "${VS_COMMON}/Lame" "${VS_COMMON}/Lame/include")
target_compile_definitions(vs_mp3 PRIVATE HAVE_CONFIG_H)
target_compile_options(vs_mp3 PRIVATE -fvisibility=hidden)
target_link_libraries(vs_mp3 PRIVATE m)
target_link_options(vs_mp3 PRIVATE -Wl,--exclude-libs,ALL -Wl,-Bsymbolic -Wl,-z,max-page-size=16384)
