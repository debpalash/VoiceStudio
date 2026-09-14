"""Tests for backend/services/ffmpeg_utils.py — Phase 1 Wave 3 (issue #76).

Covers :func:`resolve_ffprobe`'s env-first / PATH-fallback cascade and the
legacy-name alias (``FFPROBE_PATH`` still works, ``OMNIVOICE_FFPROBE_PATH``
takes precedence).
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip both ffprobe env vars before each test so we control the cascade."""
    monkeypatch.delenv("OMNIVOICE_FFPROBE_PATH", raising=False)
    monkeypatch.delenv("FFPROBE_PATH", raising=False)


def test_resolve_ffprobe_prefers_omnivoice_env_var(monkeypatch, tmp_path):
    """OMNIVOICE_FFPROBE_PATH set to a real file → that path wins."""
    from services import ffmpeg_utils

    fake = tmp_path / "ffprobe"
    fake.write_text("#!/bin/sh\necho 1\n")
    fake.chmod(0o755)
    monkeypatch.setenv("OMNIVOICE_FFPROBE_PATH", str(fake))

    assert ffmpeg_utils.resolve_ffprobe() == str(fake)


def test_resolve_ffprobe_falls_back_to_legacy_FFPROBE_PATH(monkeypatch, tmp_path):
    """OMNIVOICE_FFPROBE_PATH absent but legacy FFPROBE_PATH set → legacy used."""
    from services import ffmpeg_utils

    fake = tmp_path / "ffprobe-legacy"
    fake.write_text("#!/bin/sh\necho 1\n")
    fake.chmod(0o755)
    monkeypatch.setenv("FFPROBE_PATH", str(fake))

    assert ffmpeg_utils.resolve_ffprobe() == str(fake)


def test_resolve_ffprobe_omnivoice_path_takes_precedence_over_legacy(
    monkeypatch, tmp_path,
):
    """Both env vars set → OMNIVOICE_FFPROBE_PATH wins."""
    from services import ffmpeg_utils

    canonical = tmp_path / "ffprobe-canonical"
    legacy = tmp_path / "ffprobe-legacy"
    for f in (canonical, legacy):
        f.write_text("#!/bin/sh\necho 1\n")
        f.chmod(0o755)

    monkeypatch.setenv("OMNIVOICE_FFPROBE_PATH", str(canonical))
    monkeypatch.setenv("FFPROBE_PATH", str(legacy))

    assert ffmpeg_utils.resolve_ffprobe() == str(canonical)


def test_resolve_ffprobe_falls_back_to_PATH(monkeypatch, tmp_path):
    """No env var → shutil.which result is returned."""
    from services import ffmpeg_utils

    # Stub shutil.which inside the module so we do not depend on the host PATH.
    fake = "/fake/path/from/system/ffprobe"

    def _fake_which(name):
        if name == "ffprobe":
            return fake
        return None

    monkeypatch.setattr(ffmpeg_utils, "shutil", _ShutilStub(_fake_which))
    # The fake path isn't a real binary — bypass the runnability probe here
    # (rejection behavior has its own test below).
    monkeypatch.setattr(ffmpeg_utils, "_binary_runs", lambda _p: True)
    assert ffmpeg_utils.resolve_ffprobe() == fake


def test_resolve_ffprobe_rejects_non_runnable_candidate(monkeypatch, tmp_path):
    """#360/#361/#362: a candidate that exists but cannot execute (corrupt /
    wrong-arch binary → WinError 193 on Windows) is skipped and the cascade
    falls through to the next runnable source."""
    from services import ffmpeg_utils

    broken = tmp_path / "ffprobe-broken"
    broken.write_bytes(b"\x00\x01not-a-binary")
    broken.chmod(0o755)
    good = tmp_path / "ffprobe-good"
    good.write_text("#!/bin/sh\necho 1\n")
    good.chmod(0o755)

    monkeypatch.setenv("OMNIVOICE_FFPROBE_PATH", str(broken))
    monkeypatch.setattr(
        ffmpeg_utils, "shutil",
        _ShutilStub(lambda name: str(good) if name == "ffprobe" else None),
    )
    ffmpeg_utils._BINARY_OK.clear()

    assert ffmpeg_utils.resolve_ffprobe() == str(good)


