"""The subprocess OmniVoice engine's availability probe must not import the model.

`reconcile_active_profile()` asks every engine whether it is available during
backend startup. This engine runs the model in a sidecar, yet its probe
imported `omnivoice.models.omnivoice` (torch + transformers + the model
definition), about 6.6 s of every boot spent loading a module the parent never
uses.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _run(code: str) -> str:
    """Run in a fresh interpreter so earlier tests' imports can't mask the result."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=BACKEND, capture_output=True, text=True, check=True, timeout=120,
    ).stdout.strip()


def test_probe_reports_ready_without_importing_the_model():
    out = _run(
        """
        import sys
        from engines.omnivoice_subprocess import OmniVoiceSubprocessBackend
        ok, why = OmniVoiceSubprocessBackend.is_available()
        print(ok, why, "omnivoice.models.omnivoice" in sys.modules)
        """
    )
    assert out == "True ready False"


def test_probe_reports_missing_package():
    out = _run(
        """
        import importlib.util
        from engines.omnivoice_subprocess import OmniVoiceSubprocessBackend
        importlib.util.find_spec = lambda name, *a, **k: None
        ok, why = OmniVoiceSubprocessBackend.is_available()
        print(ok, why.startswith("omnivoice package missing"))
        """
    )
    assert out == "False True"
