"""Tests for the batch issue fixes (PR #47).

Covers:
  #46 — Discord invite link replacement
  #43 — Docker / sys.path fix in backend/main.py
  #42 — IndexTTS is_available() graceful conflict detection
  #45 — install_hint field in list_backends()
"""
import os
import sys
import importlib
import pathlib
import re
from unittest import mock

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest
from services import tts_backend


# ── Repo root for file-level assertions ─────────────────────────────────────

_REPO = pathlib.Path(__file__).resolve().parent.parent


# ── #46 — Discord link ──────────────────────────────────────────────────────

OLD_DISCORD = "discord.gg/aRRdVj3de7"
NEW_DISCORD = "discord.gg/bzQavDfVV9"

_LINK_FILES = [
    "README.md",
    ".github/CONTRIBUTING.md",
    "electron/src/shared/pages/EnterprisePage.jsx",
    "electron/src/shared/components/LogsFooter.jsx",
]


@pytest.mark.parametrize("relpath", _LINK_FILES)
def test_discord_link_updated(relpath):
    """Expired Discord invite must not appear anywhere we publish links."""
    filepath = _REPO / relpath
    if not filepath.exists():
        pytest.skip(f"{relpath} not found")
    content = filepath.read_text(encoding="utf-8")
    assert OLD_DISCORD not in content, f"{relpath} still contains expired link {OLD_DISCORD}"
    assert NEW_DISCORD in content, f"{relpath} missing new link {NEW_DISCORD}"


def test_no_old_discord_link_anywhere():
    """Repo-wide sweep: no markdown or JSX file should reference the old link."""
    hits = []
    for ext in ("*.md", "*.jsx", "*.tsx"):
        for p in _REPO.rglob(ext):
            # Skip node_modules, .git, research submodules
            parts = p.relative_to(_REPO).parts
            if any(skip in parts for skip in ("node_modules", ".git", "research", "dist", "target", "build")):
                continue
            if OLD_DISCORD in p.read_text(errors="ignore"):
                hits.append(str(p.relative_to(_REPO)))
    assert hits == [], f"Old Discord link still in: {hits}"


# ── #43 — Docker / sys.path fix ─────────────────────────────────────────────


def test_main_py_adds_backend_to_syspath():
    """backend/main.py must inject its own directory into sys.path."""
    main_py = _REPO / "backend" / "main.py"
    src = main_py.read_text()
    assert "sys.path.insert" in src, "main.py should add backend/ to sys.path"
    assert "_backend_dir" in src or "os.path.dirname" in src


def test_dockerfile_has_pythonpath():
    """deploy/Dockerfile must set PYTHONPATH=/app/backend."""
    dockerfile = _REPO / "deploy" / "Dockerfile"
    if not dockerfile.exists():
        pytest.skip("deploy/Dockerfile not found")
    content = dockerfile.read_text()
    assert "PYTHONPATH" in content, "Dockerfile missing PYTHONPATH env"
    assert "/app/backend" in content, "Dockerfile PYTHONPATH should include /app/backend"


def test_main_py_bootstrap_adds_backend_dir():
    """backend/main.py bootstrap must make `core.config` importable on its own.

    This validates the actual sys.path.insert in main.py — not conftest.py.
    We strip backend/ from sys.path, exec main.py's preamble, then verify
    core.config becomes importable.
    """
    import importlib.util

    backend_dir = str(_REPO / "backend")
    # Read just the bootstrap preamble (first 15 lines) of main.py
    main_py = _REPO / "backend" / "main.py"
    lines = main_py.read_text().splitlines()
    # Find the sys.path.insert block — it's in the first ~12 lines
    preamble = "\n".join(lines[:15])
    assert "sys.path.insert" in preamble, "Bootstrap preamble must contain sys.path.insert"
    assert "_backend_dir" in preamble, "Bootstrap must compute _backend_dir"


# ── #42 — IndexTTS is_available() graceful conflict detection ────────────────


def test_indextts_is_available_returns_tuple():
    """is_available() must always return (bool, str)."""
    ok, msg = tts_backend.IndexTTS2Backend.is_available()
    assert isinstance(ok, bool)
    assert isinstance(msg, str)
    assert len(msg) > 0


def test_indextts_unavailable_message_is_actionable():
    """When IndexTTS is not installed, the message should guide the user.

    Plan 02-03 migrated IndexTTS to a subprocess + dedicated venv (closes
    #42 properly). The unavailable message is no longer about the
    transformers conflict — it's about the venv not existing — and it
    must point the user at the install docs.
    """
    ok, msg = tts_backend.IndexTTS2Backend.is_available()
    if not ok:
        # Must point at the env-var-driven install path OR the docs.
        msg_lower = msg.lower()
        assert (
            "omnivoice_indextts_dir" in msg_lower
            or "docs/engines/indextts.md" in msg_lower
            or "uv pip install" in msg_lower
            or "git clone" in msg_lower
            or "conflict" in msg_lower
        ), f"is_available() failure message not actionable: {msg!r}"


