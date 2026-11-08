"""The desktop installers ship the OmniVoice GGUF runtime (#2780).

Before this, `build-omnivoice-tts.yml` produced the binary only as a CI
artifact and no installer packaged it, so the engine reported "native binary
not found" on every packaged install. These checks pin the whole chain: the
build is one reusable workflow, every installer job consumes its artifact for
the slug its backend resolves, the Windows build carries its C++ runtime, and
the packaged layout matches the backend's lookup.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
BASH = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"

# What backend/engines/omnivoice_gguf/backend.py::_platform_slug() returns on
# each installer target. Windows on ARM runs the emulated x64 Python runtime.
EXPECTED_SLUG = {
    ("linux", "x64"): "linux-x86_64",
    ("win32", "x64"): "windows-x86_64",
    ("win32", "arm64"): "windows-x86_64",
    ("darwin", "arm64"): "darwin-arm64",
    # Intel Macs cannot run the local backend (docs/install/macos.md).
    ("darwin", "x64"): "",
}


def _workflow(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_build_workflow_is_reusable_and_uploads_every_runtime_file():
    wf = _workflow("build-omnivoice-tts.yml")
    call = wf["on"]["workflow_call"]["inputs"]
    assert {"ref", "desktop"} <= set(call)
    job = wf["jobs"]["build-omnivoice-tts"]
    assert job["steps"][0]["with"]["ref"] == "${{ inputs.ref }}"
    upload = next(s for s in job["steps"] if s.get("name") == "Upload binary artifact")
    paths = upload["with"]["path"]
    for pattern in ("bin/libggml*", "bin/ggml*.dll", "bin/msvcp140*.dll",
                    "bin/vcruntime140*.dll", "bin/checksums.sha256"):
        assert pattern in paths
    assert upload["with"]["if-no-files-found"] == "error"
    # A matrix `include` that matches no combination re-adds it, which would
    # defeat the desktop exclude; runners come from a lookup instead.
    assert "include" not in job["strategy"]["matrix"]
    for platform in job["strategy"]["matrix"]["platform"]:
        assert f'"{platform}":' in job["runs-on"]


@pytest.mark.parametrize("name", ["electron-release.yml", "electron-build.yml"])
def test_every_installer_job_packages_the_runtime_its_backend_resolves(name):
    wf = _workflow(name)
    build = wf["jobs"]["omnivoice-tts"]
    assert build["uses"] == "./.github/workflows/build-omnivoice-tts.yml"
    assert build["with"]["desktop"] == "true"
    package = wf["jobs"]["package"]
    needs = package["needs"] if isinstance(package["needs"], list) else [package["needs"]]
    assert "omnivoice-tts" in needs
    assert package["env"]["VOICESTUDIO_OMNIVOICE_TTS_PLATFORM"] == "${{ matrix.gguf_platform }}"
    legs = package["strategy"]["matrix"]["include"]
    assert {(leg["platform"], leg["arch"]) for leg in legs} == set(EXPECTED_SLUG)
    for leg in legs:
        assert leg["gguf_platform"] == EXPECTED_SLUG[(leg["platform"], leg["arch"])], leg
    steps = [s.get("name") for s in package["steps"]]
    stage = package["steps"][steps.index("Stage the OmniVoice GGUF runtime")]
    assert stage["with"]["name"] == "omnivoice-tts-${{ matrix.gguf_platform }}"
    assert stage["with"]["path"] == "electron/build/omnivoice-tts"
    # Staged before the packaging contract first runs (check:electron).
    assert steps.index("Stage the OmniVoice GGUF runtime") < steps.index("Validate and build Electron")


def test_release_builds_the_runtime_from_the_release_ref():
    build = _workflow("electron-release.yml")["jobs"]["omnivoice-tts"]
    assert "release_tag" in build["with"]["ref"]


def test_packaged_layout_matches_the_backend_lookup():
    # The packaged backend runs from resources/, so `<root>/bin` is
    # resources/bin — exactly where the installer puts the runtime.
    from engines.omnivoice_gguf import backend

    assert (backend._REPO_ROOT / "backend" / "engines" / "omnivoice_gguf").resolve() == \
        backend._PKG_DIR
    assert backend._binary_path("windows-x86_64") == backend._REPO_ROOT / "bin" / \
        "omnivoice-tts-windows-x86_64.exe"
    config = (REPO / "electron" / "electron-builder.config.mjs").read_text(encoding="utf-8")
    assert "from: '../backend',\n      to: 'backend'" in config
    bundle = (REPO / "electron" / "omnivoice-tts-bundle.mjs").read_text(encoding="utf-8")
    assert "OMNIVOICE_TTS_RESOURCE_DIR = 'bin'" in bundle


def _run_windows_build(tmp_path: Path, runtime: dict[str, str]):
    scripts = tmp_path / "scripts"
    shutil.copytree(REPO / "scripts/lib", scripts / "lib")
    shutil.copy2(REPO / "scripts/build-omnivoice-tts.sh", scripts)
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    tools = {
        "git": 'if [[ "$1" == clone ]]; then for d in "$@"; do :; done; mkdir -p "$d"; fi',
        "cmake": 'if [[ "$1" == --build ]]; then mkdir -p build/Release;'
                 ' printf rt > build/Release/omnivoice-tts.exe;'
                 ' printf lib > build/Release/ggml-cpu-haswell.dll; fi',
    }
    for name, body in tools.items():
        (fake / name).write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body + "\n")
        (fake / name).chmod(0o755)
    msvc = tmp_path / "msvc"
    msvc.mkdir()
    for dll, body in runtime.items():
        (msvc / dll).write_text(body)
    env = {**os.environ, "PATH": f"{fake}:/usr/bin:/bin",
           "OMNIVOICE_MSVC_RUNTIME_DIR": msvc.as_posix()}
    env.pop("VULKAN_SDK", None)
    return subprocess.run(
        [BASH, str(scripts / "build-omnivoice-tts.sh"), "--platform", "windows-x86_64",
         "--commit-sha", "a" * 40],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
    )


def test_windows_build_ships_its_cpp_runtime_with_checksums(tmp_path):
    if not BASH or not Path(BASH).is_file():
        pytest.skip("POSIX bash required for the build script")
    result = _run_windows_build(tmp_path, {
        "msvcp140.dll": "msvcp", "vcruntime140.dll": "vcrt",
        "vcruntime140_1.dll": "vcrt1", "vcomp140.dll": "omp",
    })
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = (tmp_path / "bin" / "checksums.sha256").read_text(encoding="utf-8")
    for name in ("omnivoice-tts-windows-x86_64.exe", "ggml-cpu-haswell.dll", "msvcp140.dll",
                 "vcruntime140.dll", "vcruntime140_1.dll", "vcomp140.dll"):
        assert (tmp_path / "bin" / name).is_file(), name
        assert f"  {name}\n" in manifest, name


def test_windows_build_fails_without_the_cpp_runtime(tmp_path):
    if not BASH or not Path(BASH).is_file():
        pytest.skip("POSIX bash required for the build script")
    result = _run_windows_build(tmp_path, {"vcruntime140.dll": "vcrt"})
    assert result.returncode != 0
    assert "Visual C++ runtime DLLs not found" in result.stderr


def test_every_parallel_build_is_bounded():
    # A bare `cmake --build -j` compiles every CPU variant at once and the
    # Linux runner OOM-kills cc1plus, so the installer runtime never builds.
    for rel in ("scripts/build-omnivoice-tts.sh", "scripts/lib/omnivoice-vulkan.sh"):
        text = (REPO / rel).read_text(encoding="utf-8")
        builds = [line for line in text.splitlines() if "cmake --build" in line]
        assert builds, rel
        for line in builds:
            assert '-j "$BUILD_JOBS"' in line, f"{rel}: {line.strip()}"
    script = (REPO / "scripts/build-omnivoice-tts.sh").read_text(encoding="utf-8")
    assert script.index("BUILD_JOBS=") < script.index('source "$REPO_ROOT/scripts/lib/omnivoice-vulkan.sh"')
