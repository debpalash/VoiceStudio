"""The GGUF runtime must run on every x86-64 CPU (#2780).

A plain ggml configure compiles for the build host (GGML_NATIVE=ON), so a
runtime built on an AVX-512 CI runner dies with an illegal instruction on an
AVX2-only CPU. Every x86-64 CPU build uses runtime-dispatched CPU variants
instead. If an incompatible runtime is still on disk, the engine reports a
typed, actionable error and stays unavailable until the file changes.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from build_script_stubs import run_build
from gguf_cache_fixtures import gguf_binary, gguf_cache, gguf_engine

__all__ = ["gguf_binary", "gguf_cache", "gguf_engine"]

PORTABLE = ("-DGGML_NATIVE=OFF", "-DGGML_CPU_ALL_VARIANTS=ON", "-DGGML_BACKEND_DL=ON",
            "-DBUILD_SHARED_LIBS=ON")


@pytest.mark.parametrize("platform", ["linux-x86_64", "windows-x86_64", "darwin-x86_64"])
def test_x86_cpu_build_is_portable(tmp_path, platform):
    tools = {
        # Clone target is the last argument; buildcpu.sh would be a native build.
        "git": 'if [[ "$1" == clone ]]; then for d in "$@"; do :; done; mkdir -p "$d";'
               ' printf "#!/bin/sh\\necho native >> \\"$CMAKE_LOG\\"\\nexit 1\\n" > "$d/buildcpu.sh";'
               ' chmod +x "$d/buildcpu.sh"; fi',
        "cmake": 'printf "%s\\n" "$*" >> "$CMAKE_LOG"\n'
                 'if [[ "$1" == --build ]]; then mkdir -p build/Release;'
                 ' printf rt > build/omnivoice-tts; printf rt > build/Release/omnivoice-tts.exe;'
                 ' printf rt > build/tts-server; printf rt > build/Release/tts-server.exe;'
                 ' printf lib > build/libggml-cpu-haswell.so; fi',
    }
    log = tmp_path / "cmake.log"
    msvc = tmp_path / "msvc"
    msvc.mkdir()
    for dll in ("msvcp140.dll", "vcruntime140.dll"):
        (msvc / dll).write_text("crt")
    result = run_build(tmp_path, platform, tools=tools, env={
        "CMAKE_LOG": log.as_posix(), "OMNIVOICE_MSVC_RUNTIME_DIR": msvc.as_posix()})
    assert result.returncode == 0, result.stdout + result.stderr
    configure = [line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("-B build")]
    assert configure, log.read_text(encoding="utf-8")
    assert "native" not in log.read_text(encoding="utf-8")
    for line in configure:
        for flag in PORTABLE:
            assert flag in line, line


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
