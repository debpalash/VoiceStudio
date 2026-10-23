"""#2780 — the OmniVoice GGUF engine was unusable from every installer.

Two failures stacked on Windows v0.5.6:

1. No installer packaged ``bin/``: the release workflow never built the
   runtime and electron-builder never copied it, so the engine always
   reported its binary missing.
2. The CI-built runtime itself was compiled with ggml's default
   ``GGML_NATIVE=ON`` on an AVX-512 runner. On AVX2-only CPUs (most AMD
   Ryzen, recent Intel consumer chips) every spawn died with an illegal
   instruction, and ``--help`` still passed, so nothing reported it.
"""

import importlib
import os
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Resolved through PATH: on Windows a bare "bash" in subprocess finds the WSL
# launcher in System32 before Git Bash.
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(
    BASH is None or Path(BASH).parent.name.lower() == "system32",
    reason="needs a POSIX bash (Git Bash on Windows)",
)

PORTABLE_FLAGS = (
    "-DBUILD_SHARED_LIBS=ON",
    "-DGGML_NATIVE=OFF",
    "-DGGML_BACKEND_DL=ON",
    "-DGGML_CPU_ALL_VARIANTS=ON",
)


@pytest.fixture()
def gguf(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "backend"))
    module = importlib.import_module("engines.omnivoice_gguf.backend")
    monkeypatch.setattr(module, "_cpu_incompatible_binary", None)
    return module


# ── the build script produces binaries that run on every x86-64 CPU ─────────


def _write_fake_tool(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
    path.chmod(0o755)


def _configure_calls(tmp_path: Path, platform: str) -> list[str]:
    """Run the real build script against fake git/cmake; return configure argv."""
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts/build-omnivoice-tts.sh", checkout / "scripts")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    cmake_log = tmp_path / "cmake.log"
    executable = (
        "build/Release/omnivoice-tts.exe"
        if platform.startswith("windows")
        else "build/omnivoice-tts"
    )
    _write_fake_tool(
        fake_bin / "git",
        """
if [[ "${1:-}" == "clone" ]]; then
    for destination in "$@"; do :; done
    mkdir -p "$destination"
fi
""",
    )
    _write_fake_tool(
        fake_bin / "cmake",
        """
printf '%s\\n' "$*" >> "$FAKE_CMAKE_LOG"
if [[ "${1:-}" == "--build" ]]; then
    mkdir -p "$(dirname EXE)"
    printf 'MZfake' > EXE
fi
""".replace("EXE", executable),
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(fake_bin) + os.pathsep + env["PATH"],
            "FAKE_CMAKE_LOG": str(cmake_log),
        }
    )
    result = subprocess.run(
        [
            BASH,
            str(checkout / "scripts/build-omnivoice-tts.sh"),
            "--platform",
            platform,
            "--commit-sha",
            "a" * 40,
        ],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "bin" / Path(executable).name.replace(
        "omnivoice-tts", f"omnivoice-tts-{platform}"
    )).is_file()
    calls = cmake_log.read_text().splitlines()
    return [call for call in calls if not call.startswith("--build")]


@needs_bash
@pytest.mark.parametrize("platform", ["windows-x86_64", "linux-x86_64", "darwin-x86_64"])
def test_x86_builds_load_every_cpu_variant_at_runtime(tmp_path, platform):
    configure = _configure_calls(tmp_path, platform)
    assert configure, f"{platform} never configured CMake"
    for call in configure:
        for flag in PORTABLE_FLAGS:
            assert flag in call, f"{platform} configure lacks {flag}: {call}"


@needs_bash
def test_linux_x86_build_keeps_blas_as_an_optional_module(tmp_path):
    # Upstream buildcpu.sh compiles for the build host, so the script must
    # not delegate to it; OpenBLAS stays enabled as a loadable module.
    (configure,) = _configure_calls(tmp_path, "linux-x86_64")
    assert "-DGGML_BLAS=ON" in configure
    script = (REPO / "scripts/build-omnivoice-tts.sh").read_text()
    assert "./buildcpu.sh" not in script


# ── an illegal-instruction exit becomes the typed, actionable error ─────────


ILLEGAL_INSTRUCTION_EXITS = [0xC000001D, 0xC000001D - (1 << 32), -int(signal.SIGILL)]