def test_resolve_ffprobe_returns_None_when_nothing_resolves(monkeypatch):
    """No env, no PATH → returns None (no crash)."""
    from services import ffmpeg_utils

    monkeypatch.setattr(ffmpeg_utils, "shutil", _ShutilStub(lambda _name: None))
    assert ffmpeg_utils.resolve_ffprobe() is None


def test_resolve_ffprobe_env_var_with_command_name_resolves_via_which(
    monkeypatch, tmp_path,
):
    """Legacy shape: env var set to a bare command name (not a path) — resolve
    via shutil.which so older Tauri shells that set FFPROBE_PATH=ffprobe still
    work."""
    from services import ffmpeg_utils

    fake = tmp_path / "ffprobe"
    fake.write_text("#!/bin/sh\necho 1\n")
    fake.chmod(0o755)

    def _fake_which(name):
        if name == "ffprobe":
            return str(fake)
        return None

    monkeypatch.setattr(ffmpeg_utils, "shutil", _ShutilStub(_fake_which))
    monkeypatch.setenv("OMNIVOICE_FFPROBE_PATH", "ffprobe")

    assert ffmpeg_utils.resolve_ffprobe() == str(fake)


@pytest.mark.parametrize("binary_name", ["ffmpeg", "ffmpeg.exe"])
def test_find_ffprobe_preserves_ffmpeg_parent_directory(monkeypatch, tmp_path, binary_name):
    from services import ffmpeg_utils

    bindir = tmp_path / "ffmpeg" / "bin"
    bindir.mkdir(parents=True)
    ffmpeg = bindir / binary_name
    ffprobe = bindir / binary_name.replace("ffmpeg", "ffprobe")
    ffprobe.touch()
    monkeypatch.setattr(ffmpeg_utils, "resolve_ffprobe", lambda: None)
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: str(ffmpeg))

    assert ffmpeg_utils.find_ffprobe() == str(ffprobe)


class _ShutilStub:
    """A tiny shim that mimics the parts of shutil ffmpeg_utils touches.

    monkeypatching the whole module avoids pulling in real PATH lookups while
    keeping the existing `import shutil` call sites untouched.
    """

    def __init__(self, which_impl):
        self._which = which_impl

    def which(self, name):
        return self._which(name)


# ── windows_tool_candidates: no hardcoded system-drive assumptions ─────────


def test_windows_tool_candidates_empty_off_windows(monkeypatch):
    from services import ffmpeg_utils

    monkeypatch.setattr(ffmpeg_utils.os, "name", "posix")
    assert ffmpeg_utils.windows_tool_candidates("ffmpeg") == []


def test_windows_tool_candidates_derive_from_environment(monkeypatch):
    """Program Files / the system drive can live on a non-C: drive — the
    probe list must follow the environment instead of assuming C: (the
    cross-drive install class)."""
    from services import ffmpeg_utils

    monkeypatch.setattr(ffmpeg_utils.os, "name", "nt")
    monkeypatch.setenv("SystemDrive", "E:")
    monkeypatch.setenv("ProgramFiles", "E:\\Program Files")
    monkeypatch.setenv("ProgramW6432", "E:\\Program Files")

    got = ffmpeg_utils.windows_tool_candidates("ffprobe")
    assert "E:\\ffmpeg\\bin\\ffprobe.exe" in got            # relocated system drive
    assert any(p.startswith("E:\\Program Files") for p in got)  # env-derived Program Files
    assert "C:\\ffmpeg\\bin\\ffprobe.exe" in got            # conventional fallbacks kept
    assert "D:\\ffmpeg\\bin\\ffprobe.exe" in got
