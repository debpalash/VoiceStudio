"""ROCm source setup must check VC++ locally and require manual installation."""

import ctypes
import importlib.util
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def load_setup():
    spec = importlib.util.spec_from_file_location(
        "setup_rocm_prerequisite", Path(__file__).parents[1] / "scripts/setup.py"
    )
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    return setup


@pytest.mark.parametrize("args", [[], ["--windows-prerequisites-only"]])
def test_missing_rocm_vcredist_stops_both_paths_without_download_or_process(monkeypatch, args):
    setup = load_setup()
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", " ROCm ")
    monkeypatch.setattr(ctypes, "WinDLL", Mock(side_effect=OSError("not installed")), raising=False)
    download = Mock(side_effect=AssertionError("unapproved prerequisite download"))
    process = Mock(side_effect=AssertionError("process before prerequisite"))
    monkeypatch.setattr(urllib.request, "urlretrieve", download)
    monkeypatch.setattr(setup.os, "remove", Mock())
    monkeypatch.setattr(setup.subprocess, "run", process)
    monkeypatch.setattr(setup.subprocess, "check_call", process)
    with pytest.raises(RuntimeError, match="manually") as error:
        setup.main(args)
    assert "https://aka.ms/vs/17/release/vc_redist.x64.exe" in str(error.value)
    assert "bun run setup:api" in str(error.value)
    download.assert_not_called()
    process.assert_not_called()


@pytest.mark.parametrize("args", [[], ["--windows-prerequisites-only"]])
def test_present_rocm_vcredist_continues_without_download(monkeypatch, args):
    setup = load_setup()
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=object()), raising=False)
    monkeypatch.setattr(urllib.request, "urlretrieve", lambda *a: pytest.fail("unexpected download"))
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **k: pytest.fail("unexpected subprocess"))
    install = Mock()
    monkeypatch.setattr(setup.subprocess, "check_call", install)
    setup.main(args)
    assert install.call_count == (0 if args else 1)
    if not args:
        assert install.call_args.args[0][1:3] == ["-m", "backend.core.windows_rocm.install"]


def test_default_windows_prerequisite_auto_install_keeps_preexisting_behavior(monkeypatch):
    setup = load_setup()
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.delenv("OMNIVOICE_TORCH_VARIANT", raising=False)
    monkeypatch.setattr(ctypes, "WinDLL", Mock(side_effect=[OSError("missing"), object()]), raising=False)
    download = Mock()
    process = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(urllib.request, "urlretrieve", download)
    monkeypatch.setattr(setup.subprocess, "run", process)
    monkeypatch.setattr(setup.os, "remove", Mock())
    setup.main(["--windows-prerequisites-only"])
    assert download.call_args.args[0] == "https://aka.ms/vs/17/release/vc_redist.x64.exe"
    assert process.call_args.args[0][1:] == ["/install", "/quiet", "/norestart"]
