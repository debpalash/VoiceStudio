"""The runtime app version must come from package metadata, not a stale literal
(prevents the recurring "0.4.0"/"0.2.7" drift — Greptile #145)."""
import re
from importlib.metadata import version

from core.version import APP_VERSION


def test_app_version_is_semver():
    assert re.match(r"^\d+\.\d+\.\d+", APP_VERSION), APP_VERSION


def test_app_version_matches_installed_package_metadata():
    # In any synced env the package is installed; APP_VERSION must equal it
    # (i.e. it's read from pyproject, not hardcoded).
    assert APP_VERSION == version("omnivoice")


def test_all_version_files_in_lockstep():
    """``frontend/package.json`` is the SINGLE SOURCE OF TRUTH for the app
    version: Electron's builder reads it and Vite injects ``__APP_VERSION__``.

    The maintained toolchain-required CI-guarded mirrors are pyproject.toml and
    backend/core/version.py's ``_FALLBACK_VERSION`` (the frozen-backend last
    resort). Archived Tauri manifests stay frozen at their final release.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]

    # Every read names utf-8: these files carry em dashes, and a bare
    # read_text() decodes in the locale code page, so this guard could not run
    # at all on a Chinese, Japanese or Korean Windows
    # (tests/test_repo_data_locale_decoding.py).
    def _toml_version(p: Path) -> str:
        return re.search(
            r'(?m)^version\s*=\s*"([^"]+)"', p.read_text(encoding="utf-8")
        ).group(1)

    def _named_literal(p: Path, name: str) -> str:
        return re.search(
            rf'(?m)^{name}\s*=\s*"([^"]+)"', p.read_text(encoding="utf-8")
        ).group(1)

    import json

    canonical = json.loads(
        (root / "frontend/package.json").read_text(encoding="utf-8")
    )["version"]
    mirrors = {
        "pyproject.toml": _toml_version(root / "pyproject.toml"),
        "core/version.py": _named_literal(root / "backend/core/version.py", "_FALLBACK_VERSION"),
    }
    drifted = {k: v for k, v in mirrors.items() if v != canonical}
    assert not drifted, f"version mirrors drifted from package.json={canonical!r}: {drifted}"


def test_release_policy_requires_owner_approved_manual_bumps():
    """The maintained mirrors follow an owner-approved bump, never an automatic one."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    constitution = (root / "CLAUDE.md").read_text(encoding="utf-8")
    release_guide = (root / "docs/RELEASING.md").read_text(encoding="utf-8")

    assert "main is always **latest release + 1 patch**" not in constitution
    assert "Version bumps are manual and happen only when the owner asks" in constitution
    assert "Version bumps are manual and require owner approval" in release_guide


def test_fallback_version_resolves_to_pyproject():
    """When package metadata is unavailable (frozen build / raw checkout), the
    version must still resolve to pyproject — never the stale literal that made
    the v0.3.6 build report "0.3.5"."""
    from pathlib import Path

    from core.version import _fallback_version

    root = Path(__file__).resolve().parents[1]
    pyproject = re.search(
        r'(?m)^version\s*=\s*"([^"]+)"',
        (root / "pyproject.toml").read_text(encoding="utf-8"),
    ).group(1)
    assert _fallback_version() == pyproject


def test_frozen_build_collects_package_metadata():
    """backend.spec must copy_metadata('omnivoice') so the frozen backend reads
    its real version via importlib.metadata instead of the fallback literal."""
    from pathlib import Path

    spec = (Path(__file__).resolve().parents[1] / "backend.spec").read_text(
        encoding="utf-8"
    )
    assert (
        "copy_metadata('omnivoice')" in spec or 'copy_metadata("omnivoice")' in spec
    ), "backend.spec must copy_metadata('omnivoice') (frozen-build version reporting)"
