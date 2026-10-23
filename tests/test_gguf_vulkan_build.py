"""Execute optional Vulkan build paths with hermetic compiler stand-ins."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BASH = (shutil.which("bash") if os.name != "nt" else
        "C:/Program Files/Git/bin/bash.exe")


@pytest.mark.parametrize("platform", ["windows-x86_64", "linux-x86_64", "linux-aarch64"])
@pytest.mark.parametrize("mode", ["absent", "configure-fails", "build-fails", "success"])
def test_vulkan_build_and_cpu_fallback(tmp_path, platform, mode):
    if not BASH or not Path(BASH).is_file():
        pytest.skip("POSIX bash required for the build script")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(REPO / "scripts/build-omnivoice-tts.sh", scripts)
    if (REPO / "scripts/lib").exists():
        shutil.copytree(REPO / "scripts/lib", scripts / "lib")
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    tools = {
        "git": 'if [[ "${1:-}" == clone ]]; then for d in "$@"; do :; done; mkdir -p "$d"; fi',
        "c++": "exit 0",
        "cmake": '''
printf '%s\\n' "$*" >> "$CMAKE_LOG"
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
    printf library > build/libggml-vulkan.so
    printf library > build/Release/ggml-vulkan.dll
fi
''',
    }
    if mode != "absent":
        tools["glslc"] = "exit 0"
    for name, body in tools.items():
        tool = fake / name
        tool.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body + "\n")
        tool.chmod(0o755)
    launcher = tmp_path / "run.sh"
    launcher.write_text('''#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
unset VULKAN_SDK
# System utilities remain available; only the optional compiler probe is masked.
command() {
    if [[ "$MODE" == absent && "${1:-}" == -v && "${2:-}" == glslc ]]; then
        return 1
    fi
    builtin command "$@"
}
export -f command
export PATH="$PWD/fake-bin:/usr/bin:/bin"
export CMAKE_LOG="$PWD/cmake.log" VULKAN_ATTEMPT="$PWD/vulkan-attempt" FAILED="$PWD/failed"
bash scripts/build-omnivoice-tts.sh --platform "$PLATFORM" --commit-sha aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
''')
    env = {**os.environ, "MODE": mode, "PLATFORM": platform}
    result = subprocess.run([BASH, launcher.as_posix()], env=env,
                            capture_output=True, text=True, timeout=30,
                            check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    log = (tmp_path / "cmake.log").read_text()
    if mode == "absent":
        assert "GGML_VULKAN=ON" not in log
    elif mode == "success":
        assert "-DGGML_VULKAN=ON" in log
        assert "-DGGML_BACKEND_DL=ON" in log
        assert "-DBUILD_SHARED_LIBS=ON" in log
        assert "-DGGML_NATIVE=OFF" in log
        assert "-DGGML_CPU_ALL_VARIANTS=ON" in log
        assert log.count("-DCMAKE_BUILD_TYPE=Release") == 1, result.stdout + result.stderr
        suffix = ".dll" if platform.startswith("windows") else ".so"
        assert any((tmp_path / "bin").glob("*ggml-vulkan" + suffix))
    else:
        assert "GGML_VULKAN=ON" in log
        assert log.count("-DCMAKE_BUILD_TYPE=Release") == 2
