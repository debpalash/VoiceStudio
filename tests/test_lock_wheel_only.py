"""First-run setup must never compile an sdist on a user's machine (#4253).

`uv sync` builds any locked package that ships no wheel. On some Windows hosts
the build crashes setuptools (`Failed to build docopt==0.6.2`), which blocks
the runtime install and the backend never starts. Every package reachable from
the default install on Windows, macOS and Linux must therefore lock to a wheel.
Source-only PyPI packages are vendored as wheels under `vendor/wheels/` and
pointed at from `[tool.uv.sources]`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.markers import Marker

ROOT = Path(__file__).resolve().parent.parent

# Intel macOS runs the UI against a remote backend (README, docs/install/macos.md);
# its demucs 4.0.1 pin is never installed on Windows, Linux or Apple Silicon.
_ALLOWED_SDIST_ONLY: dict[tuple[str, str], str] = {
    ("demucs", "4.0.1"): "Intel-Mac-only resolution; no Windows/Linux install",
}

_PLATFORMS = [
    {"sys_platform": "win32", "platform_machine": "AMD64", "os_name": "nt", "platform_system": "Windows"},
    {"sys_platform": "win32", "platform_machine": "ARM64", "os_name": "nt", "platform_system": "Windows"},
    {"sys_platform": "linux", "platform_machine": "x86_64", "os_name": "posix", "platform_system": "Linux"},
    {"sys_platform": "linux", "platform_machine": "aarch64", "os_name": "posix", "platform_system": "Linux"},
    {"sys_platform": "darwin", "platform_machine": "arm64", "os_name": "posix", "platform_system": "Darwin"},
]
_PYTHONS = ["3.11", "3.12"]
_EXTRAS = ["group-9-omnivoice-cuda", "group-9-omnivoice-cpu"]


def _lock() -> dict:
    return tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))


def _reachable(lock: dict, env: dict[str, str], extra: str) -> set[tuple[str, str]]:
    pkgs: dict[str, list[dict]] = {}
    for p in lock["package"]:
        pkgs.setdefault(p["name"], []).append(p)
    root = next(p for p in lock["package"] if "editable" in p["source"] or "virtual" in p["source"])
    seen: set[tuple[str, str]] = set()
    stack = [root]
    while stack:
        pkg = stack.pop()
        for dep in pkg.get("dependencies", []):
            marker = dep.get("marker")
            if marker and not Marker(marker).evaluate({**env, "extra": extra}):
                continue
            for cand in pkgs.get(dep["name"], []):
                if "version" in dep and cand.get("version") != dep["version"]:
                    continue
                key = (cand["name"], cand.get("version", ""))
                if key not in seen:
                    seen.add(key)
                    stack.append(cand)
    return seen


def test_default_install_never_builds_an_sdist() -> None:
    lock = _lock()
    by_key = {(p["name"], p.get("version", "")): p for p in lock["package"]}
    offenders: set[tuple[str, str]] = set()
    for env in _PLATFORMS:
        for py in _PYTHONS:
            full = {**env, "python_version": py, "python_full_version": f"{py}.0", "implementation_name": "cpython"}
            for extra in _EXTRAS:
                for key in _reachable(lock, full, extra):
                    p = by_key[key]
                    if "wheels" not in p and key not in _ALLOWED_SDIST_ONLY:
                        offenders.add(key)
    assert not offenders, (
        f"locked without a wheel, so first-run setup would compile them: {sorted(offenders)}. "
        "Vendor a wheel under vendor/wheels/ and add it to [tool.uv.sources] (#4253)."
    )


def test_vendored_wheels_are_shipped_with_the_app() -> None:
    config = (ROOT / "electron" / "electron-builder.config.mjs").read_text(encoding="utf-8")
    assert "vendor/wheels" in config, "packaged setup resolves path wheels relative to the bundled uv.lock"
    for p in _lock()["package"]:
        src = p["source"].get("path")
        if src:
            assert (ROOT / src).is_file(), f"{p['name']} points at missing {src}"
