"""The GGUF runtime must run on every x86-64 CPU (#2780).

A plain ggml configure compiles for the build host (GGML_NATIVE=ON), so a
runtime built on an AVX-512 CI runner dies with an illegal instruction on an
AVX2-only CPU. Every x86-64 CPU build uses runtime-dispatched CPU variants
instead. If an incompatible runtime is still on disk, the engine reports a
typed, actionable error and stays unavailable until the file changes.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from gguf_cache_fixtures import gguf_binary, gguf_cache, gguf_engine

__all__ = ["gguf_binary", "gguf_cache", "gguf_engine"]

REPO = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"
PORTABLE = ("-DGGML_NATIVE=OFF", "-DGGML_CPU_ALL_VARIANTS=ON", "-DGGML_BACKEND_DL=ON",
            "-DBUILD_SHARED_LIBS=ON")


def _bash_path(path: Path) -> str:
    path = path.resolve()
    if os.name == "nt":
        if len(path.drive) != 2 or path.drive[1] != ":":
            raise ValueError("Mock build paths must use a local Windows drive")
        return f"/{path.drive[0].lower()}{path.as_posix()[2:]}"
    return str(path)


_OWNED_TOOL_PREFLIGHT = """
set -euo pipefail
owned="$1"
export PATH="$2"
shift 2
hash -r
for tool in git cmake; do
    resolved="$(command -v "$tool")" || resolved=""
    expected="$owned/$tool"
    if [[ "$resolved" != "$expected" || ! -f "$expected" || ! -x "$expected" ]]; then
        printf 'Refusing unverified mock tool: %s (%s)\\n' "$tool" "$resolved" >&2
        exit 97
    fi
