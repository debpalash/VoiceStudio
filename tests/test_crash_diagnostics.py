from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest


@pytest.fixture(autouse=True)
def crash_support(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_DISABLE_FAULTHANDLER", raising=False)
    return importlib.reload(importlib.import_module("core.crash_diagnostics"))


def test_handler_is_enabled_in_the_ordinary_case(crash_support, monkeypatch):
    calls = []
    monkeypatch.setattr(crash_support.faulthandler, "enable", lambda **options: calls.append(options))
    assert crash_support.enable_fault_handler() is True
    assert len(calls) == 1


@pytest.mark.parametrize("switch", ["1", " TRUE ", "yes", "On"])
def test_documented_opt_out_spellings_skip_activation(crash_support, monkeypatch, switch):
    monkeypatch.setenv("OMNIVOICE_DISABLE_FAULTHANDLER", switch)
    monkeypatch.setattr(
        crash_support.faulthandler,
        "enable",
        lambda **_options: pytest.fail("handler should remain disabled"),
    )
    assert crash_support.enable_fault_handler() is False


@pytest.mark.parametrize("setting", ["", "0", "false", "no", "off"])
def test_non_opt_out_values_do_not_disable_diagnostics(crash_support, monkeypatch, setting):
    observed = []
    monkeypatch.setenv("OMNIVOICE_DISABLE_FAULTHANDLER", setting)
    monkeypatch.setattr(crash_support.faulthandler, "enable", lambda **kw: observed.append(kw))
    assert crash_support.enable_fault_handler() is True
    assert observed


def test_detached_output_stream_is_safe(crash_support):
    class Detached:
        def fileno(self):
            raise OSError("stream has no descriptor")

    assert crash_support.enable_fault_handler(Detached()) is False


@pytest.mark.parametrize("activate", [True, False])
def test_fatal_child_frame_is_visible_only_when_armed(activate):
    program = f"""
import faulthandler
from core.crash_diagnostics import enable_fault_handler

def trigger_native_fault():
    faulthandler._sigsegv()

if {activate!r}:
    enable_fault_handler()
trigger_native_fault()
"""
    environment = os.environ.copy()
    environment.pop("OMNIVOICE_DISABLE_FAULTHANDLER", None)
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=os.path.join(os.path.dirname(__file__), "..", "backend"),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode != 0
    if activate:
        assert "trigger_native_fault" in completed.stderr
        assert "fatal" in completed.stderr.casefold()
    else:
        assert "trigger_native_fault" not in completed.stderr