def _fake_runtime(gguf, monkeypatch, tmp_path):
    binary = tmp_path / "omnivoice-tts-windows-x86_64.exe"
    binary.write_bytes(b"MZ" + b"\0" * 64)
    monkeypatch.setattr(gguf, "_binary_path", lambda slug=None: binary)
    monkeypatch.setattr(gguf, "_load_checksum_manifest", lambda: {})
    monkeypatch.setattr(gguf, "_is_macos_quarantined", lambda p: False)
    return binary


def _ready_backend(gguf, monkeypatch, tmp_path):
    backend = gguf._make_backend_class()()
    base = tmp_path / "omnivoice-base-Q4_K_M.gguf"
    tokenizer = tmp_path / "omnivoice-tokenizer-Q4_K_M.gguf"
    base.write_bytes(b"x")
    tokenizer.write_bytes(b"x")
    monkeypatch.setattr(
        backend,
        "_resolve_quant_paths",
        lambda: (base, tokenizer, {"base": base.name, "tokenizer": tokenizer.name}),
    )
    return backend


@pytest.mark.parametrize("returncode", ILLEGAL_INSTRUCTION_EXITS)
def test_generate_illegal_instruction_names_the_cpu_and_the_fix(
    gguf, monkeypatch, tmp_path, returncode
):
    from services.binary_preflight import InvalidBinaryError

    binary = _fake_runtime(gguf, monkeypatch, tmp_path)
    backend = _ready_backend(gguf, monkeypatch, tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, returncode, "", ""),
    )

    with pytest.raises(InvalidBinaryError) as exc_info:
        backend.generate("hola")
    message = str(exc_info.value)
    assert "illegal-instruction" in message
    assert "build-omnivoice-tts.sh" in message

    # The Model Catalogue now says why, instead of offering a crashing engine.
    ok, reason = gguf._make_backend_class().is_available()
    assert not ok
    assert "cannot run it" in reason

    # A rebuilt or reinstalled runtime is tried again without a restart.
    binary.write_bytes(b"MZ" + b"\1" * 128)
    assert gguf._make_backend_class().is_available() == (True, "ready")


def test_ordinary_failures_stay_generic_and_do_not_disable_the_engine(
    gguf, monkeypatch, tmp_path
):
    from services.binary_preflight import InvalidBinaryError

    _fake_runtime(gguf, monkeypatch, tmp_path)
    backend = _ready_backend(gguf, monkeypatch, tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "model load failed"),
    )

    with pytest.raises(RuntimeError) as exc_info:
        backend.generate("hola")
    assert not isinstance(exc_info.value, InvalidBinaryError)
    assert gguf._make_backend_class().is_available() == (True, "ready")


def test_probe_load_illegal_instruction_falls_back_to_in_process(
    gguf, monkeypatch, tmp_path
):
    _fake_runtime(gguf, monkeypatch, tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0xC000001D, b"", b""),
    )
    assert gguf.select_default_engine() == "omnivoice"


# ── release packages build the runtime and must ship it ─────────────────────


def test_release_builds_the_runtime_before_packaging_requires_it():
    import yaml

    workflow = yaml.load(
        (REPO / ".github/workflows/electron-release.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    package = workflow["jobs"]["package"]
    slugs = {
        entry["target"]: entry.get("gguf_platform", "")
        for entry in package["strategy"]["matrix"]["include"]
    }
    assert slugs["x86_64-pc-windows-msvc"] == "windows-x86_64"
    assert slugs["x86_64-unknown-linux-gnu"] == "linux-x86_64"
    assert slugs["aarch64-apple-darwin"] == "darwin-arm64"

    names = [step.get("name") for step in package["steps"]]
    build_index = names.index("Build the OmniVoice GGUF runtime")
    assert build_index < names.index("Validate and build Electron")
    build = package["steps"][build_index]
    assert "scripts/build-omnivoice-tts.sh" in build["run"]
    assert "VOICESTUDIO_REQUIRE_GGUF_RUNTIME=1" in build["run"]

    contract = (REPO / "electron/tests/packaging-contract.mjs").read_text()
    assert "VOICESTUDIO_REQUIRE_GGUF_RUNTIME" in contract
