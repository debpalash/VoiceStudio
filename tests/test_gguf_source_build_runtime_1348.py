"""#1348 — a source-built GGUF runtime must actually run.

Two independent failures from the same report (LXC Debian, CPU-only,
built from source):

1. ``scripts/build-omnivoice-tts.sh`` copied only the executable out of the
   temp build tree; a dynamically-linked build (buildcpu.sh, or cmake with
   BLAS present) left its ``libggml*`` shared libraries behind for the EXIT
   trap to delete, so the shipped binary died on first spawn with exit 127
   — "libggml.so.0: cannot open shared object file".
2. ``_GENERATE_TIMEOUT_S`` was hardcoded to 120s, which reaped legitimate
   CPU-only generates mid-synthesis with no way to raise it.
"""

import ast
import importlib
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.markers import Marker, default_environment

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def gguf(monkeypatch):
    # Function-scoped runtime import: no module-level app imports that go
    # stale under sys.modules pollution from other tests.
    monkeypatch.syspath_prepend(str(REPO / "backend"))
    return importlib.import_module("engines.omnivoice_gguf.backend")


# ── the per-spawn timeout is env-tunable and CPU-realistic ──────────────────


def test_default_timeout_exceeds_the_pool_generate_budget(gguf, monkeypatch):
    # The pool guard (OMNIVOICE_GENERATE_TIMEOUT_S, 300s floor) must be the
    # deadline users actually hit — it classifies and explains. The inner
    # subprocess timeout only reaps a wedged C++ process, so it must sit
    # strictly above the pool floor or it fires first with a worse error.
    monkeypatch.delenv("OMNIVOICE_GGUF_GENERATE_TIMEOUT_S", raising=False)
    assert gguf._generate_timeout_s() > 300.0


def test_env_override_is_read_at_call_time(gguf, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_GGUF_GENERATE_TIMEOUT_S", "45.5")
    assert gguf._generate_timeout_s() == 45.5


@pytest.mark.parametrize("raw", ["unlimited", "inf", "-inf", "nan"])
def test_bad_env_values_fall_back_to_the_default(gguf, monkeypatch, raw):
    # inf would disarm the wedge guard entirely (subprocess.run never times
    # out); nan poisons the max() clamp. Both parse as float, so a plain
    # ValueError guard is not enough.
    monkeypatch.setenv("OMNIVOICE_GGUF_GENERATE_TIMEOUT_S", raw)
    assert gguf._generate_timeout_s() == 600.0


def test_tiny_values_are_floored_not_instant_kill(gguf, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_GGUF_GENERATE_TIMEOUT_S", "0")
    assert gguf._generate_timeout_s() == 1.0


def test_timeout_error_names_the_env_knob():
    # The user in #1348 had to read the source to find the constant; the
    # error itself must carry the escape hatch now.
    src = (REPO / "backend/engines/omnivoice_gguf/backend.py").read_text()
    timed_out = src[src.index("timed out after") :]
    assert "OMNIVOICE_GGUF_GENERATE_TIMEOUT_S" in timed_out[:300]


# ── spawns carry a loader path that can see bin/'s shared libs ──────────────


def test_spawn_env_puts_bin_on_the_loader_path(gguf, monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/elsewhere")
    monkeypatch.delenv("DYLD_FALLBACK_LIBRARY_PATH", raising=False)
    env = gguf._spawn_env()
    bin_dir = str(gguf._binary_path().parent)
    # Prepended, with the pre-existing path preserved after it.
    assert env["LD_LIBRARY_PATH"] == bin_dir + os.pathsep + "/opt/elsewhere"
    assert env["DYLD_FALLBACK_LIBRARY_PATH"] == bin_dir


def test_every_spawn_of_the_engine_binary_passes_the_loader_env():
    # Class rule: any subprocess.run that execs the GGUF binary must pass
    # env=_spawn_env(), or a dynamically-linked build fails exit 127 from
    # that call site. (The /usr/bin/xattr quarantine probe is the one
    # legitimate literal-argv exception.)
    src = (REPO / "backend/engines/omnivoice_gguf/backend.py").read_text()
    tree = ast.parse(src)
    spawns = 0
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ):
            continue
        first = node.args[0] if node.args else None
        if (
            isinstance(first, ast.List)
            and first.elts
            and isinstance(first.elts[0], ast.Constant)
        ):
            continue  # literal argv (xattr probe) — not the engine binary
        spawns += 1
        kw = {k.arg for k in node.keywords}
        assert "env" in kw, (
            f"subprocess.run at line {node.lineno} spawns the engine binary "
            f"without env=_spawn_env() — a dynamically-linked source build "
            f"dies with exit 127 there (#1348)"
        )
    assert spawns >= 2  # probe_load --help + _run_subprocess


# ── the build script ships the shared libs it links against ─────────────────


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Linux", "aarch64", "linux-aarch64"),
        ("Linux", "arm64", "linux-aarch64"),
        ("Linux", "x86_64", "linux-x86_64"),
        ("FreeBSD", "aarch64", "linux-x86_64"),
    ],
)
def test_platform_slug_maps_linux_arm64_to_aarch64_binary(
    gguf, monkeypatch, system, machine, expected
):
    """Asahi Apple Silicon — Linux/aarch64 hosts must resolve the
    linux-aarch64 binary, not silently fall into linux-x86_64 (which
    can never run on ARM)."""
    monkeypatch.setattr(gguf.platform, "system", lambda: system)
    monkeypatch.setattr(gguf.platform, "machine", lambda: machine)
    assert gguf._platform_slug() == expected


