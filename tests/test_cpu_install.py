"""The CPU install must omit GPU wheels, keep CUDA available, and skip cuDNN."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from packaging.markers import default_environment
from packaging.requirements import Requirement
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _frozen_requirements(group, tmp_path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv required to validate its frozen dependency selection")
    args = [uv, "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project"]
    if group == "cpu":
        args += ["--no-group", "cuda", "--group", "cpu"]
    result = subprocess.run(
        args, cwd=ROOT, text=True, capture_output=True, check=True, timeout=30,
        env={**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv"), "UV_OFFLINE": "1"},
    )
    return [
        Requirement(line) for line in result.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "-"))
    ]


@pytest.mark.parametrize("python_version", ["3.11", "3.12"])
@pytest.mark.parametrize("group", ["cpu", "cuda"])
def test_frozen_torch_graph_on_supported_platforms(group, python_version, tmp_path):
    requirements = _frozen_requirements(group, tmp_path)
    for platform, machine in [("linux", "x86_64"), ("win32", "AMD64"), ("darwin", "arm64"), ("linux", "aarch64")]:
        env = {**default_environment(), "sys_platform": platform, "platform_machine": machine,
               "python_full_version": f"{python_version}.1", "python_version": python_version}
        selected = {req.name: str(req.specifier) for req in requirements
                    if req.marker is None or req.marker.evaluate(env)}
        native = platform == "darwin" or machine == "aarch64"
        suffix = "" if native else "+cpu" if group == "cpu" else "+cu128"
        assert selected["torch"] == f"==2.10.0{suffix}"
        assert selected["torchaudio"] == f"==2.10.0{suffix}"
        assert selected["torchvision"] == f"==0.25.0{suffix}"
        gpu_libs = {name for name in selected if name == "triton" or
                    (name.startswith("nvidia-") and "-cu" in name)}
        if group == "cpu" or native:
            assert not gpu_libs, gpu_libs
        elif platform == "linux":
            assert "nvidia-cudnn-cu12" in gpu_libs
            assert "triton" in gpu_libs


@pytest.mark.parametrize("python_version", ["3.11", "3.12", "3.13", "3.14"])
@pytest.mark.parametrize("group", ["cpu", "cuda"])
def test_frozen_numpy_preserves_existing_platform_versions(group, python_version, tmp_path):
    requirements = [req for req in _frozen_requirements(group, tmp_path) if req.name == "numpy"]
    for platform, machine in [("linux", "x86_64"), ("win32", "AMD64"), ("darwin", "arm64"), ("linux", "aarch64")]:
        env = {**default_environment(), "sys_platform": platform, "platform_machine": machine,
               "python_full_version": f"{python_version}.1", "python_version": python_version}
        selected = [str(req.specifier) for req in requirements
                    if req.marker is None or req.marker.evaluate(env)]
        expected = "2.0.2" if python_version == "3.12" and platform in {"linux", "win32"} else "2.2.6"
        assert selected == [f"=={expected}"]


def _import_setup_script():
    spec = importlib.util.spec_from_file_location("_setup_cudnn_probe", ROOT / "scripts" / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("probe", ["cpu", "cuda", "rocm", "failed", "timeout"])
def test_cudnn_compat_installs_only_after_positive_cuda_probe(probe, monkeypatch, tmp_path):
    setup = _import_setup_script()
    monkeypatch.setattr(setup.sys, "platform", "linux")
    monkeypatch.setattr(setup, "_ensure_vcredist_windows", lambda *a, **k: None)
    monkeypatch.setattr(setup, "_ensure_rocm_torch", lambda *a, **k: None)
    monkeypatch.setattr(setup, "_find_compat_dir", lambda *a, **k: str(tmp_path / "compat"))
    monkeypatch.setattr(setup, "_count_cudnn8_libs", lambda *a, **k: 7)

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: probe != "cpu"),
        version=SimpleNamespace(hip="6.2.41133" if probe == "rocm" else None),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    issued = []

    def fake_run(cmd, *args, **kwargs):
        cmd = list(cmd)
        issued.append(cmd)
        if cmd and cmd[0] == setup.sys.executable:
            if probe == "timeout":
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 30))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exec(cmd[2], {"__name__": "__probe__"})
            code = 1 if probe == "failed" else 0
            return subprocess.CompletedProcess(cmd, code, stdout=out.getvalue(), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(setup.subprocess, "run", fake_run)

    setup.main()

    uv_installs = [cmd for cmd in issued if cmd[:3] == ["uv", "pip", "install"]]
    assert len(uv_installs) == (1 if probe == "cuda" else 0), issued
