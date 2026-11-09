"""Direct Python post-install uses the shared Windows installer before imports."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


def load_setup():
    spec = importlib.util.spec_from_file_location("windows_rocm_setup", Path(__file__).parents[1] / "scripts/setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_direct_windows_opt_in_uses_shared_installer_before_torch(monkeypatch):
    setup = load_setup()
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", " ROCm ")
    monkeypatch.setattr(setup, "_ensure_vcredist_windows", Mock())
    native_probe = Mock(side_effect=AssertionError("native import before overlay"))
    monkeypatch.setattr(setup, "_installed_torch_is_rocm", native_probe)
    monkeypatch.setattr(setup, "_find_compat_dir", Mock(side_effect=AssertionError("CUDA path")))
    install = Mock()
    monkeypatch.setattr(setup.subprocess, "check_call", install)
    setup.main()
    command = install.call_args.args[0]
    assert command == [setup.sys.executable, "-m", "backend.core.windows_rocm.install", "--uv", "uv"]
    assert Path(install.call_args.kwargs["cwd"]) == Path(__file__).parents[1]
    native_probe.assert_not_called()


def test_direct_windows_opt_in_failure_is_fatal(monkeypatch):
    setup = load_setup()
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.setattr(setup, "_ensure_vcredist_windows", Mock())
    monkeypatch.setattr(setup.subprocess, "check_call", Mock(side_effect=subprocess.CalledProcessError(1, "installer")))
    with pytest.raises(subprocess.CalledProcessError):
        setup.main()


def test_windows_prerequisites_mode_never_imports_or_installs_torch(monkeypatch):
    setup = load_setup()
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    prerequisite = Mock()
    monkeypatch.setattr(setup, "_ensure_vcredist_windows", prerequisite)
    monkeypatch.setattr(setup.subprocess, "check_call", Mock(side_effect=AssertionError("overlay too early")))
    setup.main(["--windows-prerequisites-only"])
    prerequisite.assert_called_once_with()


@pytest.mark.skipif(sys.platform != "win32" or sys.version_info[:2] == (3, 12), reason="needs non-cp312 Windows interpreter")
def test_electron_reuse_probe_rejects_wrong_abi_with_python_optimization():
    root = Path(__file__).parents[1]
    module = (root / "electron/src/main/runtime-windows-rocm.ts").as_uri()
    script = f"const m = await import({json.dumps(module)}); console.log(m.WINDOWS_ROCM_ABI_PROBE);"
    code = subprocess.check_output(["node", "--input-type=module", "-e", script], text=True).strip()
    result = subprocess.run([sys.executable, "-O", "-c", code], cwd=root,
                            capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode != 0, "optimized Python must not bypass Windows ROCm ABI validation"