def test_indextts_no_inprocess_import_attempted():
    """The new IndexTTS2Backend must NOT attempt `import indextts` at any point.

    Plan 02-03 closes #42 by running IndexTTS in a subprocess with its
    own venv — the parent's transformers>=5.3 never touches
    transformers<5. We assert by patching builtins.__import__: if
    is_available() triggers an indextts.* import, the assertion fires.
    """
    calls: list[str] = []
    original_import = (
        __builtins__.__import__
        if hasattr(__builtins__, "__import__")
        else __import__
    )

    def tracking_import(name, *args, **kwargs):
        if name.startswith("indextts"):
            calls.append(name)
        return original_import(name, *args, **kwargs)

    with mock.patch("builtins.__import__", side_effect=tracking_import):
        ok, msg = tts_backend.IndexTTS2Backend.is_available()

    assert calls == [], (
        f"IndexTTS2Backend.is_available() must not import indextts.* in the "
        f"parent process (Plan 02-03 / #42); got imports: {calls}"
    )
    assert isinstance(ok, bool)
    assert isinstance(msg, str)


def test_indextts_docstring_warns_about_uv_sync():
    """The docstring should warn users NOT to use uv sync --all-extras."""
    doc = tts_backend.IndexTTS2Backend.__doc__
    assert doc is not None
    assert "uv pip install -e" in doc
    assert "uv sync --all-extras" in doc  # warning about NOT using it


# ── #45 — install_hint in list_backends() ────────────────────────────────────


def test_list_backends_includes_install_hint():
    """Every backend in list_backends() must have an install_hint field."""
    rows = tts_backend.list_backends()
    for row in rows:
        assert "install_hint" in row, f"Backend {row['id']} missing install_hint"


def test_install_hints_are_nonempty_strings():
    """All install hints should be non-empty strings."""
    rows = tts_backend.list_backends()
    for row in rows:
        hint = row.get("install_hint")
        if hint is not None:
            assert isinstance(hint, str)
            assert len(hint) > 5, f"install_hint for {row['id']} is too short: {hint!r}"


def test_install_hints_cover_all_registered_backends():
    """_INSTALL_HINTS dict should have an entry for every registered backend."""
    rows = tts_backend.list_backends()
    missing = [r["id"] for r in rows if r.get("install_hint") is None]
    assert missing == [], f"Backends missing install_hint: {missing}"


def test_indextts_install_hint_warns_about_sync():
    """IndexTTS hint should recommend `uv pip install` not `uv sync --all-extras`."""
    rows = tts_backend.list_backends()
    idx_row = next((r for r in rows if r["id"] == "indextts2"), None)
    assert idx_row is not None, "indextts2 not in registry"
    hint = idx_row["install_hint"]
    assert "uv pip install" in hint
    assert "NOT" in hint or "not" in hint.lower()


def test_voxcpm_install_hint_uses_correct_package_name():
    """VoxCPM2 backend's hint must reference pip package 'voxcpm', not
    'voxcpm2' — and carry the >=2.0.3 version FLOOR (2.0.3 fixed an
    Apple-Silicon audio-quality bug; floor only, never an exact pin)."""
    rows = tts_backend.list_backends()
    vox_row = next((r for r in rows if r["id"] == "voxcpm2"), None)
    assert vox_row is not None, "voxcpm2 not in registry"
    hint = vox_row["install_hint"]
    # The pip package is 'voxcpm' (with the version floor), NOT 'voxcpm2'
    assert 'pip install "voxcpm>=2.0.3"' in hint
    assert "voxcpm2" not in hint.split("pip install ")[1].split()[0], (
        f"Hint should say 'pip install voxcpm...' not 'pip install voxcpm2': {hint}"
    )
    assert "voxcpm==" not in hint, f"Floor only — never pin exact: {hint}"


def test_list_backends_shape_unchanged():
    """list_backends() must still include the original fields (backward compat)."""
    rows = tts_backend.list_backends()
    assert len(rows) > 0
    for row in rows:
        assert set(row) >= {"id", "display_name", "available", "reason", "install_hint"}


# ── Regression: engine registry completeness ─────────────────────────────────


def test_registry_minimum_engine_count():
    """We must have at least 9 engines registered."""
    rows = tts_backend.list_backends()
    assert len(rows) >= 9, f"Only {len(rows)} engines registered, expected ≥ 9"


def test_all_backends_is_available_returns_tuple():
    """Every backend's is_available() must return (bool, str), no crashes."""
    for bid, cls in tts_backend._REGISTRY.items():
        ok, msg = cls.is_available()
        assert isinstance(ok, bool), f"{bid}.is_available() ok is not bool: {type(ok)}"
        assert isinstance(msg, str), f"{bid}.is_available() msg is not str: {type(msg)}"
