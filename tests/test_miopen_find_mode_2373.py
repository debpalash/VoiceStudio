"""MIOpen must start in FAST find mode by default (#2373).

MIOpen's default find mode runs an exhaustive algorithm search the first time
it sees each convolution *shape*. BigVGAN in the IndexTTS sidecar gets a
different mel length nearly every chunk, so ROCm hosts paid ~18 s per new
shape inside a synthesis. ``MIOPEN_FIND_MODE=FAST`` turns that into well
under a second with a near-optimal kernel (verified: 17–24 s chunks →
~0.7 s end to end ~1.4x realtime).

These tests pin the two properties of the fix that a reviewer would
otherwise have to remember: it must be ``setdefault`` (an exported override
wins), and it must land after the prefs restore in ``backend/main.py`` (a
saved or exported value that arrives first is what ``setdefault`` protects).
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "backend" / "main.py"


def _main_source() -> str:
    return _MAIN.read_text(encoding="utf-8")


def test_the_fast_default_exists_as_a_setdefault():
    src = _main_source()
    lines = [
        ln
        for ln in src.splitlines()
        if "MIOPEN_FIND_MODE" in ln and not ln.strip().startswith("#")
    ]
    assert len(lines) == 1, (
        "backend/main.py should hold exactly one MIOPEN_FIND_MODE statement, "
        f"found {len(lines)}: {[ln.strip() for ln in lines]}"
    )
    line = lines[0]
    # An assignment would clobber an exported MIOPEN_FIND_MODE — the issue
    # asks for the FAST default "unless the user overrides it".
    assert "os.environ.setdefault(" in line, (
        f"MIOPEN_FIND_MODE must be set with setdefault, got: {line.strip()}"
    )
    assert '"FAST"' in line


def test_the_default_lands_after_the_prefs_restore():
    """Position is the actual fix.

    ``core.prefs.restore_env`` applies saved ``env.*`` prefs with
    ``setdefault`` too, so anything we set before it would shadow a saved
    value; anything set after it still wins over nothing at all. One line,
    one order — a test, not a comment.
    """
    src = _main_source()
    i_restore = src.index("restore_env(_load_all_prefs())")
    i_default = src.index('os.environ.setdefault("MIOPEN_FIND_MODE"')
    assert i_restore < i_default, (
        "MIOPEN_FIND_MODE must be set after restore_env so externally "
        "provided or saved values win"
    )
    # ...and before torch/HIP can possibly load: env_prefs precedes
    # native_preload / ml_imports in the startup sequence.
    i_preload = src.index("_preload_cudnn8()")
    assert i_default < i_preload


def test_nothing_in_the_backend_assigns_the_find_mode():
    """A plain ``os.environ[...] = ...`` anywhere would defeat the
    setdefault contract (the class of bug, not the instance)."""
    for path in (_ROOT / "backend").rglob("*.py"):
        src = path.read_text(encoding="utf-8", errors="replace")
        for i, ln in enumerate(src.splitlines(), 1):
            stripped = ln.strip()
            if stripped.startswith("#"):
                continue
            assert "MIOPEN_FIND_MODE] =" not in stripped and 'MIOPEN_FIND_MODE"] =' not in stripped, (
                f"{path}:{i} assigns MIOPEN_FIND_MODE directly — use setdefault"
            )
