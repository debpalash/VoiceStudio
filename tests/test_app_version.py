"""The runtime app version must come from this checkout, not a stale cache.

A checkout declares its version in pyproject.toml; the installed metadata is a
cache that a skipped re-sync leaves behind, so it is only the fallback.
"""
import re

from core.version import APP_VERSION


def test_app_version_is_semver():
    assert re.match(r"^\d+\.\d+\.\d+", APP_VERSION), APP_VERSION


def test_app_version_matches_the_checkout_not_a_stale_install():
    """A venv whose metadata lags ``pyproject.toml`` must not change the reported version.

    The old test compared ``APP_VERSION`` with ``importlib.metadata.version``,
    which is the same source when the package is installed, so it could never
    catch the drift it was written for: a checkout bumped without a re-sync kept
    reporting the old version.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pyproject = re.search(
        r'(?m)^version\s*=\s*"([^"]+)"',
        (root / "pyproject.toml").read_text(encoding="utf-8"),
    ).group(1)
    assert APP_VERSION == pyproject, (
        f"the backend reports {APP_VERSION!r} while this checkout declares {pyproject!r}"
    )


def test_installed_metadata_is_only_a_fallback(monkeypatch):
    """The checkout wins, then the installed metadata, then the literal."""
    import core.version as version_module

    # Both present and different: the checkout decides. Without this the test
    # would pass on a runner whose metadata happens to match the checkout.
    monkeypatch.setattr(version_module, "_source_version", lambda: "1.2.3")
    monkeypatch.setattr(version_module, "_installed_version", lambda: "9.9.9")
    assert version_module._resolve_version() == "1.2.3"

    monkeypatch.setattr(version_module, "_source_version", lambda: None)
    assert version_module._resolve_version() == "9.9.9"

    monkeypatch.setattr(version_module, "_installed_version", lambda: None)
    assert version_module._resolve_version() == version_module._FALLBACK_VERSION


def test_an_unreadable_manifest_falls_through_to_metadata(monkeypatch, tmp_path):
    """A pyproject that cannot be decoded must not stop `core.version` importing."""
    import core.version as version_module

    broken = tmp_path / "pyproject.toml"
    broken.write_bytes(b'\xff\xfeversion = "9.9.9"')
    monkeypatch.setattr(version_module, "_installed_version", lambda: "8.8.8")

    class _BrokenPath:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        @staticmethod
        def resolve() -> "_BrokenPath":
            return _BrokenPath()

        @property
        def parents(self) -> tuple:
            return (tmp_path,)

    monkeypatch.setattr(version_module, "Path", _BrokenPath)
    assert version_module._resolve_version() == "8.8.8"


def test_all_version_files_in_lockstep():
    """The root ``package.json`` is the SINGLE SOURCE OF TRUTH for the app
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

    canonical = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
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


def test_ios_app_reads_the_full_version():
    # CFBundleShortVersionString is X.Y.Z only; About and licence receipts must show
    # the root version like Android's versionName (AppVersion.full).
    from pathlib import Path
    sources = (Path(__file__).resolve().parents[1] / "mobile/ios/VoiceStudio").rglob("*.swift")
    offenders = [p.name for p in sources if "CFBundleShortVersionString" in p.read_text(encoding="utf-8")]
    assert not offenders
