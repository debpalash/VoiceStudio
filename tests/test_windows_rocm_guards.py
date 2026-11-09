"""Windows setup uses its native installer; Linux indexes never cross platforms."""
import importlib
import importlib.util
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]


def _load_setup_script():
    spec = importlib.util.spec_from_file_location("_setup_rocm_guard", _REPO / "scripts" / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_source_setup_never_installs_linux_rocm_wheels(platform, monkeypatch):
    setup = _load_setup_script()
    monkeypatch.setattr(setup.sys, "platform", platform)
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")

    rocm_calls = []
    commands = []
    monkeypatch.setattr(setup, "_ensure_vcredist_windows", lambda *a, **k: None)
    monkeypatch.setattr(setup, "_ensure_rocm_torch", lambda *a, **k: rocm_calls.append((a, k)))
    monkeypatch.setattr(setup, "_find_compat_dir", lambda *a, **k: None)
    monkeypatch.setattr(setup.subprocess, "check_call", lambda cmd, *a, **k: commands.append(list(cmd)))

    setup.main()

    assert rocm_calls == []
    if platform == "win32":
        assert [cmd[1:3] for cmd in commands] == [["-m", "backend.core.windows_rocm.install"]]
    else:
        assert commands == []


@pytest.mark.parametrize("family", ["cpu", "rocm"])
def test_windows_sidecar_cannot_select_linux_rocm_index(family, monkeypatch):
    si = importlib.import_module("services.sidecar_install")
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.setattr(si, "_host_family", lambda: family)
    assert si._rocm_index_url() is None


@pytest.mark.parametrize("state", ["amd_cuda_build:win32", "amd_cpu_build:win32"])
def test_windows_gpu_report_has_no_unavailable_automatic_recipe(state):
    report = importlib.import_module("core.gpu_report")
    assert report._OPTIONS[state] == ("vulkan_engines", "rocm_windows_manual")