done
exec "$BASH" --noprofile --norc "$@"
"""


@pytest.fixture
def mock_build(tmp_path):
    if not BASH or not Path(BASH).is_file():
        pytest.skip("POSIX bash required for the build script")
    scripts = tmp_path / "scripts"
    shutil.copytree(REPO / "scripts/lib", scripts / "lib")
    shutil.copy2(REPO / "scripts/build-omnivoice-tts.sh", scripts)
    for script in scripts.rglob("*.sh"):
        script.write_text(script.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    tools = {
        # Clone target is the last argument; buildcpu.sh would be a native build.
        "git": 'if [[ "$1" == clone ]]; then for d in "$@"; do :; done; mkdir -p "$d";'
               ' printf "#!/bin/sh\\necho native >> \\"$CMAKE_LOG\\"\\nexit 1\\n" > "$d/buildcpu.sh";'
               ' chmod +x "$d/buildcpu.sh"; fi',
        "cmake": 'printf "%s\\n" "$*" >> "$CMAKE_LOG"\n'
                 'if [[ "$1" == --build ]]; then mkdir -p build/Release;'
                 ' printf rt > build/omnivoice-tts; printf rt > build/Release/omnivoice-tts.exe;'
                 ' printf lib > build/libggml-cpu-haswell.so; fi',
    }
    for name, body in tools.items():
        (fake / name).write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            f'printf "%s\\n" {name} >> "$TOOL_LOG"\n' + body + "\n",
            encoding="utf-8", newline="\n",
        )
        (fake / name).chmod(0o755)
    log = tmp_path / "cmake.log"
    msvc = tmp_path / "msvc"
    msvc.mkdir()
    for dll in ("msvcp140.dll", "vcruntime140.dll"):
        (msvc / dll).write_text("crt")
    tool_log = tmp_path / "tools.log"
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"BASH_ENV", "ENV"} and not key.startswith("BASH_FUNC_")
    }
    env.update(
        PATH=f"{_bash_path(fake)}:/usr/bin:/bin", CMAKE_LOG=_bash_path(log),
        TOOL_LOG=_bash_path(tool_log), TMPDIR=_bash_path(tmp_path),
        OMNIVOICE_MSVC_RUNTIME_DIR=_bash_path(msvc),
    )
    env.pop("VULKAN_SDK", None)
    return SimpleNamespace(
        root=tmp_path, scripts=scripts, fake=fake, log=log, tool_log=tool_log, env=env,
    )


def _run_mock_build(build, platform):
    return subprocess.run(
        [BASH, "--noprofile", "--norc", "-c", _OWNED_TOOL_PREFLIGHT, "mock-build",
         _bash_path(build.fake), build.env["PATH"],
         _bash_path(build.scripts / "build-omnivoice-tts.sh"),
         "--platform", platform,
         "--commit-sha", "a" * 40],
        env=build.env, cwd=build.root, capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False,
    )


@pytest.mark.parametrize("platform", ["linux-x86_64", "windows-x86_64", "darwin-x86_64"])
def test_x86_cpu_build_is_portable(mock_build, platform):
    result = _run_mock_build(mock_build, platform)
    assert result.returncode == 0, result.stdout + result.stderr
    log = mock_build.log.read_text(encoding="utf-8")
    configure = [line for line in log.splitlines() if line.startswith("-B build")]
    assert configure, log
    assert "native" not in log
    assert set(mock_build.tool_log.read_text(encoding="utf-8").splitlines()) == {"git", "cmake"}
    for line in configure:
        for flag in PORTABLE:
            assert flag in line, line


@pytest.mark.parametrize("fault", ["wrong-path", "missing-git", "missing-cmake"])
def test_mock_build_rejects_unverified_tools(mock_build, fault):
    """Unexpected tools are inert even if the fail-closed preflight regresses."""
    decoys = mock_build.root / "unexpected-bin"
    decoys.mkdir()
    for name in ("git", "cmake"):
        tool = decoys / name
        tool.write_text(
            f'#!/usr/bin/env bash\nprintf "%s\\n" unexpected-{name} >> "$TOOL_LOG"\nexit 98\n',
            encoding="utf-8", newline="\n",
        )
        tool.chmod(0o755)
    if fault == "wrong-path":
        mock_build.env["PATH"] = f"{_bash_path(decoys)}:/usr/bin:/bin"
    else:
        (mock_build.fake / fault.removeprefix("missing-")).unlink()
        mock_build.env["PATH"] = f"{_bash_path(mock_build.fake)}:{_bash_path(decoys)}:/usr/bin:/bin"
    result = _run_mock_build(mock_build, "linux-x86_64")
    assert result.returncode == 97, result.stdout + result.stderr
    assert "Refusing unverified mock tool:" in result.stderr
    assert not mock_build.tool_log.exists()
    assert not mock_build.log.exists()
    assert not (mock_build.root / "bin").exists()


@pytest.mark.parametrize("code", [-4, 132, 0xC000001D, -1073741795])
def test_illegal_instruction_is_typed_and_sticky(gguf_binary, gguf_engine, monkeypatch, code):
    from services.binary_preflight import InvalidBinaryError

    monkeypatch.setattr(gguf_engine, "_CPU_INCOMPATIBLE", {})
    monkeypatch.setattr("services.binary_preflight.validate_executable", lambda *a, **k: None)
    monkeypatch.setattr(
        gguf_engine.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=code, stdout="", stderr=""),
    )
    assert gguf_engine.OmniVoiceGGUFBackend.is_available()[0] is True
    with pytest.raises(InvalidBinaryError, match="illegal instruction"):
        gguf_engine.OmniVoiceGGUFBackend()._run_subprocess([str(gguf_binary)], stdin_text="hi")
    ok, reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is False and "illegal instruction" in reason

    # A rebuilt/reinstalled runtime is checked afresh.
    gguf_binary.write_bytes(b"\x7fELF-rebuilt-portable")
    assert gguf_engine.OmniVoiceGGUFBackend.is_available()[0] is True


def test_probe_reports_illegal_instruction(gguf_binary, gguf_engine, monkeypatch):
    from services.binary_preflight import InvalidBinaryError

    monkeypatch.setattr(gguf_engine, "_CPU_INCOMPATIBLE", {})
    monkeypatch.setattr(
        gguf_engine.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=-4, stdout=b"", stderr=b""),
    )
    with pytest.raises(InvalidBinaryError, match="illegal instruction"):
        gguf_engine.OmniVoiceGGUFBackend.probe_load(timeout=1)


def test_ordinary_failure_is_not_misreported(gguf_binary, gguf_engine, monkeypatch):
    monkeypatch.setattr(gguf_engine, "_CPU_INCOMPATIBLE", {})
    monkeypatch.setattr("services.binary_preflight.validate_executable", lambda *a, **k: None)
    monkeypatch.setattr(
        gguf_engine.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="bad prompt"),
    )
    with pytest.raises(RuntimeError, match="exited 1"):
        gguf_engine.OmniVoiceGGUFBackend()._run_subprocess([str(gguf_binary)], stdin_text="hi")
    assert gguf_engine.OmniVoiceGGUFBackend.is_available()[0] is True
