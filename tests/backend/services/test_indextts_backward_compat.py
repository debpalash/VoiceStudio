"""Tests for backend/engines/indextts/bootstrap.py — Plan 02-03 Task 1.

These tests cover the venv-probe priority order and the cache-reuse
contract (ENGINE-07): an existing v0.2.7 user who already cloned
IndexTTS, ran ``uv pip install -e .``, and downloaded the 6 GB model
weights MUST hit zero re-download and zero re-install when they upgrade
to v0.3.x.

We never actually invoke ``uv pip install -e`` here — that would require
network access and minutes of wall-clock per test. Instead, we
monkeypatch ``subprocess.run`` to capture the arguments, and we build
stub ``indextts`` packages on disk so the ``_venv_can_import_indextts``
probe can succeed with the system Python.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import pytest


# tests/conftest.py prepends ./backend to sys.path.
from engines.indextts import bootstrap


# ── helpers ────────────────────────────────────────────────────────────────


def _make_fake_venv(venv_dir: Path) -> Path:
    """Create a minimal venv layout using the existing Python installation.

    Returns a symlink or copy of the running interpreter. Prefix metadata
    locates the existing base Python even when a copied POSIX executable
    can no longer resolve its original installation through a symlink.
    No packages or additional interpreter are installed.
    """
    if sys.platform == "win32":
        bin_dir = venv_dir / "Scripts"
        py_name = "python.exe"
    else:
        bin_dir = venv_dir / "bin"
        py_name = "python"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (venv_dir / "pyvenv.cfg").write_text(
        f"home = {Path(sys._base_executable).parent}\n"
        "include-system-site-packages = false\n",
        encoding="utf-8",
    )
    py_path = bin_dir / py_name
    if py_path.exists():
        py_path.unlink()
    # Symlink avoids copying the whole interpreter binary on macOS/Linux;
    # Windows test paths fall back to copyfile because symlinks require
    # admin rights on some default Windows configurations.
    try:
        py_path.symlink_to(sys.executable)
    except (OSError, NotImplementedError):
        shutil.copyfile(sys.executable, py_path)
        os.chmod(py_path, 0o755)
    return py_path


@pytest.mark.parametrize("force_copy", [False, True])
def test_fake_venv_runs_existing_interpreter(monkeypatch, tmp_path, force_copy):
    if force_copy:
        def deny_symlink(*args, **kwargs):
            raise OSError("symlinks unavailable in this fixture")

        monkeypatch.setattr(Path, "symlink_to", deny_symlink)
    venv_dir = tmp_path / "fixture with spaces"
    python_path = _make_fake_venv(venv_dir)

    process = subprocess.run(
        [str(python_path), "-c", "import sys; print(sys.base_prefix); print(sys.prefix)"],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert process.returncode == 0, process.stderr
    base_prefix, prefix = process.stdout.splitlines()
    assert Path(base_prefix) == Path(sys.base_prefix)
    assert Path(prefix) == venv_dir


def _install_stub_indextts(venv_dir: Path) -> None:
    """Place an importable ``indextts.infer_v2`` stub on the venv's sys.path.

    The probe runs ``python -c "import indextts.infer_v2"`` — it doesn't
    care about the package's contents. We just need the import to
    succeed when invoked under the venv python.

    Because the symlink trick above means the venv python IS the system
    python, we drop the stub into the venv's ``site-packages`` directory
    and set PYTHONPATH at probe time. To do that without polluting the
    test's own env, we wrap the probe via PYTHONPATH directly on the
    bootstrap candidate.
    """
    site_packages = venv_dir / "lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    pkg = site_packages / "indextts"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "infer_v2.py").write_text("class IndexTTS2:\n    pass\n")


def _patch_probe_to_use_pythonpath(monkeypatch, venv_dir: Path) -> None:
    """Wrap ``_venv_can_import_indextts`` so it runs with PYTHONPATH set.

    The venv python is a symlink to the system python (so the stub
    indextts is NOT on its real site-packages). We patch the probe to
    inject PYTHONPATH=<venv>/lib/site-packages so the import resolves.
    """
    original = bootstrap._venv_can_import_indextts

    site_packages = venv_dir / "lib" / "site-packages"

    def wrapped(python_path: Path) -> bootstrap.ProbeResult:
        # Only intercept the matching venv — other paths follow the real
        # probe so we still exercise the failure code path.
        if str(python_path).startswith(str(venv_dir)):
            try:
                proc = subprocess.run(
                    [str(python_path), "-c", "import indextts.infer_v2"],
                    capture_output=True,
                    timeout=10,
                    env={**os.environ, "PYTHONPATH": str(site_packages)},
                )
                return "yes" if proc.returncode == 0 else "no"
            except Exception:
                return "no"
        return original(python_path)

    monkeypatch.setattr(bootstrap, "_venv_can_import_indextts", wrapped)


@pytest.fixture(autouse=True)
def reset_bootstrap_cache() -> Iterator[None]:
    """Clear the per-process resolution cache between tests."""
    bootstrap.invalidate()
    yield
    bootstrap.invalidate()


@pytest.fixture
def isolated_engines_venv(monkeypatch, tmp_path) -> Iterator[Path]:
    """Re-point ``_ENGINES_VENV_DIR`` to a per-test tmpdir.

    Otherwise tests would race the repo's real
    ``backend/engines/indextts/.venv`` (which may exist on a developer
    machine that already installed IndexTTS).
    """
    fake_engines = tmp_path / "engines_venv"
    monkeypatch.setattr(bootstrap, "_ENGINES_VENV_DIR", fake_engines)
    yield fake_engines


# ── ENGINE-07: venv-probe priority order ───────────────────────────────────


def test_venv_probe_prefers_omnivoice_indextts_dir(
    monkeypatch, tmp_path, isolated_engines_venv
):
    """Probe 1 wins when both Probe 1 and Probe 2 are viable.

    Existing v0.2.7 users with OMNIVOICE_INDEXTTS_DIR + .venv must keep
    using their venv even after upgrading to v0.3.x.
    """
    omv_dir = tmp_path / "user_indextts_clone"
    omv_dir.mkdir()
    user_venv = omv_dir / ".venv"
    py_path = _make_fake_venv(user_venv)
    _install_stub_indextts(user_venv)

    # Also create the engines/.venv (Probe 2) so we prove priority.
    engines_venv = isolated_engines_venv
    _make_fake_venv(engines_venv)
    _install_stub_indextts(engines_venv)

    monkeypatch.setenv("OMNIVOICE_INDEXTTS_DIR", str(omv_dir))

    # Patch probe to honour our PYTHONPATH stubbing.
    def wrapped(python_path: Path) -> bootstrap.ProbeResult:
        for venv in (user_venv, engines_venv):
            if str(python_path).startswith(str(venv)):
                site = venv / "lib" / "site-packages"
                proc = subprocess.run(
                    [str(python_path), "-c", "import indextts.infer_v2"],
                    capture_output=True,
                    timeout=10,
                    env={**os.environ, "PYTHONPATH": str(site)},
                )
                return "yes" if proc.returncode == 0 else "no"
        return "no"
    monkeypatch.setattr(bootstrap, "_venv_can_import_indextts", wrapped)

    resolved = bootstrap.resolve_indextts_venv()
    assert resolved == py_path, (
        f"expected probe 1 ({py_path}) to win, got {resolved}"
    )


def test_venv_probe_falls_back_to_engines_path(
    monkeypatch, tmp_path, isolated_engines_venv
):
    """Probe 2 fires when OMNIVOICE_INDEXTTS_DIR is unset."""
    monkeypatch.delenv("OMNIVOICE_INDEXTTS_DIR", raising=False)
    engines_venv = isolated_engines_venv
    py_path = _make_fake_venv(engines_venv)
    _install_stub_indextts(engines_venv)

    _patch_probe_to_use_pythonpath(monkeypatch, engines_venv)

    resolved = bootstrap.resolve_indextts_venv()
    assert resolved == py_path


def test_venv_probe_bootstraps_when_neither_exists(
    monkeypatch, tmp_path, isolated_engines_venv
):
    """No venvs on disk + OMNIVOICE_INDEXTTS_DIR set => uv venv + uv pip install."""
    omv_dir = tmp_path / "user_clone_no_venv"
    omv_dir.mkdir()
    monkeypatch.setenv("OMNIVOICE_INDEXTTS_DIR", str(omv_dir))
    # Pretend uv is available at a stub path.
    fake_uv = tmp_path / "uv-stub"
    fake_uv.write_text("# fake")
    fake_uv.chmod(0o755)
    monkeypatch.setattr(bootstrap, "_locate_uv", lambda: str(fake_uv))

    captured: list[list[str]] = []

    def fake_run(cmd, **kw):
        captured.append(list(cmd))
        # Mimic ``uv venv`` creating the venv layout we promised.
        if len(cmd) >= 2 and cmd[1] == "venv":
            target = Path(cmd[2])
            _make_fake_venv(target)
            _install_stub_indextts(target)
        # Both calls succeed.
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    # Direct probe to read the stub via PYTHONPATH.
    _patch_probe_to_use_pythonpath(monkeypatch, isolated_engines_venv)

    resolved = bootstrap.resolve_indextts_venv()
    assert resolved == bootstrap._venv_python_path(isolated_engines_venv)
    # uv venv came first.
    assert captured[0][:2] == [str(fake_uv), "venv"]
    # Then uv pip install -e <clone>.
    assert any(
        c[:3] == [str(fake_uv), "pip", "install"] and str(omv_dir) in c
        for c in captured
    ), f"expected uv pip install -e {omv_dir} in {captured}"


def test_venv_probe_raises_clear_error_when_no_install_possible(
    monkeypatch, tmp_path, isolated_engines_venv
):
    """No venv on disk AND OMNIVOICE_INDEXTTS_DIR unset => raise with docs link."""
    monkeypatch.delenv("OMNIVOICE_INDEXTTS_DIR", raising=False)
    # Engines venv directory is the per-test tmpdir; it's empty.
    with pytest.raises(RuntimeError) as excinfo:
        bootstrap.resolve_indextts_venv()
    message = str(excinfo.value)
    assert "OMNIVOICE_INDEXTTS_DIR" in message
    assert "docs/engines/indextts.md" in message


# ── ENGINE-07: cache & spawn discipline ────────────────────────────────────


def test_is_indextts_installed_no_spawn(
    monkeypatch, tmp_path, isolated_engines_venv
):
    """is_indextts_installed must NOT invoke any subprocess.

    The Settings UI calls list_backends() on every render — paying for a
    sidecar spawn each time would deadlock the UI and break the
    isolation test in the registry suite.
    """
    engines_venv = isolated_engines_venv
    _make_fake_venv(engines_venv)
    monkeypatch.delenv("OMNIVOICE_INDEXTTS_DIR", raising=False)

    # Hard-fail if anyone calls subprocess.run during is_indextts_installed.
    def boom(*args, **kw):
        raise AssertionError(
            f"is_indextts_installed must not spawn a subprocess; got {args!r}"
        )

    monkeypatch.setattr(subprocess, "run", boom)
    assert bootstrap.is_indextts_installed() is True


def test_is_indextts_installed_returns_false_when_no_venv(
    monkeypatch, tmp_path, isolated_engines_venv
):
    """Negative path — no venv anywhere returns False without spawning."""
    monkeypatch.delenv("OMNIVOICE_INDEXTTS_DIR", raising=False)
    # isolated_engines_venv is an empty tmpdir; no fake venv created.

    def boom(*args, **kw):
        raise AssertionError("must not spawn")

    monkeypatch.setattr(subprocess, "run", boom)
    assert bootstrap.is_indextts_installed() is False


def test_hf_home_marker_present_after_bootstrap(
    monkeypatch, tmp_path, isolated_engines_venv
):
    """HF cache is read-only from the bootstrap path. (ENGINE-07 / Pitfall 4)

    The existing user's downloaded model weights at
    ``$HF_HOME/hub/models--IndexTeam--IndexTTS-2/`` must survive the
    bootstrap byte-for-byte. We seed a marker file, run a full
    resolve_indextts_venv() (Probe 1 path with stubbed indextts), and
    verify the marker is untouched.
    """
    hf_home = tmp_path / "hf_home"
    hub_dir = hf_home / "hub" / "models--IndexTeam--IndexTTS-2"
    hub_dir.mkdir(parents=True)
    marker = hub_dir / "MARKER"
    marker_text = "do-not-redownload-this-is-6gb"
    marker.write_text(marker_text)
    marker_mtime = marker.stat().st_mtime
    monkeypatch.setenv("HF_HOME", str(hf_home))

    # Set up a user-clone-level venv with stub indextts (Probe 1 wins).
    omv_dir = tmp_path / "user_indextts_clone"
    omv_dir.mkdir()
    user_venv = omv_dir / ".venv"
    _make_fake_venv(user_venv)
    _install_stub_indextts(user_venv)
    monkeypatch.setenv("OMNIVOICE_INDEXTTS_DIR", str(omv_dir))

    _patch_probe_to_use_pythonpath(monkeypatch, user_venv)

    # Both queries must complete with the cache marker untouched.
    assert bootstrap.is_indextts_installed() is True
    bootstrap.resolve_indextts_venv()

    assert marker.read_text() == marker_text
    assert marker.stat().st_mtime == marker_mtime


def test_resolve_caches_result(monkeypatch, tmp_path, isolated_engines_venv):
    """Second call returns the cached path without re-probing."""
    omv_dir = tmp_path / "user_clone"
    omv_dir.mkdir()
    user_venv = omv_dir / ".venv"
    _make_fake_venv(user_venv)
    _install_stub_indextts(user_venv)
    monkeypatch.setenv("OMNIVOICE_INDEXTTS_DIR", str(omv_dir))

    _patch_probe_to_use_pythonpath(monkeypatch, user_venv)

    first = bootstrap.resolve_indextts_venv()

    # Replace the probe with a sentinel that would fail if called.
    sentinel_called = {"count": 0}

    def sentinel(_path):
        sentinel_called["count"] += 1
        return "no"

    monkeypatch.setattr(bootstrap, "_venv_can_import_indextts", sentinel)
    second = bootstrap.resolve_indextts_venv()

    assert first == second
    assert sentinel_called["count"] == 0, (
        "second resolve_indextts_venv() call probed the venv again instead "
        "of using the cache"
    )