def _write_fake_tool(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
    path.chmod(0o755)


def _run_linux_arm_build(
    tmp_path: Path,
    *,
    prerequisites: bool,
    fail_configure: bool,
    fail_build: bool,
):
    """Execute the real build script against hermetic fake build tools."""
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(REPO / "scripts/build-omnivoice-tts.sh", scripts)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    cmake_log = tmp_path / "cmake.log"
    failed_once = tmp_path / "failed-once"
    _write_fake_tool(
        fake_bin / "git",
        """
if [[ "${1:-}" == "clone" ]]; then
    for destination in "$@"; do :; done
    mkdir -p "$destination"
fi
""",
    )
    _write_fake_tool(fake_bin / "glslc", "exit 0\n")
    _write_fake_tool(fake_bin / "c++", "exit " + ("0" if prerequisites else "1") + "\n")
    _write_fake_tool(
        fake_bin / "cmake",
        """
printf '%s\n' "$*" >> "$FAKE_CMAKE_LOG"
if [[ "${1:-}" == "--build" ]]; then
    if [[ "$FAKE_FAIL_BUILD" == "1" && ! -e "$FAKE_FAILED_ONCE" ]]; then
        touch "$FAKE_FAILED_ONCE"
        exit 1
    fi
    mkdir -p build
    printf '\177ELFfake' > build/omnivoice-tts
    chmod +x build/omnivoice-tts
else
    mkdir -p build
    if [[ "$FAKE_FAIL_CONFIGURE" == "1" && "$*" == *"-DGGML_VULKAN=ON"* ]]; then
        exit 1
    fi
fi
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": str(fake_bin) + os.pathsep + env["PATH"],
            "FAKE_CMAKE_LOG": str(cmake_log),
            "FAKE_FAIL_CONFIGURE": "1" if fail_configure else "0",
            "FAKE_FAIL_BUILD": "1" if fail_build else "0",
            "FAKE_FAILED_ONCE": str(failed_once),
        }
    )
    result = subprocess.run(
        [
            "bash",
            str(scripts / "build-omnivoice-tts.sh"),
            "--platform",
            "linux-aarch64",
            "--commit-sha",
            "a" * 40,
        ],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
    )
    log = cmake_log.read_text().splitlines() if cmake_log.exists() else []
    return result, log


@pytest.mark.parametrize(
    ("prerequisites", "fail_configure", "fail_build"),
    [
        (True, False, False),
        (False, False, False),
        (True, True, False),
        (True, False, True),
    ],
)
def test_linux_arm_build_falls_back_to_cpu(
    tmp_path, prerequisites, fail_configure, fail_build
):
    result, cmake_calls = _run_linux_arm_build(
        tmp_path,
        prerequisites=prerequisites,
        fail_configure=fail_configure,
        fail_build=fail_build,
    )

    assert result.returncode == 0, result.stderr
    configure_calls = [call for call in cmake_calls if not call.startswith("--build")]
    build_calls = [call for call in cmake_calls if call.startswith("--build")]
    if prerequisites and not (fail_configure or fail_build):
        assert any("-DGGML_VULKAN=ON" in call for call in configure_calls)
        assert "-DGGML_VULKAN=ON" in configure_calls[-1]
        assert len(build_calls) == 1
    elif prerequisites:
        assert any("-DGGML_VULKAN=ON" in call for call in configure_calls)
    else:
        assert all("-DGGML_VULKAN=ON" not in call for call in configure_calls)
        assert len(build_calls) == 1
    if fail_configure:
        assert len(build_calls) == 1
        assert "-DGGML_VULKAN=ON" not in configure_calls[-1]
    elif fail_build:
        assert len(build_calls) == 2
        assert "-DGGML_VULKAN=ON" not in configure_calls[-1]


def test_cuda_sources_exclude_both_linux_arm64_spellings():
    sources = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["uv"]["sources"]
    for package in ("torch", "torchaudio", "torchvision"):
        # Native PyPI and CPU sources can precede CUDA. Validate the named
        # index rather than depending on incidental source-array ordering.
        markers = [
            Marker(source["marker"])
            for source in sources[package]
            if source.get("index") == "pytorch-cuda"
        ]
        assert markers, f"{package} has no CUDA source"
        for machine in ("aarch64", "arm64"):
            environment = default_environment()
            environment.update({"sys_platform": "linux", "platform_machine": machine})
            assert not any(marker.evaluate(environment) for marker in markers), (
                f"{package} selected CUDA on {machine}"
            )
        for system in ("linux", "win32"):
            environment = default_environment()
            environment.update({"sys_platform": system, "platform_machine": "x86_64"})
            assert any(marker.evaluate(environment) for marker in markers), (
                f"{package} skipped CUDA on {system}/x86_64"
            )


def test_build_script_copies_shared_libs_on_every_platform_branch():
    script = (REPO / "scripts/build-omnivoice-tts.sh").read_text()
    assert "copy_shared_libs()" in script
    # Every copy of the executable is followed by the shared-lib copy —
    # the bug lived in ALL platform branches, not just Linux.
    build_case = script.rsplit('case "$PLATFORM" in', 1)[1].split("\nesac", 1)[0]
    branches = dict(
        re.findall(
            r"(?ms)^    ([a-z0-9_-]+)\)\n(.*?)(?=^    [a-z0-9_-]+\)\n|\Z)",
            build_case,
        )
    )
    for platform in (
        "linux-x86_64",
        "linux-aarch64",
        "windows-x86_64",
        "darwin-x86_64",
        "darwin-arm64",
    ):
        branch = branches[platform]
        copies = list(
            re.finditer(
                r'cp -v build/(?:Release/)?omnivoice-tts(?:\.exe)? "\$BIN_DIR',
                branch,
            )
        )
        shared_lib_copies = list(
            re.finditer(r"(?m)^\s*copy_shared_libs$", branch)
        )
        assert len(copies) == 1, f"{platform} must copy exactly one executable"
        assert len(shared_lib_copies) == 1, (
            f"{platform} must copy shared libraries exactly once"
        )
        assert shared_lib_copies[0].start() > copies[0].start(), (
            f"{platform} must copy shared libraries after its executable"
        )
    # The finder must cover all three platforms' shared-lib extensions.
    for pattern in ("libggml*.so*", "libggml*.dylib", "ggml*.dll"):
        assert pattern in script


def test_ci_artifact_upload_includes_the_shared_libs():
    # Greptile P1 on the fix itself: copy_shared_libs is useless if the
    # workflow's upload glob then drops the libs from the artifact — the
    # downloaded binary would be exactly as broken as before.
    wf = (REPO / ".github/workflows/build-omnivoice-tts.yml").read_text()
    assert "bin/libggml*" in wf
    assert "bin/ggml*.dll" in wf
