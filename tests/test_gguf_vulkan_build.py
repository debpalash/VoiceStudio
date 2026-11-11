"""Execute optional Vulkan build paths with hermetic compiler stand-ins."""
from pathlib import Path

import pytest
from build_script_stubs import run_bash, stage_build_script

REPO = Path(__file__).resolve().parents[1]
SPIRV_COMMIT = "09913f088a1197aba4aefd300a876b2ebbaa3391"
# `git clone` makes the destination; `git -C <dir> rev-parse HEAD` reports the
# pinned SPIRV-Headers commit unless a test overrides FAKE_SPIRV_COMMIT.
GIT_STUB = '''
if [[ "${1:-}" == clone ]]; then for d in "$@"; do :; done; mkdir -p "$d"; fi
if [[ "$*" == *rev-parse* ]]; then printf '%s\\n' "${FAKE_SPIRV_COMMIT:-@COMMIT@}"; fi
'''.replace("@COMMIT@", SPIRV_COMMIT)


@pytest.mark.parametrize("platform", ["windows-x86_64", "linux-x86_64", "linux-aarch64"])
@pytest.mark.parametrize("mode", ["absent", "configure-fails", "build-fails", "success"])
def test_vulkan_build_and_cpu_fallback(tmp_path, platform, mode):
    stage_build_script(tmp_path)
    tools = {
        "git": GIT_STUB,
        "c++": "exit 0",
        "cmake": '''
printf '%s\\n' "$*" >> "$CMAKE_LOG"
printf 'PREFIX_PATH=%s\\n' "${CMAKE_PREFIX_PATH:-}" >> "$CMAKE_LOG"
# Match pinned ggml's default and its dynamic CPU backend configure constraint.
if [[ "$*" == *-DGGML_BACKEND_DL=ON* && "$*" != *-DGGML_NATIVE=OFF* ]]; then
    echo 'GGML_NATIVE is not compatible with GGML_BACKEND_DL' >&2
    exit 1
fi
if [[ "$*" == *-DGGML_VULKAN=ON* && "$MODE" == configure-fails ]]; then exit 1; fi
if [[ "$*" == *-DGGML_VULKAN=ON* ]]; then touch "$VULKAN_ATTEMPT"; fi
if [[ "${1:-}" == --build ]]; then
    if [[ "$MODE" == build-fails && -f "$VULKAN_ATTEMPT" && ! -f "$FAILED" ]]; then touch "$FAILED"; exit 1; fi
    mkdir -p build/Release
    printf runtime > build/omnivoice-tts
    printf runtime > build/Release/omnivoice-tts.exe
    printf server > build/tts-server
    printf server > build/Release/tts-server.exe
    printf library > build/libggml-vulkan.so
    printf library > build/Release/ggml-vulkan.dll
fi
''',
    }
    if mode != "absent":
        tools["glslc"] = "exit 0"
    script = (tmp_path / "run.sh")
    script.write_bytes(b'''set -euo pipefail
unset VULKAN_SDK
# System utilities remain available; only the optional compiler probe is masked.
command() {
    if [[ "$MODE" == absent && "${1:-}" == -v && "${2:-}" == glslc ]]; then
        return 1
    fi
    builtin command "$@"
}
export -f command
export CMAKE_LOG="$PWD/cmake.log" VULKAN_ATTEMPT="$PWD/vulkan-attempt" FAILED="$PWD/failed"
bash scripts/build-omnivoice-tts.sh --platform "$PLATFORM" --commit-sha aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
''')
    msvc = tmp_path / "msvc"
    msvc.mkdir()
    for dll in ("msvcp140.dll", "vcruntime140.dll"):
        (msvc / dll).write_text("crt")
    result = run_bash(tmp_path, [script.name], tools=tools, timeout=30, env={
        "MODE": mode, "PLATFORM": platform, "OMNIVOICE_MSVC_RUNTIME_DIR": msvc.as_posix()})
    assert result.returncode == 0, result.stdout + result.stderr
    log = (tmp_path / "cmake.log").read_text(encoding="utf-8")
    if mode == "absent":
        assert "GGML_VULKAN=ON" not in log
    elif mode == "success":
        assert "-DGGML_VULKAN=ON" in log
        assert "-DGGML_BACKEND_DL=ON" in log
        assert "-DBUILD_SHARED_LIBS=ON" in log
        assert "-DGGML_NATIVE=OFF" in log
        assert "-DGGML_CPU_ALL_VARIANTS=ON" in log
        assert log.count("-DCMAKE_BUILD_TYPE=Release") == 1, result.stdout + result.stderr
        if platform.startswith("windows"):
            # ggml's find_package(SPIRV-Headers CONFIG) needs a config the
            # LunarG SDK lacks: it is installed and put on CMAKE_PREFIX_PATH.
            assert "--install" in log
            assert "PREFIX_PATH=" in log and "spirv-headers-prefix" in log
        suffix = ".dll" if platform.startswith("windows") else ".so"
        assert any((tmp_path / "bin").glob("*ggml-vulkan" + suffix))
    else:
        assert "GGML_VULKAN=ON" in log
        assert log.count("-DCMAKE_BUILD_TYPE=Release") == 2


def test_header_probe_cannot_lose_a_race_with_the_compiler():
    """`printf | c++` under pipefail failed when c++ exited before reading (#3787 CI)."""
    probe = (REPO / "scripts/lib/omnivoice-vulkan.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in probe.splitlines() if not line.lstrip().startswith("#"))
    assert "| c++" not in code and "|\n            c++" not in code
    assert "c++ -fsyntax-only -x c++ -" in code and "<<<" in code


def test_windows_vulkan_falls_back_to_cpu_when_spirv_headers_do_not_match_the_pin(tmp_path):
    stage_build_script(tmp_path)
    tools = {
        "git": GIT_STUB,
        "glslc": "exit 0",
        "cmake": '''
printf '%s\\n' "$*" >> "$CMAKE_LOG"
if [[ "${1:-}" == --build ]]; then
    mkdir -p build/Release
    printf runtime > build/Release/omnivoice-tts.exe
    printf server > build/Release/tts-server.exe
fi
''',
    }
    msvc = tmp_path / "msvc"
    msvc.mkdir()
    for dll in ("msvcp140.dll", "vcruntime140.dll"):
        (msvc / dll).write_text("crt")
    script = tmp_path / "run.sh"
    script.write_bytes(b'''set -euo pipefail
unset VULKAN_SDK
export CMAKE_LOG="$PWD/cmake.log"
bash scripts/build-omnivoice-tts.sh --platform windows-x86_64 --commit-sha aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
''')
    result = run_bash(tmp_path, [script.name], tools=tools, timeout=30, env={
        "FAKE_SPIRV_COMMIT": "b" * 40, "OMNIVOICE_MSVC_RUNTIME_DIR": msvc.as_posix()})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "expected " + SPIRV_COMMIT in result.stderr
    assert "GGML_VULKAN=ON" not in (tmp_path / "cmake.log").read_text(encoding="utf-8")
