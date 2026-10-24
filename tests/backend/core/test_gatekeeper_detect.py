"""Tests for backend/core/gatekeeper_detect.py (Phase 1 Wave 3, issue #54).

Covers :func:`is_app_quarantined`'s detection across platforms, missing
``xattr`` binary, timeouts, and the bundle-path walk. Also asserts the
:func:`quarantine_status` payload shape that the React ErrorBoundary
consumes via /system/quarantine-status.
"""
import subprocess
import sys

import pytest


@pytest.fixture
def detect():
    """Import the module fresh — it has no module-level state, but this
    keeps tests stable if any future refactor adds caching."""
    import importlib

    from core import gatekeeper_detect

    importlib.reload(gatekeeper_detect)
    return gatekeeper_detect


def test_returns_false_on_non_macos(monkeypatch, detect):
    """On any non-Darwin platform the function returns False without ever
    invoking xattr."""
    monkeypatch.setattr(sys, "platform", "linux")

    def _explode(*args, **kwargs):  # pragma: no cover — should never be called
        raise AssertionError("subprocess.run must not be called on non-darwin")

    monkeypatch.setattr(subprocess, "run", _explode)
    assert detect.is_app_quarantined() is False
    # Status payload also reflects it.
    status = detect.quarantine_status()
    assert status["quarantined"] is False
    assert status["error_class"] is None


def test_returns_true_when_xattr_reads_quarantine(monkeypatch, detect):
    """On Darwin with a quarantined bundle, xattr stdout contains
    quarantine metadata → returns True."""
    monkeypatch.setattr(sys, "platform", "darwin")

    class _Result:
        stdout = "0083;6450c4b8;Chrome;\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())
    # Provide an explicit bundle path so we do not depend on sys.executable.
    assert detect.is_app_quarantined("/Applications/Fake.app") is True


def test_returns_false_when_xattr_absent(monkeypatch, detect):
    """The attribute query returns nonzero when quarantine is absent."""
    monkeypatch.setattr(sys, "platform", "darwin")

    class _Result:
        stdout = ""
        stderr = ""
        returncode = 1

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())
    assert detect.is_app_quarantined("/Applications/Fake.app") is False


def test_returns_false_when_xattr_binary_missing(monkeypatch, detect):
    """FileNotFoundError (xattr not on PATH) → safe False, no crash."""
    monkeypatch.setattr(sys, "platform", "darwin")

    def _raise(*a, **kw):
        raise FileNotFoundError("xattr not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert detect.is_app_quarantined("/Applications/Fake.app") is False


def test_returns_false_on_timeout(monkeypatch, detect):
    """xattr timeout → safe False, log warning, do not propagate."""
    monkeypatch.setattr(sys, "platform", "darwin")

    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["xattr"], timeout=5)

    monkeypatch.setattr(subprocess, "run", _raise)
    assert detect.is_app_quarantined("/Applications/Fake.app") is False


def test_quarantine_status_shape(monkeypatch, detect):
    """quarantine_status() returns the keys the frontend ErrorBoundary
    expects: quarantined, bundle_path, error_class."""
    monkeypatch.setattr(sys, "platform", "darwin")

    class _Result:
        stdout = "com.apple.quarantine: 0083;6450c4b8;Chrome;"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())
    # Force the bundle resolver to return a known path so the assertion
    # below isn't platform-dependent.
    monkeypatch.setattr(detect, "_resolve_app_bundle_path", lambda: "/Applications/Fake.app")

    status = detect.quarantine_status()
    assert set(status.keys()) == {"quarantined", "bundle_path", "error_class"}
    assert status["quarantined"] is True
    assert status["bundle_path"] == "/Applications/Fake.app"
    assert status["error_class"] == detect.ERROR_CLASS == "GATEKEEPER_QUARANTINE"


def test_returns_false_when_not_inside_app_bundle(monkeypatch, detect):
    """Dev runs (sys.executable not inside a .app) → returns False without
    invoking xattr."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", "/usr/local/bin/python3.12")

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("subprocess.run must not be called for dev runs")

    monkeypatch.setattr(subprocess, "run", _explode)
    # Call without passing bundle_path so _resolve_app_bundle_path runs.
    assert detect.is_app_quarantined() is False


@pytest.mark.skipif(sys.platform != "darwin", reason="requires native macOS extended attributes")
def test_quarantine_probe_uses_exact_attribute_on_isolated_bundle(tmp_path, detect):
    import shutil

    if not shutil.which("xattr"):
        pytest.skip("xattr is unavailable")
    bundle = tmp_path / "Isolated.app"
    bundle.mkdir()
    subprocess.run(["xattr", "-w", "user.notes", "com.apple.quarantine", str(bundle)], check=True)
    assert detect.is_app_quarantined(str(bundle)) is False
    subprocess.run(["xattr", "-w", "com.apple.quarantine", "0083;fixture", str(bundle)], check=True)
    assert detect.is_app_quarantined(str(bundle)) is True
    subprocess.run(["xattr", "-d", "com.apple.quarantine", str(bundle)], check=True)
    assert detect.is_app_quarantined(str(bundle)) is False
