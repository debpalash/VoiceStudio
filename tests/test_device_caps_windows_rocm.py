"""Windows HIP diagnostics must not infer DLL availability from PATH (#3045)."""
from __future__ import annotations

import importlib
import types

import pytest


def _notes(monkeypatch, *, override=None):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delenv("HIP_PATH", raising=False)
    monkeypatch.delenv("HSA_OVERRIDE_GFX_VERSION", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    if override:
        monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", override)
    torch = types.SimpleNamespace(
        version=types.SimpleNamespace(hip="7.2.1", cuda=None),
        cuda=types.SimpleNamespace(is_available=lambda: False),
    )
    return importlib.import_module("core.device_caps").why_no_gpu(torch)


def test_windows_hip_without_sdk_path_does_not_claim_dll_missing(monkeypatch):
    # Official wheels may carry their own runtime; PATH is not the DLL loader.
    notes = _notes(monkeypatch)
    assert len(notes) == 1
    assert "ROCm 7.2.1 is installed on Windows but no GPU was enumerated" in notes[0]
    assert "supported GPU" in notes[0]
    assert "amdhip64.dll was not found" not in notes[0]
    assert "device nodes" not in notes[0]


def test_windows_override_takes_precedence_over_sdk_path(monkeypatch):
    notes = _notes(monkeypatch, override="11.0.0")
    assert len(notes) == 1
    assert "HSA_OVERRIDE_GFX_VERSION=11.0.0" in notes[0]
    assert "device nodes" not in notes[0]


@pytest.mark.parametrize("hip", ['hip = "7.2.1"', 'hip: str | None = "7.2.1"'])
def test_windows_only_venv_layout_detects_hip(tmp_path, hip):
    site = tmp_path / "Lib" / "site-packages" / "torch"
    site.mkdir(parents=True)
    (site / "version.py").write_text(hip, encoding="utf-8")
    si = importlib.import_module("services.sidecar_install")
    assert si.venv_torch_hip(tmp_path) is True


def test_windows_only_venv_cpu_build_stays_cpu(tmp_path):
    site = tmp_path / "Lib" / "site-packages" / "torch"
    site.mkdir(parents=True)
    (site / "version.py").write_text("hip = None\n", encoding="utf-8")
    si = importlib.import_module("services.sidecar_install")
    assert si.venv_torch_hip(tmp_path) is False
